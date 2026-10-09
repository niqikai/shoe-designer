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

    def test_m1_design_keeps_solid_structure_by_default(self):
        legacy = json.loads((ROOT / "designs/history/v001.json").read_text(encoding="utf-8"))
        self.assertNotIn("midsole_structure", legacy)
        params, warnings = normalize_params(legacy)
        self.assertEqual(warnings, [])
        self.assertEqual(params["midsole_structure"], "solid")
        self.assertEqual(params["lattice_type"], "gyroid")
        self.assertEqual(params["resolution"], "preview")
        self.assertEqual(params["lattice_density_heel"], .32)
        self.assertEqual(params["lattice_density_arch"], .45)
        self.assertEqual(params["lattice_density_forefoot"], .35)
        self.assertEqual(params["lattice_rod_mm"], 2.0)

    def test_m2_bounds_clamp_with_chinese_messages(self):
        bounds = {"lattice_density_heel": (.20, .60), "lattice_density_arch": (.20, .60),
                  "lattice_density_forefoot": (.20, .60), "lattice_rod_mm": (1.5, 3.0)}
        for key, (lower, upper) in bounds.items():
            for requested, expected in ((-10, lower), (10, upper)):
                with self.subTest(key=key, requested=requested):
                    params, warnings = normalize_params({"midsole_structure": "lattice", key: requested})
                    self.assertEqual(params[key], expected)
                    self.assertEqual(len(warnings), 1)
                    self.assertIn("已截断", warnings[0])
                    self.assertEqual(normalize_params(params), (params, []))

    def test_valid_m2_values_and_structure_types_are_preserved(self):
        for structure in ("solid", "lattice"):
            for kind in ("gyroid", "diamond", "octet"):
                for resolution in ("preview", "export"):
                    values = {"midsole_structure": structure, "lattice_type": kind,
                              "resolution": resolution, "lattice_density_heel": .25,
                              "lattice_density_arch": .55, "lattice_density_forefoot": .40,
                              "lattice_rod_mm": 2.5}
                    params, warnings = normalize_params(values)
                    self.assertEqual(warnings, [])
                    for key, value in values.items():
                        self.assertEqual(params[key], value)

    def test_m2_numeric_values_reject_non_finite_and_wrong_types(self):
        for key in ("lattice_density_heel", "lattice_density_arch", "lattice_density_forefoot", "lattice_rod_mm"):
            for value in (math.nan, math.inf, -math.inf, True, "0.32", None):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    normalize_params({key: value})

    def test_m2_design_receives_m3_defaults_without_changing_geometry(self):
        legacy = json.loads((ROOT / "designs/history/v002.json").read_text(encoding="utf-8"))
        self.assertNotIn("print_process", legacy)
        params, warnings = normalize_params(legacy)
        self.assertEqual(warnings, [])
        expected = {"print_process": "SLS", "material": "TPU", "tpu_shore_a": 90,
                    "build_size_x_mm": 250, "build_size_y_mm": 250,
                    "build_size_z_mm": 250, "build_margin_mm": 2,
                    "build_orientation": "auto"}
        for key, value in expected.items():
            self.assertEqual(params[key], value)
        for key, value in legacy.items():
            self.assertEqual(params[key], value)

    def test_m3_numeric_bounds_clamp_with_chinese_messages(self):
        bounds = {"tpu_shore_a": (70, 95), "build_size_x_mm": (100, 1000),
                  "build_size_y_mm": (100, 1000), "build_size_z_mm": (100, 1000),
                  "build_margin_mm": (0, 10)}
        for key, (lower, upper) in bounds.items():
            for requested, expected in ((-10, lower), (1001, upper)):
                with self.subTest(key=key, requested=requested):
                    params, warnings = normalize_params({key: requested})
                    self.assertEqual(params[key], expected)
                    self.assertEqual(len(warnings), 1)
                    self.assertIn("已截断", warnings[0])
                    self.assertEqual(normalize_params(params), (params, []))
        self.assertIsInstance(normalize_params({"tpu_shore_a": 100})[0]["tpu_shore_a"], int)

    def test_legacy_designs_leave_boundary_rounding_disabled(self):
        for revision in ("v000", "v001", "v002"):
            legacy = json.loads((ROOT / "designs/history" / f"{revision}.json").read_text(encoding="utf-8"))
            self.assertNotIn("boundary_rounding_mm", legacy)
            params, warnings = normalize_params(legacy)
            self.assertEqual(params["boundary_rounding_mm"], 0)
            self.assertEqual(warnings, [])

    def test_boundary_rounding_numeric_bounds_and_valid_values(self):
        for requested, expected, warning_count in ((-1, 0, 1), (2, 1.5, 1),
                                                    (0, 0, 0), (1.2, 1.2, 0),
                                                    (1.5, 1.5, 0)):
            with self.subTest(requested=requested):
                params, warnings = normalize_params({"boundary_rounding_mm": requested,
                                                     "upper_thickness_mm": 4})
                self.assertEqual(params["boundary_rounding_mm"], expected)
                self.assertEqual(len(warnings), warning_count)
                if warnings:
                    self.assertIn("边缘收口半径", warnings[0])
                    self.assertIn("已截断", warnings[0])
                self.assertEqual(normalize_params(params), (params, []))

    def test_boundary_rounding_is_limited_by_effective_upper_thickness(self):
        for wall, radius, expected, warning_count in ((2.4, 1.5, 1.2, 1),
                                                     (1.8, 1.2, .9, 1),
                                                     (1, 1.5, .9, 2),
                                                     (2.4, 1.2, 1.2, 0)):
            with self.subTest(wall=wall, radius=radius):
                values = {"upper_thickness_mm": wall, "boundary_rounding_mm": radius}
                params, warnings = normalize_params(values)
                self.assertEqual(params["boundary_rounding_mm"], expected)
                self.assertEqual(len(warnings), warning_count)
                if warnings:
                    self.assertIn("鞋面围条厚度的一半", warnings[-1])
                    self.assertIn("已截断", warnings[-1])
                self.assertEqual(normalize_params(params), (params, []))
                self.assertEqual(values["boundary_rounding_mm"], radius)

    def test_boundary_rounding_rejects_non_finite_and_wrong_types(self):
        for value in (math.nan, math.inf, -math.inf, True, False, "1.2", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_params({"boundary_rounding_mm": value})

    def test_valid_m3_settings_are_preserved(self):
        for process in ("SLS", "MJF", "FDM"):
            for orientation in ("auto", "as_designed"):
                values = {"print_process": process, "material": "TPU", "tpu_shore_a": 85,
                          "build_size_x_mm": 320, "build_size_y_mm": 220.5,
                          "build_size_z_mm": 200, "build_margin_mm": 3.5,
                          "build_orientation": orientation}
                params, warnings = normalize_params(values)
                self.assertEqual(warnings, [])
                for key, value in values.items():
                    self.assertEqual(params[key], value)

    def test_m3_bad_settings_are_rejected(self):
        for values in ({"print_process": "sls"}, {"print_process": "SLA"},
                       {"material": "PLA"}, {"tpu_shore_a": 90.0},
                       {"tpu_shore_a": "90A"}, {"build_orientation": "scale_to_fit"},
                       {"build_orientation": True}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                normalize_params(values)
        for key in ("tpu_shore_a", "build_size_x_mm", "build_size_y_mm",
                    "build_size_z_mm", "build_margin_mm"):
            for value in (math.nan, math.inf, -math.inf, True, "250", None):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    normalize_params({key: value})

    def test_m3_current_design_preserves_m2_shape_and_lattice(self):
        previous = json.loads((ROOT / "designs/history/v002.json").read_text(encoding="utf-8"))
        current = json.loads((ROOT / "designs/current.json").read_text(encoding="utf-8"))
        self.assertEqual(previous["revision"], 2)
        self.assertGreaterEqual(current["revision"], 3)
        # This initial manufacturing-only revision must not resize the user's shoe.
        if current["revision"] == 3:
            for key, value in previous.items():
                if key != "revision":
                    self.assertEqual(current[key], value)

    def test_saved_design_and_history_are_valid(self):
        current = json.loads((ROOT / "designs/current.json").read_text(encoding="utf-8"))
        baseline = json.loads((ROOT / "designs/history/v000.json").read_text(encoding="utf-8"))
        m1 = json.loads((ROOT / "designs/history/v001.json").read_text(encoding="utf-8"))
        m2 = json.loads((ROOT / "designs/history/v002.json").read_text(encoding="utf-8"))
        self.assertEqual(baseline["revision"], 0)
        self.assertEqual(m1["revision"], 1)
        self.assertEqual(m2["revision"], 2)
        self.assertGreater(current["revision"], m2["revision"])
        for values in (baseline, m1, m2, current):
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
                       {"outsole_flare_mm": None}, {"heel_sole_mm": False},
                       {"midsole_structure": "hollow"}, {"lattice_type": "unknown"},
                       {"resolution": "ultra"}, {"resolution": True}):
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
