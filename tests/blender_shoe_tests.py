"""M1 integration checks; run inside Blender after tools/run.sh.

Append ``-- --stress`` to include four boundary designs. These tests establish
geometric behavior and export units, not fit, strength, rebound, or printability.
The optional cases do not replace the later M5 randomized stress milestone.
"""
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

from engine.build import build_shoe, mirror_mesh
from engine.params import normalize_params
from engine.shoe.last import _import_mesh, source_manifest
from engine.validate import coordinates, mesh_health, surface_tree


def bounds(points):
    return np.stack((points.min(axis=0), points.max(axis=0)))


def ray(tree, x, y, z, direction):
    return tree.ray_cast(Vector((float(x), float(y), float(z))),
                         Vector((0, 0, direction)), 1000)


def remove_build(built):
    mesh = built.model.data
    bpy.data.objects.remove(built.model, do_unlink=True)
    bpy.data.meshes.remove(mesh)
    bpy.data.meshes.remove(built.last)


class ShoeIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.before = source_manifest()
        cls.output = ROOT / "out/m1"
        path = cls.output / "report.json"
        if not path.is_file():
            raise RuntimeError("先运行 tools/run.sh 生成 out/m1 下的默认 M1 导出。")
        cls.export_report = json.loads(path.read_text(encoding="utf-8"))
        if cls.export_report.get("status") != "pass_pending_visual_confirmation":
            raise RuntimeError("out/m1/report.json 未记录通过的 M1 生成结果。")
        cls.params, _ = normalize_params({"foot_side": "right", "collar_style": "low", "collar_height_mm": 75})
        # A single build serves all default tests; exported files are checked
        # independently so a stale or wrongly scaled artifact cannot pass.
        cls.built = build_shoe(cls.params)
        cls.shoe_tree = surface_tree(cls.built.model.data)
        cls.last_tree = surface_tree(cls.built.last)
        cls.last_bounds = bounds(coordinates(cls.built.last))

    @classmethod
    def tearDownClass(cls):
        remove_build(cls.built)
        if source_manifest() != cls.before:
            raise AssertionError("M1 测试修改了原始鞋楦素材。")

    def assertHealthy(self, health):
        self.assertEqual(health["status"], "pass", health)
        self.assertTrue(health["closed"])
        self.assertEqual(health["connected_components"], 1)
        self.assertEqual(health["normal_orientation"], "outward")
        self.assertEqual(health["boundary_edges"], 0)
        self.assertEqual(health["non_manifold_edges"], 0)
        self.assertEqual(health["self_intersection_pairs"], 0)
        self.assertGreater(health["signed_volume_mm3"], 0)

    def test_default_is_one_closed_outward_body(self):
        self.assertHealthy(mesh_health(self.built.model.data))
        self.assertEqual(self.built.model["foot_side"], "right")
        self.assertEqual(self.built.report["sole"]["midsole"],
                         "solid; top sampled from deformed high-poly plantar surface")

    def test_mirror_preserves_volume_and_reflects_x_bounds(self):
        mirrored = mirror_mesh(self.built.model.data)
        try:
            health = mesh_health(mirrored)
            self.assertHealthy(health)
            right = bounds(coordinates(self.built.model.data))
            expected = right.copy()
            expected[:, 0] = -right[::-1, 0]
            np.testing.assert_allclose(bounds(coordinates(mirrored)), expected, atol=1e-5)
            self.assertAlmostEqual(health["signed_volume_mm3"],
                                   self.built.report["mesh_health"]["signed_volume_mm3"], delta=.1)
        finally:
            bpy.data.meshes.remove(mirrored)

    def test_stl_roundtrip_is_closed_and_uses_millimetres(self):
        path = self.output / "shoe_right.stl"
        self.assertTrue(path.is_file(), path)
        imported = _import_mesh(path)
        try:
            health = mesh_health(imported)
            self.assertHealthy(health)
            exported_health = self.export_report["mesh_health"]
            np.testing.assert_allclose(health["bounds_mm"]["min"], exported_health["bounds_mm"]["min"], atol=1e-4)
            np.testing.assert_allclose(health["bounds_mm"]["max"], exported_health["bounds_mm"]["max"], atol=1e-4)
            np.testing.assert_allclose(bounds(coordinates(imported)),
                                       bounds(coordinates(self.built.model.data)), atol=1e-4)
            length = health["dimensions_mm_xyz"][1]
            self.assertGreater(length, 270)
            self.assertLess(length, 310)
            self.assertEqual(self.export_report["exports"]["stl_coordinate_unit"], "mm")
        finally:
            bpy.data.meshes.remove(imported)

    def test_glb_roundtrip_metres_match_stl_and_keep_material_regions(self):
        path = self.output / "shoe_right.glb"
        self.assertTrue(path.is_file(), path)
        existing = set(bpy.data.objects)
        bpy.ops.import_scene.gltf(filepath=str(path))
        imported = set(bpy.data.objects) - existing
        try:
            meshes = [obj for obj in imported if obj.type == "MESH"]
            self.assertTrue(meshes, "GLB 未导入网格")
            # Evaluate world transforms: glTF's Y-up conversion and node scales
            # must both survive the round-trip before metres become millimetres.
            world = np.concatenate([np.asarray([obj.matrix_world @ vertex.co for vertex in obj.data.vertices],
                                               dtype=np.float64) for obj in meshes]) * 1000
            expected = self.export_report["mesh_health"]["bounds_mm"]
            np.testing.assert_allclose(bounds(world), [expected["min"], expected["max"]], atol=.002)
            materials = {material.name for obj in meshes for material in obj.data.materials if material is not None}
            self.assertGreaterEqual(len(materials), 3)
            used = {obj.data.materials[polygon.material_index].name for obj in meshes
                    for polygon in obj.data.polygons if obj.data.materials[polygon.material_index] is not None}
            self.assertGreaterEqual(len(used), 3)
            self.assertEqual(self.export_report["exports"]["glb_coordinate_unit"], "m")
        finally:
            data = {obj.data for obj in imported if obj.type == "MESH"}
            for obj in imported:
                bpy.data.objects.remove(obj, do_unlink=True)
            for mesh in data:
                if mesh.users == 0:
                    bpy.data.meshes.remove(mesh)

    def test_collar_is_open_and_rays_reach_the_insole(self):
        heel = self.built.report["sole"]["anchors"]["heel"]
        collar = self.params["collar_height_mm"]
        for dx in (-5, 0, 5):
            with self.subTest(offset_x_mm=dx):
                x, y = heel["x_mm"] + dx, heel["y_mm"]
                location, normal, _, _ = ray(self.shoe_tree, x, y, collar + 10, -1)
                plantar, _, _, _ = ray(self.last_tree, x, y, -200, 1)
                self.assertIsNotNone(location)
                self.assertIsNotNone(plantar)
                self.assertLess(location.z, collar - 20, "鞋口被意外封顶")
                self.assertAlmostEqual(location.z, plantar.z, delta=1.25)
                self.assertGreater(normal.z, .35)

    def test_insole_follows_the_plantar_arch(self):
        length = self.last_bounds[1, 1] - self.last_bounds[0, 1]
        for fraction in (.3, .4, .5):
            for x in (-15, 0, 15):
                with self.subTest(x_mm=x, length_fraction=fraction):
                    y = self.last_bounds[0, 1] + length * fraction
                    plantar, normal, _, _ = ray(self.last_tree, x, y, -200, 1)
                    self.assertIsNotNone(plantar)
                    self.assertLess(normal.z, -.35, "测试点未落在朝下楦底")
                    insole, insole_normal, _, _ = ray(self.shoe_tree, x, y, plantar.z + 3, -1)
                    self.assertIsNotNone(insole)
                    self.assertAlmostEqual(insole.z, plantar.z, delta=1.25)
                    self.assertGreater(insole_normal.z, .35)

    def test_sole_anchor_thickness_matches_the_requested_parameters(self):
        for name, key in (("heel", "heel_sole_mm"), ("forefoot", "forefoot_sole_mm")):
            with self.subTest(anchor=name):
                anchor = self.built.report["sole"]["anchors"][name]
                x, y = anchor["x_mm"], anchor["y_mm"]
                bottom, bottom_normal, _, _ = ray(self.shoe_tree, x, y, -200, 1)
                insole, insole_normal, _, _ = ray(self.shoe_tree, x, y, anchor["bottom_z_mm"] + 3, -1)
                self.assertIsNotNone(bottom)
                self.assertIsNotNone(insole)
                self.assertLess(bottom_normal.z, -.35)
                self.assertGreater(insole_normal.z, .35)
                self.assertAlmostEqual(insole.z - bottom.z, self.params[key], delta=1.25)


