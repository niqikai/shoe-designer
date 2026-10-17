"""Closeout orchestration must preserve source evidence and honest acceptance scope."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from shoe_poc.reporting import sha256

_SCRIPT = Path(__file__).resolve().parents[2]/'scripts/verify_lattice.py'
_MODULE_SPEC = importlib.util.spec_from_file_location('verify_lattice_closeout', _SCRIPT)
verify = importlib.util.module_from_spec(_MODULE_SPEC)
_MODULE_SPEC.loader.exec_module(verify)


def _domain(tmp_path):
    directory = tmp_path/'domain'
    directory.mkdir()
    policy = {'toe_start_fraction': .925, 'coverage_tolerance_mm': .1, 'max_queries': 30000}
    refinement = {'status': 'COMPLETE', 'query_budget_reached': False, 'policy': policy}
    metadata = {'spacing_mm': .5, 'sole_z_bounds_mm': [-2., 10.], 'envelope': {'version': 'test'},
                'refinement': refinement}
    baseline = {'accepted_run_id': 'm1'}
    spec = SimpleNamespace(lattice=SimpleNamespace(domain=SimpleNamespace(clearance_mm=2., bounds_mm=None,
        refinement=SimpleNamespace(model_dump=lambda **_: policy))))
    source = {'mode': 'seed_plus_adaptive_CAD_certificates', 'run_id': 'adaptive',
              'seed_source': {'mode': 'fresh_CAD_sampling', 'run_id': 'adaptive'}, 'refinement': refinement}
    (directory/'domain_field.npz').write_bytes(b'field-hash-input')
    (directory/'domain_metadata.json').write_text(json.dumps(metadata))
    provenance = {'run_id': 'adaptive', 'baseline': baseline,
        'parameters': {'spacing_mm': .5, 'clearance_mm': 2., 'bounds_mm': None,
                       'sole_z_bounds_mm': [-2., 10.], 'envelope': metadata['envelope'], 'refinement': policy},
        'domain_field_sha256': sha256(directory/'domain_field.npz'),
        'domain_metadata_sha256': sha256(directory/'domain_metadata.json'), 'domain_source': source}
    (directory/'domain_provenance.json').write_text(json.dumps(provenance))
    return directory, baseline, spec, provenance


def test_output_rejects_source_tree_alias_and_historical_outputs(tmp_path):
    source = tmp_path/'complete'
    source.mkdir()
    alias = tmp_path/'alias'
    alias.symlink_to(source, target_is_directory=True)
    for output in (source, source/'variants', tmp_path, alias, tmp_path/'m2_toe_complete'):
        with pytest.raises(ValueError):
            verify._output_root(source, output)
    assert verify._output_root(source, tmp_path/'m2_closeout') == tmp_path/'m2_closeout'


def test_resolution_domain_is_read_only_sibling_of_variant_outputs(tmp_path):
    target, primary = tmp_path/'m2_closeout', tmp_path/'complete'
    domain = target/'resolution_domain'
    path = verify._variant_output(target, 'resolution_0p5', (primary, domain))
    assert path == target/'variants/resolution_0p5'
    path.mkdir(parents=True)
    (path/'terminal_expectations.json').symlink_to(domain/'domain_metadata.json')
    with pytest.raises(ValueError, match='symbolic link'):
        verify._variant_output(target, 'resolution_0p5', (primary, domain))


def test_variant_directory_symlink_cannot_escape_into_frozen_source(tmp_path):
    target, primary = tmp_path/'m2_closeout', tmp_path/'complete'
    (target/'variants').mkdir(parents=True)
    primary.mkdir()
    (target/'variants/repeat').symlink_to(primary, target_is_directory=True)
    with pytest.raises(ValueError, match='alias escapes'):
        verify._variant_output(target, 'repeat', (primary,))


def test_final_adaptive_domain_identifies_its_own_run_and_policy(tmp_path):
    directory, baseline, spec, provenance = _domain(tmp_path)
    actual = verify._bound_domain_provenance(directory, baseline, spec)
    assert actual['run_id'] == 'adaptive'
    provenance['parameters']['refinement'] = None
    (directory/'domain_provenance.json').write_text(json.dumps(provenance))
    with pytest.raises(ValueError, match='actual grid'):
        verify._bound_domain_provenance(directory, baseline, spec)


def test_resolution_domain_may_survive_later_meshing_failure(tmp_path):
    directory, baseline, spec, provenance = _domain(tmp_path)
    execution = {'status': 'FAIL', 'run_id': 'adaptive', 'baseline': baseline,
                 'domain_source': provenance['domain_source']}
    assert verify._bound_domain_provenance(directory, baseline, spec,
        execution=execution, require_execution_pass=False) == provenance
    with pytest.raises(ValueError, match='execution'):
        verify._bound_domain_provenance(directory, baseline, spec, execution=execution)


def test_incomplete_native_certification_is_rejected_even_if_stl_failed_later(tmp_path):
    directory, baseline, spec, provenance = _domain(tmp_path)
    provenance['domain_source']['refinement']['query_budget_reached'] = True
    (directory/'domain_provenance.json').write_text(json.dumps(provenance))
    with pytest.raises(ValueError, match='complete final-domain'):
        verify._bound_domain_provenance(directory, baseline, spec)


def test_stale_domain_hash_and_fake_adaptive_seed_are_rejected(tmp_path):
    directory, baseline, spec, provenance = _domain(tmp_path)
    (directory/'domain_field.npz').write_bytes(b'changed')
    with pytest.raises(ValueError, match='hash changed'):
        verify._bound_domain_provenance(directory, baseline, spec)
    provenance['domain_field_sha256'] = sha256(directory/'domain_field.npz')
    provenance['domain_source']['seed_source'] = {}
    (directory/'domain_provenance.json').write_text(json.dumps(provenance))
    with pytest.raises(ValueError, match='identified native'):
        verify._bound_domain_provenance(directory, baseline, spec)


def test_technical_pass_cannot_imply_visual_or_milestone_acceptance():
    partial = verify._closeout_acceptance('PASS', False)
    assert partial['technical_parameter_status'] == 'NOT_VERIFIED'
    assert partial['parameter_set_complete'] is False
    external = {'path': '/bound/appearance.json', 'sha256': 'abc'}
    complete = verify._closeout_acceptance('PASS', True, external)
    assert complete['technical_parameter_status'] == 'PASS'
    assert complete['user_appearance_acceptance'] == external
    assert 'M2_overall_status' not in complete and 'M3_entry_gate' not in complete


def test_field_rejects_external_metadata_mismatch_and_nonfinite_grid(tmp_path):
    metadata = {'shape_xyz': [3, 3, 3], 'spacing_mm': .5, 'origin_mm': [0., 0., 0.],
                'estimated_peak_bytes': 4096}
    field = np.ones((3, 3, 3), dtype=np.float32)
    field[1, 1, 1] = np.nan
    np.savez_compressed(tmp_path/'domain_field.npz', field_mm=field, spacing_mm=.5,
                        origin_mm=[0., 0., 0.], metadata_json=np.asarray(json.dumps(metadata)))
    (tmp_path/'domain_metadata.json').write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match='same grid'):
        verify.load_domain(tmp_path)


def test_actual_pipeline_failure_payload_does_not_invalidate_complete_domain(tmp_path):
    directory, baseline, spec, provenance = _domain(tmp_path)
    failure = {'milestone': 2, 'run_id': 'adaptive', 'status': 'FAIL', 'milestone_status': 'PARTIAL',
               'outputs': {}, 'error': 'later meshing failed'}
    assert verify._bound_domain_provenance(directory, baseline, spec,
        execution=failure, require_execution_pass=False) == provenance
    failure['run_id'] = 'another-run'
    with pytest.raises(ValueError, match='execution'):
        verify._bound_domain_provenance(directory, baseline, spec,
            execution=failure, require_execution_pass=False)


def test_repeat_field_uses_exact_values_and_physical_metadata_not_elapsed_time():
    values = np.arange(8, dtype=np.float32).reshape((2, 2, 2))
    metadata = {'period_mm': 12., 'threshold': .6, 'phase_origin_mm': [0., 0., 0.],
                'sampling_elapsed_seconds': 1., 'maximum_memory_bytes': 1024}
    saved = {'values': values, 'origin_mm': np.array([0., 0., 0.]), 'spacing_mm': np.array(.4),
             'metadata_json': np.asarray(json.dumps(metadata))}
    field = SimpleNamespace(values=values.copy(), origin_mm=(0., 0., 0.), spacing_mm=.4,
        metadata={**metadata, 'sampling_elapsed_seconds': 2., 'maximum_memory_bytes': 2048})
    assert verify._same_sampled_field(saved, field)
    field.metadata['threshold'] = .45
    assert not verify._same_sampled_field(saved, field)
    field.metadata['threshold'] = .6
    field.values[0, 0, 0] = 1.
    assert not verify._same_sampled_field(saved, field)


def _saved_case(path, *, stl=b'current-algorithm-STL', value=0., elapsed=1.):
    path.mkdir(parents=True, exist_ok=True)
    values = np.ones((2, 2, 2), np.float32)
    values[0, 0, 0] = value
    np.savez_compressed(path/'gyroid_field.npz', values=values, origin_mm=[0., 0., 0.], spacing_mm=.4,
        metadata_json=np.asarray(json.dumps({'period_mm': 12., 'threshold': .6, 'sampling_elapsed_seconds': elapsed})))
    (path/'lattice.stl').write_bytes(stl)
    return path


def test_five_case_contract_does_not_treat_missing_resolution_as_complete():
    four = {'current_primary', 'period_16', 'threshold_045', 'repeat'}
    assert verify.CLOSEOUT_CASES-four == {'resolution_0p5'}
    partial = verify._closeout_acceptance('PASS', four == verify.CLOSEOUT_CASES)
    assert partial['technical_parameter_status'] == 'NOT_VERIFIED'


def test_repeat_reference_is_two_current_runs_not_frozen_historical_stl(tmp_path):
    frozen = tmp_path/'frozen.stl'
    frozen.write_bytes(b'historical-extractor-STL-with-different-connectivity')
    current = _saved_case(tmp_path/'current_primary')
    repeated = _saved_case(tmp_path/'repeat', elapsed=2.)
    assert sha256(frozen) != sha256(current/'lattice.stl')
    result = verify._repeat_comparison(current, repeated)
    assert result['status'] == 'PASS'
    assert result['reference_case'] == 'current_primary'
    assert result['identical_sampled_field'] and result['identical_exported_stl_sha256']
    assert result['reference_field_sha256'] != result['repeated_field_sha256']


@pytest.mark.parametrize('field_change,stl_change', [(True, False), (False, True)])
def test_current_repeat_must_match_both_actual_field_and_stl(tmp_path, field_change, stl_change):
    current = _saved_case(tmp_path/'current_primary')
    repeated = _saved_case(tmp_path/'repeat', value=1. if field_change else 0.,
        stl=b'different-current-STL' if stl_change else b'current-algorithm-STL')
    result = verify._repeat_comparison(current, repeated)
    assert result['status'] == 'FAIL'
    assert result['identical_sampled_field'] is not field_change
    assert result['identical_exported_stl_sha256'] is not stl_change


def test_repeat_rejects_different_actual_extraction_level_even_with_equal_stl(tmp_path):
    current, repeated = _saved_case(tmp_path/'first'), _saved_case(tmp_path/'second')
    result = verify._repeat_comparison(current, repeated,
        reference_implementation={'actual_level': 0.}, repeated_implementation={'actual_level': -1e-5})
    assert result['status'] == 'FAIL'
    assert not result['identical_extraction_implementation']


def test_current_frozen_difference_measures_actual_bounds_volume_and_both_directions(tmp_path):
    import trimesh
    frozen = trimesh.creation.box(extents=[2., 4., 6.])
    current = frozen.copy()
    current.apply_translation([.01, 0., 0.])
    frozen.export(tmp_path/'frozen.stl')
    current.export(tmp_path/'current.stl')
    result = verify._frozen_geometry_difference(tmp_path/'frozen.stl', tmp_path/'current.stl')
    assert result['frozen_volume_mm3'] == pytest.approx(48.)
    assert result['volume_delta_mm3'] == pytest.approx(0., abs=1e-4)
    assert np.asarray(result['bounds_delta_mm'])[:, 0] == pytest.approx([.01, .01], abs=1e-6)
    assert result['symmetric_nearest_vertex_minimum_mm'] == pytest.approx(.01, abs=1e-6)
    assert result['symmetric_nearest_vertex_maximum_mm'] == pytest.approx(.01, abs=1e-6)
    assert result['frozen_to_current_nearest_vertex']['vertex_count'] == 8
    assert result['current_to_frozen_nearest_vertex']['vertex_count'] == 8
    assert result['appearance_approval_transferred'] is False


def test_extraction_implementation_explicitly_versions_actual_policy_and_level():
    metadata = {'method': 'scikit-image Lewiner Marching Cubes', 'level': -1e-5,
        'target_level': 0., 'allow_degenerate': True, 'connectivity_policy': 'retain raw Lewiner identities',
        'mesh_repair_applied': False, 'components_removed': 0,
        'numerical_level_inset_dimensionless': 1e-5, 'numerical_domain_inset_mm': 4e-6}
    result = verify._extraction_implementation(metadata)
    assert result['algorithm_contract_version'] == 'lewiner-raw-connectivity-bounded-inward-v2'
    assert result['actual_level'] == -1e-5 and result['allow_degenerate'] is True
    assert len(result['implementation_sha256']) == 64
    metadata['allow_degenerate'] = False
    with pytest.raises(ValueError, match='raw-connectivity'):
        verify._extraction_implementation(metadata)


def test_four_case_partial_then_resolution_only_resume_preserves_frozen_inputs(tmp_path, monkeypatch):
    """Exercise real planning, hash-bound reuse and aggregation without native CAD."""
    from concurrent.futures import Future
    primary_parent, alt_parent = tmp_path/'primary', tmp_path/'alternative'
    primary_parent.mkdir()
    alt_parent.mkdir()
    primary, baseline, spec, provenance = _domain(primary_parent)
    alternative, _, _, alt_provenance = _domain(alt_parent)
    baseline.update(directory=str(tmp_path/'m1'), acceptance_file=str(tmp_path/'acceptance.json'))
    spec.lattice.period_mm, spec.lattice.threshold, spec.lattice.phase_origin_mm = 12., .6, [0., 0., 0.]
    for directory, proof, spacing, run_id in ((primary, provenance, .4, 'primary-run'),
                                               (alternative, alt_provenance, .5, 'resolution-run')):
        metadata = json.loads((directory/'domain_metadata.json').read_text())
        metadata.update(spacing_mm=spacing, computation_bounds_mm=[[0., 0., -2.], [10., 10., 10.]], clearance_mm=2.)
        (directory/'domain_metadata.json').write_text(json.dumps(metadata))
        proof.update(baseline=baseline, run_id=run_id, domain_metadata_sha256=sha256(directory/'domain_metadata.json'))
        proof['parameters']['spacing_mm'] = spacing
        proof['domain_source']['run_id'] = run_id
        proof['domain_source']['seed_source']['run_id'] = run_id
        (directory/'domain_provenance.json').write_text(json.dumps(proof))
    for name in ('shoe_spec.json', 'validation_report.json', 'gyroid_field.npz', 'lattice.stl'):
        (primary/name).write_bytes(b'frozen historical source '+name.encode())
    (primary/'lattice_metadata.json').write_text(json.dumps({'meshing': {'integrity': {'volume_mm3': 10., 'triangles': 10}}}))
    files = ('domain_field.npz', 'domain_metadata.json', 'domain_provenance.json', 'shoe_spec.json',
             'lattice_metadata.json', 'lattice.stl', 'gyroid_field.npz', 'validation_report.json')
    execution = {'status': 'PASS', 'run_id': 'primary-run', 'baseline': baseline,
                 'domain_source': provenance['domain_source'],
                 'outputs': {name: {'sha256': sha256(primary/name)} for name in files}}
    (primary/'execution_report.json').write_text(json.dumps(execution))
    frozen_hashes = {name: sha256(path) for name in (*files, 'execution_report.json') if (path := primary/name).is_file()}
    monkeypatch.setattr(verify.ShoeSpec, 'load', lambda path: spec)
    monkeypatch.setattr(verify, 'accepted_baseline', lambda *args: baseline)
    def load(directory):
        metadata = json.loads((directory/'domain_metadata.json').read_text())
        spacing = metadata['spacing_mm']
        return np.ones((3, 3, 3), np.float32), verify.GridPlan(tuple(np.arange(3)*spacing for _ in range(3)), spacing, 4096), metadata
    monkeypatch.setattr(verify, 'load_domain', load)
    monkeypatch.setattr(verify, '_frozen_geometry_difference', lambda *args: {'appearance_approval_transferred': False})
    executed = []
    def worker(task):
        executed.append(task['name'])
        path = Path(task['destination'])/'variants'/task['name']
        _saved_case(path, stl=b'new-primary' if task['name'] in ('current_primary', 'repeat') else task['name'].encode())
        (path/'terminal_expectations.json').write_text('{}')
        meshing = {'method': 'scikit-image Lewiner Marching Cubes', 'level': 0., 'allow_degenerate': True,
            'connectivity_policy': 'retain raw Lewiner identities', 'mesh_repair_applied': False, 'components_removed': 0,
            'integrity': {'volume_mm3': 10. if task['name'] in ('current_primary', 'repeat') else 20., 'triangles': 10}}
        metadata = json.loads((Path(task['domain_source'])/'domain_metadata.json').read_text())
        report = {'name': task['name'], 'status': 'PASS', 'primary_m2_run_id': task['primary_m2_run_id'],
            'frozen_baseline': baseline, 'domain_field_sha256': task['domain_hashes']['domain_field.npz'],
            'domain_metadata_sha256': task['domain_hashes']['domain_metadata.json'],
            'settings': {'period_mm': task['period'], 'threshold': task['threshold'],
                         'resolution_mm': metadata['spacing_mm'], 'phase_origin_mm': [0., 0., 0.]},
            'validation': {'principal_terminal_coverage': {'geometry_status': 'PASS'}},
            'lattice_sha256': sha256(path/'lattice.stl'), 'lattice_bytes': (path/'lattice.stl').stat().st_size,
            'gyroid_field_sha256': sha256(path/'gyroid_field.npz'),
            'terminal_expectations_sha256': sha256(path/'terminal_expectations.json'),
            'source_artifact_hashes': task['primary_hashes'], 'domain_artifact_hashes': task['domain_hashes'],
            'validator_code_hashes': task['validator_code_hashes'], 'domain_provenance': task['domain_provenance'],
            'domain_sampling_run_id': task['domain_sampling_run_id'], 'closeout_report_version': verify.CLOSEOUT_REPORT_VERSION,
            'extraction_implementation': verify._extraction_implementation(meshing), 'meshing': meshing}
        (path/'verification_report.json').write_text(json.dumps(report))
        return report
    class Pool:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def submit(self, fn, task):
            future = Future()
            future.set_result(fn(task))
            return future
    monkeypatch.setattr(verify, '_verify_variant', worker)
    monkeypatch.setattr(verify, 'ProcessPoolExecutor', Pool)
    target = tmp_path/'m2_closeout'
    with pytest.raises(RuntimeError, match='did not qualify'):
        verify.verify(primary, None, destination=target, closeout=True)
    partial = json.loads((target/'parameter_change_report.json').read_text())
    assert partial['status'] == 'NOT_VERIFIED' and len(partial['runs']) == 4
    assert all(run['status'] == 'PASS' for run in partial['runs'].values())
    assert partial['repeatability']['status'] == 'PASS'
    assert partial['repeatability']['reference_stl_sha256'] != sha256(primary/'lattice.stl')
    executed.clear()
    final = verify.verify(primary, alternative, destination=target, closeout=True, reuse_passed=True)
    assert executed == ['resolution_0p5']
    assert final['status'] == 'PASS' and set(final['runs']) == verify.CLOSEOUT_CASES
    assert final['acceptance']['parameter_set_complete'] is True
    assert final['frozen_primary_geometry_difference']['appearance_approval_transferred'] is False
    assert frozen_hashes == {name: sha256(primary/name) for name in frozen_hashes}
