"""Replay real M2 files and preserve the previously accepted M1 evidence."""
from pathlib import Path
import json
import os

import numpy as np
import pytest
import trimesh

from shoe_poc.lattice.pipeline import accepted_baseline
from shoe_poc.reporting import sha256
from shoe_poc.schemas import ShoeSpec

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.real_last


@pytest.fixture(scope='module')
def actual_m2(request):
    directory = Path(os.environ.get('SHOE_POC_M2_OUTPUT', ROOT/'output/m2'))
    if not (directory/'lattice_metadata.json').is_file():
        pytest.skip('Actual M2 artifacts unavailable; real lattice replay NOT_TESTED')
    execution = json.loads((directory/'execution_report.json').read_text())
    assert execution['status'] == 'PASS'
    for name, record in execution['outputs'].items():
        assert sha256(directory/name) == record['sha256'], name
        assert (directory/name).stat().st_size == record['bytes'], name
    request.config._shoe_lattice_provenance = {
        'directory': str(directory.resolve()), 'run_id': execution['run_id'],
        'files': {name: {'sha256': sha256(directory/name), 'bytes': (directory/name).stat().st_size}
                  for name in ('lattice.stl', 'gyroid_field.npz', 'domain_field.npz', 'lattice_metadata.json',
                               'validation_report.json', 'parameter_change_report.json', 'parameter_sections.png')},
        'accepted_m1_run_id': execution['baseline']['accepted_run_id']}
    return directory, execution


def test_actual_real_lattice_is_closed_and_shell_orientation_matches_nesting(actual_m2):
    directory, execution = actual_m2
    mesh = trimesh.load_mesh(directory/'lattice.stl', process=False)
    mesh.merge_vertices(digits_vertex=12)
    report = json.loads((directory/'lattice_metadata.json').read_text())
    assert mesh.is_watertight and mesh.is_winding_consistent
    assert mesh.volume > 0 and np.isfinite(mesh.vertices).all()
    assert len(mesh.faces) == report['meshing']['integrity']['triangles']
    incidence = np.bincount(mesh.edges_unique_inverse)
    assert np.all(incidence == 2)
    assert np.all(mesh.area_faces > 1e-12)
    assert report['meshing']['components_removed'] == 0
    assert not report['meshing']['mesh_repair_applied']
    assert report['validation']['mesh']['shell_nesting']['geometry_status'] == 'PASS'
    assert report['validation']['mesh']['shell_nesting']['orientation_violation_count'] == 0
    assert report['validation']['mesh']['shell_nesting']['cross_shell_intersection_status'] == 'PASS'
    assert execution['milestone_status'] == 'PARTIAL'
    assert execution['user_acceptance'] == 'PENDING'


def test_actual_field_clipping_and_independent_reports_preserve_unknowns(actual_m2):
    directory, _ = actual_m2
    with np.load(directory/'gyroid_field.npz', allow_pickle=False) as saved:
        field = saved['values']
        meta = json.loads(str(saved['metadata_json']))
        assert field.min() < 0 < field.max()
        assert field.shape == tuple(meta['shape_xyz'])
        assert all(np.all(np.take(field, (0,-1), axis=i)>0) for i in range(3))
        with np.load(directory/'domain_field.npz', allow_pickle=False) as domain:
            assert np.all(field >= (domain['field_mm']+meta['cap_inset_mm'])/float(saved['spacing_mm'])-1e-5)
            assert not np.any((field<0) & (domain['field_mm']>=0))
    checks = json.loads((directory/'validation_report.json').read_text())
    assert checks['geometry_status'] == 'PASS'
    assert checks['native_domain_samples']['outside_sole_count'] == 0
    assert checks['native_domain_samples']['inside_cavity_count'] == 0
    assert checks['native_domain_samples']['outside_z_bounds_count'] == 0
    assert checks['native_domain_samples']['classification_errors'] == []
    assert checks['native_domain_samples']['global_containment_status'] == 'NOT_VERIFIED'
    assert checks['gyroid_residual']['interior']['sample_count'] > 0
    assert checks['manufacturing_status'] == 'NOT_VERIFIED'
    assert checks['M3_shell_fusion'] == 'NOT_IMPLEMENTED'


