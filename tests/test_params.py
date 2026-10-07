import math
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from engine.params import normalize_params
from tools.find_blender import find_blender


class ParameterTests(unittest.TestCase):
    def test_style_defaults(self):
        self.assertEqual(normalize_params({})[0]["collar_height_mm"], 75)
        self.assertEqual(normalize_params({"collar_style": "mid"})[0]["collar_height_mm"], 100)

    def test_style_dependent_clamps_are_reported(self):
        for style, height, expected in (("low", -20, 60), ("low", 120, 85), ("mid", 65, 86), ("mid", 900, 115)):
            with self.subTest(style=style, height=height):
                params, warnings = normalize_params({"collar_style": style, "collar_height_mm": height})
                self.assertEqual(params["collar_height_mm"], expected)
                self.assertEqual(len(warnings), 1)
                self.assertIn("已截断", warnings[0])

    def test_invalid_values_are_rejected(self):
        for values in ({"collar_height_mm": math.nan}, {"collar_height_mm": math.inf},
                       {"collar_height_mm": True}, {"collar_height_mm": "75"},
                       {"collar_style": "high"}, {"unexpected": 2}, {"revision": -1},
                       {"revision": 1.5}, {"schema_version": 2}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                normalize_params(values)

    def test_normalizing_does_not_mutate_input(self):
        original = {"collar_style": "low", "collar_height_mm": 100}
        normalize_params(original)
        self.assertEqual(original["collar_height_mm"], 100)


class BlenderFinderTests(unittest.TestCase):
    def test_explicit_missing_path_does_not_silently_fall_back(self):
        with patch.dict("os.environ", {"BLENDER_PATH": "/missing/shoe-designer/blender"}):
            with self.assertRaisesRegex(FileNotFoundError, "BLENDER_PATH"):
                find_blender()

    def test_app_path_and_spaces(self):
        with tempfile.TemporaryDirectory() as directory:
            app = Path(directory) / "Blender Test.app"
            executable = app / "Contents/MacOS/Blender"
            executable.parent.mkdir(parents=True)
            executable.write_text("#!/bin/sh\nexit 0\n")
            executable.chmod(0o755)
            with patch.dict("os.environ", {"BLENDER_PATH": str(app)}):
                self.assertEqual(find_blender(), str(executable.resolve()))


if __name__ == "__main__":
    unittest.main()
