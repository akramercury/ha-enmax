"""Fetches Enmax data and imports it into Home Assistant long-term statistics.

Enmax only publishes per-bill (roughly monthly) totals. Each bill's usage and
cost is spread evenly over the days of its billing period and written as
hourly external statistics (one point per day at local midnight), which the
Energy dashboard and statistics-graph cards can show by day, week or month.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any

import aiohttp

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
    list_statistic_ids,
    statistics_during_period,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, UnitOfEnergy, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import EnergyConverter, VolumeConverter

from .api import EnmaxAuthError, EnmaxClient, EnmaxError
from .const import (
    CONF_ACCOUNT_NO,
    CONF_BP,
    CONF_STATS_VERSION,
    DOMAIN,
    HISTORY_DAYS,
    STATS_VERSION,
    UPDATE_INTERVAL,
    WASTE_KEYS,
)

try:  # HA 2025.7+
    from homeassistant.components.recorder.models import StatisticMeanType

    _MEAN = {"mean_type": StatisticMeanType.NONE}
except ImportError:  # older HA
    _MEAN = {"has_mean": False}

_LOGGER = logging.getLogger(__name__)


def _d(value: Any) -> date | None:
    if not value or str(value).startswith("0001"):
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _num(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _g(obj: Any, *path: str) -> Any:
    for p in path:
        obj = obj.get(p) if isinstance(obj, dict) else None
    return obj


def waste_amount(municipal: dict[str, Any] | None) -> float | None:
    vals = [_num(_g(municipal, k, "amount")) for k in WASTE_KEYS]
    vals = [v for v in vals if v is not None]
    return round(sum(vals), 2) if vals else None


# series key -> (name, unit, unit_class, section, getter(record) -> value, period getter)
def _period(
    rec: dict[str, Any], section: dict[str, Any] | None, prev_end: date | None
) -> tuple[date, date] | None:
    """Billing period for one series. Uses explicit dates when present; otherwise
    runs from the day after the previous bill's period up to the day before this bill."""
    start, end = _d(_g(section, "start_date")), _d(_g(section, "end_date"))
    if start and end and end >= start:
        if prev_end and start <= prev_end:  # never overlap the previous bill
            start = prev_end + timedelta(days=1)
        return (start, end) if end >= start else None
    bill = _d(rec.get("bill_date"))
    if not bill:
        return None
    end = bill - timedelta(days=1)
    if prev_end:
        start = prev_end + timedelta(days=1)
    else:
        start = bill - timedelta(days=int(_num(_g(section, "days_billed")) or 30))
    return (start, end) if end >= start else None


# Old Opower (Enmax) statistic ids look like opower:enmx_elec_<id>_energy_consumption
LEGACY_MATCH: dict[str, tuple[str, str]] = {
    "electricity_consumption": ("_elec_", "_consumption"),
    "electricity_cost": ("_elec_", "_cost"),
    "gas_consumption": ("_gas_", "_consumption"),
    "gas_cost": ("_gas_", "_cost"),
}

SERIES: dict[str, dict[str, Any]] = {
    "electricity_consumption": {
        "name": "Electricity consumption", "unit": UnitOfEnergy.KILO_WATT_HOUR, "unit_class": EnergyConverter.UNIT_CLASS,
        "section": lambda r: r.get("electricity"), "value": lambda r: _num(_g(r, "electricity", "usage")),
    },
    "electricity_cost": {
        "name": "Electricity cost", "unit": None, "unit_class": None,
        "section": lambda r: r.get("electricity"), "value": lambda r: _num(_g(r, "electricity", "bill_amount")),
    },
    "gas_consumption": {
        "name": "Gas consumption", "unit": UnitOfEnergy.GIGA_JOULE, "unit_class": EnergyConverter.UNIT_CLASS,
        "section": lambda r: r.get("natural_gas"), "value": lambda r: _num(_g(r, "natural_gas", "usage")),
    },
    "gas_cost": {
        "name": "Gas cost", "unit": None, "unit_class": None,
        "section": lambda r: r.get("natural_gas"), "value": lambda r: _num(_g(r, "natural_gas", "bill_amount")),
    },
    "water_consumption": {
        "name": "Water consumption", "unit": UnitOfVolume.CUBIC_METERS, "unit_class": VolumeConverter.UNIT_CLASS,
        "section": lambda r: _g(r, "municipal", "water"), "value": lambda r: _num(_g(r, "municipal", "water", "usage")),
    },
    "water_cost": {  # water + wastewater + stormwater = what the Energy dashboard calls water cost
        "name": "Water cost (incl. wastewater & stormwater)", "unit": None, "unit_class": None,
        "section": lambda r: _g(r, "municipal", "water"),
        "value": lambda r: _sum_or_none(
            _num(_g(r, "municipal", "water", "amount")),
            _num(_g(r, "municipal", "wastewater", "amount")),
            _num(_g(r, "municipal", "stormwater_management", "amount")),
        ),
    },
    "waste_cost": {
        "name": "Waste & recycling cost", "unit": None, "unit_class": None,
        "section": lambda r: r.get("municipal"), "value": lambda r: waste_amount(r.get("municipal")),
    },
}