class ShoeBoundaryTests(unittest.TestCase):
    """Opt-in deterministic corners; this is not M5 or manufacturing validation."""

    def test_four_boundary_designs_remain_closed(self):
        cases = [
            {"size_eu": 35, "foot_width": "narrow", "collar_style": "low", "collar_height_mm": 60,
             "upper_thickness_mm": 1.8, "heel_sole_mm": 12, "forefoot_sole_mm": 8,
             "toe_roundness": -.15, "toe_height_scale": .85, "edge_radius_mm": .5, "outsole_flare_mm": 4},
            {"size_eu": 46, "foot_width": "wide", "collar_style": "mid", "collar_height_mm": 115,
             "upper_thickness_mm": 1.8, "heel_sole_mm": 40, "forefoot_sole_mm": 30,
             "toe_roundness": .15, "toe_height_scale": 1.15, "edge_radius_mm": 4, "outsole_flare_mm": 8},
            {"size_eu": 35, "foot_width": "wide", "collar_style": "mid", "collar_height_mm": 115,
             "upper_thickness_mm": 4, "heel_sole_mm": 40, "forefoot_sole_mm": 8,
             "toe_roundness": .15, "toe_height_scale": .85, "edge_radius_mm": 4, "outsole_flare_mm": 4},
            {"size_eu": 46, "foot_width": "narrow", "foot_side": "left", "collar_style": "low", "collar_height_mm": 85,
             "upper_thickness_mm": 1.8, "heel_sole_mm": 12, "forefoot_sole_mm": 30,
             "toe_roundness": -.15, "toe_height_scale": 1.15, "edge_radius_mm": 4, "outsole_flare_mm": 8},
        ]
        before = source_manifest()
        try:
            for case in cases:
                with self.subTest(params=case):
                    effective, _ = normalize_params(case)
                    built = build_shoe(case)
                    try:
                        health = built.report["mesh_health"]
                        self.assertEqual(health["status"], "pass", health)
                        self.assertEqual(health["connected_components"], 1)
                        self.assertEqual(health["normal_orientation"], "outward")
                        self.assertEqual(health["self_intersection_pairs"], 0)
                        self.assertEqual(built.features["size_estimate"]["nominal_size_eu"], effective["size_eu"])
                        self.assertEqual(built.features["selected_foot_side"], effective["foot_side"])
                        self.assertGreater(health["dimensions_mm_xyz"][1], 210)
                        self.assertLess(health["dimensions_mm_xyz"][1], 340)
                        self.assertAlmostEqual(coordinates(built.model.data)[:, 2].max(),
                                               effective["collar_height_mm"], delta=1.25)
                    finally:
                        remove_build(built)
        finally:
            self.assertEqual(source_manifest(), before)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stress", action="store_true", help="额外生成四个边界设计")
    options = parser.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else [])
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ShoeIntegrationTests)
    if options.stress:
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(ShoeBoundaryTests))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise RuntimeError("M1 鞋体或导出集成测试失败")
