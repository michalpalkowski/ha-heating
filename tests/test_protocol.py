import json
import struct
import unittest
from pathlib import Path

from custom_components.ufh_heating.client.model import Gateway, ZoneConfig
from custom_components.ufh_heating.client.wire import (
    BUS_DOMAIN,
    Packet,
    Zone,
    control_payload,
    decode,
)

KEY = bytes([0x5A]) * 32
SESSION = bytes([0x11]) * 16


def gateway():
    return Gateway(
        KEY,
        tuple(
            ZoneConfig(f"zone_{i}", f"Room {i}", f"sensor_{i}", f"reports/{i}", 900_000, 2200)
            for i in range(9)
        ),
    )


def status(
    client=bytes(16),
    accepted=0,
    uptime=1000,
    sequence=1,
    heat=False,
    operation_enabled=True,
    session=SESSION,
):
    payload = bytearray(60)
    struct.pack_into(">16sQQ", payload, 0, client, accepted, uptime)
    payload[34] = int(heat)
    payload[49] = int(operation_enabled)
    payload[56:60] = bytes((2, 0, 1 if operation_enabled else 0, 4))
    return Packet(2, 1, session, sequence, uptime, bytes(payload)).encode(KEY)


def report(timestamp=1_000_000, event="boot:1", temperature=2100, quality="valid"):
    return json.dumps(
        {
            "schema": 1,
            "sensor_id": "sensor_0",
            "event_id": event,
            "reported_at_unix_ms": timestamp,
            "temperature_centi_c": temperature,
            "quality": quality,
        }
    ).encode()


class WireTests(unittest.TestCase):
    def test_rust_interoperability_vector(self):
        vector = Path(__file__).with_name("control-vector.hex").read_text().strip()
        packet = Packet(3, 2, SESSION, 1, 5000, b"\x01")
        self.assertEqual(packet.encode(KEY, BUS_DOMAIN).hex(), vector)
        self.assertEqual(decode(bytes.fromhex(vector), KEY, BUS_DOMAIN), packet)

    def test_modification_truncation_and_wrong_domain_fail(self):
        raw = Packet(3, 2, SESSION, 1, 5000, b"\x01").encode(KEY, BUS_DOMAIN)
        for index in range(len(raw)):
            tampered = bytearray(raw)
            tampered[index] ^= 1
            with self.assertRaises(ValueError):
                decode(bytes(tampered), KEY, BUS_DOMAIN)
        with self.assertRaises(ValueError):
            decode(raw, KEY)

    def test_exact_schema_layout_for_c6(self):
        zones = (Zone(True, 2200, 2100, 0, 60_000),) + (Zone(False, 2000, None, 1, 0),) * 8
        payload = control_payload(SESSION, zones, 0, 5000)
        self.assertEqual(len(payload), 144)
        self.assertEqual(struct.unpack_from(">hhBQ", payload, 18), (2200, 2100, 0, 60_000))
        self.assertEqual(payload[135], 0)
        self.assertEqual(struct.unpack_from(">Q", payload, 136)[0], 5000)


class FreshnessTests(unittest.TestCase):
    def test_retained_old_future_duplicate_and_cached_reports_are_rejected(self):
        g = gateway()
        for raw, retained in [
            (report(), True),
            (report(timestamp=1000), False),
            (report(timestamp=1_002_000), False),
            (b'{"temperature":21,"battery":90}', False),
        ]:
            with self.assertRaises(ValueError):
                g.measurement(0, raw, retained=retained, unix_ms=1_000_000, monotonic=10)
        g.measurement(0, report(), retained=False, unix_ms=1_000_000, monotonic=10)
        expiry = g.samples[0].expires_monotonic
        for raw in [report(), report(timestamp=1_000_001)]:
            with self.assertRaises(ValueError):
                g.measurement(0, raw, retained=False, unix_ms=1_000_001, monotonic=11)
        self.assertEqual(g.samples[0].expires_monotonic, expiry)

    def test_identical_new_real_temperature_report_does_refresh(self):
        g = gateway()
        g.measurement(0, report(), retained=False, unix_ms=1_000_000, monotonic=10)
        g.measurement(
            0,
            report(timestamp=1_060_000, event="boot:2"),
            retained=False,
            unix_ms=1_060_000,
            monotonic=70,
        )
        self.assertEqual(g.samples[0].temperature, 2100)
        self.assertEqual(g.samples[0].expires_monotonic, 970)

    def test_transport_delay_is_subtracted_from_temperature_validity(self):
        g = gateway()
        g.measurement(0, report(), retained=False, unix_ms=1_060_000, monotonic=10)
        self.assertEqual(g.samples[0].expires_monotonic, 850)

    def test_sensor_fault_overrides_previous_good_sample_without_waiting_for_stale(
        self,
    ):
        g = gateway()
        g.accept_status(status(), retained=False, monotonic=10)
        g.measurement(0, report(), retained=False, unix_ms=1_000_000, monotonic=10)
        g.measurement(
            0,
            report(timestamp=1_000_001, event="boot:2", temperature=None, quality="fault"),
            retained=False,
            unix_ms=1_000_001,
            monotonic=11,
        )
        packet = decode(g.command(source_authority=True, monotonic=11), KEY)
        self.assertEqual(packet.payload[22], 3)
        self.assertEqual(struct.unpack_from(">Q", packet.payload, 23)[0], 0)

    def test_source_ack_expiry_is_independent_of_temperature(self):
        g = gateway()
        g.accept_status(status(), retained=False, monotonic=10)
        raw = json.dumps({"schema": 1, "state": "off", "reported_at_unix_ms": 1_000_000}).encode()
        g.source_evidence(raw, retained=False, unix_ms=1_000_000, monotonic=10)
        packet = decode(g.command(source_authority=True, monotonic=11), KEY)
        self.assertEqual(packet.payload[135], 0)
        g.accept_status(status(uptime=4000, sequence=2), retained=False, monotonic=13)
        packet = decode(g.command(source_authority=True, monotonic=13), KEY)
        self.assertEqual(packet.payload[135], 2)