def _sum_or_none(*vals: float | None) -> float | None:
    vals2 = [v for v in vals if v is not None]
    return round(sum(vals2), 2) if vals2 else None


class EnmaxCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=DOMAIN, update_interval=UPDATE_INTERVAL)
        self.account_no: str = entry.data[CONF_ACCOUNT_NO]
        self.bp: str | None = entry.data.get(CONF_BP)
        self.client = EnmaxClient(
            async_get_clientsession(hass), entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD]
        )

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            if not self.bp:
                accts = await self.client.associated_accounts()
                self.bp = next((a.get("bp_id") for a in accts if a["account_no"] == self.account_no), None)
            full = self.config_entry.data.get(CONF_STATS_VERSION) != STATS_VERSION
            # Enmax only returns the latest 24 bills (older start dates give HTTP 400)
            insights = await self.client.energy_insights(
                self.account_no, date.today() - timedelta(days=HISTORY_DAYS), date.today()
            )
            balance = await self.client.balance(self.account_no, self.bp) if self.bp else {}
            invoices = await self.client.invoices(self.account_no)
            plans = await self.client.plans(self.account_no)
        except EnmaxAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except (EnmaxError, aiohttp.ClientError, TimeoutError) as err:
            raise UpdateFailed(str(err)) from err

        insights = sorted(insights, key=lambda r: r.get("bill_date") or "")
        invoices = sorted(
            [b for b in invoices if b.get("doc_ind") == "I"], key=lambda b: b.get("bill_date") or "", reverse=True
        )
        await self._import_statistics(insights, full)
        if full:
            if insights:
                _LOGGER.info(
                    "Enmax full import: %s bills from %s to %s",
                    len(insights), insights[0].get("bill_date", "")[:10], insights[-1].get("bill_date", "")[:10],
                )
            self.hass.config_entries.async_update_entry(
                self.config_entry, data={**self.config_entry.data, CONF_STATS_VERSION: STATS_VERSION}
            )
        return {
            "insights": insights,
            "latest": insights[-1] if insights else {},
            "balance": balance or {},
            "invoices": invoices,
            "plans": plans or {},
        }

    # ------------------------------------------------------------ statistics
    def statistic_id(self, key: str) -> str:
        return f"{DOMAIN}:{self.account_no}_{key}"

    async def _import_statistics(self, insights: list[dict[str, Any]], full: bool = False) -> None:
        """Write statistics. full=True rewrites every day from the first bill (sums
        restart at 0); points with the same timestamp are overwritten, so this also
        cleanly replaces anything imported earlier."""
        tz = dt_util.get_time_zone(self.hass.config.time_zone)
        for key, spec in SERIES.items():
            sid = self.statistic_id(key)

            # spread each bill evenly over its days
            daily: dict[date, float] = {}
            prev_end: date | None = None
            for rec in insights:
                value = spec["value"](rec)
                period = _period(rec, spec["section"](rec), prev_end)
                if value is None or period is None:
                    continue
                start, end = period
                prev_end = end
                n = (end - start).days + 1
                for i in range(n):
                    day = start + timedelta(days=i)
                    daily[day] = daily.get(day, 0.0) + value / n
            if not daily:
                continue

            running, last_ts = 0.0, None
            stats: list[StatisticData] = []
            first_day = min(daily)
            first_dt = datetime(first_day.year, first_day.month, first_day.day, tzinfo=tz)

            if full:
                # keep history from before Enmax's 24-bill window: our own earlier
                # points if we have them, otherwise the old Opower statistics
                for start_dt, delta in await self._legacy_history(key, sid, first_dt, daily, tz):
                    running += delta
                    stats.append(StatisticData(start=start_dt, state=round(delta, 4), sum=round(running, 4)))
            else:
                last = await get_instance(self.hass).async_add_executor_job(
                    get_last_statistics, self.hass, 1, sid, True, {"sum"}
                )
                if last.get(sid):
                    running = float(last[sid][0].get("sum") or 0.0)
                    last_ts = _ts(last[sid][0]["start"])

            for day in sorted(daily):
                start_dt = datetime(day.year, day.month, day.day, tzinfo=tz)
                if last_ts is not None and start_dt.timestamp() <= last_ts:
                    continue
                running += daily[day]
                stats.append(StatisticData(start=start_dt, state=round(daily[day], 4), sum=round(running, 4)))
            if not stats:
                continue

            meta = StatisticMetaData(
                **_MEAN,
                has_sum=True,
                name=f"Enmax {spec['name']}",
                source=DOMAIN,
                statistic_id=sid,
                unit_class=spec["unit_class"],
                unit_of_measurement=spec["unit"],
            )
            _LOGGER.debug("Adding %s statistics for %s", len(stats), sid)
            async_add_external_statistics(self.hass, meta, stats)

    # ------------------------------------------------------------ legacy history
    async def _rows(self, sid: str, start: datetime, end: datetime | None) -> list[tuple[float, float]]:
        """(start_ts, delta) for every stored hour of a statistic, oldest first."""
        res = await get_instance(self.hass).async_add_executor_job(
            statistics_during_period, self.hass, start, end, {sid}, "hour", None, {"sum"}
        )
        out, prev = [], 0.0
        for row in res.get(sid, []):
            total = row.get("sum")
            if total is None:
                continue
            out.append((_ts(row["start"]), float(total) - prev))
            prev = float(total)
        return out

    async def _legacy_history(
        self, key: str, sid: str, first_dt: datetime, daily: dict[date, float], tz
    ) -> list[tuple[datetime, float]]:
        epoch = datetime(2000, 1, 1, tzinfo=tz)

        # 1) our own points from before the Enmax window (already merged earlier)
        own = [(t, d) for t, d in await self._rows(sid, epoch, None) if t < first_dt.timestamp()]
        if own:
            _LOGGER.info("%s: keeping %s earlier points already in Home Assistant", sid, len(own))
            return [(datetime.fromtimestamp(t, tz), d) for t, d in own]

        # 2) old Opower statistics
        if key not in LEGACY_MATCH:
            return []
        kind, suffix = LEGACY_MATCH[key]
        ids = await get_instance(self.hass).async_add_executor_job(
            list_statistic_ids, self.hass, None, "sum"
        )
        legacy = [
            i["statistic_id"] for i in ids
            if i.get("source") == "opower" and "enmx" in i["statistic_id"]
            and kind in i["statistic_id"] and i["statistic_id"].endswith(suffix)
        ]
        if not legacy:
            return []
        if len(legacy) > 1:
            _LOGGER.warning("%s: several Opower statistics match (%s); using %s", sid, legacy, legacy[0])
        old = await self._rows(legacy[0], epoch, None)
        if not old:
            return []

        # unit check: compare totals over the months both sources cover
        # Opower rows are per billing period, stamped at the period start. Compare
        # both sources over [first Opower row inside the Enmax window, last Opower row).
        factor = 1.0
        first_ts = first_dt.timestamp()
        inside = [t for t, _ in old if t >= first_ts]
        old_overlap = new_overlap = 0.0
        if len(inside) >= 2:
            w0, w1 = inside[0], inside[-1]
            old_overlap = sum(d for t, d in old if w0 <= t < w1)
            new_overlap = sum(
                v for day, v in daily.items()
                if w0 <= datetime(day.year, day.month, day.day, tzinfo=tz).timestamp() < w1
            )
        if old_overlap > 0 and new_overlap > 0:
            ratio = new_overlap / old_overlap
            if key == "gas_consumption":
                factor = ratio  # Opower labelled Enmax gas as CCF; Enmax reports GJ
            _LOGGER.info("%s: Opower→Enmax ratio over overlap = %.4f (factor used %.4f)", sid, ratio, factor)
        elif key == "gas_consumption":
            _LOGGER.warning("%s: no overlap with Opower gas; importing it unconverted", sid)

        keep = [(datetime.fromtimestamp(t, tz), d * factor) for t, d in old if t < first_dt.timestamp() and d > 0]
        _LOGGER.info("%s: merged %s points of old history from %s", sid, len(keep), legacy[0])
        return keep


def _ts(value: Any) -> float:
    return value.timestamp() if isinstance(value, datetime) else float(value)
