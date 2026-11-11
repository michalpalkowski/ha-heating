"""Whitelisted diagnostics, never arbitrary configuration or protocol payloads."""

from __future__ import annotations

import time

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory

from .client.model import FAULTS, PHASES
from .entity import HeatingEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities(
        HeatingSensor(entry.runtime_data, kind)
        for kind in ("phase", "fault", "bus_errors", "rejected", "topology")
    )


class HeatingSensor(HeatingEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, runtime, kind):
        self.kind = kind
        super().__init__(
            runtime,
            kind,
            {
                "phase": "Controller phase",
                "fault": "Controller fault",
                "bus_errors": "RS485 errors",
                "rejected": "Rejected messages",
                "topology": "Primary physical floor",
            }[kind],
        )
        if kind in ("phase", "fault"):
            self._attr_device_class = SensorDeviceClass.ENUM
            self._attr_options = list(PHASES if kind == "phase" else FAULTS)
        elif kind == "topology":
            self._attr_device_class = SensorDeviceClass.ENUM
            self._attr_options = ["ground", "upper", "mismatch", "no_report"]

    @property
    def available(self):
        return self.kind in ("rejected", "topology") or super().available

    @property
    def native_value(self):
        if self.kind == "topology":
            model = self.runtime.model
            if model.status is None or time.monotonic() - model.status_at > 3:
                return "no_report"
            return (
                model.status.primary_floor
                if model.status.primary_floor == self.runtime.settings.primary_floor
                else "mismatch"
            )
        if self.kind == "rejected":
            return self.runtime.rejected
        status = self.runtime.model.status
        if status is None or not self.available:
            return None
        if self.kind == "phase":
            return PHASES[status.phase]
        if self.kind == "fault":
            return FAULTS[status.fault]
        return status.bus_errors
