"""Exercise update orchestration with signed device evidence and real release crypto."""

import hashlib
import struct
import tempfile
import unittest
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from helpers import sign
from test_protocol import gateway
from test_protocol import status as control_status

from custom_components.ufh_heating.client.maintenance import (
    MQTT_DOMAIN,
    STATUS,
    JobPhase,
    Maintenance,
    Node,
)
from custom_components.ufh_heating.client.releases import Catalog, Package, Version, image_version
from custom_components.ufh_heating.client.wire import Packet

KEY = b"M" * 32
IDENTITY = b"I" * 16
BOOT = b"N" * 16


def image(version="0.2.1", role=1):
    header = bytearray(24)
    header[0:2] = b"\xe9\x01"
    header[12:14] = b"\x0d\x00"
    header[23] = 1
    app = bytearray(256)
    struct.pack_into("<I", app, 0, 0xABCD5432)
    encoded = version.encode()
    app[16 : 16 + len(encoded)] = encoded
    name = b"underfloor-main" if role == 1 else b"underfloor-upper"
    app[48 : 48 + len(name)] = name
    data = bytes(header) + struct.pack("<II", 0x42000000, len(app)) + app
    checksum = 0xEF
    for byte in app:
        checksum ^= byte
    data += bytes((len(data) | 15) - len(data)) + bytes((checksum,))
    return data + hashlib.sha256(data).digest()


def wire(
    sequence,
    *,
    session=BOOT,
    version=(0, 2, 0),
    generation=1,
    phase=0,
    error=0,
    offset=0,
    total=0,
    ack=0,
    hold=0,
    uptime=1000,
    identity=IDENTITY,
):
    payload = STATUS.pack(
        *version,
        generation,
        0,
        phase,
        error,
        0,
        offset,
        total,
        ack,
        uptime,
        identity,
        bytes(16),
        hold,
        bytes(9),
    )
    return Packet(6, 1, session, sequence, uptime, payload).encode(KEY, MQTT_DOMAIN)


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.signing = Ed25519PrivateKey.from_private_bytes(b"K" * 32)
        self.public = self.signing.public_key().public_bytes_raw()
        self.path = self.root / "main-floor-0.2.1.ufu"
        self.path.write_bytes(sign(image(), 1, self.signing))
        self.client = Maintenance(KEY, (Node(1, IDENTITY, self.public),), self.root)
        self.client.refresh_catalog(0)
        self.client.accept(wire(1), False, 0)
        self.model = gateway()
        self.model.accept_status(control_status(), retained=False, monotonic=0)
        command = self.model.command(source_authority=True, monotonic=0)
        from custom_components.ufh_heating.client.wire import decode

        packet = decode(command, self.model.key)
        self.model.accept_status(
            control_status(
                client=self.model.client_session,
                accepted=packet.sequence,
                sequence=2,
                uptime=1500,
                operation_enabled=False,
            ),
            retained=False,
            monotonic=0.1,
        )

    def test_signature_role_full_esp_image_and_numeric_version(self):
        parsed = Package.parse(self.path.read_bytes(), self.public, 1)
        self.assertEqual(parsed.version, Version(0, 2, 1))
        self.assertEqual(image_version(parsed.image, 1), parsed.version)
        corrupt = bytearray(self.path.read_bytes())
        corrupt[90] ^= 1
        with self.assertRaises(ValueError):
            Package.parse(bytes(corrupt), self.public, 1)
        with self.assertRaises(ValueError):
            Package.parse(self.path.read_bytes(), self.public, 2)
        for version in ("0.2.1-dev", "01.2.3", "0.2", "65536.0.1"):
            with self.assertRaises(ValueError):
                Version.parse(version)

    def test_retained_replayed_identity_mismatched_or_stale_status_cannot_authorize(
        self,
    ):
        for raw, retained in (
            (wire(2), True),
            (wire(1), False),
            (wire(2, identity=b"X" * 16), False),
        ):
            with self.assertRaises(ValueError):
                self.client.accept(raw, retained, 0.2)
        with self.assertRaises(ValueError):
            self.client.start(1, self.model, 4)

    def test_stream_ack_then_trial_confirm_then_new_normal_boot(self):
        from custom_components.ufh_heating.client.wire import decode

        self.client.start(1, self.model, 0.2)
        self.assertFalse(any(self.model.enabled))
        self.assertEqual(self.client.tick(self.model, 0.2), [])
        begin = self.client.tick(self.model, 0.3)[0]
        request = decode(begin, KEY, MQTT_DOMAIN)
        self.assertEqual(request.payload[0], 1)
        total = len(image())
        now = 0.4
        sequence = 2
        offset = 0
        ack = request.sequence
        while offset < total:
            self.client.accept(
                wire(sequence, phase=1, total=total, offset=offset, ack=ack, hold=1),
                False,
                now,
            )
            outgoing = self.client.tick(self.model, now)[0]
            request = decode(outgoing, KEY, MQTT_DOMAIN)
            self.assertEqual(request.payload[0], 2)
            self.assertEqual(struct.unpack_from(">I", request.payload, 1)[0], offset)
            offset += len(request.payload) - 5
            ack = request.sequence
            sequence += 1
            now += 0.1
        self.client.accept(
            wire(sequence, phase=1, total=total, offset=offset, ack=ack, hold=1),
            False,
            now,
        )
        finish = decode(self.client.tick(self.model, now)[0], KEY, MQTT_DOMAIN)
        self.assertEqual(finish.payload, b"\x03")
        sequence += 1
        now += 0.1
        self.client.accept(wire(sequence, phase=3, ack=finish.sequence, hold=1), False, now)
        self.client.tick(self.model, now)
        self.assertEqual(self.client.job.phase, JobPhase.REBOOT)
        # Even a normal-looking new version cannot skip the authenticated trial handshake.
        self.client.accept(wire(1, session=b"T" * 16, version=(0, 2, 1)), False, now + 0.1)
        self.client.tick(self.model, now + 0.1)
        self.assertTrue(self.client.active)
        self.client.accept(
            wire(2, session=b"T" * 16, version=(0, 2, 1), phase=4, hold=1),
            False,
            now + 0.2,
        )
        confirmation = decode(self.client.tick(self.model, now + 0.2)[0], KEY, MQTT_DOMAIN)
        self.assertEqual(confirmation.payload[0], 4)
        self.client.accept(wire(1, session=b"V" * 16, version=(0, 2, 1)), False, now + 0.3)
        self.client.tick(self.model, now + 0.3)
        self.assertEqual(self.client.job.phase, JobPhase.SUCCEEDED)

    def test_wrong_offset_device_error_and_disconnect_are_failures_not_success(self):
        self.client.start(1, self.model, 0.2)
        self.client.tick(self.model, 0.2)
        self.client.tick(self.model, 0.3)
        job = self.client.job
        self.client.accept(
            wire(
                2,
                phase=1,
                total=len(image()),
                offset=128,
                ack=job.waiting_sequence,
                hold=1,
            ),
            False,
            0.4,
        )
        self.client.tick(self.model, 0.4)
        self.assertEqual(job.phase, JobPhase.FAILED)
        self.assertIn("offset", job.error)

    def test_unconfirmed_command_and_boot_have_bounded_timeouts(self):
        self.client.start(1, self.model, 0.2)
        self.client.tick(self.model, 0.2)
        self.client.tick(self.model, 0.3)
        self.client.accept(wire(2), False, 9)
        self.client.tick(self.model, 9)
        self.assertEqual(self.client.job.phase, JobPhase.FAILED)
        self.assertIn("potwierdzenia", self.client.job.error)

    def test_catalog_does_not_install_an_older_release_when_newest_is_corrupt(self):
        (self.root / "main-floor-0.2.2.ufu").write_bytes(b"invalid")
        with self.assertRaises(ValueError):
            Catalog(self.root, self.public, 1).latest()
        self.client.refresh_catalog(31)
        self.assertIn(1, self.client.catalog_errors)

    def test_second_install_is_rejected_while_the_first_is_active(self):
        self.client.start(1, self.model, 0.2)
        with self.assertRaises(ValueError):
            self.client.start(1, self.model, 0.3)


