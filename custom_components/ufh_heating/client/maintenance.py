"""Authenticated device management and one bounded firmware update job."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from .model import BASE, Gateway
from .releases import Catalog, Package, Version
from .wire import Packet, decode

MQTT_DOMAIN = b"underfloor/manage-mqtt/v1\0"
BUS_DOMAIN = b"underfloor/manage-rs485/v1\0"
COMMAND_TOPIC = BASE + "/management/command"
STATUS_TOPIC = BASE + "/management/status"
STATUS = struct.Struct(">HHHQBBBBIIQQ16s16sB9s")


@dataclass(frozen=True, slots=True)
class Node:
    number: int
    device_id: bytes
    public_key: bytes

    @property
    def domain(self) -> bytes:
        return MQTT_DOMAIN if self.number == 1 else BUS_DOMAIN

    @property
    def name(self) -> str:
        return "Parter" if self.number == 1 else "Piętro"


@dataclass(frozen=True, slots=True)
class DeviceState:
    session: bytes
    sequence: int
    version: Version
    generation: int
    slot: int
    phase: int
    error: int
    config_rollback: bool
    offset: int
    total: int
    ack: int
    uptime: int
    artifact: bytes
    hold: bool
    observed: float


class JobKind(StrEnum):
    FIRMWARE = "firmware"
    OPERATION = "operation"


class JobPhase(StrEnum):
    QUIESCE = "quiesce"
    BEGIN = "begin"
    STREAM = "stream"
    FINISH = "finish"
    REBOOT = "reboot"
    NORMAL = "normal"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(slots=True)
class Job:
    node: int
    version: Version
    blob: bytes
    begin: bytes
    expected_generation: int | None
    started: float
    original_session: bytes
    kind: JobKind = JobKind.FIRMWARE
    phase: JobPhase = JobPhase.QUIESCE
    offset: int = 0
    waiting_sequence: int | None = None
    waiting_offset: int | None = None
    sent_at: float = 0
    reboot_session: bytes | None = None
    error: str | None = None


class Maintenance:
    def __init__(self, key: bytes, nodes: tuple[Node, ...], directory: Path):
        if (
            len(key) != 32
            or not any(key)
            or not nodes
            or nodes[0].number != 1
            or len({n.number for n in nodes}) != len(nodes)
        ):
            raise ValueError("explicit management key and main node required")
        if len({n.device_id for n in nodes}) != len(nodes) or any(
            type(n.number) is not int
            or n.number not in (1, 2)
            or len(n.device_id) != 16
            or not any(n.device_id)
            or len(n.public_key) != 32
            or not any(n.public_key)
            for n in nodes
        ):
            raise ValueError("invalid management node identity")
        self.key = key
        self.nodes = {n.number: n for n in nodes}
        self.catalog = {n.number: Catalog(directory, n.public_key, n.number) for n in nodes}
        self.states: dict[int, DeviceState] = {}
        self.sequences = {n.number: 0 for n in nodes}
        self.packages: dict[int, Package | None] = {}
        self.catalog_errors: dict[int, str] = {}
        self.request_errors: dict[int, str] = {}
        self.job: Job | None = None
        self.next_catalog = 0.0
        self.next_probe = 0.0

    @classmethod
    def from_config(cls, config: dict) -> Maintenance:
        if config.get("schema") != 1:
            raise ValueError("unsupported management settings")
        nodes = tuple(
            Node(
                n["node"],
                bytes.fromhex(n["device_id_hex"]),
                bytes.fromhex(n["ota_public_key_hex"]),
            )
            for n in config["nodes"]
        )
        return cls(
            bytes.fromhex(config["hmac_key_hex"]),
            nodes,
            Path(config["release_directory"]),
        )

    def fresh(self, node: int, now: float) -> DeviceState | None:
        state = self.states.get(node)
        return state if state is not None and 0 <= now - state.observed < 3 else None

    def accept(self, raw: bytes, retained: bool, now: float) -> DeviceState:
        if retained or len(raw) < 6:
            raise ValueError("retained/short management status")
        node = self.nodes.get(raw[5])
        if node is None:
            raise ValueError("unconfigured management node")
        packet = decode(raw, self.key, node.domain)
        if packet.kind != 6 or len(packet.payload) != STATUS.size:
            raise ValueError("management status schema mismatch")
        (
            major,
            minor,
            patch,
            generation,
            slot,
            phase,
            error,
            rollback,
            offset,
            total,
            ack,
            uptime,
            identity,
            artifact,
            hold,
            reserved,
        ) = STATUS.unpack(packet.payload)
        if (
            identity != node.device_id
            or generation < 1
            or slot > 1
            or phase > 6
            or error > 12
            or rollback > 1
            or hold > 1
            or any(reserved)
            or offset > total
            or packet.deadline != uptime
        ):
            raise ValueError("invalid management evidence")
        old = self.states.get(node.number)
        if (
            old is not None
            and old.session == packet.session
            and (packet.sequence <= old.sequence or uptime < old.uptime)
        ):
            raise ValueError("replayed/regressing management status")
        if old is None or old.session != packet.session:
            self.sequences[node.number] = ack
        else:
            self.sequences[node.number] = max(ack, self.sequences[node.number])
        state = DeviceState(
            packet.session,
            packet.sequence,
            Version(major, minor, patch),
            generation,
            slot,
            phase,
            error,
            bool(rollback),
            offset,
            total,
            ack,
            uptime,
            artifact,
            bool(hold),
            now,
        )
        self.states[node.number] = state
        return state

    def command(self, node: int, payload: bytes, now: float) -> tuple[int, bytes]:
        state = self.fresh(node, now)
        if state is None:
            raise ValueError("fresh device nonce required")
        self.sequences[node] += 1
        sequence = self.sequences[node]
        if sequence >= 2**64:
            raise ValueError("management sequence exhausted")
        deadline = state.uptime + int((now - state.observed) * 1000) + 5000
        return sequence, Packet(5, node, state.session, sequence, deadline, payload).encode(
            self.key, self.nodes[node].domain
        )

    def refresh_catalog(self, now: float, force: bool = False) -> None:
        if not force and now < self.next_catalog:
            return
        self.next_catalog = now + 30
        for node, catalog in self.catalog.items():
            try:
                self.packages[node] = catalog.latest()
                self.catalog_errors.pop(node, None)
            except (ValueError, OSError):
                self.packages[node] = None
                self.catalog_errors[node] = "Nieprawidłowy lub nieczytelny podpisany katalog wydań"

    def start(self, node: int, model: Gateway, now: float) -> None:
        if self.active:
            raise ValueError("one maintenance job at a time")
        state = self.fresh(node, now)
        package = self.packages.get(node)
        if state is None or package is None or package.version <= state.version:
            raise ValueError("no verified newer release for this device")
        if state.phase not in (0, 6) or state.hold:
            raise ValueError("device is already in maintenance/trial")
        self.job = Job(
            node,
            package.version,
            package.image,
            b"\x01" + package.manifest,
            None,
            now,
            state.session,
        )
        self.request_errors.pop(node, None)
        model.enabled = [False] * 9

    def start_operation(
        self, enabled: bool, relay_profile: int, model: Gateway, now: float
    ) -> None:
        if (
            type(enabled) is not bool
            or type(relay_profile) is not int
            or relay_profile not in (0, 1, 2)
            or enabled
            and relay_profile == 0
        ):
            raise ValueError("explicit operation and interface required")
        if self.active:
            raise ValueError("one maintenance job at a time")
        state = self.fresh(1, now)
        if state is None or state.version < Version(0, 3, 0):
            raise ValueError("firmware 0.3.0+ required")
        if state.phase != 0 or state.hold:
            raise ValueError("controller is in maintenance/trial")
        self.job = Job(
            1,
            state.version,
            b"",
            bytes((8, int(enabled), relay_profile)),
            state.generation + 1,
            now,
            state.session,
            kind=JobKind.OPERATION,
        )
        model.enabled = [False] * 9

    @property
    def active(self) -> bool:
        return self.job is not None and self.job.phase not in (
            JobPhase.SUCCEEDED,
            JobPhase.FAILED,
        )

    def fail(self, message: str) -> None:
        if self.job is not None:
            self.job.phase = JobPhase.FAILED
            self.job.error = message

    def disconnected(self) -> None:
        self.states.clear()
        if self.active:
            self.fail("Przerwano połączenie; wynik instalacji wymaga sprawdzenia wersji urządzenia")

    @staticmethod
    def _control_stopped(model: Gateway, now: float, session: bytes | None = None) -> bool:
        status = model.status
        return bool(
            model.is_live(now)
            and status
            and (session is None or model.node_session == session)
            and not status.ground
            and not status.upper
            and not status.heat_request
            and model.confirmed is not None
            and not any(zone.enabled for zone in model.confirmed)
            and (
                not status.operation_enabled
                or status.phase == 0
                and model.source == 0
                and 0 <= now - model.source_at < 3
            )
        )

    def tick(self, model: Gateway, now: float) -> list[bytes]:
        out = []
        job = self.job
        if not self.active:
            if now >= self.next_probe:
                self.next_probe = now + 1
                for node in self.nodes:
                    if self.fresh(node, now) is not None:
                        out.append(self.command(node, b"\x00", now)[1])
                if job is not None and job.node == 2 and job.phase == JobPhase.FAILED:
                    upper = self.fresh(2, now)
                    main = self.fresh(1, now)
                    if (
                        upper is not None
                        and upper.phase == 0
                        and not upper.hold
                        and main is not None
                        and main.hold
                    ):
                        out.append(self.command(1, b"\x07", now)[1])
            return out
        assert job is not None
        if now - job.started > 1800:
            self.fail("Przekroczony czas instalacji")
            return out
        state = self.fresh(job.node, now)
        if job.phase == JobPhase.QUIESCE:
            safe = self._control_stopped(model, now)
            if safe and state is not None:
                job.phase = JobPhase.BEGIN
            elif now - job.started > 1000:
                self.fail("Nie potwierdzono bezpiecznego zatrzymania CO i wyjść")
            return out
        if job.phase in (JobPhase.REBOOT, JobPhase.NORMAL):
            if now - job.sent_at > 200:
                self.fail("Nie potwierdzono poprawnego rozruchu urządzenia")
                return out
            if state is None:
                return out
            if state.session == job.original_session:
                # Commit ACK is useful for rejection details, but a restart may
                # close MQTT before it arrives. Only authenticated trial + normal
                # boots confirm success; never require an ACK from a dead session.
                if job.waiting_sequence == state.ack:
                    if state.error:
                        self.fail(f"Urządzenie odrzuciło operację (kod {state.error})")
                    else:
                        job.waiting_sequence = None
                return out
            if state.version != job.version:
                self.fail("Urządzenie wróciło do poprzedniej wersji (rollback)")
                return out
            if job.expected_generation is not None and state.generation != job.expected_generation:
                self.fail(
                    "Sterownik wrócił do poprzedniej konfiguracji; ustawienie nie zostało zapisane"
                )
                return out
            if job.phase == JobPhase.REBOOT and state.phase == 4:
                if job.node == 1 and not self._control_stopped(model, now, state.session):
                    return out
                if job.reboot_session != state.session:
                    job.reboot_session = state.session
                    job.waiting_sequence = None  # Old boot ACK cannot confirm a new boot.
                if job.waiting_sequence is None or now - job.sent_at > 2:
                    seq, wire = self.command(
                        job.node,
                        b"\x04" + state.version.pack() + struct.pack(">Q", state.generation),
                        now,
                    )
                    job.waiting_sequence = seq
                    job.sent_at = now
                    out.append(wire)
            if state.phase == 5:
                job.phase = JobPhase.NORMAL
            if (
                state.phase == 0
                and not state.hold
                and job.reboot_session is not None
                and state.session != job.reboot_session
            ):
                job.phase = JobPhase.SUCCEEDED
                if job.node == 2 and self.fresh(1, now) is not None:
                    out.append(self.command(1, b"\x07", now)[1])
            return out
        if state is None:
            return out
        if state.session != job.original_session:
            self.fail("Urządzenie zrestartowało się podczas zapisu")
            return out
        if job.waiting_sequence is not None:
            if state.ack == job.waiting_sequence:
                if state.error:
                    self.fail(f"Urządzenie odrzuciło operację (kod {state.error})")
                    return out
                if job.waiting_offset is not None and state.offset != job.waiting_offset:
                    self.fail("Niezgodny potwierdzony offset obrazu")
                    return out
                job.waiting_sequence = None
                if job.phase == JobPhase.BEGIN:
                    if job.kind == JobKind.OPERATION:
                        job.phase = JobPhase.REBOOT
                        return out
                    job.phase = JobPhase.STREAM
                elif job.phase == JobPhase.STREAM:
                    job.offset = state.offset
                elif job.phase == JobPhase.FINISH:
                    job.phase = JobPhase.REBOOT
                    return out
            elif now - job.sent_at > 8:
                self.fail("Nie otrzymano potwierdzenia bloku")
                return out
            else:
                return out
        if job.phase == JobPhase.BEGIN:
            payload = job.begin
            expected = 0 if job.kind == JobKind.FIRMWARE else None
        elif job.phase == JobPhase.STREAM:
            if job.offset == len(job.blob):
                job.phase = JobPhase.FINISH
                payload = b"\x03"
                expected = None
            else:
                block = job.blob[job.offset : job.offset + 128]
                payload = b"\x02" + struct.pack(">I", job.offset) + block
                expected = job.offset + len(block)
        elif job.phase == JobPhase.FINISH:
            payload = b"\x03"
            expected = None
        else:
            return out
        sequence, wire = self.command(job.node, payload, now)
        job.waiting_sequence = sequence
        job.waiting_offset = expected
        job.sent_at = now
        if payload[0] == 3 or job.kind == JobKind.OPERATION:
            job.phase = JobPhase.REBOOT
        out.append(wire)
        return out

    def presentation(self, node: int, now: float) -> tuple[bool, dict, dict]:
        state = self.fresh(node, now)
        package = self.packages.get(node)
        job = self.job if self.job is not None and self.job.node == node else None
        current = str(state.version) if state else None
        latest = (
            str(package.version)
            if package and (state is None or package.version > state.version)
            else current
        )
        data = {
            "installed_version": current,
            "latest_version": latest,
            "title": f"Podłogówka · {self.nodes[node].name}",
            "in_progress": bool(job and job.kind == JobKind.FIRMWARE and self.active),
            "update_percentage": 100
            if job and job.phase == JobPhase.SUCCEEDED
            else round(90 * job.offset / len(job.blob), 1)
            if job and job.blob
            else 0,
        }
        error = (
            self.request_errors.get(node)
            or (job.error if job else None)
            or self.catalog_errors.get(node)
        )
        if error:
            data["release_summary"] = error[:255]
        diagnostic = {
            "phase": job.phase if job else None,
            "error": error,
            "device_error": state.error if state else None,
            "config_rollback": state.config_rollback if state else None,
        }
        return state is not None and node not in self.catalog_errors, data, diagnostic
