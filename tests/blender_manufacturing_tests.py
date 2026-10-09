"""M3 thickness, continuous edge finishing, powder paths and export gate tests."""
from pathlib import Path
import json
import math
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from engine.build import run
from engine.manufacturing import check_powder_paths, measure_thickness, process_profile
from engine.params import normalize_params
from engine.shoe.edge_finish import round_material_edges, smooth_finished_surface
from engine.shoe.volume import mesh_from_field
from engine.validate import coordinates, mesh_health, triangles, validate_manufacturing


def box_mesh(dimensions):
    from mathutils import Matrix
    bpy.ops.mesh.primitive_cube_add(size=1)
    obj = bpy.context.object
    mesh = obj.data.copy()
    mesh.transform(Matrix.Diagonal((*dimensions, 1)))
    bpy.data.objects.remove(obj, do_unlink=True)
    return mesh


class ThicknessTests(unittest.TestCase):
    def test_known_plates_fail_below_and_pass_above_threshold(self):
        for thickness in (.8, 2.0):
            mesh = box_mesh((20, 20, thickness))
            try:
                report, thin = measure_thickness(mesh, 1.2, samples=3000)
                self.assertAlmostEqual(report['sampled_minimum_mm'], thickness, delta=1e-4)
                self.assertEqual(report['status'], 'fail' if thickness < 1.2 else 'pass')
                self.assertEqual(report['missing_samples'], 0)
                self.assertEqual(bool(thin), thickness < 1.2)
            finally:
                bpy.data.meshes.remove(mesh)

    def test_thin_circular_rods_are_detected(self):
        bpy.ops.mesh.primitive_cylinder_add(vertices=64, radius=.5, depth=20)
        obj = bpy.context.object
        report, _ = measure_thickness(obj.data, 1.2, samples=3000)
        self.assertEqual(report['status'], 'fail')
        self.assertAlmostEqual(report['sampled_minimum_mm'], 1, delta=.01)
        bpy.data.objects.remove(obj, do_unlink=True)

    def test_unmeasurable_open_surface_fails(self):
        bpy.ops.mesh.primitive_plane_add(size=20)
        obj = bpy.context.object
        report, _ = measure_thickness(obj.data, 1.2, samples=100)
        self.assertEqual(report['status'], 'fail')
        self.assertEqual(report['missing_samples'], 100)
        self.assertIsNone(report['sampled_minimum_mm'])
        bpy.data.objects.remove(obj, do_unlink=True)

    def test_profiles_follow_project_process_thresholds(self):
        for process, expected in [('SLS',1.2),('MJF',1.2),('FDM',1.5)]:
            p, _ = normalize_params({'print_process':process})
            profile=process_profile(p)
            self.assertEqual(profile['minimum_wall_mm'],expected)
            self.assertEqual(profile['minimum_rod_mm'],expected)


class PowderPathTests(unittest.TestCase):
    def fixture(self, radius, one_exit=False):
        axis=np.arange(-14,14.01,.5)
        x,y,z=axis[:,None,None],axis[None,:,None],axis[None,None,:]
        envelope=np.maximum(np.maximum(abs(x),abs(y)),abs(z))-10
        interior=np.maximum(np.maximum(abs(x),abs(y)),abs(z))-8
        field=np.maximum(envelope,-interior)
        tube=np.sqrt(y*y+z*z)-radius
        if one_exit: tube=np.maximum(tube,-x)
        field=np.maximum(field,-tube).astype(np.float32)
        core=(envelope<0)&(abs(z)<7)
        return field,envelope<0,core,(axis,axis,axis)

    def test_two_wide_holes_connect_inner_chamber_to_exterior(self):
        result=check_powder_paths(*self.fixture(4))
        self.assertEqual(result['status'],'pass')
        self.assertEqual(result['detected_separate_exit_count'],2)
        self.assertEqual(result['unreachable_wide_core_centres'],0)
        self.assertTrue(all(p['conservative_clearance_diameter_mm']>=4 for p in result['exit_examples']))

    def test_single_hole_does_not_count_twice(self):
        result=check_powder_paths(*self.fixture(4,True))
        self.assertEqual(result['status'],'fail')
        self.assertEqual(result['detected_separate_exit_count'],1)

    def test_connected_but_narrow_throats_fail_clearance(self):
        result=check_powder_paths(*self.fixture(1.5))
        self.assertEqual(result['status'],'fail')
        self.assertEqual(result['detected_separate_exit_count'],0)
        self.assertGreater(result['unreachable_wide_core_centres'],0)

    def test_surface_displacement_reserves_extra_clearance(self):
        fixture = self.fixture(4)
        self.assertEqual(check_powder_paths(*fixture)['detected_separate_exit_count'], 2)
        result = check_powder_paths(*fixture, surface_displacement_mm=3)
        self.assertEqual(result['status'], 'fail')
        self.assertEqual(result['detected_separate_exit_count'], 0)
        self.assertEqual(result['surface_displacement_guard_mm'], 3)
        self.assertAlmostEqual(result['clearance_guard_mm'], math.sqrt(3) * .5 + 3)

    def test_invalid_surface_displacement_is_rejected(self):
        fixture = self.fixture(4)
        for displacement in (-.1, math.nan, math.inf, -math.inf):
            with self.subTest(displacement=displacement), self.assertRaises(ValueError):
                check_powder_paths(*fixture, surface_displacement_mm=displacement)


