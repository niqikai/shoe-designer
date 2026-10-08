"""M3 positive manufacturing/export path and current failed-candidate evidence."""
from pathlib import Path
import json
import sys
import tempfile
import unittest

import bpy
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from engine.build import run
from engine.shoe.last import _import_mesh
from engine.validate import coordinates, mesh_health


class ManufacturingIntegrationTests(unittest.TestCase):
    def test_solid_fixture_exports_the_verified_print_pose_in_millimetres(self):
        params=json.loads((ROOT/'designs/current.json').read_text())
        params['midsole_structure']='solid'
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'solid.json'
            path.write_text(json.dumps(params))
            report=run(path,Path(folder)/'result',render=False)
            self.assertTrue(report['manufacturing']['export_allowed'],report['manufacturing']['blockers'])
            self.assertEqual(report['manufacturing']['thickness']['status'],'pass')
            self.assertEqual(report['manufacturing']['powder_removal']['status'],'not_applicable')
            original=_import_mesh(Path(report['exports']['stl']))
            printed=_import_mesh(Path(report['exports']['print_stl']))
            try:
                self.assertEqual(mesh_health(printed)['status'],'pass')
                pose=report['manufacturing']['build_volume']['selected']
                expected=coordinates(original)@np.array(pose['rotation_matrix_3x3']).T+pose['translation_mm']
                actual=coordinates(printed)
                np.testing.assert_allclose(actual.min(axis=0),expected.min(axis=0),atol=1e-4)
                np.testing.assert_allclose(actual.max(axis=0),expected.max(axis=0),atol=1e-4)
                np.testing.assert_allclose(actual.min(axis=0),[2,2,0],atol=1e-4)
                self.assertTrue(np.all(actual.max(axis=0)<250))
                # Import into a metre scene like a fresh glTF consumer; the
                # Blender importer otherwise adapts to this build's mm scene.
                bpy.context.scene.unit_settings.scale_length=1
                existing=set(bpy.data.objects)
                bpy.ops.import_scene.gltf(filepath=report['exports']['print_glb'])
                imported=set(bpy.data.objects)-existing
                glb_points=np.concatenate([np.array([tuple(obj.matrix_world@v.co) for v in obj.data.vertices])*1000 for obj in imported if obj.type=='MESH'])
                np.testing.assert_allclose(glb_points.min(axis=0),expected.min(axis=0),atol=.002)
                np.testing.assert_allclose(glb_points.max(axis=0),expected.max(axis=0),atol=.002)
                for obj in imported:bpy.data.objects.remove(obj,do_unlink=True)
            finally:
                bpy.data.meshes.remove(original)
                bpy.data.meshes.remove(printed)

    def test_current_preview_and_fine_reports_enforce_their_failures(self):
        for directory in ('m3','m3-export'):
            output=ROOT/'out'/directory
            report=json.loads((output/'report.json').read_text())
            self.assertEqual(report['stage'],'M3')
            self.assertTrue(report['original_sources_unchanged'])
            self.assertEqual(report['mesh_health']['status'],'pass')
            manufacturing=report['manufacturing']
            self.assertEqual(report['status'],manufacturing['status'])
            if manufacturing['blockers']:
                self.assertFalse(manufacturing['export_allowed'])
                self.assertEqual(report['exports'],{})
                self.assertFalse(list(output.glob('*.stl')))
                self.assertFalse(list(output.glob('*.glb')))
            self.assertTrue(manufacturing['build_volume']['fits_selected'])
            self.assertGreaterEqual(manufacturing['powder_removal']['detected_separate_exit_count'],2)
            self.assertEqual(manufacturing['thickness']['missing_samples'],0)


if __name__=='__main__':
    if not unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])).wasSuccessful():
        raise SystemExit(1)
