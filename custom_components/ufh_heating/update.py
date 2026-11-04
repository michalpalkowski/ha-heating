"""HA Update service backed by signed artifact, ACK stream and proven normal boot."""

from __future__ import annotations

import time

from homeassistant.components.update import UpdateDeviceClass, UpdateEntity, UpdateEntityFeature

from .entity import HeatingEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities(
        HeatingUpdate(entry.runtime_data, node) for node in entry.runtime_data.maintenance.nodes
    )


class HeatingUpdate(HeatingEntity, UpdateEntity):
    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_supported_features = UpdateEntityFeature.INSTALL | UpdateEntityFeature.PROGRESS

    def __init__(self, runtime, node):
        self.node = node
        super().__init__(runtime, "firmware", "Firmware", node)

    @property
    def _presentation(self):
        return self.runtime.maintenance.presentation(self.node, time.monotonic())

    @property
    def available(self):
        return self._presentation[0] and self.runtime.live

    @property
    def installed_version(self):
        return self._presentation[1]["installed_version"]

    @property
    def latest_version(self):
        return self._presentation[1]["latest_version"]

    @property
    def in_progress(self):
        return self._presentation[1]["in_progress"]

    @property
    def update_percentage(self):
        return self._presentation[1]["update_percentage"]

    @property
    def release_summary(self):
        return self._presentation[1].get("release_summary")

    @property
    def extra_state_attributes(self):
        return self._presentation[2]

    async def async_install(self, version: str | None, backup: bool, **kwargs) -> None:
        await self.runtime.async_install(self.node, version, backup)
