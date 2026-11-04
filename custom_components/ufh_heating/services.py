"""Explicit operator commands and a system-only observation port for the CO owner."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.auth.permissions.const import POLICY_CONTROL
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN

PROFILES = {"unverified": 0, "direct_low_3v3": 1, "inverting_buffer": 2}
IDENTITY = vol.Match(r"^[0-9a-f]{32}$")


def register(hass: HomeAssistant, entry) -> None:
    def runtime(call: ServiceCall):
        if call.data["controller_id"] != entry.data["device_id"]:
            raise HomeAssistantError("Controller identity does not match this installation")
        return entry.runtime_data

    async def set_operation(call: ServiceCall) -> None:
        current = runtime(call)
        entity_id = er.async_get(hass).async_get_entity_id(
            "binary_sensor", DOMAIN, f"{current.settings.device_id}_operation_enabled"
        )
        if entity_id is None:
            raise HomeAssistantError("Operation entity is not registered")
        if call.context.user_id is not None:
            user = await hass.auth.async_get_user(call.context.user_id)
            if user is None or not user.permissions.check_entity(entity_id, POLICY_CONTROL):
                raise HomeAssistantError("No permission to control this heating controller")
        current.request_operation(call.data["enabled"], PROFILES[call.data["relay_profile"]])

    async def report_source(call: ServiceCall) -> None:
        # The configured HA coordinator reads the actual switch. A user or MQTT
        # publisher cannot substitute a fabricated ON/OFF payload.
        if call.context.user_id is not None:
            raise HomeAssistantError("Source observation is a system-only operation")
        entity_id = call.data["entity_id"]
        if not entity_id.startswith(("switch.", "binary_sensor.")):
            raise HomeAssistantError("Expected an actual on/off source entity")
        runtime(call).observe_source(entity_id, call.data["authority"])

    hass.services.async_register(
        DOMAIN,
        "set_operation",
        set_operation,
        schema=vol.Schema(
            {
                vol.Required("controller_id"): IDENTITY,
                vol.Required("enabled"): cv.boolean,
                vol.Required("relay_profile"): vol.In(PROFILES),
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        "report_source",
        report_source,
        schema=vol.Schema(
            {
                vol.Required("controller_id"): IDENTITY,
                vol.Required("entity_id"): cv.entity_id,
                vol.Required("authority"): cv.boolean,
            }
        ),
    )


def unregister(hass: HomeAssistant) -> None:
    for name in ("set_operation", "report_source"):
        hass.services.async_remove(DOMAIN, name)
