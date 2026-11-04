"""Home Assistant adapter for the authenticated UFH1 underfloor controller."""

from __future__ import annotations

from homeassistant.components import mqtt
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from . import services
from .const import PLATFORMS
from .runtime import HeatingRuntime
from .settings import Settings

type HeatingEntry = ConfigEntry[HeatingRuntime]


async def async_setup_entry(hass: HomeAssistant, entry: HeatingEntry) -> bool:
    if not await mqtt.async_wait_for_mqtt_client(hass):
        raise ConfigEntryNotReady("MQTT is not connected")
    try:
        settings = Settings.parse(entry.data, entry.options)
    except (KeyError, ValueError, TypeError) as exc:
        raise ConfigEntryNotReady(
            "Invalid heating configuration; reconfigure the integration"
        ) from exc
    runtime = HeatingRuntime(hass, entry, settings)
    entry.runtime_data = runtime
    await runtime.async_start()
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        await runtime.async_stop()
        raise
    services.register(hass, entry)
    entry.async_on_unload(entry.add_update_listener(async_options_updated))
    return True


async def async_options_updated(hass: HomeAssistant, entry: HeatingEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: HeatingEntry) -> bool:
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        services.unregister(hass)
        await entry.runtime_data.async_stop()
        return True
    return False
