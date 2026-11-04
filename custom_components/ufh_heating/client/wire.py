"""Version 1 wire contract; interoperates with the no_std Rust codec."""

from __future__ import annotations

import hashlib
import hmac
import struct
from dataclasses import dataclass

HEADER = struct.Struct(">4sBBH16sQQH")
MQTT_DOMAIN = b"underfloor/mqtt/v1\0"
BUS_DOMAIN = b"underfloor/rs485/v1\0"
MAX_PACKET = 224


@dataclass(frozen=True, slots=True)
class Packet:
    kind: int
    node: int
    session: bytes
    sequence: int
    deadline: int
    payload: bytes

    def encode(self, key: bytes, domain: bytes = MQTT_DOMAIN) -> bytes:
        if len(key) != 32 or not any(key) or len(self.session) != 16 or not any(self.session):
            raise ValueError("invalid key or session")
        if (
            self.kind not in (1, 2, 3, 4, 5, 6)
            or not 1 <= self.node <= 255
            or not 1 <= self.sequence < 2**64
        ):
            raise ValueError("invalid envelope")
        body = (
            HEADER.pack(
                b"UFH1",
                self.kind,
                self.node,
                0,
                self.session,
                self.sequence,
                self.deadline,
                len(self.payload),
            )
            + self.payload
        )
        result = body + hmac.digest(key, domain + body, hashlib.sha256)
        if len(result) > MAX_PACKET:
            raise ValueError("packet too large")
        return result


def decode(raw: bytes, key: bytes, domain: bytes = MQTT_DOMAIN) -> Packet:
    if not HEADER.size + 32 <= len(raw) <= MAX_PACKET or len(key) != 32 or not any(key):
        raise ValueError("invalid envelope length")
    magic, kind, node, flags, session, sequence, deadline, size = HEADER.unpack_from(raw)
    if (
        magic != b"UFH1"
        or flags != 0
        or kind not in (1, 2, 3, 4, 5, 6)
        or node == 0
        or sequence == 0
        or not any(session)
    ):
        raise ValueError("invalid envelope fields")
    if size + HEADER.size + 32 != len(raw):
        raise ValueError("invalid payload length")
    if not hmac.compare_digest(hmac.digest(key, domain + raw[:-32], hashlib.sha256), raw[-32:]):
        raise ValueError("authentication failed")
    return Packet(kind, node, session, sequence, deadline, raw[HEADER.size : -32])


@dataclass(frozen=True, slots=True)
class Status:
    client_session: bytes
    accepted_sequence: int
    uptime_ms: int
    ground: int
    upper: int
    heat_request: bool
    phase: int
    fault: int
    demand: int
    zone_faults: tuple[int, ...]
    island_fault: int
    commissioned: bool
    rejected: int
    bus_errors: int

    @classmethod
    def decode(cls, raw: bytes) -> Status:
        if len(raw) != 58 or raw[34] > 1 or raw[49] > 1 or raw[56:] != b"\0\0":
            raise ValueError("invalid status")
        if raw[35] > 5 or raw[36] > 5 or raw[48] > 4 or any(f > 6 for f in raw[39:48]):
            raise ValueError("unknown diagnostic code")
        client, accepted, uptime = struct.unpack_from(">16sQQ", raw)
        demand = struct.unpack_from(">H", raw, 37)[0]
        if demand & ~0x1FF:
            raise ValueError("invalid demand mask")
        return cls(
            client,
            accepted,
            uptime,
            raw[32],
            raw[33],
            bool(raw[34]),
            raw[35],
            raw[36],
            demand,
            tuple(raw[39:48]),
            raw[48],
            bool(raw[49]),
            struct.unpack_from(">I", raw, 50)[0],
            struct.unpack_from(">H", raw, 54)[0],
        )


@dataclass(frozen=True, slots=True)
class Zone:
    enabled: bool
    target: int
    temperature: int | None
    quality: int
    valid_until: int


def control_payload(
    client: bytes, zones: tuple[Zone, ...], source: int, source_until: int
) -> bytes:
    if len(client) != 16 or not any(client) or len(zones) != 9 or source not in (0, 1, 2):
        raise ValueError("invalid snapshot")
    mask = sum(1 << i for i, zone in enumerate(zones) if zone.enabled)
    out = bytearray(struct.pack(">16sH", client, mask))
    for zone in zones:
        if not 1000 <= zone.target <= 2800 or zone.quality not in range(5):
            raise ValueError("invalid zone")
        if zone.quality == 0:
            if (
                zone.temperature is None
                or not -2000 <= zone.temperature <= 6000
                or zone.valid_until == 0
            ):
                raise ValueError("invalid measurement")
        elif zone.temperature is not None or zone.valid_until != 0:
            raise ValueError("invalid missing measurement")
        out.extend(
            struct.pack(
                ">hhBQ",
                zone.target,
                zone.temperature if zone.temperature is not None else 0,
                zone.quality,
                zone.valid_until,
            )
        )
    out.extend(struct.pack(">BQ", source, source_until))
    if len(out) != 144:
        raise AssertionError("wire schema drift")
    return bytes(out)
