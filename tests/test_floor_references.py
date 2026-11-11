"""Explicit shared references for two floors, with independent later replacement."""

import unittest

from test_adapter import DATA

from custom_components.ufh_heating.client.model import ZONE_IDS
from custom_components.ufh_heating.settings import Settings


class FloorReferencesTests(unittest.TestCase):
    def options(self):
        result = {
            "primary_floor": "upper",
            "zones": list(ZONE_IDS[4:]),
            "shared_upper_reference_zones": list(ZONE_IDS[4:]),
            "shared_ground_reference_zones": list(ZONE_IDS[:4]),
        }
        for zone in ZONE_IDS:
            result["sensor_" + zone] = (
                "sensor.upper_air" if zone in ZONE_IDS[4:] else "sensor.living_air"
            )
        result["sensor_upper_bathroom_hall"] = "sensor.bathroom_air"
        return result

    def test_all_upper_rooms_ready_and_bathroom_keeps_own_air(self):
        settings = Settings.parse(DATA, self.options())
        self.assertEqual(settings.allowed, frozenset(range(4, 9)))
        self.assertEqual(settings.sensors[4:8], ("sensor.upper_air",) * 4)
        self.assertEqual(settings.sensors[8], "sensor.bathroom_air")
        self.assertEqual(settings.sensors[:4], ("sensor.living_air",) * 4)

    def test_replacing_one_room_sensor_needs_no_group_change(self):
        options = self.options()
        options["sensor_bedroom_right"] = "sensor.right_air"
        settings = Settings.parse(DATA, options)
        self.assertEqual(settings.sensors[6], "sensor.right_air")
        self.assertEqual(settings.sensors[7], "sensor.upper_air")

    def test_cross_floor_sharing_undeclared_duplicates_and_invalid_groups_fail(self):
        for field, value in (
            ("shared_upper_reference_zones", ["wardrobe"]),
            ("shared_upper_reference_zones", ["wardrobe", "wardrobe"]),
            ("shared_upper_reference_zones", ["wardrobe", "living"]),
            ("shared_upper_reference_zones", "wardrobe"),
            ("shared_upper_reference_zones", []),
        ):
            options = self.options()
            options[field] = value
            with self.assertRaises(ValueError):
                Settings.parse(DATA, options)
