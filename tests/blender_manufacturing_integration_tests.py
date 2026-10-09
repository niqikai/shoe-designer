"""M3 export gates and independent checks of the actual current print files."""
from pathlib import Path
import json
import sys
import tempfile
import unittest

import bpy
import numpy as np
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from engine.build import mirror_mesh, run
from engine.manufacturing import measure_thickness
from engine.params import load_params
from engine.shoe.last import _import_mesh, deform_last, load_last, source_manifest
from engine.validate import coordinates, mesh_health, surface_tree


def glb_bounds_mm(path):
    """Read glTF metres in a fresh-consumer unit scale, then compare in mm."""
    scene = bpy.context.scene
    previous_scale = scene.unit_settings.scale_length
    existing = set(bpy.data.objects)
    try:
        scene.unit_settings.scale_length = 1
        bpy.ops.import_scene.gltf(filepath=str(path))
        bounds = []
        for obj in set(bpy.data.objects) - existing:
            if obj.type != 'MESH':
                continue
            matrix = np.asarray(obj.matrix_world, dtype=float)
            points = coordinates(obj.data) @ matrix[:3, :3].T + matrix[:3, 3]
            bounds.append([points.min(axis=0), points.max(axis=0)])
        if not bounds:
            raise AssertionError('GLB contains no mesh geometry')
        bounds = np.asarray(bounds) * 1000
        return {'min': bounds[:, 0].min(axis=0), 'max': bounds[:, 1].max(axis=0)}
    finally:
        scene.unit_settings.scale_length = previous_scale
        for obj in set(bpy.data.objects) - existing:
            mesh = obj.data if obj.type == 'MESH' else None
            bpy.data.objects.remove(obj, do_unlink=True)
            if mesh is not None and mesh.users == 0:
                bpy.data.meshes.remove(mesh)


