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
    with np.load(path, allow_pickle=False) as saved:
        values = saved['field_mm'].copy()
        spacing = float(saved['spacing_mm'])
        origin = saved['origin_mm'].copy()
        metadata = json.loads(str(saved['metadata_json']))
    external = json.loads((directory/'domain_metadata.json').read_text())
    if (metadata != external or list(values.shape) != metadata['shape_xyz']
            or not np.isclose(spacing, metadata['spacing_mm'], rtol=0., atol=1e-9)
            or not np.allclose(origin, metadata['origin_mm'], rtol=0., atol=1e-9)):
        raise ValueError('Domain field and external metadata do not identify the same grid')
    axes = tuple(origin[i]+np.arange(values.shape[i])*spacing for i in range(3))
    return values, GridPlan(axes, spacing, int(metadata['estimated_peak_bytes'])), metadata


def _check_hashes(directory, expected):
    for name, digest in expected.items():
        if sha256(directory/name) != digest:
            raise ValueError(f'Verification source artifact changed: {directory/name}')


def _verify_variant(task):
    """One isolated OCP session and independent RSS monitor per spawned process."""
    directory, domain_source = Path(task['directory']), Path(task['domain_source'])
    name, period, threshold = task['name'], task['period'], task['threshold']
    path = directory/'variants'/name
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
            probe = NativeDomainProbe(shoe, cavity, *metadata['sole_z_bounds_mm'], envelope=envelope)
            settings = GyroidParameters(period, threshold, plan.spacing_mm, tuple(spec.lattice.phase_origin_mm))
            field = sample_gyroid(settings, plan, domain_signed_field=values, domain_metadata=metadata,
                                 max_memory_bytes=allocation_bytes,
                                 max_voxels=task.get('max_voxels', 10_000_000))
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
                'environment': environment()}
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
           worker_memory_mb: float = 4096, threshold_variant: float = .45):
    if isinstance(jobs, bool) or jobs not in (1, 2):
        raise ValueError('Variant jobs must be 1 or 2 isolated spawned processes')
    if not np.isfinite(worker_memory_mb) or worker_memory_mb <= 0:
        raise ValueError('Worker memory budget must be positive and finite')
    if threshold_variant not in (.45, .5):
        raise ValueError('Threshold comparison must select the explicit 0.45 or 0.5 case')
    directory = directory.resolve()
    resolution_domain = resolution_domain.resolve() if resolution_domain else None
    execution = json.loads((directory/'execution_report.json').read_text())
    if execution['status'] != 'PASS':
        raise ValueError('Primary M2 run must pass before parameter verification')
    spec = ShoeSpec.load(directory/'shoe_spec.json')
    baseline = execution['baseline']
    base = Path(baseline['directory'])
    current = accepted_baseline(base, spec, Path(baseline['acceptance_file']))
    if current != baseline:
        raise ValueError('Accepted source provenance changed')
    for name in ('domain_field.npz', 'domain_metadata.json', 'lattice.stl', 'shoe_spec.json', 'lattice_metadata.json'):
        if sha256(directory/name) != execution['outputs'][name]['sha256']:
            raise ValueError(f'Primary M2 artifact changed: {name}')
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
    threshold_name = f'threshold_{round(threshold_variant*100):03d}'
    controls = [('period_16', 16., parameters.threshold, directory),
                (threshold_name, parameters.period_mm, threshold_variant, directory),
                ('repeat', parameters.period_mm, parameters.threshold, directory)]
    if resolution_domain:
        provenance = json.loads((resolution_domain/'domain_provenance.json').read_text())
        if provenance['baseline'] != baseline:
            raise ValueError('Resolution domain does not bind the same frozen CAD inputs')
        for filename, key in [('domain_field.npz', 'domain_field_sha256'),
                              ('domain_metadata.json', 'domain_metadata_sha256')]:
            if sha256(resolution_domain/filename) != provenance[key]:
                raise ValueError(f'Resolution domain evidence changed: {filename}')
        alt = json.loads((resolution_domain/'domain_metadata.json').read_text())
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
        controls.append(('resolution', parameters.period_mm, parameters.threshold, resolution_domain))
    primary_hashes = {name: sha256(directory/name) for name in
        ('execution_report.json', 'shoe_spec.json', 'lattice_metadata.json', 'lattice.stl')}
    tasks, reused, reused_hashes = [], {}, {}
    for name, period, threshold, domain_source in controls:
        source_hashes = {filename: sha256(domain_source/filename)
                         for filename in ('domain_field.npz', 'domain_metadata.json')}
        if domain_source != directory:
            source_hashes['domain_provenance.json'] = sha256(domain_source/'domain_provenance.json')
        path = directory/'variants'/name
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
            reused[name] = previous
            reused_hashes[name] = sha256(saved_report)
            print(f'Reusing bound PASS evidence: {name}', flush=True)
            continue
        for stale in ('failure_report.json', 'verification_report.json'):
            (path/stale).unlink(missing_ok=True)
        tasks.append({'name': name, 'period': period, 'threshold': threshold,
            'directory': str(directory), 'domain_source': str(domain_source),
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
        if sha256(directory/'variants'/name/'verification_report.json') != digest:
            raise ValueError(f'Reused verification report changed during computation: {name}')
    if errors:
        write_json(directory/'parameter_change_report.json', {'status': 'FAIL',
            'generated_at': utc_now(), 'primary_run_id': execution['run_id'],
            'runs': runs, 'worker_errors': errors, 'jobs': jobs,
            'manufacturing_status': 'NOT_VERIFIED'})
        raise RuntimeError(f'Variant worker failure: {errors}')
    original = json.loads((directory/'lattice_metadata.json').read_text())['meshing']['integrity']
    repeatable = runs['repeat']['lattice_sha256'] == sha256(directory/'lattice.stl')
    changes = {name: {'different_stl_hash': run['lattice_sha256'] != sha256(directory/'lattice.stl'),
              'volume_delta_mm3': run['meshing']['integrity']['volume_mm3']-original['volume_mm3'],
              'triangle_delta': run['meshing']['integrity']['triangles']-original['triangles']}
              for name, run in runs.items() if name != 'repeat'}
    status = 'PASS' if all(run['status'] == 'PASS' for run in runs.values()) and repeatable and all(c['different_stl_hash'] and abs(c['volume_delta_mm3']) > 1.
                                        for c in changes.values()) else 'FAIL'
    report = {'status': status, 'generated_at': utc_now(), 'primary_run_id': execution['run_id'],
        'primary_stl_sha256': sha256(directory/'lattice.stl'), 'primary_domain_sha256': sha256(directory/'domain_field.npz'),
        'parameter_geometry_changes': changes, 'repeatability': {
            'status': 'PASS' if repeatable else 'FAIL', 'identical_exported_stl_sha256': repeatable,
            'scope': 'Repeated sampling of identical certified domain and identical parameters, independent MC extraction and STL export; not a second CAD-domain run'},
        'resolution_scope': 'Independently certified CAD domains; full cell diagonal reserve and conservative omitted volume change with spacing. Not a fixed-domain convergence proof.',
        'runs': runs, 'parallel_execution': {'jobs': jobs, 'start_method': 'spawn',
            'parent_rss_limit_mb': 8192, 'worker_rss_limit_mb': worker_memory_mb,
            'rss_scope': 'Parent guard covers only parent RSS; each worker independently monitors its own RSS, no aggregate OS memory limit'},
        'reused_verified_reports': reused_hashes,
        'environment': environment(), 'manufacturing_status': 'NOT_VERIFIED'}
    accepted_baseline(base, spec, Path(baseline['acceptance_file']))
    write_json(directory/'parameter_change_report.json', report)
    if status != 'PASS':
        raise RuntimeError('Parameter geometry change or repeatability did not qualify')
    from shoe_poc.lattice_preview import render_parameter_sections
    render_parameter_sections(directory, directory/'parameter_sections.png')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('output/m2'))
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
    with MemoryBudget(8192, args.output, 'verify-lattice'):
        verify(args.output, args.resolution_domain, jobs=args.jobs, reuse_passed=args.reuse_passed,
               worker_memory_mb=args.worker_memory_mb, threshold_variant=args.threshold_variant)
