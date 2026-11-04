"""Gateway policy: freshness, session proof and acknowledged UI settings."""

from __future__ import annotations

import json
import secrets
from collections import OrderedDict
from dataclasses import dataclass

from .wire import Packet, Status, Zone, control_payload, decode

BASE = "heating/house/v1"
ZONE_IDS = (
    "ground_bathroom",
    "office",
    "living",
    "kitchen_hall_boiler",
    "wardrobe",
    "bedroom",
    "bedroom_right",
    "bedroom_left",
    "upper_bathroom_hall",
)
PHASES = ("idle", "opening", "heating", "awaiting_source_off", "overrun", "blocked")
FAULTS = ("ok", "not_commissioned", "input_expired", "source_unknown", "output", "time")
ZONE_FAULTS = (
    "ok",
    "disabled",
    "missing",
    "stale",
    "sensor",
    "unverified",
    "runtime_limit",
)


@dataclass(frozen=True, slots=True)
class ZoneConfig:
    id: str
    name: str
    sensor_id: str
    report_topic: str
    maximum_age_ms: int
    initial_target: int


@dataclass(slots=True)
class Sample:
    temperature: int | None
    quality: int
    expires_monotonic: float
    observed_unix_ms: int


class Gateway:
    def __init__(self, key: bytes, zones: tuple[ZoneConfig, ...]) -> None:
        if len(zones) != 9 or len({z.id for z in zones}) != 9:
            raise ValueError("exactly nine distinct zone slots are required")
        if len({z.report_topic for z in zones}) != 9 or len({z.sensor_id for z in zones}) != 9:
            raise ValueError("each zone requires an explicit independent reference source")
        self.key = key
        self.config = zones
        self.client_session = secrets.token_bytes(16)
        self.node_session: bytes | None = None
        self.status: Status | None = None
        self.status_at = 0.0
        self.status_sequence = 0
        self.sequence = 0
        self.pending: OrderedDict[int, tuple[Zone, ...]] = OrderedDict()
        self.confirmed: tuple[Zone, ...] | None = None
        self.target = [z.initial_target for z in zones]
        self.enabled = [False] * 9  # explicit safe startup, operator must arm climate entities
        self.samples: list[Sample | None] = [None] * 9
        self.last_report = [0] * 9
        self.last_event = [None] * 9
        self.source = 2
        self.source_at = float("-inf")
        self.source_reported = 0

    def disconnected(self) -> None:
        self.status = None
        self.confirmed = None
        self.pending.clear()
        self.enabled = [False] * 9
        self.samples = [None] * 9
        self.source = 2
        self.source_at = float("-inf")

    def measurement(
        self,
        index: int,
        payload: bytes,
        *,
        retained: bool,
        unix_ms: int,
        monotonic: float,
    ) -> None:
        if retained or len(payload) > 512:
            raise ValueError("retained/oversized measurement")
        data = json.loads(payload)
        expected = {
            "schema",
            "sensor_id",
            "event_id",
            "reported_at_unix_ms",
            "temperature_centi_c",
            "quality",
        }
        if (
            not isinstance(data, dict)
            or set(data) != expected
            or type(data["schema"]) is not int
            or data["schema"] != 1
        ):
            raise ValueError("invalid temperature-report schema")
        zone = self.config[index]
        timestamp, temperature = (
            data["reported_at_unix_ms"],
            data["temperature_centi_c"],
        )
        if type(timestamp) is not int or data["quality"] not in ("valid", "fault"):
            raise ValueError("invalid report quality")
        if (
            data["quality"] == "valid"
            and (type(temperature) is not int or not -2000 <= temperature <= 6000)
            or data["quality"] == "fault"
            and temperature is not None
        ):
            raise ValueError("invalid report values")
        if (
            data["sensor_id"] != zone.sensor_id
            or not isinstance(data["event_id"], str)
            or not 1 <= len(data["event_id"]) <= 96
        ):
            raise ValueError("wrong source identity")
        if (
            data["event_id"] == self.last_event[index]
            or timestamp <= self.last_report[index]
            or not -1000 <= unix_ms - timestamp < zone.maximum_age_ms
        ):
            raise ValueError("duplicate, expired or future report")
        # Preserve original age. Re-publishing a battery message or cached HA
        # state is explicitly outside this accepted report contract.
        self.last_report[index] = timestamp
        self.last_event[index] = data["event_id"]
        self.samples[index] = Sample(
            temperature,
            0 if data["quality"] == "valid" else 3,
            monotonic + (zone.maximum_age_ms - max(0, unix_ms - timestamp)) / 1000,
            timestamp,
        )

    def source_evidence(
        self, payload: bytes, *, retained: bool, unix_ms: int, monotonic: float
    ) -> None:
        if retained or len(payload) > 256:
            raise ValueError("invalid source evidence")
        data = json.loads(payload)
        if (
            not isinstance(data, dict)
            or set(data) != {"schema", "state", "reported_at_unix_ms"}
            or type(data["schema"]) is not int
            or data["schema"] != 1
        ):
            raise ValueError("invalid source schema")
        timestamp = data["reported_at_unix_ms"]
        if (
            type(timestamp) is not int
            or timestamp <= self.source_reported
            or not -1000 <= unix_ms - timestamp <= 3000
        ):
            raise ValueError("expired or replayed source evidence")
        if data["state"] not in ("off", "on", "unknown"):
            raise ValueError("unknown source state")
        self.source_reported = timestamp
        self.source = {"off": 0, "on": 1, "unknown": 2}[data["state"]]
        self.source_at = monotonic - max(0, unix_ms - timestamp) / 1000

    def accept_status(self, raw: bytes, *, retained: bool, monotonic: float) -> Status:
        if retained:
            raise ValueError("retained status cannot establish a live session")
        packet = decode(raw, self.key)
        if packet.kind != 2 or packet.node != 1:
            raise ValueError("unexpected sender")
        state = Status.decode(packet.payload)
        if packet.deadline != state.uptime_ms:
            raise ValueError("status clock mismatch")
        if self.node_session != packet.session:
            self.node_session = packet.session
            self.status_sequence = 0
            self.status = None
            self.pending.clear()
            self.confirmed = None
            self.enabled = [False] * 9
        if packet.sequence <= self.status_sequence:
            raise ValueError("replayed status")
        if (
            self.status is not None
            and self.status.client_session == state.client_session
            and state.uptime_ms < self.status.uptime_ms
        ):
            raise ValueError("receiver clock regressed")
        self.status_sequence = packet.sequence
        self.sequence = max(self.sequence, state.accepted_sequence)
        self.status = state
        self.status_at = monotonic
        if state.client_session == self.client_session and state.accepted_sequence in self.pending:
            self.confirmed = self.pending[state.accepted_sequence]
            for sequence in tuple(self.pending):
                if sequence < state.accepted_sequence:
                    del self.pending[sequence]
        return state

    def command(self, *, monotonic: float) -> bytes | None:
        if self.status is None or self.node_session is None or monotonic - self.status_at > 3:
            return None
        uptime = self.status.uptime_ms + int((monotonic - self.status_at) * 1000)
        zones = []
        for index, _cfg in enumerate(self.config):
            sample = self.samples[index]
            fresh = sample is not None and sample.expires_monotonic > monotonic + 0.5
            until = (
                uptime + int((sample.expires_monotonic - monotonic) * 1000)
                if fresh and sample is not None and sample.quality == 0
                else 0
            )
            zones.append(
                Zone(
                    self.enabled[index],
                    self.target[index],
                    sample.temperature if fresh and sample else None,
                    sample.quality if fresh and sample else (2 if sample else 1),
                    until,
                )
            )
        typed = tuple(zones)
        source = self.source if monotonic - self.source_at < 3 else 2
        source_until = (
            uptime + max(0, int((3 - (monotonic - self.source_at)) * 1000)) if source != 2 else 0
        )
        payload = control_payload(self.client_session, typed, source, source_until)
        self.sequence += 1
        raw = Packet(1, 1, self.node_session, self.sequence, uptime + 5000, payload).encode(
            self.key
        )
        self.pending[self.sequence] = typed
        while len(self.pending) > 32:
            self.pending.popitem(last=False)
        return raw

    def is_live(self, monotonic: float) -> bool:
        state = self.status
        return bool(
            state
            and monotonic - self.status_at < 2
            and self.confirmed is not None
            and state.client_session == self.client_session
            and state.accepted_sequence in self.pending
        )

    def heat_permitted(self, monotonic: float) -> bool:
        state = self.status
        return bool(
            state
            and self.is_live(monotonic)
            and state.commissioned
            and state.fault == 0
            and state.island_fault == 0
            and state.heat_request
        )

    def set_mode(self, index: int, mode: str) -> None:
        if mode not in ("off", "heat"):
            raise ValueError("only off/heat are supported for water-floor heating")
        self.enabled[index] = mode == "heat"

    def set_target(self, index: int, value: str) -> None:
        # Decimal avoids binary rounding and rejects NaN/Infinity.
        from decimal import Decimal, InvalidOperation

        try:
            target = Decimal(value) * 100
            if (
                not target.is_finite()
                or target != target.to_integral_value()
                or not 1000 <= target <= 2800
            ):
                raise ValueError("setpoint outside 10–28°C")
            self.target[index] = int(target)
        except InvalidOperation as error:
            raise ValueError("invalid setpoint") from error
