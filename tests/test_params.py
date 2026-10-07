import math
import json
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

    def test_m0_5_design_receives_m1_defaults(self):
        legacy = {"schema_version": 1, "revision": 0, "collar_style": "low", "collar_height_mm": 75.0}
        params, warnings = normalize_params(legacy)
        self.assertEqual(warnings, [])
        expected = {"foot_side": "right", "size_eu": 42, "foot_width": "standard",
                    "toe_roundness": 0, "toe_height_scale": 1, "heel_sole_mm": 24,
                    "forefoot_sole_mm": 16, "edge_radius_mm": 2, "outsole_flare_mm": 6,
                    "upper_thickness_mm": 2.4}
        for key, value in expected.items():
            self.assertEqual(params[key], value)
        self.assertEqual(params["revision"], 0)
        self.assertNotIn("foot_side", legacy)

    def test_all_m1_numeric_bounds_clamp_with_chinese_messages(self):
        bounds = {"size_eu": (35, 46), "toe_roundness": (-0.15, 0.15),
                  "toe_height_scale": (0.85, 1.15), "heel_sole_mm": (12, 40),
                  "forefoot_sole_mm": (8, 30), "edge_radius_mm": (0.5, 4),
                  "outsole_flare_mm": (4, 8), "upper_thickness_mm": (1.8, 4)}
        for key, (lower, upper) in bounds.items():
            for value, expected in ((-100, lower), (100, upper)):
                with self.subTest(key=key, value=value):
                    params, warnings = normalize_params({key: value})
                    self.assertEqual(params[key], expected)
                    self.assertEqual(len(warnings), 1)
                    self.assertIn("已截断", warnings[0])
        self.assertIsInstance(normalize_params({"size_eu": 100})[0]["size_eu"], int)

    def test_valid_m1_values_are_preserved(self):
        values = {"foot_side": "left", "size_eu": 39, "foot_width": "wide",
                  "toe_roundness": 0.04, "toe_height_scale": 0.95,
                  "heel_sole_mm": 27, "forefoot_sole_mm": 20,
                  "edge_radius_mm": 3.5, "outsole_flare_mm": 7, "upper_thickness_mm": 3}
        params, warnings = normalize_params(values)
        self.assertEqual(warnings, [])
        for key, value in values.items():
            self.assertEqual(params[key], value)

    def test_width_and_roundness_joint_clamp_is_visible_in_effective_json(self):
        for width, requested, expected in (("wide", .15, .05), ("narrow", -.15, -.05)):
            params, warnings = normalize_params({"foot_width": width, "toe_roundness": requested})
            self.assertEqual(params["toe_roundness"], expected)
            self.assertEqual(len(warnings), 1)
            self.assertIn("已截断", warnings[0])
            self.assertEqual(normalize_params(params), (params, []))

    def test_saved_design_and_previous_revision_are_valid(self):
        current = json.loads((ROOT / "designs/current.json").read_text(encoding="utf-8"))
        previous = json.loads((ROOT / "designs/history/v000.json").read_text(encoding="utf-8"))
        self.assertEqual(previous["revision"], 0)
        self.assertGreater(current["revision"], previous["revision"])
        for values in (previous, current):
            params, warnings = normalize_params(values)
            self.assertEqual(warnings, [])
            self.assertEqual(params["schema_version"], 1)

    def test_invalid_values_are_rejected(self):
        for values in ({"collar_height_mm": math.nan}, {"collar_height_mm": math.inf},
                       {"collar_height_mm": True}, {"collar_height_mm": "75"},
                       {"collar_style": "high"}, {"unexpected": 2}, {"revision": -1},
                       {"revision": 1.5}, {"schema_version": 2}, {"size_eu": 42.5},
                       {"size_eu": True}, {"foot_width": "extra-wide"}, {"foot_side": "both"},
                       {"toe_roundness": math.inf}, {"upper_thickness_mm": "2.4"},
                       {"outsole_flare_mm": None}, {"heel_sole_mm": False}):
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