class ManufacturingIntegrationTests(unittest.TestCase):
    def setUp(self):
        # This also verifies that every raw file still matches the read-only ZIP.
        self.sources_before = source_manifest()

    def tearDown(self):
        self.assertEqual(source_manifest(), self.sources_before)

    def assert_bounds_equal(self, actual, expected, *, tolerance):
        for side in ('min', 'max'):
            np.testing.assert_allclose(actual[side], expected[side], atol=tolerance, rtol=0)

    def assert_export_manifest(self, exports):
        units = {key: value for key, value in exports.items() if key.endswith('_coordinate_unit')}
        self.assertEqual(units, {'stl_coordinate_unit': 'mm', 'glb_coordinate_unit': 'm',
                                 'print_stl_coordinate_unit': 'mm', 'print_glb_coordinate_unit': 'm'})
        files = {key: value for key, value in exports.items() if key not in units}
        self.assertEqual(set(files), {'stl', 'glb', 'print_stl', 'print_glb'})
        for key, value in files.items():
            path = Path(value)
            self.assertTrue(path.is_file(), value)
            self.assertEqual(path.suffix, '.' + key.rsplit('_', 1)[-1])

    def assert_print_pose(self, original_points, printed_points, report):
        volume = report['manufacturing']['build_volume']
        pose = volume['selected']
        expected = (original_points @ np.asarray(pose['rotation_matrix_3x3']).T
                    + pose['translation_mm'])
        bounds = {'min': expected.min(axis=0), 'max': expected.max(axis=0)}
        self.assert_bounds_equal(
            {'min': printed_points.min(axis=0), 'max': printed_points.max(axis=0)},
            bounds, tolerance=1e-4)
        margin = volume['margin_mm']
        np.testing.assert_allclose(printed_points.min(axis=0), [margin, margin, 0], atol=1e-4, rtol=0)
        # Both Z margins are above the part; XY margins are on opposite sides.
        upper_limit = np.asarray(volume['machine_dimensions_mm']) - [margin, margin, 2 * margin]
        self.assertTrue(np.all(printed_points.max(axis=0) <= upper_limit + 1e-4))
        return bounds

    def test_solid_fixture_exports_the_verified_print_pose_in_millimetres(self):
        params = json.loads((ROOT / 'designs/current.json').read_text())
        # Keep the current edge finish active for this positive solid fixture;
        # changing the midsole structure must not silently disable that feature.
        params['midsole_structure'] = 'solid'
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'solid.json'
            path.write_text(json.dumps(params))
            report = run(path, Path(folder) / 'result', render=False)
            self.assertTrue(report['manufacturing']['export_allowed'], report['manufacturing']['blockers'])
            self.assertEqual(report['manufacturing']['thickness']['status'], 'pass')
            self.assertEqual(report['manufacturing']['powder_removal']['status'], 'not_applicable')
            self.assertEqual(report['edge_finish']['enabled'], params.get('boundary_rounding_mm', 0) > 0)
            self.assert_export_manifest(report['exports'])
            original = _import_mesh(Path(report['exports']['stl']))
            printed = None
            try:
                printed = _import_mesh(Path(report['exports']['print_stl']))
                self.assertEqual(mesh_health(printed)['status'], 'pass')
                expected = self.assert_print_pose(coordinates(original), coordinates(printed), report)
                self.assert_bounds_equal(glb_bounds_mm(report['exports']['print_glb']), expected, tolerance=.002)
            finally:
                bpy.data.meshes.remove(original)
                if printed is not None:
                    bpy.data.meshes.remove(printed)

    def test_current_preview_and_fine_reports_match_the_export_gate(self):
        current, _ = load_params(ROOT / 'designs/current.json')
        reports = []
        for directory in ('m3', 'm3-export'):
            with self.subTest(directory=directory):
                output = ROOT / 'out' / directory
                report = json.loads((output / 'report.json').read_text())
                reports.append(report)
                self.assertEqual(report['stage'], 'M3')
                self.assertTrue(report['original_sources_unchanged'])
                self.assertEqual(report['mesh_health']['status'], 'pass')
                # Fine sampling may be selected by a CLI override or resolution.
                self.assertEqual({k: v for k, v in report['effective_params'].items() if k != 'resolution'},
                                 {k: v for k, v in current.items() if k != 'resolution'})
                manufacturing = report['manufacturing']
                self.assertEqual(report['status'], manufacturing['status'])
                checks = {'mesh': report['mesh_health'],
                          **{key: manufacturing[key] for key in
                             ('thickness', 'powder_removal', 'build_volume', 'overhang')}}
                blockers = {key for key, check in checks.items()
                            if check.get('status') not in ('pass', 'warning', 'not_applicable')}
                self.assertEqual(set(manufacturing['blockers']), blockers)
                self.assertEqual(manufacturing['export_allowed'], not blockers)
                if blockers:
                    self.assertEqual(report['status'], 'fail')
                    self.assertEqual(report['exports'], {})
                    self.assertFalse(list(output.glob('*.stl')))
                    self.assertFalse(list(output.glob('*.glb')))
                else:
                    self.assertIn(report['status'], ('pass', 'warning'))
                    self.assert_export_manifest(report['exports'])
                    self.assertEqual(manufacturing['thickness']['status'], 'pass')
                    self.assertEqual(manufacturing['thickness']['thin_sample_count'], 0)
                    self.assertEqual(manufacturing['thickness']['missing_samples'], 0)
                    self.assertTrue(manufacturing['build_volume']['fits_selected'])
                    if manufacturing['powder_removal']['status'] != 'not_applicable':
                        self.assertGreaterEqual(manufacturing['powder_removal']['detected_separate_exit_count'],
                                                manufacturing['powder_removal']['required_exit_count'])
        self.assertLessEqual(reports[1]['voxel_mm'], reports[0]['voxel_mm'])

    def test_current_fine_exports_pass_roundtrip_thickness_and_footbed_checks(self):
        report = json.loads((ROOT / 'out/m3-export/report.json').read_text())
        self.assertTrue(report['manufacturing']['export_allowed'], report['manufacturing']['blockers'])
        self.assert_export_manifest(report['exports'])
        self.assertLessEqual(report['voxel_mm'], .5)
        original = _import_mesh(Path(report['exports']['stl']))
        printed = None
        try:
            health = mesh_health(original)
            self.assertEqual(health['status'], 'pass', health)
            self.assert_bounds_equal(health['bounds_mm'], report['mesh_health']['bounds_mm'], tolerance=1e-4)
            printed = _import_mesh(Path(report['exports']['print_stl']))
            printed_health = mesh_health(printed)
            self.assertEqual(printed_health['status'], 'pass', printed_health)
            print_bounds = self.assert_print_pose(coordinates(original), coordinates(printed), report)
            self.assert_bounds_equal(glb_bounds_mm(report['exports']['glb']), health['bounds_mm'], tolerance=.002)
            self.assert_bounds_equal(glb_bounds_mm(report['exports']['print_glb']), print_bounds, tolerance=.002)

            # Twice the 192k gate gives different area-stratum positions, using
            # the reimported binary STL rather than the mesh held during build.
            minimum = report['manufacturing']['profile']['minimum_wall_mm']
            thickness, thin = measure_thickness(original, minimum, samples=384000)
            print('M3 independent exported-STL thickness: ' + json.dumps({key: thickness[key] for key in
                  ('samples', 'unique_sampled_triangles', 'sampled_minimum_mm', 'thin_sample_count', 'missing_samples')}), flush=True)
            self.assertEqual(thickness['status'], 'pass', thickness)
            self.assertEqual(thickness['missing_samples'], 0)
            self.assertEqual(thin, [])

            self.assert_footbed_preserved(original, report['effective_params'])
        finally:
            bpy.data.meshes.remove(original)
            if printed is not None:
                bpy.data.meshes.remove(printed)

    def assert_footbed_preserved(self, shoe, params):
        source = load_last()
        last = None
        try:
            last, _ = deform_last(source, params)
            if params['foot_side'] == 'left':
                right = last
                last = mirror_mesh(right)
                bpy.data.meshes.remove(right)
            shoe_tree, last_tree = surface_tree(shoe), surface_tree(last)
            length = float(np.ptp(coordinates(last)[:, 1]))
            deviations = []
            for fraction in (.3, .4, .5):
                y = length * fraction
                for x in (-15, 0, 15):
                    with self.subTest(footbed_xy_mm=(x, y)):
                        reference, _, _, _ = last_tree.ray_cast(Vector((x, y, -200)), Vector((0, 0, 1)), 500)
                        self.assertIsNotNone(reference)
                        floor, _, _, _ = shoe_tree.ray_cast(Vector((x, y, reference.z + 2)), Vector((0, 0, -1)), 50)
                        self.assertIsNotNone(floor)
                        deviations.append(abs(floor.z - reference.z))
                        self.assertAlmostEqual(floor.z, reference.z, delta=1.25)
            self.assertEqual(len(deviations), 9)
            print(f'M3 exported-STL footbed: {len(deviations)} rays; maximum deviation {max(deviations):.6f} mm', flush=True)
        finally:
            if last is not None:
                bpy.data.meshes.remove(last)
            bpy.data.meshes.remove(source)


if __name__ == '__main__':
    if not unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])).wasSuccessful():
        raise SystemExit(1)
