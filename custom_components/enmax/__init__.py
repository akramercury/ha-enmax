"""Enmax (Calgary) integration: electricity, gas, water and City of Calgary charges."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .coordinator import EnmaxCoordinator

PLATFORMS = [Platform.SENSOR]

type EnmaxConfigEntry = ConfigEntry[EnmaxCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: EnmaxConfigEntry) -> bool:
    coordinator = EnmaxCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: EnmaxConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
