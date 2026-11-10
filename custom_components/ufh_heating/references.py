"""Explicit opt-in for one shared room reference; never implied by duplicates."""

from collections.abc import Sequence
from dataclasses import dataclass

from .client.model import ZONE_IDS


@dataclass(frozen=True, slots=True)
class SharedReference:
    zones: tuple[str, ...]

    @classmethod
    def parse(cls, value: object, sensors: Sequence[str | None]) -> SharedReference:
        if not isinstance(value, list) or any(not isinstance(zone, str) for zone in value):
            raise ValueError("shared reference zones must be a list")
        if len(set(value)) != len(value) or any(zone not in ZONE_IDS for zone in value):
            raise ValueError("invalid shared reference zones")
        if value and len(value) < 2:
            raise ValueError("a shared reference requires at least two zones")
        group = cls(tuple(value))
        if group.zones:
            references = {sensors[ZONE_IDS.index(zone)] for zone in group.zones}
            if len(references) != 1 or None in references:
                raise ValueError("shared zones require the same explicit temperature sensor")
        return group

    def validate_installed(self, installed: Sequence[str], sensors: Sequence[str | None]) -> None:
        by_sensor: dict[str, set[str]] = {}
        for zone in installed:
            sensor = sensors[ZONE_IDS.index(zone)]
            if sensor is not None:
                by_sensor.setdefault(sensor, set()).add(zone)
        for zones in by_sensor.values():
            if len(zones) > 1 and not zones <= set(self.zones):
                raise ValueError("duplicate temperature references require explicit sharing")
