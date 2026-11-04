"""Common event-driven entity lifecycle and installation-scoped device identity."""

from __future__ import annotations

from homeassistant.helpers.entity import DeviceInfo, Entity

from .const import DOMAIN
from .runtime import HeatingRuntime


class HeatingEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, runtime: HeatingRuntime, suffix: str, name: str, node: int = 1):
        self.runtime = runtime
        item = runtime.maintenance.nodes[node]
        self._attr_unique_id = f"{item.device_id.hex()}_{suffix}"
        self._attr_name = name
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, item.device_id.hex())},
            name="UFH Heating" if node == 1 else "UFH Upper",
            manufacturer="Heating",
            model="XIAO ESP32-C6 / UFH1",
        )

    @property
    def available(self) -> bool:
        return self.runtime.live

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.runtime.subscribe(self.async_write_ha_state))
