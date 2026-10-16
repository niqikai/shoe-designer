"""Actual native-certified completion, including the principal toe component."""
import json
import os
from pathlib import Path

import numpy as np
import pytest

from shoe_poc.lattice.pipeline import accepted_baseline
from shoe_poc.reporting import sha256
from shoe_poc.schemas import ShoeSpec

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.real_last


@pytest.fixture(scope='module')
def completed(request):
    directory = Path(os.environ.get('SHOE_POC_M2_COMPLETE_OUTPUT', ROOT/'output/m2_toe_complete'))
    if not (directory/'lattice_metadata.json').is_file():
        pytest.skip('Actual adaptive toe completion is not generated')
    execution = json.loads((directory/'execution_report.json').read_text())
    assert execution['status'] == 'PASS'
    assert accepted_baseline(Path(execution['baseline']['directory']), ShoeSpec.load(directory/'shoe_spec.json'),
                             Path(execution['baseline']['acceptance_file'])) == execution['baseline']
    files = ('lattice.stl', 'domain_field.npz', 'domain_metadata.json', 'gyroid_field.npz',
             'validation_report.json', 'terminal_coverage_report.json', 'terminal_comparison.png',
             'terminal_preview_report.json', 'repeatability_report.json',
             'debug/terminal_expectations.json', 'debug/previous_terminal_coverage_report.json',
             'debug/preserved_artifacts_before.json')
    request.config._shoe_terminal_provenance = {'directory': str(directory.resolve()),
        'run_id': execution['run_id'], 'files': {name: {'sha256': sha256(directory/name),
            'bytes': (directory/name).stat().st_size} for name in files}}
    return directory, execution


def test_adaptive_contract_completes_without_weakening_clearance_or_losing_seed(completed):
    directory, execution = completed
    meta = json.loads((directory/'domain_metadata.json').read_text())
    refinement = meta['refinement']
    assert refinement['status'] == 'COMPLETE' and not refinement['query_budget_reached']
    assert refinement['query_count'] <= refinement['policy']['max_queries']
    assert refinement['added_negative_node_count'] > 0
    assert meta['clearance_mm'] == 2.
    seed = Path(execution['domain_source']['seed_source']['directory'])
    with np.load(seed/'domain_field.npz', allow_pickle=False) as saved:
        old = saved['field_mm']
    with np.load(directory/'domain_field.npz', allow_pickle=False) as saved:
        new = saved['field_mm']
        assert old.shape == new.shape
        assert np.all(new <= old)
        assert np.all(new[old < 0] < 0)
        assert int(np.count_nonzero(new < 0)) == meta['certified_negative_node_count']


def test_actual_principal_component_covers_independent_eligible_toe_sites(completed):
    directory, _ = completed
    report = json.loads((directory/'terminal_coverage_report.json').read_text())
    assert report['geometry_status'] == 'PASS'
    assert report['global_coverage_status'] == 'NOT_VERIFIED'
    assert report['eligible_sample_count'] > 0
    for key in ('missing_domain_samples', 'detached_domain_samples', 'measurement_errors',
                'failed_terminal_planes', 'lattice_field_mismatch_planes'):
        assert report[key] == []
    assert report['mesh']['components_removed'] == 0
    assert report['expected_nodes_sha256'] == sha256(directory/'debug/terminal_expectations.json')
    assert report['lattice_sha256'] == sha256(directory/'lattice.stl')
    required = [row for row in report['terminal_planes'] if row['expected_lattice_negative_node_count'] > 0]
    assert required and all(row['principal_stl_crossing_triangle_count'] > 0 for row in required)
    assert report['mesh']['principal_bounds_mm'][1][0] > max(row['x_mm'] for row in required)


def test_previous_shards_are_rejected_by_the_same_native_expectations(completed):
    directory, _ = completed
    previous = json.loads((directory/'debug/previous_terminal_coverage_report.json').read_text())
    current = json.loads((directory/'terminal_coverage_report.json').read_text())
    assert previous['geometry_status'] == 'FAIL'
    assert previous['missing_domain_samples'] and previous['failed_terminal_planes']
    assert previous['expected_nodes_sha256'] == current['expected_nodes_sha256']
    assert current['mesh']['principal_bounds_mm'][1][0] > previous['mesh']['principal_bounds_mm'][1][0]+1.2


def test_actual_mesh_closure_cavity_and_field_checks_still_pass(completed):
    directory, _ = completed
    validation = json.loads((directory/'validation_report.json').read_text())
    assert validation['geometry_status'] == 'PASS'
    for key in ('mesh', 'native_domain_samples', 'gyroid_residual', 'sole_coverage'):
        assert validation[key]['geometry_status'] == 'PASS'
    assert validation['native_domain_samples']['outside_sole_count'] == 0
    assert validation['native_domain_samples']['inside_cavity_count'] == 0
    assert validation['manufacturing_status'] == 'NOT_VERIFIED'
    repeated = json.loads((directory/'repeatability_report.json').read_text())
    assert repeated['geometry_status'] == 'PASS'
    assert repeated['identical_stl_sha256'] is True
    assert repeated['primary_stl_sha256'] == sha256(directory/'lattice.stl')


def test_preserved_models_and_component_colored_preview_bind_actual_artifacts(completed):
    directory, execution = completed
    root = Path(execution['baseline']['directory']).parent
    before = json.loads((directory/'debug/preserved_artifacts_before.json').read_text())
    for folder, files in before.items():
        for name, record in files.items():
            path = root/folder/name
            assert sha256(path) == record['sha256'] and path.stat().st_size == record['bytes']
    preview = json.loads((directory/'terminal_preview_report.json').read_text())
    assert preview['new_lattice_sha256'] == sha256(directory/'lattice.stl')
    assert preview['image_sha256'] == sha256(directory/'terminal_comparison.png')
    assert preview['components_deleted'] == 0 and preview['mesh_smoothing'] is False
