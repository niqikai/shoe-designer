"""Measure actual M2 parameter variants and exact-field extraction repeatability.

The primary domain cache is hash-bound to a completed real CAD run. The optional
resolution domain is independently sampled CAD, not a claimed pure MC convergence
study: its voxel-diagonal reserve changes with resolution.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import json
import multiprocessing
import os
from pathlib import Path
import time
import traceback
from types import SimpleNamespace

import numpy as np
from scipy.interpolate import RegularGridInterpolator

from shoe_poc.lattice.domain import GridPlan, NativeDomainProbe
from shoe_poc.lattice.envelope import SoleEnvelope
from shoe_poc.lattice.gyroid import GyroidParameters, sample_gyroid, save_field_npz
from shoe_poc.lattice.meshing import extract_lattice_mesh, export_lattice
from shoe_poc.lattice.pipeline import accepted_baseline, validate_output_paths
from shoe_poc.reporting import write_json, sha256, environment, utc_now
from shoe_poc.resource_limits import MemoryBudget
from shoe_poc.schemas import ShoeSpec
from shoe_poc.validation.lattice import (validate_lattice_mesh, validate_lattice_domain,
    validate_native_line_containment, validate_gyroid_residual)


def load_domain(directory):
    path = directory/'domain_field.npz'
    external = json.loads((directory/'domain_metadata.json').read_text())
    from shoe_poc.lattice.cache import _check_archive
    _check_archive(path, external['shape_xyz'])
    if not np.isfinite(external['spacing_mm']) or external['spacing_mm'] <= 0:
        raise ValueError('Domain sampling spacing must be finite and positive')
    with np.load(path, allow_pickle=False) as saved:
        values = saved['field_mm'].copy()
        spacing = float(saved['spacing_mm'])
        origin = saved['origin_mm'].copy()
        metadata = json.loads(str(saved['metadata_json']))
    if (metadata != external or list(values.shape) != metadata['shape_xyz']
            or not np.isclose(spacing, metadata['spacing_mm'], rtol=0., atol=1e-9)
            or not np.allclose(origin, metadata['origin_mm'], rtol=0., atol=1e-9)
            or values.dtype != np.float32 or not np.isfinite(values).all()):
        raise ValueError('Domain field and external metadata do not identify the same grid')
    axes = tuple(origin[i]+np.arange(values.shape[i])*spacing for i in range(3))
    return values, GridPlan(axes, spacing, int(metadata['estimated_peak_bytes'])), metadata


def _check_hashes(directory, expected):
    for name, digest in expected.items():
        if sha256(directory/name) != digest:
            raise ValueError(f'Verification source artifact changed: {directory/name}')


def _output_root(source, destination):
    """Resolve once and reject ancestor/descendant aliases of preserved inputs."""
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if source == destination or source in destination.parents or destination in source.parents:
        raise ValueError('Closeout output must be separate from the frozen source tree')
    # Existing M1/M2 artifacts are inputs, never a new closeout destination.
    if (destination/'execution_report.json').exists() or destination.name in {
            'm1', 'm2', 'm2_toe_complete', 'm2_toe_repair', 'm2_curved'}:
        raise ValueError('Closeout destination identifies preserved historical outputs')
    return destination


def _variant_output(target, name, sources):
    path = target/'variants'/name
    if not path.resolve().is_relative_to(target.resolve()):
        raise ValueError('Variant directory alias escapes closeout output')
    for source in sources:
        if source is None:
            continue
        source = Path(source).resolve()
        if path.resolve().is_relative_to(source) or source.is_relative_to(path.resolve()):
            raise ValueError('Variant output overlaps a preserved domain source')
    for filename in ('verification_report.json', 'terminal_expectations.json', 'failure_report.json',
                     'gyroid_field.npz', 'lattice.stl'):
        if (path/filename).is_symlink():
            raise ValueError(f'Variant output must not be a symbolic link: {filename}')
    return path


def _bound_domain_provenance(directory, baseline, spec, *, execution=None, require_execution_pass=True):
    """Bind the *saved* final domain, including adaptive policy, to its source."""
    directory = Path(directory)
    provenance = json.loads((directory/'domain_provenance.json').read_text())
    metadata = json.loads((directory/'domain_metadata.json').read_text())
    parameters = provenance.get('parameters', {})
    if (provenance.get('baseline') != baseline or not isinstance(provenance.get('run_id'), str)
            or not provenance['run_id'].strip()):
        raise ValueError('Domain provenance lacks the accepted CAD identity or sampling run')
    for filename, key in [('domain_field.npz', 'domain_field_sha256'),
                          ('domain_metadata.json', 'domain_metadata_sha256')]:
        if sha256(directory/filename) != provenance.get(key):
            raise ValueError(f'Domain provenance hash changed: {filename}')
    policy = None if spec.lattice.domain.refinement is None else spec.lattice.domain.refinement.model_dump(mode='json')
    expected = {'clearance_mm': spec.lattice.domain.clearance_mm,
        'bounds_mm': spec.lattice.domain.bounds_mm, 'envelope': metadata.get('envelope'),
        'refinement': policy, 'spacing_mm': metadata['spacing_mm'],
        'sole_z_bounds_mm': metadata['sole_z_bounds_mm']}
    if parameters != expected:
        raise ValueError('Domain provenance parameters do not bind the actual grid and requested domain policy')
    source = provenance.get('domain_source', {})
    mode = source.get('mode')
    if mode == 'seed_plus_adaptive_CAD_certificates':
        refinement = metadata.get('refinement', {})
        seed = source.get('seed_source', {})
        if seed.get('mode') == 'fresh_CAD_sampling':
            if seed.get('run_id') != provenance['run_id']:
                raise ValueError('Adaptive fresh seed does not identify this native CAD sampling run')
        elif seed.get('mode') == 'validated_cached_CAD_certificates':
            inherited = seed.get('provenance', {})
            seed_directory = Path(seed.get('directory', ''))
            if (inherited.get('baseline') != baseline or not inherited.get('run_id')
                    or sha256(seed_directory/'domain_provenance.json') != seed.get('provenance_sha256')
                    or json.loads((seed_directory/'domain_provenance.json').read_text()) != inherited
                    or sha256(seed_directory/'domain_field.npz') != inherited.get('domain_field_sha256')
                    or sha256(seed_directory/'domain_metadata.json') != inherited.get('domain_metadata_sha256')):
                raise ValueError('Adaptive cached seed is not bound to original native CAD evidence')
        else:
            raise ValueError('Adaptive domain needs an identified native or certified-cache seed')
        if (source.get('run_id') != provenance['run_id'] or source.get('refinement') != refinement
                or refinement.get('status') != 'COMPLETE' or refinement.get('query_budget_reached') is not False
                or refinement.get('policy') != policy or not isinstance(source.get('seed_source'), dict)):
            raise ValueError('Adaptive domain source does not identify the complete final-domain certification')
    elif mode == 'fresh_CAD_sampling':
        if source.get('run_id') != provenance['run_id'] or policy is not None:
            raise ValueError('Fresh CAD domain source does not bind the final domain/refinement')
    elif mode == 'validated_cached_CAD_certificates':
        inherited = source.get('provenance', {})
        if (not inherited.get('run_id') or inherited.get('baseline') != baseline
                or inherited.get('domain_field_sha256') != provenance['domain_field_sha256']
                or inherited.get('domain_metadata_sha256') != provenance['domain_metadata_sha256']
                or inherited.get('parameters') != parameters):
            raise ValueError('Cached final domain does not bind its original CAD certification')
    else:
        raise ValueError('Unrecognized domain certification source')
    if execution is not None:
        if ((require_execution_pass and execution.get('status') != 'PASS')
                or execution.get('run_id') != provenance['run_id']
                or (require_execution_pass and (execution.get('baseline') != baseline or execution.get('domain_source') != source))
                or ('baseline' in execution and execution['baseline'] != baseline)
                or ('domain_source' in execution and execution['domain_source'] != source)):
            raise ValueError('Domain execution does not bind the saved final-domain provenance')
        for filename in ('domain_field.npz', 'domain_metadata.json', 'domain_provenance.json'):
            output = execution.get('outputs', {}).get(filename)
            if (require_execution_pass and output is None) or (output is not None and sha256(directory/filename) != output.get('sha256')):
                raise ValueError(f'Domain execution artifact changed: {filename}')
    return provenance


def _closeout_acceptance(technical_status, complete, appearance=None):
    """Report technical evidence; overall milestones are evaluated separately."""
    return {'technical_parameter_status': technical_status if complete or technical_status == 'FAIL' else 'NOT_VERIFIED',
        'parameter_set_complete': complete,
        'user_appearance_acceptance': appearance or {'status': 'NO_EXTERNAL_EVIDENCE_PROVIDED'},
        'milestone_scope': 'This regression report does not decide overall M2 acceptance or the M3 entry gate; those require the separately bound milestone acceptance report.',
        'manufacturing_status': 'NOT_VERIFIED'}


def _same_sampled_field(saved, field):
    """Compare physical replay evidence; timing and allocation budgets may differ."""
    metadata = json.loads(str(saved['metadata_json']))
    physical_keys = ('schema_version', 'array_axis_order', 'coordinate_units', 'field_sign',
        'field_units', 'formula', 'clipping_formula', 'cap_inset_mm', 'boundary_strategy',
        'period_mm', 'threshold', 'phase_origin_mm', 'sampling_origin_mm', 'spacing_mm',
        'shape_xyz', 'sample_bounds_mm', 'field_minimum', 'field_maximum',
        'negative_sample_count', 'domain')
    return bool(np.array_equal(saved['values'], field.values)
        and np.array_equal(saved['origin_mm'], field.origin_mm)
        and float(saved['spacing_mm']) == field.spacing_mm
        and all(metadata.get(key) == field.metadata.get(key) for key in physical_keys))


CLOSEOUT_REPORT_VERSION = 2
CLOSEOUT_CASES = {'current_primary', 'period_16', 'threshold_045', 'repeat', 'resolution_0p5'}


def _extraction_implementation(metadata):
    if (metadata.get('allow_degenerate') is not True
            or not isinstance(metadata.get('connectivity_policy'), str)
            or metadata.get('mesh_repair_applied') is not False
            or metadata.get('components_removed') != 0):
        raise ValueError('Closeout requires the explicit current raw-connectivity extraction contract')
    import shoe_poc.lattice.meshing as meshing_module
    import importlib.metadata
    return {'algorithm_contract_version': 'lewiner-raw-connectivity-bounded-inward-v2',
        'implementation_path': str(Path(meshing_module.__file__).resolve()),
        'implementation_sha256': sha256(Path(meshing_module.__file__)),
        'scikit_image_version': importlib.metadata.version('scikit-image'),
        'method': metadata['method'], 'allow_degenerate': metadata.get('allow_degenerate'),
        'connectivity_policy': metadata.get('connectivity_policy'),
        'target_level': metadata.get('target_level'), 'actual_level': metadata['level'],
        'numerical_level_inset_dimensionless': metadata.get('numerical_level_inset_dimensionless'),
        'numerical_domain_inset_mm': metadata.get('numerical_domain_inset_mm'),
        'effective_gyroid_threshold': metadata.get('effective_gyroid_threshold'),
        'mesh_repair_applied': metadata.get('mesh_repair_applied'),
        'components_removed': metadata.get('components_removed')}


def _repeat_comparison(reference, repeated, *, reference_implementation=None, repeated_implementation=None):
    """Compare two independent current-implementation cases after both finish."""
    with np.load(reference/'gyroid_field.npz', allow_pickle=False) as first:
        with np.load(repeated/'gyroid_field.npz', allow_pickle=False) as second:
            field = SimpleNamespace(values=second['values'], origin_mm=second['origin_mm'],
                spacing_mm=float(second['spacing_mm']), metadata=json.loads(str(second['metadata_json'])))
            identical_field = _same_sampled_field(first, field)
    first_stl, second_stl = sha256(reference/'lattice.stl'), sha256(repeated/'lattice.stl')
    identical_stl = first_stl == second_stl
    identical_implementation = reference_implementation == repeated_implementation
    return {'status': 'PASS' if identical_field and identical_stl and identical_implementation else 'FAIL',
        'identical_extraction_implementation': identical_implementation,
        'reference_extraction_implementation': reference_implementation,
        'repeated_extraction_implementation': repeated_implementation,
        'reference_case': 'current_primary', 'repeat_case': 'repeat',
        'identical_sampled_field': identical_field, 'identical_exported_stl_sha256': identical_stl,
        'reference_stl_sha256': first_stl, 'repeated_stl_sha256': second_stl,
        'reference_field_sha256': sha256(reference/'gyroid_field.npz'),
        'repeated_field_sha256': sha256(repeated/'gyroid_field.npz'),
        'scope': 'Two independent samplings of the same certified domain and requested parameters, using the same current extraction implementation, followed by actual STL serialization. Physical field values/grid/metadata and STL bytes must match; elapsed times and allocation budgets may differ. Frozen historical STL bytes are not the repeatability reference. No second CAD-domain run is claimed.'}


def _nearest_vertex_distances(source, target, *, chunk_size=65536):
    from scipy.spatial import cKDTree
    if (len(source) == 0 or len(target) == 0 or not np.isfinite(source).all()
            or not np.isfinite(target).all()):
        raise ValueError('Geometry difference needs nonempty finite actual STL vertex sets')
    tree = cKDTree(target)
    minimum, maximum, square_sum, count = float('inf'), 0., 0., 0
    for offset in range(0, len(source), chunk_size):
        distance, _ = tree.query(source[offset:offset+chunk_size], k=1, workers=1)
        minimum = min(minimum, float(distance.min()))
        maximum = max(maximum, float(distance.max()))
        square_sum += float(np.dot(distance, distance))
        count += len(distance)
    return {'vertex_count': count, 'minimum_mm': minimum, 'maximum_mm': maximum,
        'rms_mm': float(np.sqrt(square_sum/count))}


def _frozen_geometry_difference(frozen_path, current_path):
    """Measure actual serialized geometry; these distances do not grant approval."""
    from shoe_poc.validation.lattice import _mesh
    frozen, current = _mesh(frozen_path), _mesh(current_path)
    forward = _nearest_vertex_distances(frozen.vertices, current.vertices)
    backward = _nearest_vertex_distances(current.vertices, frozen.vertices)
    old_bounds, new_bounds = np.asarray(frozen.bounds), np.asarray(current.bounds)
    old_volume, new_volume = float(frozen.volume), float(current.volume)
    return {'frozen_stl_sha256': sha256(frozen_path), 'current_stl_sha256': sha256(current_path),
        'frozen_bounds_mm': old_bounds.tolist(), 'current_bounds_mm': new_bounds.tolist(),
        'bounds_delta_mm': (new_bounds-old_bounds).tolist(),
        'maximum_absolute_bounds_delta_mm': float(np.max(np.abs(new_bounds-old_bounds))),
        'frozen_volume_mm3': old_volume, 'current_volume_mm3': new_volume,
        'volume_delta_mm3': new_volume-old_volume,
        'relative_volume_delta': (new_volume-old_volume)/old_volume if old_volume else None,
        'frozen_triangle_count': len(frozen.faces), 'current_triangle_count': len(current.faces),
        'frozen_to_current_nearest_vertex': forward, 'current_to_frozen_nearest_vertex': backward,
        'symmetric_nearest_vertex_minimum_mm': min(forward['minimum_mm'], backward['minimum_mm']),
        'symmetric_nearest_vertex_maximum_mm': max(forward['maximum_mm'], backward['maximum_mm']),
        'distance_scope': 'All reimported actual STL vertices, in both directions, to the nearest vertex in the other STL. These are vertex-set distances, not continuous-surface Hausdorff distances or a proof of topological equivalence.',
        'appearance_approval_transferred': False,
        'acceptance_scope': 'Measured change only; numerical acceptability and user appearance approval require the separately bound milestone acceptance report.'}


def _verify_variant(task):
    """One isolated OCP session and independent RSS monitor per spawned process."""
    directory, domain_source = Path(task['directory']), Path(task['domain_source'])
    name, period, threshold = task['name'], task['period'], task['threshold']
    target = Path(task.get('destination', directory))
    path = (_variant_output(target, name, (directory, domain_source)) if task.get('closeout')
            else target/'variants'/name)
    validate_output_paths(path, Path(task['baseline']['directory']))
    if (path/'verification_report.json').is_symlink():
        raise ValueError('Variant verification output must not be a symbolic link')
    path.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    worker_memory_mb = task.get('worker_memory_mb', 4096)
    allocation_bytes = int(worker_memory_mb*1024**2)
    try:
        with MemoryBudget(worker_memory_mb, path, 'verify-lattice-variant') as budget:
            # Both checks precede loading the source arrays or importing CAD.
            _check_hashes(directory, task['primary_hashes'])
            _check_hashes(domain_source, task['domain_hashes'])
            spec = ShoeSpec.load(directory/'shoe_spec.json')
            baseline = task['baseline']
            base = Path(baseline['directory'])
            if accepted_baseline(base, spec, Path(baseline['acceptance_file'])) != baseline:
                raise ValueError('Worker accepted source provenance changed')
            values, plan, metadata = load_domain(domain_source)
            if not np.isclose(metadata['spacing_mm'], plan.spacing_mm, rtol=0., atol=1e-9):
                raise ValueError('Domain NPZ and metadata sampling spacings differ')
            from build123d import import_step
            shoe, cavity = import_step(base/'base_shoe.step'), import_step(base/'cavity.step')
            envelope = SoleEnvelope.from_metadata(metadata['envelope']) if metadata.get('envelope') else None
            if task.get('closeout'):
                from shoe_poc.lattice.cache import load_certified_domain
                certified = load_certified_domain(domain_source, baseline=baseline, shape=shoe,
                    z_min_mm=metadata['sole_z_bounds_mm'][0], z_max_mm=metadata['sole_z_bounds_mm'][1],
                    settings=spec.lattice.domain, spacing_mm=plan.spacing_mm,
                    max_voxels=task.get('max_voxels', 25_000_000), max_memory_mb=worker_memory_mb,
                    envelope=envelope)
                values, plan, metadata = certified.field_mm, certified.plan, certified.metadata
            probe = NativeDomainProbe(shoe, cavity, *metadata['sole_z_bounds_mm'], envelope=envelope)
            settings = GyroidParameters(period, threshold, plan.spacing_mm, tuple(spec.lattice.phase_origin_mm))
            field = sample_gyroid(settings, plan, domain_signed_field=values, domain_metadata=metadata,
                                 max_memory_bytes=allocation_bytes,
                                 max_voxels=task.get('max_voxels', 10_000_000))
            frozen_field_match = None
            if task.get('closeout') and name in ('current_primary', 'repeat'):
                with np.load(directory/'gyroid_field.npz', allow_pickle=False) as original_field:
                    frozen_field_match = _same_sampled_field(original_field, field)
            save_field_npz(field, path/'gyroid_field.npz')
            result = extract_lattice_mesh(field, max_memory_bytes=allocation_bytes)
            export_lattice(result, path/'lattice.stl')
            mesh = validate_lattice_mesh(path/'lattice.stl', reference_mesh=result.mesh)
            native = validate_lattice_domain(path/'lattice.stl', classify_points=probe.classify_points,
                max_samples=2048, native_line_probe=lambda model: validate_native_line_containment(
                    model, scanline=probe.scanline, maximum_lines=24))
            implicit = validate_gyroid_residual(path/'lattice.stl', period_mm=period, threshold=threshold,
                phase_origin_mm=settings.phase_origin_mm,
                domain_field=RegularGridInterpolator(plan.axes, values, bounds_error=False, fill_value=1.),
                cap_tolerance_mm=np.sqrt(3)*plan.spacing_mm,
                max_samples=max(1, len(result.mesh.vertices)),
                residual_tolerance=(2*np.pi/period*plan.spacing_mm)**2/4+1e-4)
            if not implicit['all_vertices_checked']:
                raise RuntimeError('Variant analytic residual did not cover every exported vertex')
            checks = {'mesh': mesh, 'cad_samples': native, 'implicit_residual': implicit}
            if envelope is not None:
                from shoe_poc.validation.lattice_coverage import NativeVerticalSoleSections, validate_lattice_coverage
                sections = NativeVerticalSoleSections(shoe, cavity, upper_z=envelope.upper_z,
                                                     z_min_mm=envelope.z_bounds[0])
                checks['sole_coverage'] = validate_lattice_coverage(path/'lattice.stl',
                    native_sections=sections, bounds_mm=sections.bounds_mm,
                    period_mm=period, resolution_mm=plan.spacing_mm,
                    clearance_mm=spec.lattice.domain.clearance_mm,
                    domain_field=RegularGridInterpolator(plan.axes, values, bounds_error=False, fill_value=1.),
                    reference_datum_mm=envelope.z_bounds[0]+spec.sole.height_mm)
            if task.get('closeout'):
                if envelope is None or spec.lattice.domain.refinement is None:
                    raise ValueError('Closeout requires the curved domain and explicit terminal refinement policy')
                from shoe_poc.validation.lattice_terminal import sample_native_terminal_nodes, validate_terminal_coverage
                guide = json.loads((base/'geometry_report.json').read_text())['sole']['lower_foot_station_profiles']
                nodes = sample_native_terminal_nodes(shoe, cavity, envelope=envelope, axes=plan.axes,
                    guide=guide, period_mm=period, computation_bounds_mm=metadata['computation_bounds_mm'])
                expected_path = path/'terminal_expectations.json'
                write_json(expected_path, {'nodes': nodes, 'baseline': baseline,
                    'domain_field_sha256': task['domain_hashes']['domain_field.npz'],
                    'domain_metadata_sha256': task['domain_hashes']['domain_metadata.json'],
                    'domain_sampling_run_id': task['domain_sampling_run_id'],
                    'period_mm': period, 'origin_mm': list(plan.origin_mm), 'spacing_mm': plan.spacing_mm,
                    'shape_xyz': list(plan.shape), 'scope': 'Fresh independent native CAD terminal nodes; no generation anchors reused'})
                terminal = validate_terminal_coverage(path/'lattice.stl', field_mm=values,
                    gyroid_values=field.values, origin_mm=plan.origin_mm, spacing_mm=plan.spacing_mm,
                    expected_nodes=nodes, clearance_mm=spec.lattice.domain.clearance_mm,
                    approximation_tolerance_mm=spec.lattice.domain.refinement.coverage_tolerance_mm,
                    analytic_parameters={'period_mm': period, 'threshold': threshold,
                        'phase_origin_mm': settings.phase_origin_mm, 'cap_inset_mm': field.metadata['cap_inset_mm']},
                    actual_level=result.metadata['level'])
                terminal['expected_nodes_sha256'] = sha256(expected_path)
                checks['principal_terminal_coverage'] = terminal
            status = 'PASS' if all(r['geometry_status'] == 'PASS' for r in checks.values()) else 'FAIL'
            # Detect changes during computation as well as before loading.
            _check_hashes(directory, task['primary_hashes'])
            _check_hashes(domain_source, task['domain_hashes'])
            if accepted_baseline(base, spec, Path(baseline['acceptance_file'])) != baseline:
                raise ValueError('Worker frozen source changed during validation')
            record = {'name': name, 'status': status, 'settings': {
                    'period_mm': period, 'threshold': threshold, 'resolution_mm': plan.spacing_mm,
                    'phase_origin_mm': list(settings.phase_origin_mm)},
                'elapsed_seconds': time.perf_counter()-started, 'domain_source': str(domain_source),
                'domain_field_sha256': task['domain_hashes']['domain_field.npz'],
                'domain_metadata_sha256': task['domain_hashes']['domain_metadata.json'],
                'primary_m2_run_id': task['primary_m2_run_id'],
                'domain_sampling_run_id': task['domain_sampling_run_id'],
                'domain_provenance': task['domain_provenance'], 'frozen_baseline': baseline,
                'lattice_sha256': sha256(path/'lattice.stl'), 'lattice_bytes': (path/'lattice.stl').stat().st_size,
                'meshing': result.metadata, 'validation': checks,
                'worker': {'pid': os.getpid(), 'process_start_method': 'spawn',
                           'rss_limit_mb': worker_memory_mb, 'sampled_peak_rss_bytes': budget.peak_bytes,
                           'allocation_preflight_bytes': allocation_bytes},
                'source_artifact_hashes': task['primary_hashes'],
                'domain_artifact_hashes': task['domain_hashes'],
                'validator_code_hashes': task.get('validator_code_hashes', {}),
                'environment': environment()}
            if task.get('closeout'):
                record['closeout_report_version'] = CLOSEOUT_REPORT_VERSION
                record['identical_to_frozen_primary_sampled_field'] = frozen_field_match
                record['extraction_implementation'] = _extraction_implementation(result.metadata)
                record['gyroid_field_sha256'] = sha256(path/'gyroid_field.npz')
                record['terminal_expectations_sha256'] = sha256(path/'terminal_expectations.json')
            write_json(path/'verification_report.json', record)
            return record
    except Exception as error:
        write_json(path/'failure_report.json', {'status': 'FAIL', 'name': name,
            'primary_m2_run_id': task['primary_m2_run_id'], 'error_type': type(error).__name__,
            'error': str(error), 'traceback': traceback.format_exc(),
            'diagnostics': getattr(error, 'metadata', {}),
            'elapsed_seconds': time.perf_counter()-started})
        raise


def verify(directory: Path, resolution_domain: Path | None, jobs: int = 2, reuse_passed: bool = False,
           worker_memory_mb: float = 4096, threshold_variant: float = .45,
           destination: Path | None = None, closeout: bool = False,
           appearance_acceptance: Path | None = None):
    if isinstance(jobs, bool) or jobs not in (1, 2):
        raise ValueError('Variant jobs must be 1 or 2 isolated spawned processes')
    if not np.isfinite(worker_memory_mb) or worker_memory_mb <= 0:
        raise ValueError('Worker memory budget must be positive and finite')
    if threshold_variant not in (.45, .5):
        raise ValueError('Threshold comparison must select the explicit 0.45 or 0.5 case')
    directory = directory.resolve()
    target = _output_root(directory, destination) if destination is not None else directory
    if closeout and destination is None:
        raise ValueError('Closeout requires a separate output destination')
    if closeout and threshold_variant != .45:
        raise ValueError('Closeout requires the requested threshold 0.45; substitution is not permitted')
    resolution_domain = resolution_domain.resolve() if resolution_domain else None
    appearance = None
    if appearance_acceptance:
        appearance_acceptance = appearance_acceptance.resolve()
        appearance = {'path': str(appearance_acceptance), 'sha256': sha256(appearance_acceptance),
            'status': 'EXTERNAL_EVIDENCE_RECORDED',
            'scope': 'Recorded by hash; this numerical regression does not infer or evaluate user approval'}
    execution = json.loads((directory/'execution_report.json').read_text())
    if execution['status'] != 'PASS':
        raise ValueError('Primary M2 run must pass before parameter verification')
    spec = ShoeSpec.load(directory/'shoe_spec.json')
    baseline = execution['baseline']
    base = Path(baseline['directory'])
    validate_output_paths(target, base)
    current = accepted_baseline(base, spec, Path(baseline['acceptance_file']))
    if current != baseline:
        raise ValueError('Accepted source provenance changed')
    for name in ('domain_field.npz', 'domain_metadata.json', 'lattice.stl', 'shoe_spec.json', 'lattice_metadata.json'):
        if sha256(directory/name) != execution['outputs'][name]['sha256']:
            raise ValueError(f'Primary M2 artifact changed: {name}')
    if closeout:
        for name in ('domain_provenance.json', 'gyroid_field.npz', 'validation_report.json'):
            if sha256(directory/name) != execution['outputs'][name]['sha256']:
                raise ValueError(f'Primary M2 execution artifact changed: {name}')
    domain, grid, domain_meta = load_domain(directory)
    parameters = spec.lattice
    source = execution.get('domain_source', {})
    if source.get('mode') == 'validated_cached_CAD_certificates':
        primary_provenance = source['provenance']
        if primary_provenance.get('baseline') != baseline or not primary_provenance.get('run_id'):
            raise ValueError('Primary cached domain has no bound original CAD sampling run')
        primary_sampling_run_id = primary_provenance['run_id']
    else:
        primary_sampling_run_id = execution['run_id']
        primary_provenance = {'run_id': primary_sampling_run_id, 'baseline': baseline,
            'domain_field_sha256': sha256(directory/'domain_field.npz'),
            'domain_metadata_sha256': sha256(directory/'domain_metadata.json'),
            'parameters': {'spacing_mm': grid.spacing_mm, 'clearance_mm': parameters.domain.clearance_mm,
                'sole_z_bounds_mm': domain_meta['sole_z_bounds_mm'], 'bounds_mm': parameters.domain.bounds_mm,
                'envelope': domain_meta.get('envelope')}}
    if closeout or source.get('mode') == 'seed_plus_adaptive_CAD_certificates':
        primary_provenance = _bound_domain_provenance(directory, baseline, spec, execution=execution)
        # The adaptive run certifies the final field. Its coarse seed is not the final source.
        primary_sampling_run_id = primary_provenance['run_id']
    threshold_name = f'threshold_{round(threshold_variant*100):03d}'
    controls = [('period_16', 16., parameters.threshold, directory),
                (threshold_name, parameters.period_mm, threshold_variant, directory),
                ('repeat', parameters.period_mm, parameters.threshold, directory)]
    if closeout:
        controls.insert(0, ('current_primary', parameters.period_mm, parameters.threshold, directory))
    if resolution_domain:
        if closeout:
            # Domain-only inputs may live in target/resolution_domain; variants
            # remain siblings and can never overwrite these certificates.
            _variant_output(target, 'resolution_0p5', (directory, resolution_domain))
        provenance = json.loads((resolution_domain/'domain_provenance.json').read_text())
        if closeout:
            alt_execution_path = resolution_domain/'execution_report.json'
            alt_execution = json.loads(alt_execution_path.read_text()) if alt_execution_path.exists() else None
            provenance = _bound_domain_provenance(resolution_domain, baseline, spec, execution=alt_execution, require_execution_pass=False)
        if provenance['baseline'] != baseline:
            raise ValueError('Resolution domain does not bind the same frozen CAD inputs')
        for filename, key in [('domain_field.npz', 'domain_field_sha256'),
                              ('domain_metadata.json', 'domain_metadata_sha256')]:
            if sha256(resolution_domain/filename) != provenance[key]:
                raise ValueError(f'Resolution domain evidence changed: {filename}')
        alt = json.loads((resolution_domain/'domain_metadata.json').read_text())
        if closeout and not np.isclose(alt['spacing_mm'], .5, rtol=0., atol=1e-9):
            raise ValueError('Closeout resolution comparison requires the actual 0.5 mm certified grid')
        if np.isclose(alt['spacing_mm'], grid.spacing_mm, rtol=0., atol=1e-9):
            raise ValueError('Resolution comparison must use a different actual sampling spacing')
        for key in ('sole_z_bounds_mm', 'computation_bounds_mm', 'clearance_mm'):
            if not np.allclose(alt[key], domain_meta[key], rtol=0., atol=1e-6):
                raise ValueError(f'Resolution comparison changed the actual CAD domain parameter: {key}')
        if alt.get('envelope') != domain_meta.get('envelope'):
            raise ValueError('Resolution comparison changed the curved sole envelope')
        if (provenance['parameters']['bounds_mm'] != parameters.domain.bounds_mm
                or provenance['parameters']['clearance_mm'] != parameters.domain.clearance_mm
                or provenance['parameters'].get('envelope') != domain_meta.get('envelope')
                or not np.isclose(provenance['parameters']['spacing_mm'], alt['spacing_mm'], rtol=0., atol=1e-9)
                or not np.allclose(provenance['parameters']['sole_z_bounds_mm'], alt['sole_z_bounds_mm'], rtol=0., atol=1e-9)
                or not provenance['run_id'] or provenance['run_id'] in (execution['run_id'], primary_sampling_run_id)):
            raise ValueError('Resolution domain needs its own bound CAD sampling run and matching design parameters')
        # Parent also checks the external/NPZ identity before dispatch.
        alt_values, _, _ = load_domain(resolution_domain)
        del alt_values
        controls.append(('resolution_0p5' if closeout else 'resolution', parameters.period_mm, parameters.threshold, resolution_domain))
    primary_hashes = {name: sha256(directory/name) for name in
        ('execution_report.json', 'shoe_spec.json', 'lattice_metadata.json', 'lattice.stl')}
    if closeout:
        primary_hashes.update({name: sha256(directory/name) for name in ('domain_provenance.json', 'gyroid_field.npz', 'validation_report.json')})
    validator_code_hashes = {str(Path(__file__).resolve()): sha256(Path(__file__))}
    if closeout:
        import importlib
        for name in ('lattice.meshing', 'lattice.gyroid', 'lattice.cache', 'lattice.domain',
                     'lattice.envelope', 'lattice.pipeline', 'lattice.refinement',
                     'validation.lattice', 'validation.lattice_terminal', 'validation.lattice_coverage'):
            module = importlib.import_module('shoe_poc.'+name)
            validator_code_hashes[str(Path(module.__file__).resolve())] = sha256(Path(module.__file__))
    tasks, reused, reused_hashes = [], {}, {}
    for name, period, threshold, domain_source in controls:
        source_hashes = {filename: sha256(domain_source/filename)
                         for filename in ('domain_field.npz', 'domain_metadata.json')}
        if domain_source != directory or closeout:
            source_hashes['domain_provenance.json'] = sha256(domain_source/'domain_provenance.json')
        if closeout and (domain_source/'execution_report.json').exists():
            source_hashes['execution_report.json'] = sha256(domain_source/'execution_report.json')
        path = _variant_output(target, name, (directory, resolution_domain)) if closeout else target/'variants'/name
        validate_output_paths(path, base)
        if (path/'verification_report.json').is_symlink():
            raise ValueError('Variant verification output must not be a symbolic link')
        path.mkdir(parents=True, exist_ok=True)
        saved_report = path/'verification_report.json'
        if reuse_passed and saved_report.is_file():
            previous = json.loads(saved_report.read_text())
            expected_spacing = json.loads((domain_source/'domain_metadata.json').read_text())['spacing_mm']
            if (previous['status'] != 'PASS' or previous['primary_m2_run_id'] != execution['run_id']
                    or previous['frozen_baseline'] != baseline
                    or previous['domain_field_sha256'] != source_hashes['domain_field.npz']
                    or previous['domain_metadata_sha256'] != source_hashes['domain_metadata.json']
                    or previous['settings'] != {'period_mm':period, 'threshold':threshold,
                        'resolution_mm':expected_spacing,'phase_origin_mm':list(parameters.phase_origin_mm)}
                    or not all(r['geometry_status']=='PASS' for r in previous['validation'].values())
                    or sha256(path/'lattice.stl') != previous['lattice_sha256']
                    or (path/'lattice.stl').stat().st_size != previous['lattice_bytes']):
                raise ValueError(f'Cannot reuse changed or mismatched variant evidence: {name}')
            if closeout and (previous.get('source_artifact_hashes') != primary_hashes
                    or previous.get('domain_artifact_hashes') != source_hashes
                    or previous.get('validator_code_hashes') != validator_code_hashes
                    or previous.get('domain_provenance') != (primary_provenance if domain_source == directory else provenance)
                    or previous.get('domain_sampling_run_id') != (primary_sampling_run_id if domain_source == directory else provenance['run_id'])
                    or 'principal_terminal_coverage' not in previous['validation']
                    or previous.get('closeout_report_version') != CLOSEOUT_REPORT_VERSION
                    or previous.get('extraction_implementation') != _extraction_implementation(previous['meshing'])
                    or sha256(path/'gyroid_field.npz') != previous.get('gyroid_field_sha256')
                    or sha256(path/'terminal_expectations.json') != previous.get('terminal_expectations_sha256')):
                raise ValueError(f'Cannot reuse incomplete closeout evidence: {name}')
            reused[name] = previous
            reused_hashes[name] = sha256(saved_report)
            print(f'Reusing bound PASS evidence: {name}', flush=True)
            continue
        for stale in ('failure_report.json', 'verification_report.json'):
            (path/stale).unlink(missing_ok=True)
        tasks.append({'name': name, 'period': period, 'threshold': threshold,
            'directory': str(directory), 'destination': str(target), 'closeout': closeout,
            'validator_code_hashes': validator_code_hashes, 'domain_source': str(domain_source),
            'primary_hashes': primary_hashes, 'domain_hashes': source_hashes,
            'baseline': baseline, 'primary_m2_run_id': execution['run_id'],
            'domain_sampling_run_id': primary_sampling_run_id if domain_source == directory else provenance['run_id'],
            'domain_provenance': primary_provenance if domain_source == directory else provenance,
            'worker_memory_mb': worker_memory_mb,
            'max_voxels': execution.get('resource_limits', {}).get('max_voxels', 10_000_000)})
    del domain
    print(f'Verifying {len(tasks)} actual lattice variants with {jobs} isolated spawn workers; '
          f'parent RSS guard 8192 MiB covers parent only; each worker has its own {worker_memory_mb:g} MiB guard.', flush=True)
    runs, errors = dict(reused), {}
    with ProcessPoolExecutor(max_workers=jobs, mp_context=multiprocessing.get_context('spawn')) as pool:
        futures = {pool.submit(_verify_variant, task): task['name'] for task in tasks}
        for future in as_completed(futures):
            name = futures[future]
            try:
                runs[name] = future.result()
                print(f'Completed actual lattice variant: {name}: {runs[name]["status"]}', flush=True)
            except Exception as error:
                errors[name] = {'error_type': type(error).__name__, 'error': str(error)}
    # Stable ordering in the saved aggregate, independent of completion order.
    runs = {name: runs[name] for name, *_ in controls if name in runs}
    for name,digest in reused_hashes.items():
        if sha256(target/'variants'/name/'verification_report.json') != digest:
            raise ValueError(f'Reused verification report changed during computation: {name}')
    required = CLOSEOUT_CASES if closeout else {name for name, *_ in controls}
    frozen_comparison = None
    if closeout and 'current_primary' in runs:
        frozen_comparison = _frozen_geometry_difference(directory/'lattice.stl', target/'variants/current_primary/lattice.stl')
    complete = set(runs) == required and not errors
    if errors:
        write_json(target/'parameter_change_report.json', {'status': 'FAIL',
            'generated_at': utc_now(), 'primary_run_id': execution['run_id'],
            'runs': runs, 'worker_errors': errors, 'jobs': jobs,
            'required_variants': sorted(required), 'completed_variants': list(runs),
            'source_directory': str(directory), 'source_artifact_hashes': primary_hashes,
            'validator_code_hashes': validator_code_hashes,
            'closeout_report_version': CLOSEOUT_REPORT_VERSION if closeout else None,
            'frozen_primary_geometry_difference': frozen_comparison,
            'manufacturing_status': 'NOT_VERIFIED',
            'acceptance': _closeout_acceptance('FAIL', complete, appearance) if closeout else {}})
        raise RuntimeError(f'Variant worker failure: {errors}')
    if closeout:
        reference_run = runs['current_primary']
        original = reference_run['meshing']['integrity']
        reference_stl_sha = reference_run['lattice_sha256']
        repeat_report = _repeat_comparison(target/'variants/current_primary', target/'variants/repeat',
            reference_implementation=reference_run['extraction_implementation'],
            repeated_implementation=runs['repeat']['extraction_implementation'])
        repeatable = repeat_report['status'] == 'PASS'
    else:
        original = json.loads((directory/'lattice_metadata.json').read_text())['meshing']['integrity']
        reference_stl_sha = sha256(directory/'lattice.stl')
        repeatable = runs['repeat']['lattice_sha256'] == reference_stl_sha
        repeat_report = {'status': 'PASS' if repeatable else 'FAIL',
            'identical_exported_stl_sha256': repeatable,
            'scope': 'Repeated sampling of identical certified domain and identical parameters, independent MC extraction and STL export; not a second CAD-domain run'}
    changes = {name: {'different_stl_hash': run['lattice_sha256'] != reference_stl_sha,
              'volume_delta_mm3': run['meshing']['integrity']['volume_mm3']-original['volume_mm3'],
              'triangle_delta': run['meshing']['integrity']['triangles']-original['triangles']}
              for name, run in runs.items() if name not in ('repeat', 'current_primary')}
    status = 'PASS' if all(run['status'] == 'PASS' for run in runs.values()) and repeatable and all(c['different_stl_hash'] and abs(c['volume_delta_mm3']) > 1.
                                        for c in changes.values()) else 'FAIL'
    if closeout and not complete and status == 'PASS':
        status = 'NOT_VERIFIED'
    report = {'status': status, 'generated_at': utc_now(), 'primary_run_id': execution['run_id'],
        'primary_stl_sha256': sha256(directory/'lattice.stl'), 'primary_domain_sha256': sha256(directory/'domain_field.npz'),
        'parameter_reference_case': 'current_primary' if closeout else 'frozen_primary',
        'parameter_reference_stl_sha256': reference_stl_sha,
        'parameter_geometry_changes': changes, 'repeatability': repeat_report,
        'closeout_report_version': CLOSEOUT_REPORT_VERSION if closeout else None,
        'frozen_primary_geometry_difference': frozen_comparison,
        'resolution_scope': 'Independently certified CAD domains; full cell diagonal reserve and conservative omitted volume change with spacing. Not a fixed-domain convergence proof.',
        'resolution_comparison': {'fixed_domain_convergence': False,
            'primary_spacing_mm': grid.spacing_mm,
            'primary_cell_diagonal_reserve_mm': np.sqrt(3)*grid.spacing_mm,
            'comparison_spacing_mm': None if resolution_domain is None else alt['spacing_mm'],
            'comparison_cell_diagonal_reserve_mm': None if resolution_domain is None else np.sqrt(3)*alt['spacing_mm'],
            'domain_changes_with_voxel_reserve': resolution_domain is not None},
        'runs': runs, 'parallel_execution': {'jobs': jobs, 'start_method': 'spawn',
            'parent_rss_limit_mb': 8192, 'worker_rss_limit_mb': worker_memory_mb,
            'rss_scope': 'Parent guard covers only parent RSS; each worker independently monitors its own RSS, no aggregate OS memory limit'},
        'reused_verified_reports': reused_hashes,
        'environment': environment(), 'manufacturing_status': 'NOT_VERIFIED',
        'required_variants': sorted(required), 'completed_variants': list(runs),
        'source_directory': str(directory), 'output_directory': str(target),
        'source_artifact_hashes': primary_hashes, 'validator_code_hashes': validator_code_hashes,
        'acceptance': _closeout_acceptance(status, complete, appearance) if closeout else {}}
    accepted_baseline(base, spec, Path(baseline['acceptance_file']))
    _check_hashes(directory, primary_hashes)
    if appearance and sha256(appearance_acceptance) != appearance['sha256']:
        raise ValueError('External appearance acceptance evidence changed during computation')
    for code_path, digest in validator_code_hashes.items():
        if sha256(Path(code_path)) != digest:
            raise ValueError('Verification code changed during computation')
    write_json(target/'parameter_change_report.json', report)
    if status != 'PASS':
        raise RuntimeError('Parameter geometry change or repeatability did not qualify')
    if not closeout:
        from shoe_poc.lattice_preview import render_parameter_sections
        render_parameter_sections(directory, target/'parameter_sections.png')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('output/m2'))
    parser.add_argument('--closeout-output', type=Path, help='Write strict parameter closeout to a separate directory, e.g. output/m2_closeout; --output stays the frozen source')
    parser.add_argument('--appearance-acceptance', type=Path, help='Record external user appearance acceptance by path/SHA; overall milestone gates are handled separately')
    parser.add_argument('--resolution-domain', type=Path)
    parser.add_argument('--jobs', type=int, choices=(1, 2), default=2,
                        help='Independent spawned CAD workers (default 2, maximum 2); each has its own configurable memory guard')
    parser.add_argument('--reuse-passed', action='store_true', help='Reuse prior PASS evidence only when run, source, parameters and STL hashes are unchanged')
    parser.add_argument('--worker-memory-mb', type=float, default=4096,
                        help='Per-worker RSS and allocation preflight budget in MiB (default 4096)')
    parser.add_argument('--threshold-variant', type=float, choices=(.45, .5), default=.45,
                        help='Requested threshold comparison; failed cases remain evidence and are never silently changed')
    args = parser.parse_args()
    primary = json.loads((args.output/'execution_report.json').read_text())
    validate_output_paths(args.output, Path(primary['baseline']['directory']))
    destination = _output_root(args.output, args.closeout_output) if args.closeout_output else args.output
    validate_output_paths(destination, Path(primary['baseline']['directory']))
    with MemoryBudget(8192, destination, 'verify-lattice'):
        verify(args.output, args.resolution_domain, jobs=args.jobs, reuse_passed=args.reuse_passed,
               worker_memory_mb=args.worker_memory_mb, threshold_variant=args.threshold_variant,
               destination=args.closeout_output, closeout=args.closeout_output is not None,
               appearance_acceptance=args.appearance_acceptance)
