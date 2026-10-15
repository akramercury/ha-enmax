"""Enmax sensors: balance, latest bill breakdown, plans."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, WASTE_KEYS
from .coordinator import EnmaxCoordinator, _d, _g, _num, waste_amount

CUR = "CAD"


@dataclass(frozen=True, kw_only=True)
class EnmaxSensorDescription(SensorEntityDescription):
    value_fn: Callable[[dict[str, Any]], Any]
    attrs_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None


def _bal(key: str) -> Callable[[dict[str, Any]], Any]:
    return lambda d: _num(d["balance"].get(key))


def _bal_date(key: str) -> Callable[[dict[str, Any]], date | None]:
    return lambda d: _d(d["balance"].get(key))


def _latest(*path: str) -> Callable[[dict[str, Any]], Any]:
    return lambda d: _num(_g(d["latest"], *path))


def _money(key: str, name: str, fn, icon: str | None = None, **kw) -> EnmaxSensorDescription:
    return EnmaxSensorDescription(
        key=key, name=name, value_fn=fn, device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CUR, suggested_display_precision=2, icon=icon, **kw,
    )


def _plan(product_type: int) -> Callable[[dict[str, Any]], dict[str, Any] | None]:
    def fn(d: dict[str, Any]) -> dict[str, Any] | None:
        for site in _g(d["plans"], "site_details", "site") or []:
            cp = site.get("current_plan") or {}
            if cp.get("product_type") == product_type:
                return cp
        return None
    return fn


_elec_plan, _gas_plan = _plan(2), _plan(1)


def _bill_period(d: dict[str, Any]) -> dict[str, Any]:
    r = d["latest"]
    return {
        "bill_date": (r.get("bill_date") or "")[:10],
        "electricity_period": f"{(_g(r,'electricity','start_date') or '')[:10]} → {(_g(r,'electricity','end_date') or '')[:10]}",
        "electricity_read_type": {0: "actual", 1: "estimate"}.get(_g(r, "electricity", "read_type")),
        "gas_period": f"{(_g(r,'natural_gas','start_date') or '')[:10]} → {(_g(r,'natural_gas','end_date') or '')[:10]}",
        "gas_read_type": {0: "actual", 1: "estimate"}.get(_g(r, "natural_gas", "read_type")),
        "municipal_period": f"{(_g(r,'municipal','start_date') or '')[:10]} → {(_g(r,'municipal','end_date') or '')[:10]}",
    }


def _history(d: dict[str, Any]) -> dict[str, Any]:
    """Compact per-bill history for custom cards (ApexCharts etc.)."""
    rows = []
    for r in d["insights"]:
        m = r.get("municipal") or {}
        rows.append({
            "date": (r.get("bill_date") or "")[:10],
            "elec_kwh": _num(_g(r, "electricity", "usage")),
            "elec_cost": _num(_g(r, "electricity", "bill_amount")),
            "gas_gj": _num(_g(r, "natural_gas", "usage")),
            "gas_cost": _num(_g(r, "natural_gas", "bill_amount")),
            "water_m3": _num(_g(m, "water", "usage")),
            "water_cost": _num(_g(m, "water", "amount")),
            "wastewater_cost": _num(_g(m, "wastewater", "amount")),
            "stormwater_cost": _num(_g(m, "stormwater_management", "amount")),
            "waste_cost": waste_amount(m),
        })
    return {"bills": rows}


SENSORS: tuple[EnmaxSensorDescription, ...] = (
    # account / balance
    _money("current_balance", "Current balance", _bal("current_balance"), "mdi:cash"),
    _money("amount_due", "Amount due", _bal("amount_due"), "mdi:cash-clock"),
    EnmaxSensorDescription(key="due_date", name="Due date", device_class=SensorDeviceClass.DATE,
                           value_fn=_bal_date("current_balance_due_date"), icon="mdi:calendar-alert"),
    _money("last_bill_amount", "Last bill amount", _bal("last_bill_amount"), "mdi:receipt-text"),
    EnmaxSensorDescription(key="last_bill_date", name="Last bill date", device_class=SensorDeviceClass.DATE,
                           value_fn=_bal_date("last_bill_date"), icon="mdi:calendar"),
    _money("last_payment_amount", "Last payment", _bal("last_payment_amount"), "mdi:cash-check"),
    EnmaxSensorDescription(key="last_payment_date", name="Last payment date", device_class=SensorDeviceClass.DATE,
                           value_fn=_bal_date("last_payment_received_date"), icon="mdi:calendar-check"),
    # latest bill — energy
    EnmaxSensorDescription(key="bill_electricity_usage", name="Bill electricity usage",
                           device_class=SensorDeviceClass.ENERGY, native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
                           value_fn=_latest("electricity", "usage"), attrs_fn=_bill_period),
    _money("bill_electricity_cost", "Bill electricity", _latest("electricity", "bill_amount"), "mdi:flash"),
    EnmaxSensorDescription(key="bill_gas_usage", name="Bill gas usage",
                           device_class=SensorDeviceClass.ENERGY, native_unit_of_measurement=UnitOfEnergy.GIGA_JOULE,
                           suggested_display_precision=2, value_fn=_latest("natural_gas", "usage")),
    _money("bill_gas_cost", "Bill gas", _latest("natural_gas", "bill_amount"), "mdi:fire"),
    # latest bill — City of Calgary
    EnmaxSensorDescription(key="bill_water_usage", name="Bill water usage",
                           device_class=SensorDeviceClass.WATER, native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
                           value_fn=_latest("municipal", "water", "usage")),
    _money("bill_water_cost", "Bill water", _latest("municipal", "water", "amount"), "mdi:water"),
    _money("bill_wastewater_cost", "Bill wastewater", _latest("municipal", "wastewater", "amount"), "mdi:water-pump"),
    _money("bill_stormwater_cost", "Bill stormwater", _latest("municipal", "stormwater_management", "amount"), "mdi:weather-pouring"),
    _money("bill_black_cart", "Bill black cart (garbage)", _latest("municipal", "black_cart_program", "amount"), "mdi:trash-can"),
    _money("bill_blue_cart", "Bill blue cart (recycling)", _latest("municipal", "blue_cart_program", "amount"), "mdi:recycle"),
    _money("bill_green_cart", "Bill green cart (compost)", _latest("municipal", "green_cart_program", "amount"), "mdi:leaf"),
    _money("bill_residential_garbage", "Bill residential waste fee", _latest("municipal", "residential_garbage", "amount"), "mdi:delete"),
    _money("bill_waste_total", "Bill waste total", lambda d: waste_amount(d["latest"].get("municipal")), "mdi:delete-variant"),
    _money("bill_municipal_service_charge", "Bill municipal service charge", _latest("municipal", "service_charge"), "mdi:city"),
    _money("bill_municipal_total", "Bill City of Calgary total", _latest("municipal", "total_bill_amount"), "mdi:city-variant",
           attrs_fn=_history),
    # plans
    EnmaxSensorDescription(key="electricity_plan", name="Electricity plan", icon="mdi:file-document",
                           value_fn=lambda d: (_elec_plan(d) or {}).get("plan_description"),
                           attrs_fn=lambda d: {k: (_elec_plan(d) or {}).get(k) for k in ("price", "term", "renewal_date", "is_fixed_rate")}),
    EnmaxSensorDescription(key="gas_plan", name="Gas plan", icon="mdi:file-document",
                           value_fn=lambda d: (_gas_plan(d) or {}).get("plan_description"),
                           attrs_fn=lambda d: {k: (_gas_plan(d) or {}).get(k) for k in ("price", "term", "renewal_date", "is_fixed_rate")}),
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: EnmaxCoordinator = entry.runtime_data
    async_add_entities(EnmaxSensor(coordinator, d) for d in SENSORS)


class EnmaxSensor(CoordinatorEntity[EnmaxCoordinator], SensorEntity):
    _attr_has_entity_name = True
    entity_description: EnmaxSensorDescription

    def __init__(self, coordinator: EnmaxCoordinator, description: EnmaxSensorDescription) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.account_no}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.account_no)},
            name="Enmax",
            manufacturer="ENMAX",
            model="MyAccount",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url="https://myaccount.enmax.com/easymax/energy-insights",
        )

    @property
    def native_value(self) -> Any:
        try:
            return self.entity_description.value_fn(self.coordinator.data)
        except (KeyError, TypeError, AttributeError):
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if not self.entity_description.attrs_fn:
            return None
        try:
            return self.entity_description.attrs_fn(self.coordinator.data)
        except (KeyError, TypeError, AttributeError):
            return None
