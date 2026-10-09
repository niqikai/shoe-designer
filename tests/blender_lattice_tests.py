"""M2 field and real-shoe checks; --integration adds three whole-shoe builds."""
import argparse
import json
from pathlib import Path
import sys
import unittest

import bpy
import numpy as np
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.build import build_shoe, sampling_spacing
from engine.params import load_params, normalize_params
from engine.shoe.last import _import_mesh, source_manifest
from engine.shoe.lattice import (KINDS, clean_lattice_field, density_profile, inspect_voids,
                                 lattice_spec, periodic_metric, threshold_for_density)
from engine.validate import _only_shared_contact, coordinates, mesh_health, surface_tree


class LatticeFieldTests(unittest.TestCase):
    def test_density_calibration_on_an_independent_unit_grid(self):
        axes = [(np.arange(43) + phase) / 43 for phase in (.23, .41, .67)]
        for kind in KINDS:
            metric = periodic_metric(kind, axes[0][:, None, None], axes[1][None, :, None], axes[2][None, None, :], 1)
            previous = np.zeros(metric.shape, dtype=bool)
            for density in (.2, .32, .45, .6):
                with self.subTest(kind=kind, density=density):
                    solid = metric <= threshold_for_density(kind, density)
                    self.assertAlmostEqual(solid.mean(), density, delta=.02)
                    self.assertFalse(np.any(previous & ~solid))
                    previous = solid

    def test_periodic_phase_matches_at_every_translated_cell(self):
        rng = np.random.default_rng(42)
        points = rng.uniform(-2, 2, (200, 3))
        for kind in KINDS:
            baseline = periodic_metric(kind, *points.T, 1)
            for axis in range(3):
                shifted = points.copy()
                shifted[:, axis] += 1
                np.testing.assert_allclose(periodic_metric(kind, *shifted.T, 1), baseline, atol=5e-6)

    def test_density_zones_blend_continuously_and_stay_in_bounds(self):
        params, _ = normalize_params({"lattice_density_heel": .2, "lattice_density_arch": .6, "lattice_density_forefoot": .3})
        y = np.linspace(0, 300, 3001)
        rho = density_profile(y, 300, params)
        self.assertGreaterEqual(rho.min(), .2 - 1e-12)
        self.assertLessEqual(rho.max(), .6 + 1e-12)
        np.testing.assert_allclose(density_profile(np.array([30, 135, 240]), 300, params), [.2, .6, .3])
        for boundary in (78, 90, 102, 168, 180, 192):
            value = density_profile(np.array([boundary - 1e-6, boundary + 1e-6]), 300, params)
            self.assertLess(abs(value[0] - value[1]), 1e-6)

    def test_rod_scale_changes_cell_size_without_redefining_density(self):
        for kind in KINDS:
            small, _ = normalize_params({"lattice_type": kind, "lattice_rod_mm": 1.5})
            large = {**small, "lattice_rod_mm": 3}
            a, b = lattice_spec(small), lattice_spec(large)
            self.assertAlmostEqual(b["period_mm"], 2 * a["period_mm"])
            self.assertEqual(a["unit_cell_target_density"], b["unit_cell_target_density"])
            self.assertAlmostEqual(min(a["interior_design_thickness_mm"].values()), 1.5)

    def test_preview_export_and_small_feature_sampling_limits(self):
        params, _ = normalize_params({"midsole_structure": "lattice"})
        self.assertEqual(sampling_spacing(params)[0], .8)
        self.assertEqual(sampling_spacing({**params, "resolution": "export"})[0], .5)
        params["lattice_rod_mm"] = 1.5
        self.assertAlmostEqual(sampling_spacing(params)[0], .6)
        self.assertTrue(sampling_spacing(params, 1)[1])

    def test_closed_sampled_voids_are_filled_and_reported(self):
        axes = np.arange(-10, 11, dtype=np.float32)
        x, y, z = axes[:, None, None], axes[None, :, None], axes[None, None, :]
        radius = np.sqrt(x*x + y*y + z*z)
        field = np.maximum(radius - 7, 2 - radius)
        before = inspect_voids(field, radius < 6, 1)
        self.assertGreater(before["trapped_core_void_voxels"], 0)
        cleanup = clean_lattice_field(field, 1)
        self.assertGreater(cleanup["filled_closed_void_voxels"], 0)
        self.assertEqual(inspect_voids(field, radius < 6, 1)["trapped_core_void_voxels"], 0)


