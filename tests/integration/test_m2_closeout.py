"""Actual five-case M2 closeout, bound to the frozen appearance and CAD evidence."""
import json
import os
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from shoe_poc.acceptance import (CASES, closeout_test_provenance, file_binding,
                                 validate_m2_evidence)
from shoe_poc.reporting import sha256

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.real_last


def _read(path):
    return json.loads(path.read_text())


@pytest.fixture(scope='module')
def closeout(request):
    source = Path(os.environ.get('SHOE_POC_M2_COMPLETE_OUTPUT', ROOT/'output/m2_toe_complete')).resolve()
    directory = Path(os.environ.get('SHOE_POC_M2_CLOSEOUT_OUTPUT', ROOT/'output/m2_closeout')).resolve()
    approval = Path(os.environ.get('SHOE_POC_M2_APPEARANCE_ACCEPTANCE',
                                  ROOT/'docs/milestone2_lattice_acceptance.json')).resolve()
    required = [source/'execution_report.json', directory/'parameter_change_report.json', approval]
    required.extend(directory/'variants'/case/'verification_report.json' for case in sorted(CASES))
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        pytest.skip('Actual five-case M2 closeout is not generated: '+', '.join(missing))
    # Validate actual files before attaching any PASS provenance to this pytest run.
    result = validate_m2_evidence(source, directory, approval, project_root=ROOT)
    provenance = closeout_test_provenance(source, directory)
    request.config._shoe_m2_closeout_provenance = provenance
    yield SimpleNamespace(source=source, directory=directory, approval=approval, result=result,
                          parameters=_read(directory/'parameter_change_report.json'))
    assert closeout_test_provenance(source, directory) == provenance


@pytest.mark.parametrize('case', sorted(CASES))
def test_five_actual_cases_bind_exported_mesh_and_all_validation_checks(closeout, case):
    parameters = closeout.parameters
    assert set(parameters['runs']) == set(parameters['required_variants']) == set(parameters['completed_variants']) == CASES
    assert parameters['status'] == 'PASS' and not parameters.get('worker_errors')
    run = parameters['runs'][case]
    directory = closeout.directory/'variants'/case
    assert _read(directory/'verification_report.json') == run
    assert file_binding(directory/'lattice.stl') == {'sha256': run['lattice_sha256'], 'bytes': run['lattice_bytes']}
    assert run['worker']['process_start_method'] == 'spawn'
    assert run['worker']['pid'] > 0 and run['elapsed_seconds'] > 0
    assert run['primary_m2_run_id'] == closeout.result['source_run_id']
    assert set(run['validation']) == {'mesh', 'cad_samples', 'implicit_residual',
                                      'sole_coverage', 'principal_terminal_coverage'}
    assert all(check['geometry_status'] == 'PASS' for check in run['validation'].values())
    assert all(check['status'] == 'PASS' for check in run['validation']['mesh']['checks'])
    for name in ('lattice.stl', 'domain_provenance.json', 'gyroid_field.npz', 'validation_report.json'):
        assert run['source_artifact_hashes'][name] == sha256(closeout.source/name)
    if case not in ('current_primary', 'repeat'):
        change = parameters['parameter_geometry_changes'][case]
        assert change['different_stl_hash'] is True and abs(change['volume_delta_mm3']) > 1.


def test_threshold_045_keeps_strict_topology_cad_residual_and_terminal_checks(closeout):
    run = closeout.parameters['runs']['threshold_045']
    assert run['settings']['threshold'] == .45
    integrity = run['meshing']['integrity']
    assert integrity['status'] == 'PASS' and all(integrity['checks'].values())
    assert integrity['boundary_edges'] == integrity['nonmanifold_edges'] == integrity['singular_vertex_count'] == 0
    assert run['meshing']['allow_degenerate'] is True
    assert run['meshing']['mesh_repair_applied'] is False and run['meshing']['components_removed'] == 0
    native = run['validation']['cad_samples']
    assert native['sample_count'] > 0
    assert native['outside_sole_count'] == native['inside_cavity_count'] == native['outside_z_bounds_count'] == 0
    assert native['classification_errors'] == []
    assert native['native_line_probes']['geometry_status'] == 'PASS'
    residual = run['validation']['implicit_residual']
    assert residual['all_vertices_checked'] is True
    assert residual['sampled_vertices'] == residual['total_vertices'] > 0
    assert residual['vertices_outside_domain_tolerance'] == residual['cap_vertices_outside_gyroid_material'] == 0
    coverage = run['validation']['sole_coverage']
    assert coverage['measurement_errors'] == coverage['failed_bin_indices'] == []
    terminal = run['validation']['principal_terminal_coverage']
    assert terminal['eligible_sample_count'] > 0
    for name in ('missing_domain_samples', 'detached_domain_samples', 'measurement_errors',
                 'failed_terminal_planes', 'lattice_field_mismatch_planes'):
        assert terminal[name] == []
    assert terminal['mesh']['components_removed'] == 0
    expected = closeout.directory/'variants/threshold_045/terminal_expectations.json'
    assert terminal['expected_nodes_sha256'] == sha256(expected)


