"""M1 last-deformation integration tests; run inside Blender."""
from pathlib import Path
import sys
import unittest

import bpy
import numpy as np
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.shoe.last import deform_last, load_last, source_manifest
from engine.validate import coordinates, mesh_health, surface_tree


class LastDeformationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.before = source_manifest()
        cls.mesh = load_last()
        cls.points = coordinates(cls.mesh)

    @classmethod
    def tearDownClass(cls):
        np.testing.assert_array_equal(coordinates(cls.mesh), cls.points)
        if source_manifest() != cls.before:
            raise AssertionError("Source files changed during tests")

    def test_defaults_preserve_every_source_coordinate(self):
        mesh, metadata = deform_last(self.mesh, {})
        self.assertIsNot(mesh, self.mesh)
        np.testing.assert_array_equal(coordinates(mesh), self.points)
        self.assertEqual(metadata["global_size_scale_xyz"], [1.0, 1.0, 1.0])
        self.assertEqual(metadata["warnings"], [])
        self.assertEqual(mesh_health(mesh)["status"], "pass")
        bpy.data.meshes.remove(mesh)

    def test_size_mapping_changes_length_by_paris_point(self):
        base_length = np.ptp(self.points[:, 1])
        for size in (35, 38.5, 42, 46):
            with self.subTest(size=size):
                mesh, metadata = deform_last(self.mesh, {"size_eu": size})
                points = coordinates(mesh)
                expected = base_length + (size - 42) * 20 / 3
                self.assertAlmostEqual(np.ptp(points[:, 1]), expected, delta=5e-5)
                self.assertAlmostEqual(metadata["target_length_mm"], expected, places=8)
                self.assertAlmostEqual(points[:, 1].min(), 0, delta=1e-5)
                self.assertAlmostEqual(points[:, 2].min(), 0, delta=1e-5)
                self.assertGreater(metadata["global_size_scale_xyz"][0], 0)
                bpy.data.meshes.remove(mesh)

    def test_extremes_remain_finite_closed_and_outward(self):
        for size in (35, 46):
            for width in ("narrow", "wide"):
                for toe_roundness, toe_height in ((-0.15, 0.85), (0.15, 1.15)):
                    with self.subTest(size=size, width=width, toe_roundness=toe_roundness):
                        mesh, metadata = deform_last(self.mesh, {
                            "size_eu": size, "foot_width": width,
                            "toe_roundness": toe_roundness, "toe_height_scale": toe_height,
                        })
                        self.assertTrue(np.isfinite(coordinates(mesh)).all())
                        report = mesh_health(mesh)
                        self.assertEqual(report["status"], "pass", report)
                        self.assertGreater(report["signed_volume_mm3"], 0)
                        self.assertGreaterEqual(metadata["local_width_factor_range"][0], 0.85)
                        self.assertLessEqual(metadata["local_width_factor_range"][1], 1.15)
                        bpy.data.meshes.remove(mesh)

    def test_combined_local_controls_cannot_exceed_fifteen_percent(self):
        for width, roundness in (("wide", 0.15), ("narrow", -0.15)):
            mesh, metadata = deform_last(self.mesh, {
                "size_eu": 35, "foot_width": width,
                "toe_roundness": roundness, "toe_height_scale": 1.15,
            })
            points = coordinates(mesh)
            size_scale = np.array(metadata["global_size_scale_xyz"])
            graded = self.points * size_scale
            nonzero_x = np.abs(graded[:, 0]) > 1e-4
            nonzero_z = np.abs(graded[:, 2]) > 1e-4
            observed_width = points[nonzero_x, 0] / graded[nonzero_x, 0]
            observed_height = points[nonzero_z, 2] / graded[nonzero_z, 2]
            self.assertGreaterEqual(observed_width.min(), 0.85 - 1e-6)
            self.assertLessEqual(observed_width.max(), 1.15 + 1e-6)
            self.assertGreaterEqual(observed_height.min(), 0.85 - 1e-6)
            self.assertLessEqual(observed_height.max(), 1.15 + 1e-6)
            self.assertGreater(metadata["combined_width_clamped_vertices"], 0)
            self.assertTrue(metadata["warnings"])
            bpy.data.meshes.remove(mesh)

    def test_toe_controls_leave_posterior_seventy_percent_unchanged(self):
        mesh, _ = deform_last(self.mesh, {"toe_roundness": 0.15, "toe_height_scale": 1.15})
        cutoff = self.points[:, 1].min() + 0.7 * np.ptp(self.points[:, 1])
        posterior = self.points[:, 1] <= cutoff
        np.testing.assert_array_equal(coordinates(mesh)[posterior], self.points[posterior])
        bpy.data.meshes.remove(mesh)

    def test_toe_height_preserves_plantar_surface_at_sampled_xy(self):
        original_tree = surface_tree(self.mesh)
        for toe_height in (0.85, 1.15):
            mesh, metadata = deform_last(self.mesh, {"toe_height_scale": toe_height})
            output_tree = surface_tree(mesh)
            checked = 0
            upper_moved = 0
            for y in np.linspace(200, 270, 15):
                for x in np.linspace(-35, 35, 15):
                    bottom, normal, _, _ = original_tree.ray_cast(Vector((x, y, -100)), Vector((0, 0, 1)), 300)
                    if bottom is None or normal.z > -0.35:
                        continue
                    output_bottom, _, _, _ = output_tree.ray_cast(Vector((x, y, -100)), Vector((0, 0, 1)), 300)
                    self.assertIsNotNone(output_bottom)
                    self.assertAlmostEqual(output_bottom.z, bottom.z, delta=2e-4)
                    checked += 1
                    upper, _, _, _ = original_tree.ray_cast(Vector((x, y, 200)), Vector((0, 0, -1)), 300)
                    output_upper, _, _, _ = output_tree.ray_cast(Vector((x, y, 200)), Vector((0, 0, -1)), 300)
                    if upper and output_upper and abs(output_upper.z - upper.z) > 0.1:
                        self.assertEqual(output_upper.z > upper.z, toe_height > 1)
                        upper_moved += 1
            self.assertGreater(checked, 100)
            self.assertGreater(upper_moved, 100)
            self.assertGreater(metadata["plantar_reference_queries"], 0)
            bpy.data.meshes.remove(mesh)

    def test_direct_call_rejects_unsafe_or_unbounded_values(self):
        for params in ({"size_eu": 34}, {"size_eu": float("nan")},
                       {"size_eu": True}, {"toe_roundness": 0.2},
                       {"toe_height_scale": 2}, {"foot_width": "extra_wide"}):
            with self.subTest(params=params), self.assertRaises(ValueError):
                deform_last(self.mesh, params)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise RuntimeError("M1 last deformation tests failed")
