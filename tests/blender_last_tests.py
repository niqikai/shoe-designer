"""Geometry integration tests; run inside Blender after inspect_last.sh."""
import json
from pathlib import Path
import random
import sys
import unittest

import bmesh
import bpy
import numpy as np
from mathutils import Matrix

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.params import normalize_params
from engine.shoe.last import (ASSET_DIR, _import_mesh, cut_last, load_last, repair_copy, source_manifest)
from engine.validate import coordinates, mesh_health


class LastIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.before = source_manifest()
        cls.mesh = load_last()

    @classmethod
    def tearDownClass(cls):
        if source_manifest() != cls.before:
            raise AssertionError("Source files changed during tests")

    def test_normalized_geometry_preserves_size_and_mm_frame(self):
        original = load_last(normalized=False)
        source = coordinates(original)
        normalized = coordinates(self.mesh)
        self.assertAlmostEqual(np.ptp(source[:, 0]), np.ptp(normalized[:, 1]), delta=1e-4)
        self.assertAlmostEqual(np.ptp(source[:, 1]), np.ptp(normalized[:, 0]), delta=1e-4)
        self.assertAlmostEqual(normalized[:, 1].min(), 0, delta=1e-4)
        self.assertAlmostEqual(normalized[:, 2].min(), 0, delta=1e-4)
        original_health, normalized_health = mesh_health(original), mesh_health(self.mesh)
        self.assertEqual(normalized_health["status"], "pass")
        self.assertAlmostEqual(original_health["signed_volume_mm3"], normalized_health["signed_volume_mm3"], delta=0.2)
        bpy.data.meshes.remove(original)

    def test_cuts_at_limits_and_random_heights_remain_closed(self):
        rng = random.Random(20261007)
        cases = [("low", h) for h in (60, 75, 85)] + [("mid", h) for h in (86, 100, 115)]
        cases += [(style, rng.uniform(*bounds)) for style, bounds in (("low", (60, 85)), ("mid", (86, 115))) for _ in range(5)]
        full_volume = mesh_health(self.mesh)["signed_volume_mm3"]
        for style, height in cases:
            with self.subTest(style=style, height=height):
                params, _ = normalize_params({"collar_style": style, "collar_height_mm": height})
                mesh = cut_last(self.mesh, params["collar_height_mm"])
                report = mesh_health(mesh)
                self.assertEqual(report["status"], "pass", report)
                self.assertLess(report["signed_volume_mm3"], full_volume)
                self.assertAlmostEqual(coordinates(mesh)[:, 2].max(), height, delta=2e-4)
                self.assertGreater(mesh["cut_loops_capped"], 0)
                bpy.data.meshes.remove(mesh)

    def test_obj_roundtrip_preserves_mm_axes_and_closedness(self):
        mesh = _import_mesh(ASSET_DIR / "last_normalized.obj")
        self.assertEqual(mesh_health(mesh)["status"], "pass")
        np.testing.assert_allclose(coordinates(mesh).min(axis=0), coordinates(self.mesh).min(axis=0), atol=2e-4)
        np.testing.assert_allclose(coordinates(mesh).max(axis=0), coordinates(self.mesh).max(axis=0), atol=2e-4)
        bpy.data.meshes.remove(mesh)

    def test_saved_blend_has_mm_metadata_and_identity_object_transforms(self):
        with bpy.data.libraries.load(str(ASSET_DIR / "last_normalized.blend"), link=False) as (source, target):
            target.scenes = list(source.scenes)
        scene = target.scenes[0]
        self.assertEqual(scene.unit_settings.system, "METRIC")
        self.assertEqual(scene.unit_settings.length_unit, "MILLIMETERS")
        self.assertAlmostEqual(scene.unit_settings.scale_length, 0.001, places=8)
        for obj in scene.objects:
            if obj.type == "MESH":
                np.testing.assert_allclose(np.array(obj.matrix_world), np.eye(4), atol=1e-6)
        bpy.data.scenes.remove(scene)

    def test_feature_definitions_are_finite_and_outline_is_closed(self):
        features = json.loads((ASSET_DIR / "last_features.json").read_text())
        outline = np.array(features["plantar_outline_mm"])
        self.assertGreater(len(outline), 100)
        self.assertTrue(np.isfinite(outline).all())
        np.testing.assert_equal(outline[0], outline[-1])
        self.assertGreater(features["widest_cross_section"]["width_mm"], 95)
        self.assertGreater(features["toe_point_mm"][1], 275)
        self.assertEqual(features["normalization"]["axes"]["toe"], "+Y")
        self.assertEqual(features["normalization"]["unit"], "mm")


class ValidatorTests(unittest.TestCase):
    def test_small_closed_geometry_at_nonzero_origin(self):
        bm = bmesh.new()
        bmesh.ops.create_cube(bm, size=0.004, matrix=Matrix.Translation((5, 133, 89)))
        mesh = bpy.data.meshes.new("small_offset_fixture")
        bm.to_mesh(mesh)
        bm.free()
        report = mesh_health(mesh)
        self.assertEqual(report["status"], "pass", report)
        self.assertGreater(report["surface_area_mm2"], 0)
        bpy.data.meshes.remove(mesh)

    def test_intersecting_generated_cubes_are_detected(self):
        bm = bmesh.new()
        bmesh.ops.create_cube(bm, size=2)
        bmesh.ops.create_cube(bm, size=2, matrix=Matrix.Translation((0.5, 0.5, 0.5)))
        mesh = bpy.data.meshes.new("intersecting_fixture")
        bm.to_mesh(mesh)
        bm.free()
        report = mesh_health(mesh)
        self.assertGreater(report["self_intersection_pairs"], 0)
        self.assertEqual(report["status"], "fail")
        bpy.data.meshes.remove(mesh)

    def test_inverted_normals_are_detected_and_repaired(self):
        bm = bmesh.new()
        bmesh.ops.create_cube(bm, size=2)
        bmesh.ops.reverse_faces(bm, faces=list(bm.faces))
        mesh = bpy.data.meshes.new("inverted_fixture")
        bm.to_mesh(mesh)
        bm.free()
        self.assertTrue(mesh_health(mesh)["globally_inverted"])
        repaired, actions = repair_copy(mesh)
        self.assertEqual(mesh_health(repaired)["status"], "pass")
        self.assertTrue(actions)
        bpy.data.meshes.remove(mesh)
        bpy.data.meshes.remove(repaired)

    def test_open_mesh_is_rejected(self):
        bm = bmesh.new()
        bmesh.ops.create_cube(bm, size=2)
        bm.faces.ensure_lookup_table()
        bmesh.ops.delete(bm, geom=[bm.faces[0]], context="FACES")
        mesh = bpy.data.meshes.new("open_fixture")
        bm.to_mesh(mesh)
        bm.free()
        self.assertFalse(mesh_health(mesh)["closed"])
        self.assertEqual(mesh_health(mesh)["status"], "fail")
        bpy.data.meshes.remove(mesh)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise RuntimeError("Blender geometry integration tests failed")
