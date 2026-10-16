"""Replay the curved-toe repair and the unchanged historical artifacts."""
import json
import os
from pathlib import Path

import numpy as np
import pytest

from shoe_poc.lattice.envelope import SoleEnvelope
from shoe_poc.reporting import sha256

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.real_last


@pytest.fixture(scope='module')
def repair(request):
    directory = Path(os.environ.get('SHOE_POC_M2_REPAIR_OUTPUT', ROOT/'output/m2_toe_repair'))
    if not (directory/'lattice_metadata.json').is_file():
        pytest.skip('Real curved-toe repair has not been generated')
    execution = json.loads((directory/'execution_report.json').read_text())
    assert execution['status'] == 'PASS'
    request.config._shoe_toe_repair_provenance = {
        'directory': str(directory.resolve()), 'run_id': execution['run_id'],
        'files': {name: {'sha256': sha256(directory/name), 'bytes': (directory/name).stat().st_size}
                  for name in ('toe_coverage_report.json', 'toe_comparison.png',
                               'debug/m1_artifacts_before.json', 'debug/m2_artifacts_before.json')}}
    return directory, execution


def test_repaired_domain_has_real_material_beyond_old_toe_and_below_curved_roof(repair):
    directory, execution = repair
    domain = json.loads((directory/'domain_metadata.json').read_text())
    envelope = SoleEnvelope.from_metadata(domain['envelope'])
    base = Path(execution['baseline']['directory'])
    assert envelope.metadata['source_geometry_report_sha256'] == sha256(base/'geometry_report.json')
    spec = json.loads((directory/'shoe_spec.json').read_text())
    frozen = json.loads((base/'shoe_spec.json').read_text())
    assert all(spec[key] == frozen[key] for key in ('last', 'shoe', 'upper', 'sole', 'manufacturing'))
    old = json.loads((ROOT/'output/m2/validation_report.json').read_text())
    old_front = old['mesh']['bounds_mm'][1][0]
    with np.load(directory/'domain_field.npz', allow_pickle=False) as saved:
        field, origin, spacing = saved['field_mm'], saved['origin_mm'], float(saved['spacing_mm'])
        flat = np.flatnonzero(field < 0)
        ranks = np.unique(np.linspace(0, len(flat)-1, min(4096, len(flat)), dtype=int))
        indices = np.array(np.unravel_index(flat[ranks], field.shape)).T
        points = origin+indices*spacing
        assert np.all(envelope.contains(points))
        assert np.any((points[:, 0] > old_front+5) & (points[:, 2] > 0))


def test_native_coverage_includes_required_toe_bins_and_keeps_global_proof_unknown(repair):
    directory, _ = repair
    validation = json.loads((directory/'validation_report.json').read_text())
    coverage = validation['sole_coverage']
    assert validation['geometry_status'] == coverage['geometry_status'] == 'PASS'
    assert coverage['global_coverage_status'] == 'NOT_VERIFIED'
    assert coverage['measurement_errors'] == coverage['failed_bin_indices'] == []
    required = [row for row in coverage['bins'] if row['coverage_required']]
    assert required and all(row['status'] == 'PASS' for row in required)
    front = coverage['bounds_mm'][1][0]
    assert required[-1]['x_bounds_mm'][1] >= front-2*coverage['period_mm']
    for row in required:
        assert row['domain_support_fraction'] >= coverage['minimum_required_domain_fraction']
        assert row['lattice_support_fraction'] >= coverage['minimum_required_lattice_fraction']


def test_same_curved_cad_check_rejects_old_slab_and_measures_new_raised_toe(repair):
    directory, execution = repair
    report = json.loads((directory/'toe_coverage_report.json').read_text())
    assert report['primary_lattice_sha256'] == sha256(directory/'lattice.stl')
    assert report['source_domain_field_sha256'] == sha256(directory/'domain_field.npz')
    assert report['reference_shoe_sha256'] == sha256(Path(execution['baseline']['directory'])/'base_shoe.step')
    assert report['geometry_status'] == report['old_slab_regression_status'] == 'PASS'
    assert report['same_expected_CAD_domain_for_both_models'] is True
    old, new = report['old_lattice'], report['new_lattice']
    assert old['geometry_status'] == 'FAIL' and old['failed_bin_indices']
    assert new['geometry_status'] == 'PASS' and not new['failed_bin_indices']
    assert new['raised_coverage_required_bin_count'] > 0
    assert new['measurement_errors'] == []
    for row in new['bins']:
        if row['raised_coverage_required']:
            assert row['raised_domain_support_fraction'] >= new['minimum_required_domain_fraction']
            assert row['raised_lattice_support_fraction'] >= new['minimum_required_lattice_fraction']
    assert new['actual_mesh_bounds_mm'][1][0]-old['actual_mesh_bounds_mm'][1][0] > new['period_mm']
    assert new['global_coverage_status'] == 'NOT_VERIFIED'


@pytest.mark.parametrize('snapshot,folder', [
    ('m1_artifacts_before.json', None), ('m2_artifacts_before.json', 'm2')])
def test_historical_geometry_and_evidence_remain_byte_identical(repair, snapshot, folder):
    directory, execution = repair
    root = Path(execution['baseline']['directory']).parent
    before = json.loads((directory/'debug'/snapshot).read_text())
    groups = before if folder is None else {folder: before}
    assert sum(map(len, groups.values())) > 0
    for name, records in groups.items():
        for relative, record in records.items():
            path = root/name/relative
            assert path.stat().st_size == record['bytes'], str(path)
            assert sha256(path) == record['sha256'], str(path)
