"""Floor/node ownership, signed schema-3 evidence and mismatch quiescing."""

import unittest

from test_adapter import DATA
from test_protocol import KEY, gateway, status

from custom_components.ufh_heating.client.topology import Floor
from custom_components.ufh_heating.client.wire import Packet, Status, decode
from custom_components.ufh_heating.settings import Settings


def physical_status(model, *, floor=1, accepted=0, sequence=1):
    packet = decode(status(client=model.client_session, accepted=accepted, sequence=sequence), KEY)
    payload = bytearray(packet.payload)
    payload[56] = 3
    payload.append(floor)
    return Packet(
        packet.kind, packet.node, packet.session, packet.sequence, packet.deadline, bytes(payload)
    ).encode(KEY)


class TopologyTests(unittest.TestCase):
    def test_upper_primary_needs_no_phantom_secondary_but_ground_requires_one(self):
        prepared = Settings.parse(
            DATA,
            {"primary_floor": "upper", "zones": ["wardrobe", "bedroom", "upper_bathroom_hall"]},
        )
        self.assertEqual(prepared.allowed, frozenset({4, 5, 8}))
        self.assertEqual(len(prepared.nodes), 1)
        self.assertEqual(prepared.primary_floor.node_for_zone(8), 1)
        self.assertEqual(prepared.primary_floor.node_for_zone(0), 2)
        with self.assertRaises(ValueError):
            Settings.parse(DATA, {"primary_floor": "upper", "zones": ["living"]})
        for value in (True, None, "other"):
            with self.assertRaises(ValueError):
                Settings.parse(DATA, {"primary_floor": value})

    def test_new_status_binds_actual_floor_and_rejects_unknown_values(self):
        model = gateway()
        decoded = Status.decode(decode(physical_status(model), KEY).payload)
        self.assertEqual(decoded.schema, 3)
        self.assertEqual(decoded.primary_floor, "upper")
        legacy = Status.decode(decode(status(), KEY).payload)
        self.assertEqual(legacy.primary_floor, "ground")
        with self.assertRaises(ValueError):
            Status.decode(decode(physical_status(model, floor=2), KEY).payload)

    def test_mismatch_never_sends_enabled_zones_or_source_authority(self):
        model = gateway()
        model.accept_status(physical_status(model), retained=False, monotonic=1)
        model.set_mode(8, "heat")
        command = model.command(monotonic=1.1, source_authority=True)
        payload = decode(command, KEY).payload
        self.assertEqual(payload[16:18], bytes(2))
        self.assertEqual(payload[144], 0)
        self.assertFalse(model.is_live(1.1))

    def test_matching_upper_profile_reports_own_ack_and_does_not_restore_enables(self):
        model = gateway()
        model.primary_floor = Floor.UPPER
        model.accept_status(physical_status(model), retained=False, monotonic=1)
        self.assertEqual(model.enabled, [False] * 9)
        model.command(monotonic=1.1)
        model.accept_status(
            physical_status(model, accepted=1, sequence=2), retained=False, monotonic=1.2
        )
        self.assertTrue(model.is_live(1.2))
        model.set_mode(8, "heat")
        payload = decode(model.command(monotonic=1.3, source_authority=True), KEY).payload
        self.assertEqual(int.from_bytes(payload[16:18], "big"), 1 << 8)
        self.assertEqual(payload[144], 1)


if __name__ == "__main__":
    unittest.main()
