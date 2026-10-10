"""Real M5 smoothing regression and unchanged v007 geometry; run in Blender."""
from pathlib import Path
import json
import sys
import unittest
from unittest.mock import patch

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.build import build_shoe, mirror_mesh
from engine.params import normalize_params
from engine.shoe.edge_finish import smooth_finished_surface
from engine.shoe.last import load_last, source_manifest
from engine.shoe.volume import sample_map
from engine.validate import coordinates, mesh_health, triangles
from tools.stress_cases import make_manifest


def clear_generated(baseline):
    for name in ("objects", "meshes", "materials"):
        collection = getattr(bpy.data, name)
        for item in set(collection) - baseline[name]:
            collection.remove(item, do_unlink=True)


def legacy_smooth(mesh, axes, top_map, height):
    """The original six-iteration operator, used only for coordinate equality."""
    spacing = float(axes[0][1] - axes[0][0])
    points = coordinates(mesh)
    roof = sample_map(top_map, points[:, :2], (axes[0][0], axes[1][0]), spacing)
    weights = np.maximum(np.clip((roof - points[:, 2] - 1) / 2, 0, 1),
                         np.clip((points[:, 2] - height + 4) / 2, 0, 1))
    bins = np.floor(weights * 32).astype(np.int32)
    obj = bpy.data.objects.new("legacy_smoothing_regression", mesh)
    bpy.context.scene.collection.objects.link(obj)
    active = bpy.context.view_layer.objects.active
    result = mesh
    try:
        group = obj.vertex_groups.new(name="protected_edge_finish")
        for level in range(1, 33):
            indices = np.flatnonzero(bins == level)
            if len(indices):
                group.add(indices.tolist(), level / 32, "REPLACE")
        modifier = obj.modifiers.new("continuous_edge_smoothing", "SMOOTH")
        modifier.factor = .5
        modifier.iterations = 6
        modifier.vertex_group = group.name
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.modifier_apply(modifier=modifier.name)
        result = obj.data
    finally:
        bpy.data.objects.remove(obj, do_unlink=True)
        bpy.context.view_layer.objects.active = active
    if result is not mesh:
        bpy.data.meshes.remove(mesh)
    return result


class SmoothingGeometryRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_sources = source_manifest()
        cls.source = load_last()

    @classmethod
    def tearDownClass(cls):
        bpy.data.meshes.remove(cls.source)
        if source_manifest() != cls.original_sources:
            raise AssertionError("回归测试修改了原始素材。")

    def setUp(self):
        self.baseline = {name: set(getattr(bpy.data, name)) for name in ("objects", "meshes", "materials")}

    def tearDown(self):
        clear_generated(self.baseline)

    def test_fixed_seed_octet_case_backtracks_and_both_feet_are_healthy(self):
        case = make_manifest(count=10, seed=20261009, include_boundaries=False)["cases"][9]
        self.assertEqual(case["case_id"], "random-0010-3b983a0d859c")
        built = build_shoe(case["effective"], last_mesh=self.source, manufacturing=False)
        smoothing = built.report["edge_finish"]["surface_smoothing"]
        self.assertGreater(len(smoothing["attempts"]), 1)
        self.assertLess(smoothing["factor"], .5)
        self.assertEqual(smoothing["attempts"][0]["health"]["status"], "fail")
        self.assertGreater(smoothing["attempts"][0]["health"]["self_intersection_pairs"], 0)
        self.assertEqual(smoothing["attempts"][-1]["health"]["status"], "pass")
        self.assertEqual(smoothing["attempts"][-1]["mirrored_health"]["status"], "pass")
        self.assertEqual(smoothing["protected_max_displacement_mm"], 0)
        self.assertEqual(built.report["mesh_health"]["status"], "pass")
        opposite = mirror_mesh(built.model.data)
        try:
            health = mesh_health(opposite)
            self.assertEqual(health["status"], "pass", health)
            self.assertEqual(health["self_intersection_pairs"], 0)
            self.assertAlmostEqual(health["signed_volume_mm3"], built.report["mesh_health"]["signed_volume_mm3"], delta=.1)
        finally:
            bpy.data.meshes.remove(opposite)

    def test_v007_default_uses_original_factor_and_identical_coordinates(self):
        params, _ = normalize_params({"foot_side": "right", "collar_style": "low", "collar_height_mm": 75,
            "size_eu": 42, "midsole_structure": "lattice", "lattice_type": "gyroid",
            "boundary_rounding_mm": 1.2, "resolution": "export"})
        comparisons = []

        def compare(mesh, axes, roof, height):
            legacy = legacy_smooth(mesh.copy(), axes, roof, height)
            try:
                result, info = smooth_finished_surface(mesh, axes, roof, height)
                np.testing.assert_array_equal(coordinates(result), coordinates(legacy))
                np.testing.assert_array_equal(triangles(result), triangles(legacy))
                self.assertEqual(info["factor"], .5)
                self.assertEqual(info["iterations"], 6)
                self.assertEqual(len(info["attempts"]), 1)
                comparisons.append(True)
                return result, info
            finally:
                bpy.data.meshes.remove(legacy)

        with patch("engine.build.smooth_finished_surface", side_effect=compare):
            built = build_shoe(params, last_mesh=self.source, manufacturing=False)
        self.assertEqual(comparisons, [True])
        self.assertEqual(built.report["mesh_health"]["status"], "pass")

    def test_minimum_gyroid_and_diamond_use_inward_extraction_and_pass(self):
        for kind in ("gyroid", "diamond"):
            with self.subTest(kind=kind):
                params, _ = normalize_params({"midsole_structure": "lattice", "lattice_type": kind,
                    "lattice_rod_mm": 1.5, "boundary_rounding_mm": 0, "resolution": "preview"})
                built = build_shoe(params, last_mesh=self.source, manufacturing=False)
                mesh = built.model.data
                self.assertEqual(built.report["voxel_mm"], .6)
                self.assertEqual(mesh["extraction_requested_level_field_mm"], 0)
                self.assertAlmostEqual(mesh["extraction_level_field_mm"], -6e-5, places=12)
                attempts = json.loads(mesh["extraction_attempts_json"])
                self.assertEqual(attempts[0]["level_field_mm"], 0)
                self.assertEqual(attempts[0]["status"], "fail")
                self.assertGreater(attempts[0]["mesh_health"]["non_manifold_edges"], 0)
                self.assertEqual(attempts[-1]["status"], "pass")
                self.assertEqual(built.report["mesh_health"]["status"], "pass")
                opposite = mirror_mesh(mesh)
                try:
                    self.assertEqual(mesh_health(opposite)["status"], "pass")
                finally:
                    bpy.data.meshes.remove(opposite)
                # Bound memory before generating the other topology.
                clear_generated(self.baseline)

    def test_unhealthy_original_is_not_accepted_as_zero_factor_fallback(self):
        bpy.ops.mesh.primitive_plane_add(size=20)
        obj = bpy.context.object
        axis = np.arange(-20, 20.5, .5)
        roof = np.full((len(axis), len(axis)), 30.0)
        with self.assertRaisesRegex(ValueError, "原收口网格.*完整健康检查"):
            smooth_finished_surface(obj.data, (axis, axis, axis), roof, 50)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    if not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful():
        raise SystemExit(1)