class AdjacentIntersectionTests(unittest.TestCase):
    def test_shared_vertex_contact_is_allowed_but_folded_overlap_is_not(self):
        basis = np.eye(3)
        original = np.vstack((np.zeros(3), basis[:2]))
        first = np.arange(3)
        second = np.r_[0, np.arange(3, 5)]
        touching = np.vstack((original, -basis[:2]))
        overlapping = np.vstack((original, basis[:2] * .5))
        self.assertTrue(_only_shared_contact(touching, first, second))
        self.assertFalse(_only_shared_contact(overlapping, first, second))
        tilted = np.vstack((original, basis[0] * .5, (basis[1] + basis[2]) * .5))
        self.assertFalse(_only_shared_contact(tilted, first, second))
        tilted[3] *= -1
        self.assertTrue(_only_shared_contact(tilted, first, second))

    def test_shared_edge_preserves_fold_detection(self):
        basis = np.eye(3)
        points = np.vstack((np.zeros(3), basis))
        first, second = np.arange(3), np.r_[0, 1, 3]
        self.assertTrue(_only_shared_contact(points, first, second))
        points[3] = basis[1] * .5
        self.assertFalse(_only_shared_contact(points, first, second))
        points[3] *= -1
        self.assertTrue(_only_shared_contact(points, first, second))


class LatticeShoeTests(unittest.TestCase):
    def test_all_three_topologies_are_connected_and_preserve_fit_surfaces(self):
        before = source_manifest()
        params, _ = load_params(ROOT / "designs/current.json")
        # This is the historical M2 raw-field regression. The current Gyroid
        # boundary finish is independently checked by the M3 integration suite.
        params["boundary_rounding_mm"] = 0
        for kind in KINDS:
            with self.subTest(kind=kind):
                built = build_shoe({**params, "lattice_type": kind})
                try:
                    report = built.report
                    self.assertEqual(report["mesh_health"]["status"], "pass")
                    self.assertEqual(report["mesh_health"]["connected_components"], 1)
                    self.assertLess(report["mesh_health"]["signed_volume_mm3"], report["lattice"]["solid_reference_sample_volume_mm3"] * .95)
                    self.assertGreater(report["lattice"]["sampled_material_reduction_fraction"], .05)
                    self.assertEqual(report["lattice"]["void_connectivity"]["trapped_core_void_voxels"], 0)
                    tree, last_tree = surface_tree(built.model.data), surface_tree(built.last)
                    for anchor in report["sole"]["anchors"].values():
                        x, y = anchor["x_mm"], anchor["y_mm"]
                        floor, _, _, _ = tree.ray_cast(Vector((x, y, anchor["bottom_z_mm"] + 2)), Vector((0, 0, -1)), 50)
                        self.assertIsNotNone(floor)
                        self.assertAlmostEqual(floor.z, anchor["bottom_z_mm"], delta=1.25)
                    heel = report["sole"]["anchors"]["heel"]
                    hit, _, _, _ = tree.ray_cast(Vector((heel["x_mm"], heel["y_mm"], params["collar_height_mm"] + 10)), Vector((0, 0, -1)), 150)
                    self.assertLess(hit.z, params["collar_height_mm"] - 20)
                    for fraction in (.3, .4, .5):
                        y = built.features["dimensions_mm"]["length"] * fraction
                        for x in (-15, 0, 15):
                            reference, _, _, _ = last_tree.ray_cast(Vector((x, y, -200)), Vector((0, 0, 1)), 500)
                            floor, _, _, _ = tree.ray_cast(Vector((x, y, reference.z + 2)), Vector((0, 0, -1)), 50)
                            self.assertAlmostEqual(floor.z, reference.z, delta=1.25)
                finally:
                    mesh = built.model.data
                    bpy.data.objects.remove(built.model, do_unlink=True)
                    bpy.data.meshes.remove(mesh)

                    bpy.data.meshes.remove(built.last)
        self.assertEqual(source_manifest(), before)

    def test_default_stl_and_glb_roundtrip_dimensions(self):
        output = ROOT / "out" / getattr(self, "output_name", "m2")
        report = json.loads((output / "report.json").read_text())
        self.assertEqual(report["status"], "pass_pending_visual_confirmation")
        mesh = _import_mesh(output / "shoe_right.stl")
        try:
            health = mesh_health(mesh)
            self.assertEqual(health["status"], "pass")
            expected = report["mesh_health"]["bounds_mm"]
            np.testing.assert_allclose(health["bounds_mm"]["min"], expected["min"], atol=1e-4)
            np.testing.assert_allclose(health["bounds_mm"]["max"], expected["max"], atol=1e-4)
        finally:
            bpy.data.meshes.remove(mesh)
        existing = set(bpy.data.objects)
        bpy.ops.import_scene.gltf(filepath=str(output / "shoe_right.glb"))
        imported = set(bpy.data.objects) - existing
        try:
            points = np.concatenate([np.asarray([obj.matrix_world @ v.co for v in obj.data.vertices]) for obj in imported if obj.type == "MESH"]) * 1000
            np.testing.assert_allclose(points.min(axis=0), expected["min"], atol=.002)
            np.testing.assert_allclose(points.max(axis=0), expected["max"], atol=.002)
        finally:
            for obj in imported:
                mesh = obj.data if obj.type == "MESH" else None
                bpy.data.objects.remove(obj, do_unlink=True)
                if mesh and mesh.users == 0:
                    bpy.data.meshes.remove(mesh)


