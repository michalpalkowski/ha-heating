"""Authenticated physical mapping evidence and fail-off on configuration mismatch."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from test_adapter import DATA
from test_ota import IDENTITY, KEY, wire
from test_protocol import gateway, status

from custom_components.ufh_heating.client.maintenance import MQTT_DOMAIN, Maintenance, Node
from custom_components.ufh_heating.client.mapping import mapping_fingerprint
from custom_components.ufh_heating.client.wire import Packet, decode
from custom_components.ufh_heating.runtime import HeatingRuntime
from custom_components.ufh_heating.settings import Settings

GROUND = tuple(range(6))
UPPER = tuple(reversed(range(8)))


def mapped_status(sequence=1, fingerprint=None, **kwargs):
    packet = decode(wire(sequence, version=(0, 4, 1), **kwargs), KEY, MQTT_DOMAIN)
    payload = bytearray(packet.payload)
    payload[75] = 1
    payload.extend(fingerprint if fingerprint is not None else mapping_fingerprint(GROUND, UPPER))
    return Packet(
        packet.kind, packet.node, packet.session, packet.sequence, packet.deadline, bytes(payload)
    ).encode(KEY, MQTT_DOMAIN)


class MappingTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def test_fingerprint_distinguishes_permutations_and_rejects_invalid_channels(self):
        self.assertNotEqual(
            mapping_fingerprint(GROUND, UPPER), mapping_fingerprint(GROUND, tuple(range(8)))
        )
        for ground, upper in [
            (GROUND, (0,) * 8),
            ((0, 1, 2, 3, 4, 6), UPPER),
            (GROUND, (True, 1, 2, 3, 4, 5, 6, 7)),
        ]:
            with self.assertRaises(ValueError):
                mapping_fingerprint(ground, upper)

    def test_modern_evidence_requires_marker_and_complete_authenticated_fingerprint(self):
        client = Maintenance(KEY, (Node(1, IDENTITY, b"P" * 32),), self.root)
        result = client.accept(mapped_status(), False, 1)
        self.assertEqual(result.mapping_fingerprint, mapping_fingerprint(GROUND, UPPER))
        for candidate in [wire(2, version=(0, 4, 1)), mapped_status(2)[:-1]]:
            with self.assertRaises(ValueError):
                client.accept(candidate, False, 1.1)
        with self.assertRaises(ValueError):
            client.accept(mapped_status(2), True, 1.1)

    def test_wrong_mapping_revokes_enables_and_authority_but_allows_off_ack(self):
        model = gateway()
        model.accept_status(status(), retained=False, monotonic=1)
        model.set_mode(8, "heat")
        packet = decode(
            model.command(monotonic=1.1, source_authority=True, mapping_verified=False), model.key
        )
        self.assertEqual(packet.payload[16:18], bytes(2))
        self.assertEqual(packet.payload[144], 0)
        self.assertFalse(any(model.enabled))

    def test_runtime_compares_actual_config_and_does_not_accept_stale_proof(self):
        settings = Settings.parse(DATA, {"upper_channels": list(UPPER)})
        runtime = HeatingRuntime(MagicMock(), MagicMock(), settings)
        runtime.maintenance = Maintenance(KEY, (Node(1, IDENTITY, b"P" * 32),), self.root)
        runtime.maintenance.accept(mapped_status(), False, 1)
        self.assertTrue(runtime.mapping_matches(1.5))
        self.assertFalse(runtime.mapping_matches(4))
        runtime.maintenance.accept(mapped_status(2, fingerprint=bytes(32)), False, 4.1)
        self.assertFalse(runtime.mapping_matches(4.2))
