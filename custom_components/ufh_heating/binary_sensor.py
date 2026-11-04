"""Separate requested heat, GPIO output evidence, and installation readiness."""

from __future__ import annotations

import time

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory

from .client.presentation import loop_presentation
from .entity import HeatingEntity


async def async_setup_entry(hass, entry, async_add_entities):
    runtime = entry.runtime_data
    async_add_entities(
        [
            HeatingRequest(runtime),
            HeatingOperationEnabled(runtime),
            HeatingConflict(runtime),
            *(HeatingLoop(runtime, b) for b in runtime.settings.bindings),
        ]
    )


class HeatingRequest(HeatingEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.HEAT

    def __init__(self, runtime):
        super().__init__(runtime, "heat_request", "Heat request")

    @property
    def is_on(self):
        return self.runtime.model.heat_permitted(time.monotonic()) if self.available else None

    @property
    def extra_state_attributes(self):
        status = self.runtime.model.status
        return {
            **self.floorplan_identity,
            "ufh_role": "heat_request",
            "status_schema": status.schema if status else None,
            "operation_enabled": status.operation_enabled
            if status and self.available and status.schema == 2
            else None,
        }


class HeatingOperationEnabled(HeatingEntity, BinarySensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, runtime):
        super().__init__(runtime, "operation_enabled", "Operation enabled")

    @property
    def is_on(self):
        status = self.runtime.model.status
        return status.operation_enabled if status and self.available else None

    @property
    def extra_state_attributes(self):
        status = self.runtime.model.status
        return {
            **self.floorplan_identity,
            "ufh_role": "operation",
            "status_schema": status.schema if status else None,
            "electrical_tested": bool(status.installation_facts & 1)
            if status and status.installation_facts is not None
            else None,
            "external_cutoff_wired": bool(status.installation_facts & 2)
            if status and status.installation_facts is not None
            else None,
            "relay_profile": status.relay_profile if status else None,
            "maintenance_phase": self.runtime.maintenance.job.phase
            if self.runtime.maintenance.active
            else None,
            "operation_error": self.runtime.maintenance.job.error
            if self.runtime.maintenance.job
            else None,
        }


class HeatingConflict(HeatingEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, runtime):
        super().__init__(runtime, "client_conflict", "Multiple control clients")

    @property
    def available(self):
        return True

    @property
    def is_on(self):
        return self.runtime.conflict


class HeatingLoop(HeatingEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.HEAT

    def __init__(self, runtime, binding):
        self.binding = binding
        super().__init__(
            runtime,
            f"loop_{binding.id}",
            f"Loop {binding.number}",
            1 if binding.floor == "ground" else 2,
        )

    @property
    def _data(self):
        return loop_presentation(
            self.runtime.model, self.binding, time.monotonic(), time.time_ns() // 1_000_000
        )

    @property
    def is_on(self):
        state = self._data["state"]
        return state == "on" if self.available and state != "unknown" else None

    @property
    def extra_state_attributes(self):
        return {**self._data, **self.floorplan_identity}
