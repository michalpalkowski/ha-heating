"""UI provisioning of the HA client; it never changes device commissioning."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components import mqtt
from homeassistant.config_entries import ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector

from .client.model import ZONE_IDS
from .const import DOMAIN
from .probe import async_probe
from .settings import Settings

SECRET = selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD))


def credentials(defaults: dict[str, Any] | None = None) -> vol.Schema:
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required("device_id", default=defaults.get("device_id", "")): str,
            vol.Required("control_key"): SECRET,
            vol.Required("management_key"): SECRET,
            vol.Required("public_key", default=defaults.get("public_key", "")): str,
            vol.Optional("upper_device_id", default=defaults.get("upper_device_id", "")): str,
            vol.Optional("upper_public_key", default=defaults.get("upper_public_key", "")): str,
        }
    )


class HeatingConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        if user_input is not None:
            try:
                settings = Settings.parse(user_input, {})
            except (ValueError, KeyError, TypeError):
                errors["base"] = "invalid_config"
            else:
                if not await mqtt.async_wait_for_mqtt_client(self.hass):
                    errors["base"] = "mqtt_unavailable"
                else:
                    try:
                        await async_probe(self.hass, settings)
                    except TimeoutError:
                        errors["base"] = "cannot_connect"
                    else:
                        await self.async_set_unique_id(settings.device_id)
                        self._abort_if_unique_id_configured()
                        return self.async_create_entry(title="UFH Heating", data=user_input)
        return self.async_show_form(step_id="user", data_schema=credentials(), errors=errors)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None):
        entry = self._get_reconfigure_entry()
        errors = {}
        if user_input is not None:
            try:
                settings = Settings.parse(user_input, entry.options)
                if settings.device_id != entry.unique_id:
                    raise ValueError("reconfiguration cannot replace a device")
            except (ValueError, KeyError, TypeError):
                errors["base"] = "invalid_config"
            else:
                try:
                    await async_probe(self.hass, settings)
                except TimeoutError:
                    errors["base"] = "cannot_connect"
                else:
                    return self.async_update_reload_and_abort(entry, data_updates=user_input)
        return self.async_show_form(
            step_id="reconfigure", data_schema=credentials(dict(entry.data)), errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return HeatingOptionsFlow()


class HeatingOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        errors = {}
        if user_input is not None:
            try:
                Settings.parse(self.config_entry.data, user_input)
            except (ValueError, TypeError, KeyError):
                errors["base"] = "invalid_config"
            else:
                return self.async_create_entry(title="", data=user_input)
        options = self.config_entry.options
        schema: dict = {
            vol.Required("zones", default=options.get("zones", [])): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=list(ZONE_IDS), multiple=True, translation_key="zones"
                )
            ),
            vol.Required(
                "maximum_age_seconds", default=options.get("maximum_age_seconds", 900)
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=2700)),
        }
        schema[vol.Optional("primary_floor", default=options.get("primary_floor", "ground"))] = (
            selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=["ground", "upper"], translation_key="primary_floor"
                )
            )
        )
        for zone in ZONE_IDS:
            key = f"sensor_{zone}"
            marker = (
                vol.Optional(key, default=options[key]) if options.get(key) else vol.Optional(key)
            )
            schema[marker] = selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor", device_class="temperature")
            )
        schema[
            vol.Optional(
                "shared_reference_zones", default=options.get("shared_reference_zones", [])
            )
        ] = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=list(ZONE_IDS), multiple=True, translation_key="zones"
            )
        )
        for floor, rooms in (("ground", ZONE_IDS[:4]), ("upper", ZONE_IDS[4:])):
            key = f"shared_{floor}_reference_zones"
            schema[vol.Optional(key, default=options.get(key, []))] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=list(rooms), multiple=True, translation_key="zones"
                )
            )
        # Explicit optional permutations; do not guess changes to provisioned pins.
        for floor, count in (("ground", 6), ("upper", 8)):
            key = f"{floor}_channels"
            schema[vol.Required(key, default=options.get(key, list(range(count))))] = (
                selector.ObjectSelector()
            )
        return self.async_show_form(step_id="init", data_schema=vol.Schema(schema), errors=errors)
