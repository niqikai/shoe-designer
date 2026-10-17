"""Read-only, fail-closed binding of approved M2 appearance and numerical closeout.

No geometry is generated, evidence rewritten, or approval inferred here. M3 consumes
``selected_lattice`` from the frozen appearance source, never a regression variant.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

from shoe_poc.lattice.pipeline import accepted_baseline
from shoe_poc.reporting import sha256
from shoe_poc.schemas import ShoeSpec

CASES = frozenset({'current_primary', 'period_16', 'threshold_045', 'repeat', 'resolution_0p5'})
REQUIRED_SUITES = ('tests/unit', 'tests/integration/test_lattice_real.py',
                   'tests/integration/test_lattice_toe_complete.py', 'tests/integration/test_m2_closeout.py')
CODE_FILES = ('scripts/verify_lattice.py', *(f'src/shoe_poc/{name}.py' for name in (
    'lattice/meshing', 'lattice/gyroid', 'lattice/cache', 'lattice/domain', 'lattice/envelope',
    'lattice/pipeline', 'lattice/refinement', 'validation/lattice', 'validation/lattice_terminal',
    'validation/lattice_coverage')))
SOURCE_FILES = frozenset({'execution_report.json', 'shoe_spec.json', 'lattice_metadata.json',
                         'lattice.stl', 'domain_provenance.json', 'gyroid_field.npz', 'validation_report.json'})
APPEARANCE_FILES = frozenset({'lattice.stl', 'shoe_spec.json', 'preview.png', 'terminal_comparison.png',
    'domain_field.npz', 'domain_metadata.json', 'domain_provenance.json',
    'terminal_coverage_report.json', 'completion_report.json'})


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _read(path):
    return json.loads(Path(path).read_text())


def file_binding(path):
    """Return the actual byte identity; useful to integration provenance fixtures."""
    path = Path(path)
    return {'sha256': sha256(path), 'bytes': path.stat().st_size}


def _file(directory, name):
    path = Path(directory)/name
    _require(path.resolve().is_relative_to(Path(directory).resolve()), f'Artifact escapes directory: {name}')
    _require(path.is_file() and not path.is_symlink(), f'Missing or symbolic-link artifact: {path}')
    return path


def _records(directory, records, required=()):
    _require(isinstance(records, dict) and set(required) <= records.keys(), 'Missing required artifact bindings')
    for name, record in records.items():
        path = _file(directory, name)
        _require(file_binding(path) == record, f'Changed artifact SHA/bytes: {path}')


def _hashes(directory, hashes, required=()):
    _require(isinstance(hashes, dict) and set(required) <= hashes.keys(), 'Missing required SHA bindings')
    for name, digest in hashes.items():
        _require(sha256(_file(directory, name)) == digest, f'Changed artifact hash: {directory}/{name}')


def closeout_test_provenance(source, closeout):
    """Identity recorded by the actual closeout integration fixture, before tests run."""
    source, closeout = Path(source).resolve(), Path(closeout).resolve()
    return {'source_directory': str(source), 'source_run_id': _read(source/'execution_report.json')['run_id'],
            'parameter_report': file_binding(closeout/'parameter_change_report.json'),
            'variant_reports': {name: file_binding(closeout/'variants'/name/'verification_report.json')
                                for name in sorted(CASES)}}


def _preservation(closeout, source, baseline):
    snapshot_path = _file(closeout, 'debug/preserved_artifacts_before.json')
    snapshot = _read(snapshot_path)
    output_root = Path(baseline['directory']).resolve().parent
    required = {Path(baseline['directory']).name, 'm2', 'm2_toe_repair', source.name}
    _require(required <= snapshot.keys(), 'Preservation snapshot omits frozen M1/M2 directories')
    count = 0
    for folder, records in snapshot.items():
        directory = output_root/folder
        _require(directory.resolve().is_relative_to(output_root), 'Preservation directory escapes output root')
        actual = {p.relative_to(directory).as_posix() for p in directory.rglob('*') if p.is_file()}
        _require(set(records) == actual and records, f'Preservation snapshot must cover every file: {folder}')
        _records(directory, records)
        count += len(records)
    return {'status': 'PASS', 'checked_files': count, 'snapshot': file_binding(snapshot_path)}


def _domain(directory, baseline, spec):
    provenance = _read(_file(directory, 'domain_provenance.json'))
    metadata = _read(_file(directory, 'domain_metadata.json'))
    _require(provenance.get('baseline') == baseline and provenance.get('run_id'), 'Domain baseline/run mismatch')
    _hashes(directory, {'domain_field.npz': provenance.get('domain_field_sha256'),
                        'domain_metadata.json': provenance.get('domain_metadata_sha256')})
    policy = spec.lattice.domain.refinement.model_dump(mode='json')
    expected = {'spacing_mm': metadata['spacing_mm'], 'clearance_mm': spec.lattice.domain.clearance_mm,
        'bounds_mm': spec.lattice.domain.bounds_mm, 'sole_z_bounds_mm': metadata['sole_z_bounds_mm'],
        'envelope': metadata.get('envelope'), 'refinement': policy}
    _require(provenance.get('parameters') == expected, 'Domain provenance/grid policy mismatch')
    refinement = metadata.get('refinement', {})
    source = provenance.get('domain_source', {})
    _require(refinement.get('status') == 'COMPLETE' and refinement.get('query_budget_reached') is False
             and refinement.get('policy') == policy, 'Incomplete final native domain certification')
    _require(source.get('mode') == 'seed_plus_adaptive_CAD_certificates'
             and source.get('run_id') == provenance['run_id'] and source.get('refinement') == refinement,
             'Final adaptive domain source mismatch')
    seed = source.get('seed_source', {})
    if seed.get('mode') == 'fresh_CAD_sampling':
        _require(seed.get('run_id') == provenance['run_id'], 'Native seed run mismatch')
    else:
        _require(seed.get('mode') == 'validated_cached_CAD_certificates', 'Unidentified native seed')
        inherited, seed_dir = seed.get('provenance', {}), Path(seed.get('directory', ''))
        _require(inherited.get('baseline') == baseline and inherited.get('run_id')
                 and sha256(seed_dir/'domain_provenance.json') == seed.get('provenance_sha256')
                 and _read(seed_dir/'domain_provenance.json') == inherited, 'Cached native seed provenance mismatch')
        _hashes(seed_dir, {'domain_field.npz': inherited.get('domain_field_sha256'),
                           'domain_metadata.json': inherited.get('domain_metadata_sha256')})
    execution = _read(_file(directory, 'execution_report.json'))
    _require(execution.get('run_id') == provenance['run_id'], 'Domain execution run mismatch')
    if 'baseline' in execution:
        _require(execution['baseline'] == baseline, 'Domain execution baseline mismatch')
    if 'domain_source' in execution:
        _require(execution['domain_source'] == source, 'Domain execution provenance source mismatch')
    if execution.get('outputs'):
        domain_records = {name: execution['outputs'][name] for name in
            ('domain_field.npz', 'domain_metadata.json', 'domain_provenance.json')
            if name in execution['outputs']}
        _records(directory, domain_records, ('domain_field.npz', 'domain_metadata.json', 'domain_provenance.json'))
    return provenance, metadata


def validate_m2_evidence(source: Path, closeout: Path, appearance_acceptance: Path, *, project_root: Path | None = None) -> dict:
    """Validate technical evidence only; this deliberately never returns READY.

    Integration tests use this before their own final pytest report exists. Call
    validate_m2_acceptance for the complete M3 entry gate.
    """
    source, closeout, approval_path = map(lambda p: Path(p).resolve(), (source, closeout, appearance_acceptance))
    root = Path(project_root).resolve() if project_root else Path(__file__).resolve().parents[2]
    _require(source != closeout and source not in closeout.parents and closeout not in source.parents,
             'Closeout overlaps frozen source')
    execution = _read(_file(source, 'execution_report.json'))
    validation = _read(_file(source, 'validation_report.json'))
    completion = _read(_file(source, 'completion_report.json'))
    terminal = _read(_file(source, 'terminal_coverage_report.json'))
    run_id, baseline = execution['run_id'], execution['baseline']
    _require(execution.get('status') == validation.get('geometry_status') == completion.get('toe_completion_status')
             == terminal.get('geometry_status') == 'PASS', 'Frozen source checks must all PASS')
    _require(all(row.get('run_id') == run_id for row in (validation, completion, terminal)), 'Frozen source run mismatch')
    _require(all(validation[k].get('geometry_status') == 'PASS' for k in
        ('mesh', 'native_domain_samples', 'gyroid_residual', 'sole_coverage', 'principal_terminal_coverage')),
        'Frozen source validation subcheck failed')
    spec = ShoeSpec.load(source/'shoe_spec.json')
    _require(accepted_baseline(Path(baseline['directory']), spec, Path(baseline['acceptance_file'])) == baseline,
             'Accepted M1 input gate mismatch')
    _records(source, execution.get('outputs'), SOURCE_FILES-{'execution_report.json'})
    _hashes(source, completion.get('evidence'), ('lattice.stl', 'validation_report.json', 'terminal_coverage_report.json'))
    _hashes(source, {'lattice.stl': terminal.get('lattice_sha256'), 'domain_field.npz': terminal.get('domain_sha256'),
        'gyroid_field.npz': terminal.get('gyroid_sha256'), 'debug/terminal_expectations.json': terminal.get('expected_nodes_sha256')})
    approval = _read(approval_path)
    _require(approval.get('status') == 'ACCEPTED' and approval.get('scope') == 'M2_lattice_appearance', 'Explicit M2 appearance approval required')
    approved_directory = Path(approval.get('output_directory', ''))
    if not approved_directory.is_absolute():
        approved_directory = root/approved_directory
    _require(approval.get('run_id') == run_id and approved_directory.resolve() == source
             and approval.get('source_sha256') == baseline['source_sha256'], 'Appearance approval source/run mismatch')
    _records(source, approval.get('files'), APPEARANCE_FILES)
    parameters = _read(_file(closeout, 'parameter_change_report.json'))
    _require(parameters.get('status') == 'PASS' and parameters.get('closeout_report_version') == 2,
             'Complete versioned parameter PASS required')
    _require(parameters.get('primary_run_id') == run_id and Path(parameters.get('source_directory', '')).resolve() == source
             and Path(parameters.get('output_directory', '')).resolve() == closeout, 'Closeout source/run mismatch')
    _require(set(parameters.get('runs', {})) == set(parameters.get('required_variants', []))
             == set(parameters.get('completed_variants', [])) == CASES, 'Exactly five required closeout cases must pass')
    _require(not parameters.get('worker_errors'), 'Closeout contains worker failures')
    _hashes(source, parameters.get('source_artifact_hashes'), SOURCE_FILES)
    _require(parameters.get('primary_stl_sha256') == sha256(source/'lattice.stl')
             and parameters.get('primary_domain_sha256') == sha256(source/'domain_field.npz'), 'Frozen primary identity mismatch')
    code = {str((root/name).resolve()): sha256(root/name) for name in CODE_FILES}
    _require(parameters.get('validator_code_hashes') == code, 'Validator code identity changed or incomplete')
    appearance = parameters.get('acceptance', {}).get('user_appearance_acceptance', {})
    _require(appearance.get('path') == str(approval_path) and appearance.get('sha256') == sha256(approval_path),
             'Closeout does not bind the external appearance approval')
    primary_provenance, primary_domain = _domain(source, baseline, spec)
    alt_dir = closeout/'resolution_domain'
    alt_provenance, alt_domain = _domain(alt_dir, baseline, spec)
    _require(alt_domain['spacing_mm'] == .5 and alt_provenance['run_id'] != primary_provenance['run_id'],
             'Resolution needs a separate actual 0.5 native certification')
    for key in ('sole_z_bounds_mm', 'computation_bounds_mm', 'clearance_mm', 'envelope'):
        _require(alt_domain[key] == primary_domain[key], f'Resolution changed domain parameter: {key}')
    for name, run in parameters['runs'].items():
        directory = closeout/'variants'/name
        _require(_read(_file(directory, 'verification_report.json')) == run, f'Variant report differs from aggregate: {name}')
        _require(run.get('name') == name and run.get('status') == 'PASS' and run.get('closeout_report_version') == 2
                 and run.get('primary_m2_run_id') == run_id and run.get('frozen_baseline') == baseline, f'Variant run mismatch: {name}')
        checks = run.get('validation', {})
        _require(set(checks) == {'mesh', 'cad_samples', 'implicit_residual', 'sole_coverage', 'principal_terminal_coverage'}
                 and all(v.get('geometry_status') == 'PASS' for v in checks.values()), f'Variant subcheck failed/missing: {name}')
        _require(checks['implicit_residual'].get('all_vertices_checked') is True, f'Incomplete residual vertices: {name}')
        _require(run.get('source_artifact_hashes') == parameters['source_artifact_hashes']
                 and run.get('validator_code_hashes') == code, f'Variant source/code binding mismatch: {name}')
        domain_dir = alt_dir if name == 'resolution_0p5' else source
        provenance = alt_provenance if name == 'resolution_0p5' else primary_provenance
        metadata = alt_domain if name == 'resolution_0p5' else primary_domain
        _require(Path(run.get('domain_source', '')).resolve() == domain_dir
                 and run.get('domain_provenance') == provenance and run.get('domain_sampling_run_id') == provenance['run_id'],
                 f'Variant domain provenance mismatch: {name}')
        _hashes(domain_dir, run.get('domain_artifact_hashes'), ('domain_field.npz', 'domain_metadata.json', 'domain_provenance.json', 'execution_report.json'))
        _require(run.get('domain_field_sha256') == sha256(domain_dir/'domain_field.npz')
                 and run.get('domain_metadata_sha256') == sha256(domain_dir/'domain_metadata.json'), f'Variant domain hash mismatch: {name}')
        settings = {'period_mm': 16. if name == 'period_16' else spec.lattice.period_mm,
            'threshold': .45 if name == 'threshold_045' else spec.lattice.threshold,
            'resolution_mm': metadata['spacing_mm'], 'phase_origin_mm': list(spec.lattice.phase_origin_mm)}
        _require(run.get('settings') == settings, f'Wrong actual case settings: {name}')
        _records(directory, {'lattice.stl': {'sha256': run.get('lattice_sha256'), 'bytes': run.get('lattice_bytes')}})
        _hashes(directory, {'gyroid_field.npz': run.get('gyroid_field_sha256'),
            'terminal_expectations.json': run.get('terminal_expectations_sha256')})
        nodes = _read(directory/'terminal_expectations.json')
        _require(nodes.get('baseline') == baseline and nodes.get('domain_sampling_run_id') == provenance['run_id']
                 and nodes.get('domain_field_sha256') == run['domain_field_sha256']
                 and nodes.get('domain_metadata_sha256') == run['domain_metadata_sha256']
                 and nodes.get('period_mm') == settings['period_mm'] and nodes.get('spacing_mm') == settings['resolution_mm']
                 and checks['principal_terminal_coverage'].get('expected_nodes_sha256') == run['terminal_expectations_sha256'],
                 f'Terminal node evidence mismatch: {name}')
        impl, meshing = run.get('extraction_implementation', {}), run.get('meshing', {})
        _require(impl.get('algorithm_contract_version') == 'lewiner-raw-connectivity-bounded-inward-v2'
                 and impl.get('implementation_sha256') == code[str((root/'src/shoe_poc/lattice/meshing.py').resolve())]
                 and meshing.get('allow_degenerate') is True and meshing.get('mesh_repair_applied') is False
                 and meshing.get('components_removed') == 0 and impl.get('actual_level') == meshing.get('level'),
                 f'Extraction implementation mismatch: {name}')
    current, repeated = parameters['runs']['current_primary'], parameters['runs']['repeat']
    _require(all(run.get('identical_to_frozen_primary_sampled_field') is True for run in (current, repeated)),
             'Current primary and repeat must match the frozen primary sampled field')
    repeat = parameters.get('repeatability', {})
    _require(parameters.get('parameter_reference_case') == repeat.get('reference_case') == 'current_primary'
             and repeat.get('repeat_case') == 'repeat' and repeat.get('status') == 'PASS'
             and all(repeat.get(k) is True for k in ('identical_sampled_field', 'identical_exported_stl_sha256', 'identical_extraction_implementation'))
             and current['lattice_sha256'] == repeated['lattice_sha256'] == repeat.get('reference_stl_sha256')
             == repeat.get('repeated_stl_sha256') == parameters.get('parameter_reference_stl_sha256')
             and repeat.get('reference_field_sha256') == current['gyroid_field_sha256']
             and repeat.get('repeated_field_sha256') == repeated['gyroid_field_sha256']
             and repeat.get('reference_extraction_implementation') == current['extraction_implementation']
             and repeat.get('repeated_extraction_implementation') == repeated['extraction_implementation'], 'Repeat must bind two current runs')
    changes = parameters.get('parameter_geometry_changes', {})
    _require(set(changes) == CASES-{'current_primary', 'repeat'} and all(
        c.get('different_stl_hash') is True and abs(c.get('volume_delta_mm3', 0)) > 1. for c in changes.values()),
        'Actual parameter geometry changes not demonstrated')
    difference = parameters.get('frozen_primary_geometry_difference', {})
    _require(difference.get('appearance_approval_transferred') is False
             and difference.get('frozen_stl_sha256') == sha256(source/'lattice.stl')
             and difference.get('current_stl_sha256') == current['lattice_sha256'], 'Frozen/current comparison must not transfer approval')
    preservation = _preservation(closeout, source, baseline)
    return {'status': 'PASS', 'M3_gate': 'BLOCKED_PENDING_REGRESSION', 'appearance_status': 'ACCEPTED',
        'M2_selected_cases_status': 'PASS', 'source_run_id': run_id, 'source_directory': str(source),
        'appearance_acceptance': {'path': str(approval_path), **file_binding(approval_path)},
        'selected_lattice': {'path': str(source/'lattice.stl'), **file_binding(source/'lattice.stl'),
                             'scope': 'Frozen user-approved M2 appearance; regression variants are independent'},
        'parameter_report': {'path': str(closeout/'parameter_change_report.json'), **file_binding(closeout/'parameter_change_report.json')},
        'baseline': baseline, 'preservation': preservation, 'validator_code_hashes': code,
        'manufacturing_status': 'NOT_VERIFIED', 'physical_printing': 'NOT_TESTED',
        'global_minimum_wall_thickness': 'NOT_VERIFIED'}


def _validate_tests(report, source, closeout, root):
    _require(report.get('pytest_exit_code') == 0, 'Complete regression pytest must exit zero')
    nodes, tests = report.get('collected_nodeids', []), report.get('tests', {})
    _require(nodes and len(nodes) == len(set(nodes)) and set(nodes) == set(tests)
             and report.get('collected') == len(nodes), 'Regression collection/results mismatch')
    _require(all(row.get('status') == 'PASS' for row in tests.values())
             and report.get('counts') == {'PASS': len(nodes), 'FAIL': 0, 'NOT_TESTED': 0}, 'Every regression must PASS without skips')
    args = report.get('invocation_args', [])
    _require(all(any(Path(arg).as_posix().rstrip('/') == suite or
                 Path(arg).resolve() == (root/suite).resolve() for arg in args) for suite in REQUIRED_SUITES),
             'Full required regression suites were not invoked')
    _require(not any(arg in ('-k', '-m', '--lf', '--last-failed', '--ff', '--failed-first', '--deselect')
                     or arg.startswith(('--deselect=', '-k=', '-m=')) for arg in args), 'Filtered regression cannot close M2')
    tested = {n.split('[')[0] for n in nodes}
    for suite in REQUIRED_SUITES:
        files = (sorted((root/suite).glob('test_lattice_*.py')) + [root/suite/'test_m2_acceptance_gate.py']
                 if (root/suite).is_dir() else [root/suite])
        _require(files and all(p.is_file() for p in files), f'Required regression suite absent: {suite}')
        for path in files:
            functions = [n.name for n in ast.walk(ast.parse(path.read_text()))
                         if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith('test_')]
            relative = path.relative_to(root).as_posix()
            _require(functions and all(any(n.startswith(relative+'::') and n.endswith('::'+name)
                         for n in tested) for name in functions), f'Regression omits current tests: {relative}')
    provenance = report.get('m2_closeout_provenance', report.get('terminal_build_provenance', {}).get('closeout'))
    _require(provenance == closeout_test_provenance(source, closeout), 'Regression not bound to current closeout evidence')


def validate_m2_acceptance(source: Path, closeout: Path, appearance_acceptance: Path, *, project_root: Path | None = None) -> dict:
    """Return an immutable-evidence identity binding only when every M3 gate passes."""
    result = validate_m2_evidence(source, closeout, appearance_acceptance, project_root=project_root)
    root = Path(project_root).resolve() if project_root else Path(__file__).resolve().parents[2]
    report_path = _file(closeout, 'test_report.json')
    _validate_tests(_read(report_path), Path(source), Path(closeout), root)
    result.update(M3_gate='READY', test_report={'path': str(report_path.resolve()), **file_binding(report_path)})
    return result
