"""M3 thickness, finite powder paths, repair and fail-closed export tests."""
from pathlib import Path
import json
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
from engine.manufacturing import check_powder_paths, measure_thickness, process_profile, reinforce_thin_edges
from engine.params import normalize_params
from engine.validate import mesh_health, validate_manufacturing


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

    def test_reinforcement_is_local_and_protects_foot_contact_surface(self):
        axis=np.arange(-5,5.01,.5)
        x,y,z=axis[:,None,None],axis[None,:,None],axis[None,None,:]
        field=np.maximum(np.maximum(abs(x)-4,abs(y)-4),abs(z)-.3).astype(np.float32)
        before=field.copy()
        stats=reinforce_thin_edges(field,(axis,axis,axis),[{'midpoint_mm':[0,0,0]}],1.2,np.ones((len(axis),len(axis)))*4)
        self.assertGreater(stats['added_material_mm3'],0)
        np.testing.assert_array_equal(field[:3],before[:3])
        protected=before.copy()
        stats=reinforce_thin_edges(protected,(axis,axis,axis),[{'midpoint_mm':[0,0,0]}],1.2,np.ones((len(axis),len(axis))))
        self.assertEqual(stats['sphere_count'],0)
        np.testing.assert_array_equal(protected,before)


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
            with patch('engine.build.build_shoe',return_value=built),patch('engine.build.source_manifest',return_value={'test':'unchanged'}),patch('engine.build.export_shoe',side_effect=AssertionError('must not export')):
                report=run(ROOT/'designs/current.json',folder,render=False)
            self.assertFalse(stale.exists())
            self.assertEqual(report['exports'],{})
            self.assertEqual(json.loads((Path(folder)/'report.json').read_text())['manufacturing']['blockers'],['thickness'])
            self.assertTrue((Path(folder)/'manufacturing_report.md').exists())


if __name__=='__main__':
    if not unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])).wasSuccessful():
        raise SystemExit(1)
