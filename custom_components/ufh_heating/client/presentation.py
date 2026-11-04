"""Read-only floorplan telemetry. Never sends valve or boiler commands."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from .model import FAULTS, PHASES, ZONE_FAULTS, ZONE_IDS

if TYPE_CHECKING:
    from .model import Gateway


class VisualState(StrEnum):
    UNKNOWN = "unknown"
    IDLE = "idle"
    OPENING = "opening"
    ENERGIZED = "energized"
    HEATING_COMMAND = "heating_command"
    DRAINING = "draining"
    FAULT = "fault"


@dataclass(frozen=True, slots=True)
class LoopBinding:
    floor: str
    number: int
    channel: int
    zone_index: int

    @property
    def id(self) -> str:
        return f"{self.floor}_{self.number}"

    @property
    def entity(self) -> str:
        return f"binary_sensor.underfloor_{self.id}_active"


def bindings_from_config(config: dict) -> tuple[LoopBinding, ...]:
    result = []
    for floor, count, zones in (
        ("ground", 6, (0, 1, 2, 2, 2, 3)),
        ("upper", 8, (4, 5, 5, 6, 6, 7, 7, 8)),
    ):
        channels = config[f"{floor}_loop_channels"]
        if not isinstance(channels, list) or len(channels) != count:
            raise ValueError("explicit physical loop-channel mapping is required")
        if (
            any(type(c) is not int or not 0 <= c < count for c in channels)
            or len(set(channels)) != count
        ):
            raise ValueError("loop channels must be distinct fitted GPIO channels")
        result.extend(
            LoopBinding(floor, index + 1, channels[index], zones[index]) for index in range(count)
        )
    return tuple(result)


def loop_presentation(
    gateway: Gateway, binding: LoopBinding, monotonic: float, unix_ms: int
) -> dict:
    if gateway.config[binding.zone_index].id != ZONE_IDS[binding.zone_index]:
        raise ValueError("zone identity/order differs from firmware contract")
    status = gateway.status
    live = gateway.is_live(monotonic)
    visual = VisualState.UNKNOWN
    energized: bool | None = None
    demand: bool | None = None
    source = "unknown"
    fault = "no_live_ack"
    if live and status is not None:
        energized = bool(
            (status.ground if binding.floor == "ground" else status.upper) & (1 << binding.channel)
        )
        demand = bool(status.demand & (1 << binding.zone_index))
        zone_fault = status.zone_faults[binding.zone_index]
        fault = FAULTS[status.fault] if status.fault else ZONE_FAULTS[zone_fault]
        if monotonic - gateway.source_at < 3:
            source = ("off", "on", "unknown")[gateway.source]
        if status.fault or status.island_fault or zone_fault not in (0, 1):
            visual = VisualState.FAULT
        elif not energized:
            visual = VisualState.IDLE
        elif status.phase in (3, 4):
            visual = VisualState.DRAINING
        elif gateway.heat_permitted(monotonic) and demand and source == "on":
            visual = VisualState.HEATING_COMMAND
        elif status.phase == 1:
            visual = VisualState.OPENING
        else:
            visual = VisualState.ENERGIZED
    sample = gateway.samples[binding.zone_index]
    current = (
        sample.temperature / 100
        if sample
        and sample.temperature is not None
        and sample.quality == 0
        and monotonic < sample.expires_monotonic
        else None
    )
    confirmed = gateway.confirmed[binding.zone_index] if live and gateway.confirmed else None
    return {
        "schema": 1,
        "loop_id": binding.id,
        "zone_id": ZONE_IDS[binding.zone_index],
        "state": "on"
        if visual == VisualState.HEATING_COMMAND
        else ("off" if visual != VisualState.UNKNOWN else "unknown"),
        "visual_state": visual.value,
        "output_energized": energized,
        "demand": demand,
        "heat_request": status.heat_request if live and status else None,
        "source_feedback": source,
        "phase": PHASES[status.phase] if live and status else "unknown",
        "fault": fault,
        "current_temperature": current,
        "target_temperature": confirmed.target / 100 if confirmed else None,
        "evidence": "gpio_latch_and_co_switch_only",
        "reported_at_unix_ms": unix_ms,
    }
