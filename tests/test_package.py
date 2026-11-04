"""HACS package boundaries and synchronized release metadata."""

import json
import struct
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = ROOT / "custom_components" / "ufh_heating"


class PackageTests(unittest.TestCase):
    def test_hacs_self_contained_structure_and_manifest(self):
        self.assertEqual(
            [
                p.name
                for p in (ROOT / "custom_components").iterdir()
                if p.is_dir() and p.name != "__pycache__"
            ],
            ["ufh_heating"],
        )
        manifest = json.loads((INTEGRATION / "manifest.json").read_text())
        self.assertEqual(manifest["domain"], "ufh_heating")
        self.assertTrue(manifest["config_flow"])
        self.assertIn("mqtt", manifest["dependencies"])
        self.assertTrue(manifest["issue_tracker"].startswith("https://github.com/"))
        from custom_components.ufh_heating.const import VERSION

        self.assertEqual(manifest["version"], VERSION)
        self.assertEqual(json.loads((ROOT / "hacs.json").read_text())["homeassistant"], "2026.10.2")
        for name in (
            "__init__.py",
            "config_flow.py",
            "climate.py",
            "binary_sensor.py",
            "sensor.py",
            "update.py",
            "diagnostics.py",
        ):
            self.assertTrue((INTEGRATION / name).is_file())

    def test_translation_keys_and_brand_assets_are_complete(self):
        en = json.loads((INTEGRATION / "strings.json").read_text())

        def shape(obj):
            return {k: shape(v) for k, v in obj.items()} if isinstance(obj, dict) else None

        for language in ("en", "pl"):
            self.assertEqual(
                shape(en),
                shape(json.loads((INTEGRATION / "translations" / f"{language}.json").read_text())),
            )
        for size, name in ((256, "icon.png"), (512, "icon@2x.png")):
            data = (ROOT / "brand" / name).read_bytes()
            self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
            self.assertEqual(struct.unpack(">II", data[16:24]), (size, size))
            self.assertEqual(data, (INTEGRATION / "brand" / name).read_bytes())

    def test_public_tree_excludes_firmware_and_private_installation_data(self):
        for path in ROOT.rglob("*"):
            if ".git" in path.parts or ".ruff_cache" in path.parts or "__pycache__" in path.parts:
                continue
            self.assertFalse(path.is_symlink(), str(path))
            self.assertNotIn(
                path.name, ("gateway.json", "parter.json", "release-signing.pem", ".env")
            )
            self.assertNotIn(path.suffix, (".rs", ".ufu", ".pem", ".bin"))