def test_actual_parameters_change_stl_and_same_domain_repeats_exactly(actual_m2):
    directory, execution = actual_m2
    report = json.loads((directory/'parameter_change_report.json').read_text())
    assert report['status'] == 'PASS'
    assert report['primary_run_id'] == execution['run_id']
    assert report['primary_stl_sha256'] == sha256(directory/'lattice.stl')
    assert report['repeatability']['identical_exported_stl_sha256']
    for name, run in report['runs'].items():
        path = directory/'variants'/name/'lattice.stl'
        assert run['status'] == 'PASS'
        assert run['lattice_sha256'] == sha256(path)
        assert run['lattice_bytes'] == path.stat().st_size
        assert json.loads((path.parent/'verification_report.json').read_text()) == run
    assert report['runs']['repeat']['lattice_sha256'] == sha256(directory/'lattice.stl')
    assert 'period_16' in report['parameter_geometry_changes']
    assert any(name.startswith('threshold_') for name in report['parameter_geometry_changes'])
    for name, change in report['parameter_geometry_changes'].items():
        assert change['different_stl_hash'] and abs(change['volume_delta_mm3']) > 1
        run = report['runs'][name]
        assert run['lattice_sha256'] == sha256(directory/'variants'/name/'lattice.stl')
        assert run['validation']['mesh']['geometry_status'] == 'PASS'
        assert run['validation']['cad_samples']['geometry_status'] == 'PASS'
        assert run['validation']['implicit_residual']['geometry_status'] == 'PASS'


def test_actual_m1_frozen_artifacts_are_unchanged(actual_m2):
    directory, execution = actual_m2
    baseline = execution['baseline']
    assert accepted_baseline(Path(baseline['directory']), ShoeSpec.load(directory/'shoe_spec.json'),
                             Path(baseline['acceptance_file'])) == baseline
    before = json.loads((directory/'debug/m1_artifacts_before.json').read_text())
    count = 0
    for folder, records in before.items():
        for name, record in records.items():
            path = Path(baseline['directory']).parent/folder/name
            assert path.is_file() and sha256(path) == record['sha256'], str(path)
            assert path.stat().st_size == record['bytes']
            count += 1
    assert count > 100


def test_actual_numeric_contour_is_bounded_and_raw_field_keeps_requested_threshold(actual_m2):
    from shoe_poc.lattice.gyroid import gyroid_values
    directory, _ = actual_m2
    report = json.loads((directory/'parameter_change_report.json').read_text())
    name, = [name for name in report['runs'] if name.startswith('threshold_')]
    run = report['runs'][name]
    meshing = run['meshing']
    assert meshing['target_level'] == 0
    # Whether float32 export needs an inward level depends on the actual
    # domain. Both exact-zero extraction and a bounded recorded retry qualify.
    assert meshing['level'] <= 0
    assert meshing['level'] >= -128*np.finfo(np.float32).eps*meshing['numerical_field_scale']
    assert meshing['effective_gyroid_threshold'] == run['settings']['threshold']+meshing['level']
    assert meshing['numerical_attempts'][0]['level'] == 0
    assert meshing['numerical_attempts'][0]['status'] == ('FAIL' if meshing['level'] < 0 else 'PASS')
    assert meshing['numerical_attempts'][-1]['status'] == 'PASS'
    assert meshing['mesh_repair_applied'] is False and meshing['components_removed'] == 0
    with np.load(directory/'variants'/name/'gyroid_field.npz', allow_pickle=False) as saved:
        field, origin, spacing = saved['values'], saved['origin_mm'], float(saved['spacing_mm'])
        meta = json.loads(str(saved['metadata_json']))
        assert meta['threshold'] == run['settings']['threshold']
        ranks = np.linspace(0, field.size-1, 4096, dtype=int)
        indices = np.array(np.unravel_index(ranks, field.shape)).T
        points = origin+indices*spacing
        with np.load(directory/'domain_field.npz', allow_pickle=False) as domain:
            expected = np.maximum(np.abs(gyroid_values(points, meta['period_mm'], meta['phase_origin_mm']))-meta['threshold'],
                (domain['field_mm'][tuple(indices.T)]+meta['cap_inset_mm'])/spacing)
        assert np.allclose(field[tuple(indices.T)], expected, rtol=0, atol=2e-7)
