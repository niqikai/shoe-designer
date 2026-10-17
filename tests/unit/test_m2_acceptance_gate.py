"""M3 cannot consume a new mesh by inheriting old approval or partial test evidence."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from shoe_poc import acceptance as gate
from shoe_poc.reporting import sha256


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def _digest_records(directory):
    return {p.relative_to(directory).as_posix(): gate.file_binding(p)
            for p in directory.rglob('*') if p.is_file()}


@pytest.fixture
def evidence(tmp_path, monkeypatch):
    root = tmp_path/'project'
    source, closeout = root/'output/m2_toe_complete', root/'output/m2_closeout'
    source.mkdir(parents=True)
    closeout.mkdir()
    m1 = root/'output/m1_toe_refine'
    m1.mkdir()
    (m1/'frozen.step').write_bytes(b'approved M1')
    approval_path = root/'docs/appearance.json'
    baseline = {'directory': str(m1), 'acceptance_file': str(root/'docs/m1.json'),
                'source_sha256': 'last-sha', 'accepted_run_id': 'm1-run'}
    policy = {'toe_start_fraction': .925, 'coverage_tolerance_mm': .1, 'max_queries': 30000}
    spec = SimpleNamespace(lattice=SimpleNamespace(period_mm=12., threshold=.6,
        phase_origin_mm=[0., 0., 0.], domain=SimpleNamespace(clearance_mm=2., bounds_mm=None,
        refinement=SimpleNamespace(model_dump=lambda **_: policy))))
    monkeypatch.setattr(gate.ShoeSpec, 'load', lambda _: spec)
    monkeypatch.setattr(gate, 'accepted_baseline', lambda *_: deepcopy(baseline))
    for name in gate.CODE_FILES:
        path = root/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('# frozen implementation\n')
    for suite in gate.REQUIRED_SUITES:
        path = root/suite/'test_lattice_example.py' if suite == 'tests/unit' else root/suite
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('def test_one(): pass\ndef test_two(): pass\n')
    (root/'tests/unit/test_m2_acceptance_gate.py').write_text('def test_gate(): pass\n')
    for name in ('m2', 'm2_toe_repair'):
        (root/'output'/name).mkdir()
        (root/'output'/name/'historical.json').write_text('{}')
    for filename in gate.APPEARANCE_FILES | gate.SOURCE_FILES | {'debug/terminal_expectations.json'}:
        path = source/filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{}')
    (source/'lattice.stl').write_bytes(b'old approved 0.6 mesh')
    (source/'gyroid_field.npz').write_bytes(b'old physical field')
    domain_dirs = [source, closeout/'resolution_domain']
    domain_proofs = {}
    for index, directory in enumerate(domain_dirs):
        directory.mkdir(parents=True, exist_ok=True)
        (directory/'domain_field.npz').write_bytes(f'domain-{index}'.encode())
        refinement = {'status': 'COMPLETE', 'query_budget_reached': False, 'policy': policy}
        metadata = {'spacing_mm': .4 if index == 0 else .5, 'refinement': refinement,
                    'sole_z_bounds_mm': [-2., 10.], 'computation_bounds_mm': [[0., 0., -2.], [10., 10., 10.]],
                    'clearance_mm': 2., 'envelope': {'version': 'fixture'}}
        _write(directory/'domain_metadata.json', metadata)
        run_id = 'frozen-run' if index == 0 else 'resolution-run'
        proof = {'run_id': run_id, 'baseline': baseline,
            'domain_field_sha256': sha256(directory/'domain_field.npz'),
            'domain_metadata_sha256': sha256(directory/'domain_metadata.json'),
            'parameters': {k: metadata[k] for k in ('spacing_mm', 'sole_z_bounds_mm', 'clearance_mm', 'envelope')},
            'domain_source': {'mode': 'seed_plus_adaptive_CAD_certificates', 'run_id': run_id,
                'refinement': refinement, 'seed_source': {'mode': 'fresh_CAD_sampling', 'run_id': run_id}}}
        proof['parameters'].update(bounds_mm=None, refinement=policy)
        _write(directory/'domain_provenance.json', proof)
        domain_proofs[directory] = proof
        if index:
            _write(directory/'execution_report.json', {'status': 'PASS', 'run_id': run_id})
    terminal = {'run_id': 'frozen-run', 'geometry_status': 'PASS',
        'lattice_sha256': sha256(source/'lattice.stl'), 'domain_sha256': sha256(source/'domain_field.npz'),
        'gyroid_sha256': sha256(source/'gyroid_field.npz'),
        'expected_nodes_sha256': sha256(source/'debug/terminal_expectations.json')}
    _write(source/'terminal_coverage_report.json', terminal)
    validation = {k: {'geometry_status': 'PASS'} for k in
                  ('mesh', 'native_domain_samples', 'gyroid_residual', 'sole_coverage', 'principal_terminal_coverage')}
    validation.update(run_id='frozen-run', geometry_status='PASS')
    _write(source/'validation_report.json', validation)
    _write(source/'completion_report.json', {'run_id': 'frozen-run', 'toe_completion_status': 'PASS',
        'evidence': {name: sha256(source/name) for name in
                    ('lattice.stl', 'validation_report.json', 'terminal_coverage_report.json')}})
    execution = {'status': 'PASS', 'run_id': 'frozen-run', 'baseline': baseline,
        'outputs': {name: gate.file_binding(source/name) for name in gate.SOURCE_FILES-{'execution_report.json'} | {'domain_field.npz', 'domain_metadata.json'}}}
    _write(source/'execution_report.json', execution)
    _write(approval_path, {'status': 'ACCEPTED', 'scope': 'M2_lattice_appearance', 'run_id': 'frozen-run',
        'source_sha256': baseline['source_sha256'], 'output_directory': str(source),
        'files': {name: gate.file_binding(source/name) for name in gate.APPEARANCE_FILES}})
    code = {str(root/name): sha256(root/name) for name in gate.CODE_FILES}
    source_hashes = {name: sha256(source/name) for name in gate.SOURCE_FILES}
    impl = {'algorithm_contract_version': 'lewiner-raw-connectivity-bounded-inward-v2',
        'implementation_sha256': code[str(root/'src/shoe_poc/lattice/meshing.py')], 'actual_level': -1e-5}
    runs = {}
    for name in sorted(gate.CASES):
        directory = closeout/'variants'/name
        directory.mkdir(parents=True)
        domain = domain_dirs[1] if name == 'resolution_0p5' else source
        period, threshold = (16. if name == 'period_16' else 12.), (.45 if name == 'threshold_045' else .6)
        spacing = .5 if name == 'resolution_0p5' else .4
        (directory/'lattice.stl').write_bytes(b'new current mesh' if name in ('repeat', 'current_primary') else name.encode())
        (directory/'gyroid_field.npz').write_bytes(name.encode())
        _write(directory/'terminal_expectations.json', {'baseline': baseline,
            'domain_sampling_run_id': domain_proofs[domain]['run_id'], 'period_mm': period, 'spacing_mm': spacing,
            'domain_field_sha256': sha256(domain/'domain_field.npz'),
            'domain_metadata_sha256': sha256(domain/'domain_metadata.json')})
        checks = {k: {'geometry_status': 'PASS'} for k in
            ('mesh', 'cad_samples', 'implicit_residual', 'sole_coverage', 'principal_terminal_coverage')}
        checks['implicit_residual']['all_vertices_checked'] = True
        checks['principal_terminal_coverage']['expected_nodes_sha256'] = sha256(directory/'terminal_expectations.json')
        runs[name] = {'name': name, 'status': 'PASS', 'closeout_report_version': 2,
            'identical_to_frozen_primary_sampled_field': True if name in ('current_primary', 'repeat') else None,
            'primary_m2_run_id': 'frozen-run', 'frozen_baseline': baseline, 'validation': checks,
            'source_artifact_hashes': source_hashes, 'validator_code_hashes': code,
            'domain_source': str(domain), 'domain_provenance': domain_proofs[domain],
            'domain_sampling_run_id': domain_proofs[domain]['run_id'],
            'domain_artifact_hashes': {f: sha256(domain/f) for f in
                ('domain_field.npz', 'domain_metadata.json', 'domain_provenance.json', 'execution_report.json')},
            'domain_field_sha256': sha256(domain/'domain_field.npz'), 'domain_metadata_sha256': sha256(domain/'domain_metadata.json'),
            'settings': {'period_mm': period, 'threshold': threshold, 'resolution_mm': spacing, 'phase_origin_mm': [0., 0., 0.]},
            'lattice_sha256': sha256(directory/'lattice.stl'), 'lattice_bytes': (directory/'lattice.stl').stat().st_size,
            'gyroid_field_sha256': sha256(directory/'gyroid_field.npz'),
            'terminal_expectations_sha256': sha256(directory/'terminal_expectations.json'),
            'extraction_implementation': impl, 'meshing': {'allow_degenerate': True, 'mesh_repair_applied': False,
                'components_removed': 0, 'level': -1e-5}}
        _write(directory/'verification_report.json', runs[name])
    current, repeat = runs['current_primary'], runs['repeat']
    parameters = {'status': 'PASS', 'closeout_report_version': 2, 'primary_run_id': 'frozen-run',
        'source_directory': str(source), 'output_directory': str(closeout), 'runs': runs,
        'required_variants': sorted(gate.CASES), 'completed_variants': sorted(gate.CASES),
        'source_artifact_hashes': source_hashes, 'validator_code_hashes': code,
        'primary_stl_sha256': sha256(source/'lattice.stl'), 'primary_domain_sha256': sha256(source/'domain_field.npz'),
        'parameter_reference_case': 'current_primary', 'parameter_reference_stl_sha256': current['lattice_sha256'],
        'acceptance': {'user_appearance_acceptance': {'path': str(approval_path), 'sha256': sha256(approval_path)}},
        'repeatability': {'status': 'PASS', 'reference_case': 'current_primary', 'repeat_case': 'repeat',
            'identical_sampled_field': True, 'identical_exported_stl_sha256': True, 'identical_extraction_implementation': True,
            'reference_stl_sha256': current['lattice_sha256'], 'repeated_stl_sha256': repeat['lattice_sha256'],
            'reference_field_sha256': current['gyroid_field_sha256'], 'repeated_field_sha256': repeat['gyroid_field_sha256'],
            'reference_extraction_implementation': impl, 'repeated_extraction_implementation': impl},
        'parameter_geometry_changes': {name: {'different_stl_hash': True, 'volume_delta_mm3': 5.}
            for name in gate.CASES-{'current_primary', 'repeat'}},
        'frozen_primary_geometry_difference': {'appearance_approval_transferred': False,
            'frozen_stl_sha256': sha256(source/'lattice.stl'), 'current_stl_sha256': current['lattice_sha256']}}
    _write(closeout/'parameter_change_report.json', parameters)
    _write(closeout/'debug/preserved_artifacts_before.json', {name: _digest_records(root/'output'/name)
        for name in ('m1_toe_refine', 'm2', 'm2_toe_repair', 'm2_toe_complete')})
    nodes = []
    for path in sorted((root/'tests').rglob('test_*.py')):
        names = ['test_gate'] if path.name == 'test_m2_acceptance_gate.py' else ['test_one', 'test_two']
        nodes.extend(f'{path.relative_to(root).as_posix()}::{name}' for name in names)
    tests = {'pytest_exit_code': 0, 'invocation_args': list(gate.REQUIRED_SUITES), 'collected_nodeids': nodes,
        'collected': len(nodes), 'counts': {'PASS': len(nodes), 'FAIL': 0, 'NOT_TESTED': 0},
        'tests': {node: {'status': 'PASS'} for node in nodes}, 'm2_closeout_provenance': gate.closeout_test_provenance(source, closeout)}
    _write(closeout/'test_report.json', tests)
    return SimpleNamespace(root=root, source=source, closeout=closeout, approval=approval_path, parameters=parameters, tests=tests)


def _validate(evidence):
    return gate.validate_m2_acceptance(evidence.source, evidence.closeout, evidence.approval, project_root=evidence.root)


def test_full_gate_selects_frozen_approved_mesh_and_never_current_variant(evidence):
    before = {p: gate.file_binding(p) for p in evidence.root.rglob('*') if p.is_file()}
    result = _validate(evidence)
    assert result['M3_gate'] == 'READY' and result['appearance_status'] == 'ACCEPTED'
    assert result['selected_lattice']['path'] == str(evidence.source/'lattice.stl')
    assert result['selected_lattice']['sha256'] != evidence.parameters['runs']['current_primary']['lattice_sha256']
    assert result['manufacturing_status'] == result['global_minimum_wall_thickness'] == 'NOT_VERIFIED'
    assert before == {p: gate.file_binding(p) for p in evidence.root.rglob('*') if p.is_file()}


def test_technical_evidence_alone_never_opens_m3(evidence):
    (evidence.closeout/'test_report.json').unlink()
    result = gate.validate_m2_evidence(evidence.source, evidence.closeout, evidence.approval, project_root=evidence.root)
    assert result['M3_gate'] == 'BLOCKED_PENDING_REGRESSION'
    with pytest.raises(ValueError, match='Missing'):
        _validate(evidence)


def test_relative_appearance_directory_is_resolved_against_project_root(evidence, monkeypatch, tmp_path):
    approval = json.loads(evidence.approval.read_text())
    approval['output_directory'] = 'output/m2_toe_complete'
    _write(evidence.approval, approval)
    evidence.parameters['acceptance']['user_appearance_acceptance']['sha256'] = sha256(evidence.approval)
    _write(evidence.closeout/'parameter_change_report.json', evidence.parameters)
    monkeypatch.chdir(tmp_path)
    result = gate.validate_m2_evidence(evidence.source, evidence.closeout, evidence.approval,
                                      project_root=evidence.root)
    assert result['selected_lattice']['path'] == str(evidence.source/'lattice.stl')


@pytest.mark.parametrize('case', sorted(gate.CASES))
def test_each_missing_case_blocks_even_if_aggregate_claims_pass(evidence, case):
    evidence.parameters['runs'].pop(case)
    _write(evidence.closeout/'parameter_change_report.json', evidence.parameters)
    with pytest.raises(ValueError, match='five'):
        _validate(evidence)


@pytest.mark.parametrize('target', ['lattice.stl', 'domain_field.npz', 'gyroid_field.npz', 'debug/terminal_expectations.json'])
def test_frozen_source_tampering_blocks_gate(evidence, target):
    (evidence.source/target).write_bytes(b'tampered')
    with pytest.raises(ValueError):
        _validate(evidence)


@pytest.mark.parametrize('case', ['current_primary', 'repeat'])
@pytest.mark.parametrize('claim', [False, None])
def test_current_runs_cannot_drift_from_frozen_primary_field(evidence, case, claim):
    run = evidence.parameters['runs'][case]
    if claim is None:
        run.pop('identical_to_frozen_primary_sampled_field')
    else:
        run['identical_to_frozen_primary_sampled_field'] = claim
    _write(evidence.closeout/'variants'/case/'verification_report.json', run)
    _write(evidence.closeout/'parameter_change_report.json', evidence.parameters)
    with pytest.raises(ValueError, match='frozen primary sampled field'):
        _validate(evidence)


@pytest.mark.parametrize('target', ['lattice.stl', 'gyroid_field.npz', 'terminal_expectations.json', 'verification_report.json'])
def test_variant_tampering_blocks_gate(evidence, target):
    (evidence.closeout/'variants/threshold_045'/target).write_bytes(b'tampered')
    with pytest.raises((ValueError, json.JSONDecodeError)):
        _validate(evidence)


def test_validator_change_and_unrelated_m3_test_have_different_scope(evidence):
    (evidence.root/'tests/unit/test_future_m3.py').write_text('def test_future(): pass\n')
    assert _validate(evidence)['M3_gate'] == 'READY'
    (evidence.root/'src/shoe_poc/lattice/meshing.py').write_text('# changed')
    with pytest.raises(ValueError, match='Validator'):
        _validate(evidence)


@pytest.mark.parametrize('change', ['scope', 'run', 'stl_bytes'])
def test_appearance_identity_cannot_be_inferred_or_rebound(evidence, change):
    approval = json.loads(evidence.approval.read_text())
    if change == 'scope':
        approval['scope'] = 'M3_or_manufacturing'
    elif change == 'run':
        approval['run_id'] = 'current-algorithm'
    else:
        approval['files']['lattice.stl']['bytes'] += 1
    _write(evidence.approval, approval)
    with pytest.raises(ValueError):
        _validate(evidence)


@pytest.mark.parametrize('kind', ['one_test', 'skip', 'filtered', 'unbound', 'omitted_suite', 'omitted_m2_test'])
def test_full_regression_is_required_not_an_arbitrary_passing_test(evidence, kind):
    report = evidence.tests
    if kind == 'one_test':
        node = report['collected_nodeids'][0]
        report.update(collected_nodeids=[node], collected=1, counts={'PASS': 1, 'FAIL': 0, 'NOT_TESTED': 0}, tests={node: {'status': 'PASS'}})
    elif kind == 'skip':
        report['tests'][report['collected_nodeids'][0]]['status'] = 'NOT_TESTED'
    elif kind == 'filtered':
        report['invocation_args'].extend(['-k', 'one'])
    elif kind == 'unbound':
        report['m2_closeout_provenance']['parameter_report']['sha256'] = 'fake'
    elif kind == 'omitted_suite':
        report['invocation_args'].pop()
    else:
        (evidence.root/'tests/unit/test_lattice_example.py').write_text('def test_one(): pass\ndef test_two(): pass\ndef test_missing(): pass\n')
    _write(evidence.closeout/'test_report.json', report)
    with pytest.raises(ValueError):
        _validate(evidence)


@pytest.mark.parametrize('mutation', ['m1_changed', 'old_m2_changed', 'omitted_file', 'new_untracked_file'])
def test_preservation_covers_all_historical_files(evidence, mutation):
    if mutation == 'm1_changed':
        (evidence.root/'output/m1_toe_refine/frozen.step').write_bytes(b'new')
    elif mutation == 'old_m2_changed':
        (evidence.root/'output/m2/historical.json').write_bytes(b'new')
    elif mutation == 'new_untracked_file':
        (evidence.root/'output/m2/extra.json').write_text('{}')
    else:
        path = evidence.closeout/'debug/preserved_artifacts_before.json'
        snapshot = json.loads(path.read_text())
        snapshot['m2'].pop('historical.json')
        _write(path, snapshot)
    with pytest.raises(ValueError):
        _validate(evidence)


@pytest.mark.parametrize('mutation', ['old_reference', 'approval_transfer', 'failed_check', 'wrong_threshold', 'domain_policy'])
def test_status_pass_cannot_hide_semantic_evidence_mismatch(evidence, mutation):
    parameters = evidence.parameters
    if mutation == 'old_reference':
        parameters['repeatability']['reference_case'] = 'frozen_primary'
    elif mutation == 'approval_transfer':
        parameters['frozen_primary_geometry_difference']['appearance_approval_transferred'] = True
    elif mutation == 'failed_check':
        parameters['runs']['threshold_045']['validation']['cad_samples']['geometry_status'] = 'FAIL'
    elif mutation == 'wrong_threshold':
        parameters['runs']['threshold_045']['settings']['threshold'] = .5
    else:
        path = evidence.closeout/'resolution_domain/domain_metadata.json'
        meta = json.loads(path.read_text())
        meta['refinement']['query_budget_reached'] = True
        _write(path, meta)
    if mutation in ('failed_check', 'wrong_threshold'):
        _write(evidence.closeout/'variants/threshold_045/verification_report.json', parameters['runs']['threshold_045'])
    _write(evidence.closeout/'parameter_change_report.json', parameters)
    with pytest.raises(ValueError):
        _validate(evidence)
