import hashlib
import io
import itertools
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.params import normalize_params, read_schema
from tools.stress_cases import canonical_json, fingerprint, make_manifest, select_shard


class StressCaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = read_schema()
        cls.plan = make_manifest()
        cls.random_cases = [case for case in cls.plan["cases"] if case["kind"] == "random"]
        cls.boundary_cases = [case for case in cls.plan["cases"] if case["kind"] == "boundary"]

    def test_one_thousand_real_requests_plus_separate_boundaries(self):
        self.assertEqual(len(self.random_cases), 1000)
        self.assertGreater(len(self.boundary_cases), 0)
        self.assertEqual(self.plan["counts"], {
            "random": 1000, "boundary": len(self.boundary_cases),
            "total": len(self.plan["cases"]),
        })
        self.assertEqual(len({case["case_id"] for case in self.plan["cases"]}), len(self.plan["cases"]))
        self.assertEqual(len({case["fingerprint"] for case in self.random_cases}), 1000)

    def test_every_case_is_valid_stable_and_has_explicit_request(self):
        for case in self.plan["cases"]:
            with self.subTest(case_id=case["case_id"]):
                self.assertEqual(set(case["requested"]), set(self.schema["properties"]))
                self.assertEqual(normalize_params(case["requested"]), (case["effective"], case["clamps"]))
                self.assertEqual(case["clamps"], [])
                self.assertEqual(normalize_params(case["effective"]), (case["effective"], []))
                self.assertEqual(case["requested"]["revision"], 0)

    def test_all_numeric_schema_extrema_are_covered_by_actual_effective_cases(self):
        for key, spec in self.schema["properties"].items():
            if key in ("revision", "schema_version") or spec["type"] not in ("integer", "number"):
                continue
            with self.subTest(parameter=key):
                values = [case["effective"][key] for case in self.boundary_cases]
                self.assertEqual(min(values), spec["minimum"])
                self.assertEqual(max(values), spec["maximum"])

    def test_conditional_collar_width_and_radius_constraints_are_exercised(self):
        params = [case["effective"] for case in self.boundary_cases]
        for style, edges in (("low", (60, 85)), ("mid", (86, 115))):
            for edge in edges:
                self.assertTrue(any(p["collar_style"] == style and p["collar_height_mm"] == edge for p in params))
        self.assertTrue(any(p["foot_width"] == "wide" and p["toe_roundness"] == .05 for p in params))
        self.assertTrue(any(p["foot_width"] == "narrow" and p["toe_roundness"] == -.05 for p in params))
        self.assertTrue(any(p["upper_thickness_mm"] == 1.8 and p["boundary_rounding_mm"] == .9 for p in params))
        self.assertTrue(any(p["upper_thickness_mm"] == 4 and p["boundary_rounding_mm"] == 1.5 for p in params))

    def test_random_cases_cover_each_enum_without_permanent_field_correlation(self):
        for key, spec in self.schema["properties"].items():
            if "enum" in spec:
                with self.subTest(parameter=key):
                    self.assertEqual({case["effective"][key] for case in self.random_cases}, set(spec["enum"]))
        for keys in (("foot_side", "midsole_structure", "resolution"),
                     ("foot_width", "collar_style", "lattice_type")):
            expected = set(itertools.product(*(self.schema["properties"][key]["enum"] for key in keys)))
            observed = {tuple(case["effective"][key] for key in keys) for case in self.random_cases}
            self.assertEqual(observed, expected)
        for lattice_type in self.schema["properties"]["lattice_type"]["enum"]:
            self.assertTrue(any(case["effective"]["midsole_structure"] == "lattice"
                                and case["effective"]["lattice_type"] == lattice_type
                                for case in self.random_cases))

    def test_both_unrounded_and_rounded_random_geometries_are_kept(self):
        radii = [case["effective"]["boundary_rounding_mm"] for case in self.random_cases]
        self.assertEqual(radii.count(0), 250)
        self.assertTrue(all(case["effective"]["boundary_rounding_mm"] <=
                            case["effective"]["upper_thickness_mm"] / 2
                            for case in self.random_cases))
        self.assertGreater(sum(value > 0 for value in radii), 700)

    def test_each_lattice_topology_has_active_numeric_and_finishing_boundaries(self):
        for topology in self.schema["properties"]["lattice_type"]["enum"]:
            params = [case["effective"] for case in self.boundary_cases
                      if case["effective"]["midsole_structure"] == "lattice"
                      and case["effective"]["lattice_type"] == topology]
            for key, spec in self.schema["properties"].items():
                if key.startswith("lattice_") and spec["type"] in ("number", "integer"):
                    with self.subTest(topology=topology, parameter=key):
                        self.assertEqual(min(p[key] for p in params), spec["minimum"])
                        self.assertEqual(max(p[key] for p in params), spec["maximum"])
            self.assertTrue(any(p["boundary_rounding_mm"] == 0 for p in params))
            self.assertTrue(any(p["boundary_rounding_mm"] == 1.5 for p in params))
            self.assertTrue(any(p["upper_thickness_mm"] == 1.8
                                and p["boundary_rounding_mm"] == .9 for p in params))

    def test_historical_m1_joint_corners_are_present_and_legal(self):
        labeled = {case["label"]: case["effective"] for case in self.boundary_cases}
        expected = [
            {"size_eu": 35, "foot_width": "narrow", "collar_style": "low", "collar_height_mm": 60,
             "upper_thickness_mm": 1.8, "heel_sole_mm": 12, "forefoot_sole_mm": 8,
             "toe_roundness": -.05, "toe_height_scale": .85, "edge_radius_mm": .5, "outsole_flare_mm": 4},
            {"size_eu": 46, "foot_width": "wide", "collar_style": "mid", "collar_height_mm": 115,
             "upper_thickness_mm": 1.8, "heel_sole_mm": 40, "forefoot_sole_mm": 30,
             "toe_roundness": .05, "toe_height_scale": 1.15, "edge_radius_mm": 4, "outsole_flare_mm": 8},
            {"size_eu": 35, "foot_width": "wide", "collar_style": "mid", "collar_height_mm": 115,
             "upper_thickness_mm": 4, "heel_sole_mm": 40, "forefoot_sole_mm": 8,
             "toe_roundness": .05, "toe_height_scale": .85, "edge_radius_mm": 4, "outsole_flare_mm": 4},
            {"size_eu": 46, "foot_width": "narrow", "foot_side": "left", "collar_style": "low", "collar_height_mm": 85,
             "upper_thickness_mm": 1.8, "heel_sole_mm": 12, "forefoot_sole_mm": 30,
             "toe_roundness": -.05, "toe_height_scale": 1.15, "edge_radius_mm": 4, "outsole_flare_mm": 8},
        ]
        for index, patch in enumerate(expected):
            actual = labeled[f"corner-m1-{index + 1:02d}"]
            for key, value in patch.items():
                with self.subTest(corner=index + 1, parameter=key):
                    self.assertEqual(actual[key], value)
            self.assertEqual(actual["midsole_structure"], "solid")
            self.assertEqual(actual["boundary_rounding_mm"], 0)

    def test_combined_thin_gradient_finishing_corners_activate_every_topology_and_style(self):
        labeled = {case["label"]: case["effective"] for case in self.boundary_cases}
        for topology in self.schema["properties"]["lattice_type"]["enum"]:
            for style, size, height, resolution in (("low", 35, 60, "preview"), ("mid", 46, 115, "export")):
                expected = {"midsole_structure": "lattice", "lattice_type": topology, "collar_style": style,
                            "size_eu": size, "collar_height_mm": height, "resolution": resolution,
                            "heel_sole_mm": 12, "forefoot_sole_mm": 8, "upper_thickness_mm": 4,
                            "edge_radius_mm": 4, "outsole_flare_mm": 4, "lattice_rod_mm": 1.5,
                            "lattice_density_heel": .2, "lattice_density_arch": .6,
                            "lattice_density_forefoot": .2, "boundary_rounding_mm": 1.5}
                actual = labeled[f"corner-thin-gradient-{topology}-{style}"]
                self.assertEqual({key: actual[key] for key in expected}, expected)

    def test_both_extreme_sole_slopes_and_small_high_collar_are_covered_for_all_structures(self):
        labeled = {case["label"]: case["effective"] for case in self.boundary_cases}
        for name in ["solid", *self.schema["properties"]["lattice_type"]["enum"]]:
            for slope, heel, front in (("heel-low-front-high", 12, 30), ("heel-high-front-low", 40, 8)):
                actual = labeled[f"corner-slope-{name}-{slope}"]
                self.assertEqual((actual["heel_sole_mm"], actual["forefoot_sole_mm"]), (heel, front))
                self.assertEqual(actual["midsole_structure"], "solid" if name == "solid" else "lattice")
                if name != "solid":
                    self.assertEqual(actual["lattice_type"], name)
            actual = labeled[f"corner-high-collar-small-{name}"]
            expected = {"size_eu": 35, "collar_style": "mid", "collar_height_mm": 115,
                        "foot_width": "wide", "toe_roundness": .05, "toe_height_scale": .85,
                        "boundary_rounding_mm": 1.2}
            self.assertEqual({key: actual[key] for key in expected}, expected)
        self.assertEqual(len({case["fingerprint"] for case in self.boundary_cases}), len(self.boundary_cases))

    def test_small_build_volume_and_low_design_thickness_are_not_filtered(self):
        params = [case["effective"] for case in self.boundary_cases]
        for key in ("build_size_x_mm", "build_size_y_mm", "build_size_z_mm"):
            self.assertTrue(any(p[key] == 100 for p in params))
        self.assertTrue(any(p["lattice_rod_mm"] == 1.5 for p in params))
        self.assertTrue(any(p["upper_thickness_mm"] == 1.8 for p in params))
        # Manufacturing settings are sampled rather than forced to a pass profile.
        self.assertEqual({p["print_process"] for p in params}, {"SLS", "MJF", "FDM"})

    def test_fixed_seed_is_exactly_reproducible_and_random_prefix_does_not_depend_on_count(self):
        first = make_manifest(count=19, seed=42)
        self.assertEqual(first, make_manifest(count=19, seed=42))
        longer = make_manifest(count=31, seed=42, include_boundaries=False)
        self.assertEqual(first["cases"][:19], longer["cases"][:19])
        self.assertEqual(make_manifest(count=19, seed=42, include_boundaries=False)["cases"], first["cases"][:19])
        self.assertNotEqual(first["cases"][:19], make_manifest(count=19, seed=43)["cases"][:19])

    def test_fingerprints_bind_schema_plan_and_requested_effective_data(self):
        self.assertEqual(self.plan["schema_sha256"], fingerprint(self.schema))
        content = {key: value for key, value in self.plan.items() if key != "plan_sha256"}
        self.assertEqual(self.plan["plan_sha256"], fingerprint(content))
        for case in self.plan["cases"]:
            self.assertEqual(case["fingerprint"], fingerprint({
                key: case[key] for key in ("requested", "effective", "clamps")
            }))
            self.assertIn(case["fingerprint"][:12], case["case_id"])
        renamed_schema = json.loads(json.dumps(self.schema))
        renamed_schema["title"] += " changed"
        with patch("tools.stress_cases.read_schema", return_value=renamed_schema):
            self.assertNotEqual(make_manifest(count=3)["schema_sha256"], self.plan["schema_sha256"])

    def test_plan_is_json_compatible_and_hash_is_independent_of_key_insertion_order(self):
        self.assertEqual(json.loads(json.dumps(self.plan, allow_nan=False)), self.plan)
        self.assertEqual(fingerprint({"b": 2, "a": 1}), fingerprint({"a": 1, "b": 2}))
        self.assertEqual(fingerprint({"b": 2, "a": 1}),
                         hashlib.sha256(canonical_json({"b": 2, "a": 1}).encode()).hexdigest())

    def test_shards_are_disjoint_and_merge_all_cases_in_original_order(self):
        for shard_count in (1, 3, 7, len(self.plan["cases"]) + 1):
            shards = [select_shard(self.plan, index, shard_count) for index in range(shard_count)]
            flattened = [case for shard in shards for case in shard]
            self.assertEqual(len(flattened), len(self.plan["cases"]))
            self.assertEqual({case["case_id"] for case in flattened},
                             {case["case_id"] for case in self.plan["cases"]})
            merged = sorted(flattened, key=lambda case: (case["kind"] != "random", case["index"]))
            self.assertEqual(merged, self.plan["cases"])

    def test_plan_only_reads_schema_and_never_writes_or_reads_design_or_assets(self):
        original_open = io.open
        schema_path = (ROOT / "schema/shoe_params.schema.json").resolve()

        def guarded_open(file, mode="r", *args, **kwargs):
            self.assertFalse(any(flag in mode for flag in "wax+"))
            self.assertEqual(Path(file).resolve(), schema_path)
            return original_open(file, mode, *args, **kwargs)

        with patch("io.open", side_effect=guarded_open):
            make_manifest(count=3)

    def test_invalid_plan_counts_seeds_and_shards_are_rejected(self):
        for count in (True, 0, -1, 1.5, "1000"):
            with self.subTest(count=count), self.assertRaises(ValueError):
                make_manifest(count=count)
        for seed in (True, 1.5, "42", None):
            with self.subTest(seed=seed), self.assertRaises(ValueError):
                make_manifest(count=1, seed=seed)
        with self.assertRaises(ValueError):
            make_manifest(count=1, include_boundaries="yes")
        for index, count in ((-1, 2), (2, 2), (0, 0), (True, 2), (0, True), (0, 1.5)):
            with self.subTest(shard=(index, count)), self.assertRaises(ValueError):
                select_shard(self.plan, index, count)


if __name__ == "__main__":
    unittest.main()
