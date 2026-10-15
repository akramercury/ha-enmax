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
    FULL_HISTORY_START,
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
            insights = None
            if full:
                # First run (or after an upgrade): ask for everything Enmax has.
                try:
                    insights = await self.client.energy_insights(self.account_no, FULL_HISTORY_START, date.today())
                except EnmaxAuthError:
                    raise
                except EnmaxError as err:
                    _LOGGER.warning("Enmax refused full history (%s); using last %s days", err, HISTORY_DAYS)
            if not insights:
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
            last = {} if full else await get_instance(self.hass).async_add_executor_job(
                get_last_statistics, self.hass, 1, sid, True, {"sum"}
            )
            if last.get(sid):
                running = float(last[sid][0].get("sum") or 0.0)
                last_ts = last[sid][0]["start"]
                if isinstance(last_ts, datetime):
                    last_ts = last_ts.timestamp()

            stats: list[StatisticData] = []
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