class EdgeFinishTests(unittest.TestCase):
    def fixture(self, *, wedge=False, plantar=False):
        # A wedge represents a clipped sheet whose end tapers to zero thickness.
        # The separate 3 mm slab is an already adequate, connected control.
        axis = np.arange(-8, 8.01, .25)
        x, y, z = axis[:, None, None], axis[None, :, None], axis[None, None, :]
        half_height = 1.5 - .3 * x if wedge else 1.5
        vertical = abs(z + 1.5) - 1.5 if plantar else abs(z) - half_height
        field = np.maximum(np.maximum(abs(x) - 5, abs(y) - 4), vertical).astype(np.float32)
        top_map = np.full((len(axis), len(axis)), 0.0 if plantar else 20.0)
        return field, (axis, axis, axis), top_map

    def extract(self, field, axes, name):
        return mesh_from_field(field, tuple(axis[0] for axis in axes), .25, name)

    def test_zero_radius_preserves_field_exactly(self):
        field, axes, top_map = self.fixture(wedge=True)
        before = field.copy()
        info = round_material_edges(field, axes, top_map, 50, 0)
        self.assertFalse(info['enabled'])
        np.testing.assert_array_equal(field, before)

    def test_clipped_sliver_fails_then_rounded_fixture_passes(self):
        field, axes, top_map = self.fixture(wedge=True)
        mesh = self.extract(field, axes, 'thin_wedge')
        try:
            initial, _ = measure_thickness(mesh, 1.2, samples=6000)
            self.assertEqual(initial['status'], 'fail')
            self.assertGreater(initial['thin_sample_count'], 0)
        finally:
            bpy.data.meshes.remove(mesh)
        info = round_material_edges(field, axes, top_map, 50, 1.2)
        self.assertGreater(info['removed_material_mm3'], 0)
        mesh = self.extract(field, axes, 'rounded_wedge')
        mesh, _ = smooth_finished_surface(mesh, axes, top_map, 50)
        try:
            final, _ = measure_thickness(mesh, 1.2, samples=6000)
            self.assertEqual(final['status'], 'pass')
            self.assertEqual(final['thin_sample_count'], 0)
            self.assertEqual(final['missing_samples'], 0)
            self.assertEqual(mesh_health(mesh)['status'], 'pass')
        finally:
            bpy.data.meshes.remove(mesh)

    def test_adequate_slab_stays_connected_and_thick_enough(self):
        field, axes, top_map = self.fixture()
        round_material_edges(field, axes, top_map, 50, 1.2)
        mesh = self.extract(field, axes, 'rounded_slab')
        mesh, _ = smooth_finished_surface(mesh, axes, top_map, 50)
        try:
            health = mesh_health(mesh)
            self.assertEqual(health['status'], 'pass')
            self.assertEqual(health['connected_components'], 1)
            self.assertEqual(measure_thickness(mesh, 1.2, samples=6000)[0]['status'], 'pass')
        finally:
            bpy.data.meshes.remove(mesh)

    def test_plantar_field_and_protected_vertices_are_unchanged(self):
        field, axes, top_map = self.fixture(plantar=True)
        before = field.copy()
        height = axes[2]
        protected_field = (height >= -1) & (height <= 50 - 4)
        round_material_edges(field, axes, top_map, 50, 1.2)
        np.testing.assert_array_equal(field[:, :, protected_field], before[:, :, protected_field])
        self.assertTrue(np.any(field[:, :, ~protected_field] != before[:, :, ~protected_field]))
        mesh = self.extract(field, axes, 'protected_slab')
        points = coordinates(mesh)
        faces = triangles(mesh).copy()
        protected_vertices = (points[:, 2] >= -1) & (points[:, 2] <= 50 - 4)
        self.assertGreater(np.count_nonzero(protected_vertices), 0)
        mesh, info = smooth_finished_surface(mesh, axes, top_map, 50)
        try:
            after = coordinates(mesh)
            np.testing.assert_array_equal(after[protected_vertices], points[protected_vertices])
            np.testing.assert_array_equal(triangles(mesh), faces)
            self.assertEqual(len(after), len(points))
            self.assertEqual(info['protected_max_displacement_mm'], 0)
            self.assertGreater(info['max_surface_displacement_mm'], 0)
        finally:
            bpy.data.meshes.remove(mesh)

    def test_invalid_rounding_radius_is_rejected(self):
        field, axes, top_map = self.fixture()
        for radius in (-.1, math.nan, math.inf, -math.inf):
            with self.subTest(radius=radius), self.assertRaises(ValueError):
                round_material_edges(field.copy(), axes, top_map, 50, radius)