def test_repeat_compares_two_current_extractions_and_identical_actual_field(closeout):
    runs = closeout.parameters['runs']
    current, repeated = runs['current_primary'], runs['repeat']
    comparison = closeout.parameters['repeatability']
    assert comparison['reference_case'] == 'current_primary' and comparison['repeat_case'] == 'repeat'
    assert comparison['status'] == 'PASS'
    for key in ('identical_sampled_field', 'identical_exported_stl_sha256', 'identical_extraction_implementation'):
        assert comparison[key] is True
    first = closeout.directory/'variants/current_primary'
    second = closeout.directory/'variants/repeat'
    assert sha256(first/'lattice.stl') == sha256(second/'lattice.stl') == current['lattice_sha256'] == repeated['lattice_sha256']
    assert current['extraction_implementation'] == repeated['extraction_implementation']
    assert current['identical_to_frozen_primary_sampled_field'] is True
    assert repeated['identical_to_frozen_primary_sampled_field'] is True
    with (np.load(first/'gyroid_field.npz', allow_pickle=False) as one,
          np.load(second/'gyroid_field.npz', allow_pickle=False) as two,
          np.load(closeout.source/'gyroid_field.npz', allow_pickle=False) as frozen):
        for key in ('values', 'origin_mm', 'spacing_mm'):
            assert np.array_equal(one[key], two[key])
            assert np.array_equal(one[key], frozen[key])
        one_meta, two_meta, frozen_meta = (json.loads(str(saved['metadata_json'])) for saved in (one, two, frozen))
        for key in ('formula', 'clipping_formula', 'period_mm', 'threshold', 'phase_origin_mm',
                    'sampling_origin_mm', 'spacing_mm', 'shape_xyz', 'domain'):
            assert one_meta[key] == two_meta[key]
            assert one_meta[key] == frozen_meta[key]


def test_resolution_050_has_its_own_complete_native_cad_certification(closeout):
    directory = closeout.directory/'resolution_domain'
    metadata = _read(directory/'domain_metadata.json')
    provenance = _read(directory/'domain_provenance.json')
    primary = _read(closeout.source/'domain_provenance.json')
    run = closeout.parameters['runs']['resolution_0p5']
    assert metadata['spacing_mm'] == run['settings']['resolution_mm'] == .5
    assert provenance['run_id'] != primary['run_id']
    assert provenance['run_id'] == run['domain_sampling_run_id'] == _read(directory/'execution_report.json')['run_id']
    assert provenance == run['domain_provenance']
    assert provenance['baseline'] == primary['baseline']
    refinement = metadata['refinement']
    assert refinement['status'] == 'COMPLETE' and refinement['query_budget_reached'] is False
    assert 0 < refinement['native_distance_queries'] <= refinement['policy']['max_queries']
    assert refinement['native_probe_diagnostics']['measurement_errors'] == []
    assert provenance['domain_source']['mode'] == 'seed_plus_adaptive_CAD_certificates'
    for name in ('domain_field.npz', 'domain_metadata.json', 'domain_provenance.json', 'execution_report.json'):
        assert run['domain_artifact_hashes'][name] == sha256(directory/name)
    comparison = closeout.parameters['resolution_comparison']
    assert comparison['fixed_domain_convergence'] is False
    assert comparison['domain_changes_with_voxel_reserve'] is True


def test_frozen_approved_appearance_is_selected_and_all_historical_files_are_preserved(closeout):
    selected = closeout.result['selected_lattice']
    assert selected['path'] == str(closeout.source/'lattice.stl')
    assert {name: selected[name] for name in ('sha256', 'bytes')} == file_binding(closeout.source/'lattice.stl')
    approval = _read(closeout.approval)
    assert approval['files']['lattice.stl'] == file_binding(closeout.source/'lattice.stl')
    comparison = closeout.parameters['frozen_primary_geometry_difference']
    assert comparison['appearance_approval_transferred'] is False
    assert comparison['frozen_stl_sha256'] == selected['sha256']
    assert comparison['current_stl_sha256'] == closeout.parameters['runs']['current_primary']['lattice_sha256']
    assert closeout.result['preservation']['status'] == 'PASS'
    assert closeout.result['preservation']['checked_files'] > 0
    assert closeout.result['M3_gate'] == 'BLOCKED_PENDING_REGRESSION'
    assert closeout.result['manufacturing_status'] == closeout.result['global_minimum_wall_thickness'] == 'NOT_VERIFIED'
    assert closeout.result['physical_printing'] == 'NOT_TESTED'
    snapshot = _read(closeout.directory/'debug/preserved_artifacts_before.json')
    output = Path(closeout.result['baseline']['directory']).parent
    for folder, records in snapshot.items():
        assert {p.relative_to(output/folder).as_posix() for p in (output/folder).rglob('*') if p.is_file()} == set(records)
        for name, record in records.items():
            assert file_binding(output/folder/name) == record
