"""Acknowledged thermostats; the MCU owns timing and hysteresis."""

from __future__ import annotations

import time
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature

from .entity import HeatingEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities(
        HeatingClimate(entry.runtime_data, i) for i in sorted(entry.runtime_data.settings.allowed)
    )


class HeatingClimate(HeatingEntity, ClimateEntity):
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT]
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_min_temp = 10
    _attr_max_temp = 28
    _attr_target_temperature_step = 0.5
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )

    def __init__(self, runtime, index):
        self.index = index
        super().__init__(
            runtime,
            f"zone_{index + 1}",
            f"Zone {index + 1}",
            runtime.settings.primary_floor.node_for_zone(index),
        )

    @property
    def _confirmed(self):
        return (
            self.runtime.model.confirmed[self.index]
            if self.runtime.live and self.runtime.model.confirmed
            else None
        )

    @property
    def hvac_mode(self):
        zone = self._confirmed
        return (HVACMode.HEAT if zone.enabled else HVACMode.OFF) if zone else None

    @property
    def hvac_action(self):
        zone, model = self._confirmed, self.runtime.model
        if zone is None:
            return None
        if not zone.enabled:
            return HVACAction.OFF
        return (
            HVACAction.HEATING
            if model.heat_permitted(time.monotonic()) and model.status.demand & (1 << self.index)
            else HVACAction.IDLE
        )

    @property
    def current_temperature(self):
        sample = self.runtime.model.samples[self.index]
        return (
            sample.temperature / 100
            if sample
            and sample.quality == 0
            and sample.temperature is not None
            and time.monotonic() < sample.expires_monotonic
            else None
        )

    @property
    def target_temperature(self):
        return self._confirmed.target / 100 if self._confirmed else None

    @property
    def extra_state_attributes(self):
        status = self.runtime.model.status
        return {
            **self.floorplan_identity,
            "source_authority": time.monotonic() < self.runtime._source_authorized_until,
            "zone_id": self.runtime.settings.zones[self.index].id,
            "temperature_evidence": "ha_report"
            if self.runtime.settings.sensors[self.index]
            else "timestamped_mqtt_report",
            "temperature_sensor": self.runtime.settings.sensors[self.index],
            "shared_temperature_zones": [
                zone.id
                for i, zone in enumerate(self.runtime.settings.zones)
                if i != self.index
                and i in self.runtime.settings.allowed
                and self.runtime.settings.sensors[self.index] is not None
                and self.runtime.settings.sensors[i] == self.runtime.settings.sensors[self.index]
            ],
            "fault_code": status.zone_faults[self.index] if status and self.available else None,
        }

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        self.runtime.set_mode(self.index, hvac_mode)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        self.runtime.set_target(self.index, kwargs[ATTR_TEMPERATURE])

    async def async_turn_on(self) -> None:
        await self.async_set_hvac_mode(HVACMode.HEAT)

    async def async_turn_off(self) -> None:
        await self.async_set_hvac_mode(HVACMode.OFF)