class ManufacturingGateTests(unittest.TestCase):
    def test_each_required_failure_blocks_candidate_export(self):
        params,_=normalize_params({})
        mesh=box_mesh((20,20,20))
        try:
            health=mesh_health(mesh)
            measured={'thickness':{'status':'pass'},'powder_removal':{'status':'not_applicable'}}
            self.assertTrue(validate_manufacturing(mesh,params,health,measured)['export_allowed'])
            for name in ('thickness','powder_removal'):
                bad={**measured,name:{'status':'fail'}}
                report=validate_manufacturing(mesh,params,health,bad)
                self.assertFalse(report['export_allowed'])
                self.assertIn(name,report['blockers'])
            report=validate_manufacturing(mesh,params,health,{})
            self.assertFalse(report['export_allowed'])
            self.assertEqual(set(report['blockers']),{'thickness','powder_removal'})
        finally:
            bpy.data.meshes.remove(mesh)

    def test_failed_run_removes_stale_exports_and_retains_full_diagnosis(self):
        measured={'status':'fail','export_allowed':False,'blockers':['thickness'],'warnings':[],
                  'summary':'壁厚失败','thickness':{'message':'检测到过薄'},
                  'powder_removal':{'message':'检查完成'},'build_volume':{'message':'姿态可放入'},
                  'overhang':{'message':'不适用'}}
        built=SimpleNamespace(model=None,features={},report={'manufacturing':measured,'clamp_messages':[],
                              'mesh_health':{'status':'pass'},'voxel_mm':.8})
        with tempfile.TemporaryDirectory() as folder:
            stale=Path(folder)/'shoe_right.stl'
            stale.write_text('old output')
            old_diagnostics=[Path(folder)/name for name in
                             ('thin_locations.png','previews/thin_side.png','previews/thin_iso.png')]
            for diagnostic in old_diagnostics:
                diagnostic.parent.mkdir(exist_ok=True)
                diagnostic.write_text('old thin-point diagnostic')
            with patch('engine.build.build_shoe',return_value=built),patch('engine.build.source_manifest',return_value={'test':'unchanged'}),patch('engine.build.export_shoe',side_effect=AssertionError('must not export')):
                report=run(ROOT/'designs/current.json',folder,render=False)
            self.assertFalse(stale.exists())
            self.assertTrue(all(not path.exists() for path in old_diagnostics))
            self.assertEqual(report['exports'],{})
            self.assertEqual(json.loads((Path(folder)/'report.json').read_text())['manufacturing']['blockers'],['thickness'])
            self.assertTrue((Path(folder)/'manufacturing_report.md').exists())


if __name__=='__main__':
    if not unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])).wasSuccessful():
        raise SystemExit(1)