class OperationTests(UpdateTests):
    # Reuse the signed transport fixture and its OTA regressions.
    def test_operation_requires_new_firmware_and_verified_config_boot(self):
        from custom_components.ufh_heating.client.wire import decode

        with self.assertRaises(ValueError):
            self.client.start_operation(True, 1, self.model, 0.2)
        self.client.accept(wire(2, version=(0, 3, 0)), False, 0.2)
        self.client.start_operation(True, 1, self.model, 0.2)
        self.assertFalse(any(self.model.enabled))
        self.client.tick(self.model, 0.2)
        command = decode(self.client.tick(self.model, 0.3)[0], KEY, MQTT_DOMAIN)
        self.assertEqual(command.payload, b"\x08\x01\x01")
        self.client.accept(
            wire(3, version=(0, 3, 0), phase=3, ack=command.sequence, hold=1), False, 0.4
        )
        self.client.tick(self.model, 0.4)
        self.assertEqual(self.client.job.phase, JobPhase.REBOOT)
        self.client.accept(
            wire(1, session=b"T" * 16, version=(0, 3, 0), generation=2, phase=4, hold=1), False, 0.5
        )
        confirmation = decode(self.client.tick(self.model, 0.5)[0], KEY, MQTT_DOMAIN)
        self.assertEqual(
            confirmation.payload, b"\x04" + Version(0, 3, 0).pack() + struct.pack(">Q", 2)
        )
        self.assertTrue(self.client.active)
        self.client.accept(wire(1, session=b"V" * 16, version=(0, 3, 0), generation=2), False, 0.6)
        self.client.tick(self.model, 0.6)
        self.assertEqual(self.client.job.phase, JobPhase.SUCCEEDED)

    def test_operation_cannot_arm_an_unspecified_interface(self):
        self.client.accept(wire(2, version=(0, 3, 0)), False, 0.2)
        for enabled, profile in ((True, 0), (1, 1), (True, True), (True, 3)):
            with self.subTest(enabled=enabled, profile=profile), self.assertRaises(ValueError):
                self.client.start_operation(enabled, profile, self.model, 0.2)
