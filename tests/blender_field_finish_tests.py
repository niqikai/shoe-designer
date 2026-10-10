"""Field connectivity backtracking and opt-in actual M5 Octet regressions."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys
import unittest

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from engine.build import build_shoe
from engine.shoe.edge_finish import round_material_edges
from engine.shoe.last import load_last, source_manifest
from engine.shoe.lattice import clean_lattice_field
from engine.shoe.volume import geometry_dependencies, mesh_from_field
from engine.validate import mesh_health
from tools.stress_cases import make_manifest


class FieldFinishTests(unittest.TestCase):
    def fixture(self, neck=False, plantar=False):
        axis = np.arange(-9, 9.01, .5)
        x, y, z = axis[:, None, None], axis[None, :, None], axis[None, None, :]
        if neck:
            left = np.sqrt((x + 4) ** 2 + y*y + z*z) - 3
            right = np.sqrt((x - 4) ** 2 + y*y + z*z) - 3
            bridge = np.maximum(abs(x) - 4, np.sqrt(y*y + z*z) - .6)
            field = np.minimum(np.minimum(left, right), bridge).astype(np.float32)
        else:
            vertical = abs(z + 1.5) - 1.5 if plantar else abs(z) - 2
            field = np.maximum(np.maximum(abs(x) - 5, abs(y) - 4), vertical).astype(np.float32)
        top_map = np.full((len(axis), len(axis)), 0.0 if plantar else 20.0, dtype=np.float32)
        return field, (axis, axis, axis), top_map

    def test_neck_cut_by_full_finish_uses_the_unchanged_one_percent_gate(self):
        field, axes, top_map = self.fixture(neck=True)
        original = field.copy()
        full = field.copy()
        round_material_edges(full, axes, top_map, 50, 1.5)
        with self.assertRaisesRegex(ValueError, "1%"):
            clean_lattice_field(full, .5)
        info = round_material_edges(field, axes, top_map, 50, 1.5, lattice_cleanup=clean_lattice_field)
        self.assertLess(info["applied_strength"], 1)
        self.assertEqual(info["requested_radius_mm"], 1.5)
        self.assertEqual(info["strength_attempts"][0]["status"], "fail")
        self.assertEqual(info["strength_attempts"][-1]["status"], "pass")
        self.assertIn("连接", info["strength_message"])
        geometry_dependencies()
        from scipy.ndimage import label, generate_binary_structure
        self.assertEqual(label(field < 0, generate_binary_structure(3, 1))[1], 1)
        self.assertEqual(info["added_material_mm3"], int(np.count_nonzero((field < 0) & (original >= 0))) * .5**3)
        self.assertEqual(info["removed_material_mm3"], int(np.count_nonzero((field >= 0) & (original < 0))) * .5**3)
        mesh = mesh_from_field(field, tuple(a[0] for a in axes), .5, "connected_finish_neck")
        try:
            self.assertEqual(mesh_health(mesh)["status"], "pass")
        finally:
            bpy.data.meshes.remove(mesh)

    def test_normal_first_candidate_preserves_the_existing_full_finish_exactly(self):
        field, axes, top_map = self.fixture()
        target = field.copy()
        round_material_edges(target, axes, top_map, 50, 1.2)
        info = round_material_edges(field, axes, top_map, 50, 1.2, lattice_cleanup=clean_lattice_field)
        self.assertEqual(info["applied_strength"], 1)
        self.assertEqual(len(info["strength_attempts"]), 1)
        np.testing.assert_array_equal(field, target)

    def test_plantar_field_protection_survives_cleanup(self):
        field, axes, top_map = self.fixture(plantar=True)
        original = field.copy()
        info = round_material_edges(field, axes, top_map, 50, 1.2, lattice_cleanup=clean_lattice_field)
        protected = axes[2] >= -1
        np.testing.assert_array_equal(field[:, :, protected], original[:, :, protected])
        self.assertTrue(info["protected_field_unchanged"])
        self.assertGreater(info["protected_field_sample_count"], 0)

    def test_rejecting_all_candidates_restores_the_input_field(self):
        field, axes, top_map = self.fixture()
        original = field.copy()
        calls = []

        def reject(candidate, spacing):
            calls.append(candidate.copy())
            candidate[:] = 100
            raise ValueError("已拒绝候选")

        with self.assertRaisesRegex(ValueError, "原材料场"):
            round_material_edges(field, axes, top_map, 50, 1.2, lattice_cleanup=reject)
        self.assertEqual(len(calls), 4)
        np.testing.assert_array_equal(calls[-1], original)
        np.testing.assert_array_equal(field, original)

    def test_zero_strength_fallback_reports_no_hidden_material_or_protection_change(self):
        field, axes, top_map = self.fixture()
        original = field.copy()
        calls = []

        def accept_original_only(candidate, spacing):
            calls.append(1)
            if not np.array_equal(candidate, original):
                raise ValueError("连接不允许修改")
            return clean_lattice_field(candidate, spacing)

        info = round_material_edges(field, axes, top_map, 50, 1.2, lattice_cleanup=accept_original_only)
        self.assertEqual(len(calls), 4)
        self.assertEqual(info["applied_strength"], 0)
        self.assertFalse(info["enabled"])
        self.assertEqual(info["requested_radius_mm"], 1.2)
        self.assertEqual((info["added_material_mm3"], info["removed_material_mm3"]), (0, 0))
        self.assertIn("未施加收口", info["strength_message"])
        self.assertIn("未施加收口", info["message"])
        np.testing.assert_array_equal(field, original)


class ActualOctetReplayTests(unittest.TestCase):
    def test_four_octet_post_finish_connectivity_failures_really_generate_healthy_meshes(self):
        source_before = source_manifest()
        module_spec = importlib.util.spec_from_file_location("m5_worker_helpers", ROOT / "tests/blender_stress_worker.py")
        worker = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(worker)
        designs_before = worker.design_snapshot()
        source = load_last()
        baseline = worker.data_snapshot()
        cases = [case for case in make_manifest()["cases"]
                 if case["kind"] == "boundary" and case["index"] in (84, 105, 106, 107)]
        self.assertEqual(len(cases), 4)
        results = []
        output = ROOT / "out/m5/diagnostics/field_finish_replays.json"
        for case in cases:
            built = None
            try:
                built = build_shoe(case["effective"], last_mesh=source, manufacturing=False)
                self.assertEqual(built.report["effective_params"], case["effective"])
                self.assertEqual(built.report["mesh_health"]["status"], "pass")
                self.assertEqual(built.report["deformed_last_health"]["status"], "pass")
                finish = built.report["edge_finish"]
                self.assertLess(finish["applied_strength"], 1)
                self.assertTrue(finish["protected_field_unchanged"])
                self.assertTrue(any("收口" in message for message in built.report["clamp_messages"]))
                results.append({"case_id": case["case_id"], "status": "pass", "edge_finish": finish,
                                "mesh_health": built.report["mesh_health"],
                                "deformed_last_health": built.report["deformed_last_health"]})
            except Exception as error:
                results.append({"case_id": case["case_id"], "status": "fail", "error": str(error)})
                raise
            finally:
                built = None
                worker.clean_generated_data(baseline)
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(json.dumps(results, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
        self.assertEqual(source_manifest(), source_before)
        self.assertEqual(worker.design_snapshot(), designs_before)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay", action="store_true")
    options = parser.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else [])
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(FieldFinishTests)
    if options.replay:
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(ActualOctetReplayTests))
    if not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful():
        raise SystemExit(1)
