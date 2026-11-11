"""Validate config entries at the boundary, before network or control operations."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .client.maintenance import Node
from .client.mapping import mapping_fingerprint
from .client.model import BASE, ZONE_IDS, ZoneConfig
from .client.presentation import LoopBinding, bindings_from_config
from .client.topology import Floor
from .references import SharedReference, validate_floor_references


def hex_bytes(value: object, length: int) -> bytes:
    if not isinstance(value, str) or not re.fullmatch(rf"[0-9a-fA-F]{{{length * 2}}}", value):
        raise ValueError("invalid hexadecimal value")
    result = bytes.fromhex(value)
    if not any(result):
        raise ValueError("zero identity/key is forbidden")
    return result


@dataclass(frozen=True, slots=True)
class Settings:
    device_id: str
    control_key: bytes = field(repr=False)
    management_key: bytes = field(repr=False)
    nodes: tuple[Node, ...]
    allowed: frozenset[int]
    zones: tuple[ZoneConfig, ...]
    sensors: tuple[str | None, ...]
    bindings: tuple[LoopBinding, ...]
    primary_floor: Floor = Floor.GROUND
    mapping_fingerprint: bytes = b""

    @classmethod
    def parse(cls, data: Mapping[str, Any], options: Mapping[str, Any]) -> Settings:
        primary_floor = Floor(options.get("primary_floor", "ground"))
        identity = hex_bytes(data["device_id"], 16)
        control = hex_bytes(data["control_key"], 32)
        management = hex_bytes(data["management_key"], 32)
        if control == management:
            raise ValueError("control and management keys must differ")
        nodes = [Node(1, identity, hex_bytes(data["public_key"], 32))]
        if data.get("upper_device_id"):
            nodes.append(
                Node(
                    2,
                    hex_bytes(data["upper_device_id"], 16),
                    hex_bytes(data["upper_public_key"], 32),
                )
            )
        elif data.get("upper_public_key"):
            raise ValueError("upper identity is required")
        if len({node.device_id for node in nodes}) != len(nodes):
            raise ValueError("device identities must differ")
        installed = options.get("zones", [])
        if (
            not isinstance(installed, list)
            or len(set(installed)) != len(installed)
            or any(z not in ZONE_IDS for z in installed)
        ):
            raise ValueError("invalid zone selection")
        if len(nodes) == 1 and any(
            primary_floor.node_for_zone(ZONE_IDS.index(z)) == 2 for z in installed
        ):
            raise ValueError("remote floor zones require the secondary device identity")
        age = options.get("maximum_age_seconds", 900)
        if type(age) is not int or not 1 <= age <= 900:
            raise ValueError("report age must be 1–900 seconds")
        sensors: list[str | None] = []
        zones: list[ZoneConfig] = []
        for index, zone in enumerate(ZONE_IDS):
            entity = options.get(f"sensor_{zone}") or None
            if entity is not None and (
                not isinstance(entity, str) or not re.fullmatch(r"sensor\.[a-z0-9_]+", entity)
            ):
                raise ValueError("invalid temperature entity")
            sensors.append(entity)
            zones.append(
                ZoneConfig(
                    zone, f"Zone {index + 1}", zone, f"{BASE}/temperature/{zone}", age * 1000, 2200
                )
            )
        shared = SharedReference.parse(options.get("shared_reference_zones", []), sensors)
        validate_floor_references(options, installed, sensors, shared)
        loops = bindings_from_config(
            {
                "ground_loop_channels": options.get("ground_channels", list(range(6))),
                "upper_loop_channels": options.get("upper_channels", list(range(8))),
            }
        )
        allowed = frozenset(ZONE_IDS.index(z) for z in installed)
        return cls(
            identity.hex(),
            control,
            management,
            tuple(nodes),
            allowed,
            tuple(zones),
            tuple(sensors),
            tuple(b for b in loops if b.zone_index in allowed),
            primary_floor,
            mapping_fingerprint(
                options.get("ground_channels", list(range(6))),
                options.get("upper_channels", list(range(8))),
            ),
        )