class SessionTests(unittest.TestCase):
    def test_heat_needs_signed_live_ack_of_this_gateway_session(self):
        g = gateway()
        g.accept_status(status(heat=True), retained=False, monotonic=10)
        self.assertFalse(g.heat_permitted(10))
        command = decode(g.command(source_authority=True, monotonic=10), KEY)
        g.accept_status(
            status(
                client=g.client_session,
                accepted=command.sequence,
                sequence=2,
                uptime=1500,
                heat=True,
            ),
            retained=False,
            monotonic=10.5,
        )
        self.assertTrue(g.heat_permitted(10.5))
        self.assertFalse(g.heat_permitted(13))

    def test_old_status_after_gateway_restart_cannot_enable_boiler(self):
        old = gateway()
        old.accept_status(status(), retained=False, monotonic=10)
        command = decode(old.command(source_authority=True, monotonic=10), KEY)
        stale = status(client=old.client_session, accepted=command.sequence, sequence=2, heat=True)
        new = gateway()
        new.accept_status(stale, retained=False, monotonic=20)
        self.assertFalse(new.heat_permitted(20))

    def test_replay_and_retained_status_are_rejected(self):
        g = gateway()
        with self.assertRaises(ValueError):
            g.accept_status(status(), retained=True, monotonic=1)
        g.accept_status(status(), retained=False, monotonic=1)
        with self.assertRaises(ValueError):
            g.accept_status(status(), retained=False, monotonic=2)

    def test_controller_reboot_revokes_ui_modes_and_old_ack(self):
        g = gateway()
        g.accept_status(status(), retained=False, monotonic=1)
        g.set_mode(0, "heat")
        g.accept_status(status(session=b"R" * 16, uptime=10), retained=False, monotonic=2)
        self.assertFalse(any(g.enabled))
        self.assertIsNone(g.confirmed)

    def test_commands_and_pending_acks_are_bounded(self):
        g = gateway()
        self.assertIsNone(g.command(source_authority=True, monotonic=1))
        g.accept_status(status(), retained=False, monotonic=1)
        for _ in range(100):
            self.assertIsNotNone(g.command(source_authority=True, monotonic=1))
        self.assertEqual(len(g.pending), 32)
        self.assertIsNone(g.command(source_authority=True, monotonic=5))

    def test_ui_is_non_optimistic_and_invalid_setpoints_are_rejected(self):
        g = gateway()
        for target in ["NaN", "Infinity", "9.99", "28.01", "20.001", "bad"]:
            with self.assertRaises(ValueError):
                g.set_target(0, target)
        g.set_target(0, "22.5")
        self.assertEqual(g.target[0], 2250)
        self.assertIsNone(g.confirmed)


class ProductionContractTests(unittest.TestCase):
    def test_legacy_controller_is_read_only_and_current_authority_is_explicit(self):
        from custom_components.ufh_heating.client.wire import Status

        g = gateway()
        raw = bytearray(decode(status(), KEY).payload)
        legacy = bytes(raw[:56]) + b"\0\0"
        decoded = Status.decode(legacy)
        self.assertEqual(decoded.schema, 1)
        self.assertIsNone(decoded.installation_facts)
        self.assertIsNone(decoded.relay_profile)
        packet = Packet(2, 1, SESSION, 1, 1000, legacy).encode(KEY)
        g.accept_status(packet, retained=False, monotonic=0)
        g.set_mode(0, "heat")
        command = decode(g.command(source_authority=True, monotonic=0), KEY)
        self.assertEqual(len(command.payload), 144)
        self.assertEqual(command.payload[16:18], b"\0\0")
        self.assertFalse(g.heat_permitted(0))

        current = gateway()
        current.accept_status(status(), retained=False, monotonic=0)
        current.set_mode(0, "heat")
        command = decode(current.command(source_authority=False, monotonic=0), KEY)
        self.assertEqual(len(command.payload), 145)
        self.assertEqual(command.payload[144], 0)
        current.accept_status(
            status(client=current.client_session, accepted=command.sequence, sequence=2, heat=True),
            retained=False,
            monotonic=0.1,
        )
        self.assertFalse(current.heat_permitted(0.1))
        raw[58] = 0
        with self.assertRaises(ValueError):
            Status.decode(bytes(raw))

    def test_mqtt_reconnect_does_not_retimestamp_room_measurements(self):
        g = gateway()
        g.measurement(0, report(), retained=False, unix_ms=1_000_000, monotonic=10)
        sample = g.samples[0]
        g.disconnected()
        self.assertEqual(g.samples[0], sample)
        self.assertFalse(any(g.enabled))
        self.assertIsNone(g.status)
