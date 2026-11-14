"""Paired secondary evidence and original slow-probe validity."""

import dataclasses
import struct
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, PropertyMock, patch

from test_adapter import DATA
from test_ota import IDENTITY, KEY, wire
from test_protocol import gateway, report, status

from custom_components.ufh_heating.client.maintenance import MQTT_DOMAIN, Maintenance, Node
from custom_components.ufh_heating.client.mapping import mapping_fingerprint
from custom_components.ufh_heating.client.wire import Packet, decode
from custom_components.ufh_heating.runtime import HeatingRuntime
from custom_components.ufh_heating.settings import Settings


def evidence(sequence=1, *, version=(0, 5, 0), flag=1):
    packet = decode(wire(sequence, version=version), KEY, MQTT_DOMAIN)
    payload = bytearray(packet.payload)
    payload[75] = 1
    payload.extend(mapping_fingerprint(tuple(range(6)), tuple(range(8))))
    if flag is not None:
        payload.append(flag)
    return Packet(
        packet.kind, packet.node, packet.session, packet.sequence, packet.deadline, bytes(payload)
    ).encode(KEY, MQTT_DOMAIN)


class SecondaryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.client = Maintenance(KEY, (Node(1, IDENTITY, b"P" * 32),), Path(tmp.name))

    def test_versioned_length_and_boolean_are_authenticated(self):
        result = self.client.accept(evidence(), False, 1)
        self.assertTrue(result.secondary_enabled)
        for raw in [evidence(2, flag=2), evidence(2, flag=None), evidence(2, version=(0, 4, 1))]:
            with self.assertRaises(ValueError):
                self.client.accept(raw, False, 1.1)
        self.assertIsNone(
            self.client.accept(
                evidence(2, version=(0, 4, 1), flag=None), False, 1.1
            ).secondary_enabled
        )

    def test_admission_revokes_modes_and_sends_only_explicit_flag(self):
        self.client.accept(evidence(), False, 1)
        model = gateway()
        model.enabled = [True] * 9
        self.client.start_secondary(True, model, 1.1)
        self.assertFalse(any(model.enabled))
        self.assertEqual(self.client.job.begin, b"\x0a\x01")
        with self.assertRaises(ValueError):
            self.client.start_secondary(True, model, 1.2)

    def test_room_report_keeps_original_age_and_legacy_cannot_accept_long_deadline(self):
        model = gateway()
        model.config = tuple(dataclasses.replace(z, maximum_age_ms=2_700_000) for z in model.config)
        model.accept_status(status(), retained=False, monotonic=10)
        model.measurement(0, report(), retained=False, unix_ms=1_600_000, monotonic=10)
        expires = model.samples[0].expires_monotonic
        self.assertEqual(expires, 2110)
        model.set_mode(0, "heat")
        packet = decode(model.command(monotonic=10.1), model.key)
        self.assertEqual(packet.payload[16:18], bytes(2))
        self.assertEqual(packet.payload[22], 2)
        self.assertEqual(model.samples[0].expires_monotonic, expires)
        model.set_mode(0, "heat")
        packet = decode(model.command(monotonic=10.2, temperature_limit_ms=2_700_000), model.key)
        self.assertEqual(packet.payload[22], 0)
        self.assertIn(struct.unpack_from(">Q", packet.payload, 23)[0], (2_100_999, 2_101_000))

    def test_secondary_availability_requires_admission_freshness_and_mapping(self):
        settings = Settings.parse(
            {**DATA, "upper_device_id": (b"R" * 16).hex(), "upper_public_key": (b"S" * 32).hex()},
            {},
        )
        runtime = HeatingRuntime(MagicMock(), MagicMock(), settings)
        runtime.maintenance = self.client
        runtime.model.status = SimpleNamespace(secondary_live=True)
        with (
            patch.object(HeatingRuntime, "live", new_callable=PropertyMock, return_value=True),
            patch("custom_components.ufh_heating.runtime.time.monotonic", return_value=1.1),
        ):
            primary = self.client.accept(evidence(), False, 1)
            self.assertFalse(runtime.node_live(2))
            secondary = dataclasses.replace(primary, secondary_enabled=False)
            self.client.states[2] = secondary
            self.assertTrue(runtime.node_live(2))
            runtime.model.status.secondary_live = False
            self.assertFalse(runtime.node_live(2))
            runtime.model.status.secondary_live = True
            self.client.states[2] = dataclasses.replace(secondary, mapping_fingerprint=bytes(32))
            self.assertFalse(runtime.node_live(2))
            self.client.states[2] = secondary
            self.client.states[1] = dataclasses.replace(primary, secondary_enabled=False)
            self.assertFalse(runtime.node_live(2))


class SecondaryWireTests(unittest.TestCase):
    def test_status_requires_explicit_authenticated_output_proof(self):
        from test_protocol import KEY as CONTROL_KEY

        from custom_components.ufh_heating.client.wire import Status

        raw = bytearray(decode(status(), CONTROL_KEY).payload)
        raw[56] = 4
        raw.extend((1, 1))
        decoded = Status.decode(bytes(raw))
        self.assertEqual(decoded.primary_floor, "upper")
        self.assertTrue(decoded.secondary_live)
        raw[61] = 2
        with self.assertRaises(ValueError):
            Status.decode(bytes(raw))
        with self.assertRaises(ValueError):
            Status.decode(bytes(raw[:61]))
