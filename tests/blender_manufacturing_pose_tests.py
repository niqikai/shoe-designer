"""M3 pose/overhang checks using Blender-generated synthetic primitives."""
from pathlib import Path
import sys
import unittest

import bpy
import numpy as np
from mathutils import Matrix

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from engine.manufacturing_pose import check_fdm_overhang, plan_build_pose
from engine.shoe.volume import geometry_dependencies
geometry_dependencies()


def box(dimensions, center=(0, 0, 0), rotate_y=0):
    bpy.ops.mesh.primitive_cube_add(size=1, location=center)
    obj = bpy.context.object
    obj.scale = dimensions
    obj.rotation_euler.y = rotate_y
    bpy.context.view_layer.update()
    points = np.array([tuple(obj.matrix_world @ vertex.co) for vertex in obj.data.vertices], dtype=float)
    obj.data.calc_loop_triangles()
    faces = np.array([tuple(triangle.vertices) for triangle in obj.data.loop_triangles], dtype=int)
    mesh = obj.data
    bpy.data.objects.remove(obj, do_unlink=True)
    bpy.data.meshes.remove(mesh)
    return points, faces


class BuildPoseTests(unittest.TestCase):
    def test_original_fit_is_translated_to_plate_without_input_mutation(self):
        points, _ = box((100, 200, 50), (30, -60, 40))
        original = points.copy()
        report = plan_build_pose(points, {})
        self.assertEqual(report["status"], "pass")
        np.testing.assert_array_equal(points, original)
        pose = report["selected"]
        transformed = points @ np.array(pose["rotation_matrix_3x3"]).T + pose["translation_mm"]
        np.testing.assert_allclose(transformed.min(axis=0), [2, 2, 0])
        self.assertTrue(report["fits_as_designed"])
        self.assertTrue(report["fits_selected"])

    def test_yaw_fits_long_thin_model_and_checks_full_mesh(self):
        points, _ = box((280, 30, 30))
        report = plan_build_pose(points, {"print_process": "FDM"})
        self.assertEqual(report["status"], "warning")
        self.assertFalse(report["fits_as_designed"])
        self.assertTrue(report["fits_selected"])
        self.assertTrue(report["selected"]["full_vertex_fit_verified"])
        self.assertEqual(report["selected"]["tilt_deg"], 0)
        self.assertLessEqual(max(report["selected"]["oriented_dimensions_mm"]), 246 + 1e-7)

    def test_powder_bed_tilt_fits_but_fdm_never_tilts(self):
        points, _ = box((280, 180, 30))
        powder = plan_build_pose(points, {"print_process": "SLS"})
        fdm = plan_build_pose(points, {"print_process": "FDM"})
        self.assertTrue(powder["fits_selected"])
        self.assertGreater(powder["selected"]["tilt_deg"], 0)
        self.assertFalse(fdm["fits_selected"])
        np.testing.assert_allclose(np.array(fdm["selected"]["rotation_matrix_3x3"])[2], [0, 0, 1])

    def test_oversize_and_requested_fixed_pose_fail(self):
        points, _ = box((500, 500, 500))
        report = plan_build_pose(points, {})
        self.assertEqual(report["status"], "fail")
        self.assertFalse(report["fits_selected"])
        points, _ = box((280, 30, 30))
        fixed = plan_build_pose(points, {"build_orientation": "as_designed"})
        self.assertFalse(fixed["fits_selected"])
        self.assertEqual(fixed["candidate_count"], 1)

    def test_margin_is_enforced_on_every_dimension(self):
        points, _ = box((248, 20, 20))
        report = plan_build_pose(points, {"build_orientation": "as_designed"})
        self.assertFalse(report["fits_selected"])
        report = plan_build_pose(points, {"build_orientation": "as_designed", "build_margin_mm": 1})
        self.assertTrue(report["fits_selected"])

    def test_convex_hull_fit_rechecks_original_points(self):
        bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16, radius=1)
        obj = bpy.context.object
        points = np.array([tuple(v.co) for v in obj.data.vertices]) * [140, 15, 15]
        bpy.data.objects.remove(obj, do_unlink=True)
        report = plan_build_pose(points, {"print_process": "FDM"})
        self.assertEqual(report["bound_method"], "convex_hull_vertices")
        self.assertTrue(report["fits_selected"])
        pose = report["selected"]
        actual = np.ptp(points @ np.array(pose["rotation_matrix_3x3"]).T, axis=0)
        np.testing.assert_allclose(actual, pose["oriented_dimensions_mm"])


class FdmOverhangTests(unittest.TestCase):
    def test_flat_base_is_excluded_and_upward_top_never_counts(self):
        points, faces = box((20, 20, 10))
        report = check_fdm_overhang(points, faces, plan_build_pose(points, {}))
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["overhang_triangle_count"], 0)
        self.assertEqual(report["build_plate_contact_triangle_count"], 2)
        self.assertAlmostEqual(report["build_plate_contact_area_mm2"], 400)
        self.assertEqual(report["max_downward_angle_deg"], 0)

    def test_elevated_downward_surface_is_detected(self):
        lower, lower_faces = box((10, 10, 10), (0, 0, 5))
        upper, upper_faces = box((30, 10, 5), (0, 0, 12.5))
        points = np.concatenate((lower, upper))
        faces = np.concatenate((lower_faces, upper_faces + len(lower)))
        report = check_fdm_overhang(points, faces, plan_build_pose(points, {}))
        self.assertEqual(report["status"], "warning")
        self.assertEqual(report["max_downward_angle_deg"], 90)
        self.assertAlmostEqual(report["overhang_area_mm2"], 300)
        self.assertTrue(report["requires_slicer_support_review"])

    def test_selected_rotation_is_used_and_threshold_controls_sloped_faces(self):
        points, faces = box((20, 20, 10))
        rotation = np.array(Matrix.Rotation(np.deg2rad(30), 3, "Y"))
        pose = {"rotation_matrix_3x3": rotation.tolist()}
        strict = check_fdm_overhang(points, faces, pose, 45)
        relaxed = check_fdm_overhang(points, faces, pose, 70)
        self.assertEqual(strict["status"], "warning")
        self.assertAlmostEqual(strict["max_downward_angle_deg"], 60, delta=1e-4)
        self.assertGreater(strict["overhang_area_mm2"], 0)
        self.assertEqual(relaxed["status"], "pass")


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    if not result.wasSuccessful():
        raise SystemExit(1)