class LatticeFineExportTests(unittest.TestCase):
    output_name = "m2-export"
    test_stl_and_glb_roundtrip_dimensions = LatticeShoeTests.test_default_stl_and_glb_roundtrip_dimensions

    def test_fine_sampling_preserves_design_envelope_and_volume(self):
        preview = json.loads((ROOT / "out/m2/report.json").read_text())
        fine = json.loads((ROOT / "out/m2-export/report.json").read_text())
        self.assertEqual(fine["voxel_mm"], .5)
        self.assertEqual(fine["effective_params"], preview["effective_params"])
        self.assertTrue(fine["original_sources_unchanged"])
        self.assertEqual(fine["mesh_health"]["connected_components"], 1)
        self.assertEqual(fine["lattice"]["void_connectivity"]["trapped_core_void_voxels"], 0)
        for side in ("min", "max"):
            np.testing.assert_allclose(fine["mesh_health"]["bounds_mm"][side], preview["mesh_health"]["bounds_mm"][side], atol=.5)
        ratio = fine["mesh_health"]["signed_volume_mm3"] / preview["mesh_health"]["signed_volume_mm3"]
        self.assertAlmostEqual(ratio, 1, delta=.02)


class LatticeBoundaryTests(unittest.TestCase):
    def test_thin_small_truss_and_large_gradient_sheet(self):
        params, _ = load_params(ROOT / "designs/current.json")
        params["boundary_rounding_mm"] = 0  # Historical M2 extremes, before M3 finishing.
        cases = [
            {"size_eu": 35, "foot_width": "narrow", "collar_height_mm": 60,
             "heel_sole_mm": 12, "forefoot_sole_mm": 8, "upper_thickness_mm": 1.8,
             "lattice_type": "octet", "lattice_rod_mm": 1.5, "lattice_density_heel": .2,
             "lattice_density_arch": .6, "lattice_density_forefoot": .2, "toe_roundness": -.15},
            {"size_eu": 46, "foot_width": "wide", "foot_side": "left", "collar_style": "mid",
             "collar_height_mm": 115, "heel_sole_mm": 40, "forefoot_sole_mm": 30,
             "lattice_type": "diamond", "lattice_rod_mm": 3, "lattice_density_heel": .6,
             "lattice_density_arch": .2, "lattice_density_forefoot": .6, "toe_roundness": .15},
        ]
        before = source_manifest()
        for case in cases:
            with self.subTest(case=case):
                built = build_shoe({**params, **case})
                try:
                    self.assertEqual(built.report["mesh_health"]["status"], "pass")
                    self.assertEqual(built.report["mesh_health"]["connected_components"], 1)
                    self.assertEqual(built.report["lattice"]["void_connectivity"]["trapped_core_void_voxels"], 0)
                    self.assertLess(built.report["lattice"]["lattice_sample_volume_mm3"], built.report["lattice"]["solid_reference_sample_volume_mm3"])
                finally:
                    mesh = built.model.data
                    bpy.data.objects.remove(built.model, do_unlink=True)
                    bpy.data.meshes.remove(mesh)
                    bpy.data.meshes.remove(built.last)
        self.assertEqual(source_manifest(), before)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--integration", action="store_true")
    parser.add_argument("--stress", action="store_true")
    parser.add_argument("--fine-export", action="store_true")
    options = parser.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else [])
    suite = unittest.TestSuite()
    for case in (LatticeFieldTests, AdjacentIntersectionTests):
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(case))
    if options.integration:
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(LatticeShoeTests))
    if options.stress:
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(LatticeBoundaryTests))
    if options.fine_export:
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(LatticeFineExportTests))
    if not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful():
        raise RuntimeError("M2 晶格测试失败")
