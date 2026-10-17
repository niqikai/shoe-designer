"""Fail-closed M3 assembly runner over the frozen, user-approved M1/M2 inputs."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import time
import zipfile

import numpy as np

from .schemas import AssemblySpec


class AssemblyGateError(ValueError):
    """M2 is not READY or its selected user-approved inputs do not match."""

    def __init__(self, message, report=None):
        self.report = report or {}
        super().__init__(message)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _binding(path: Path) -> dict:
    return {'path': str(path.resolve()), 'sha256': _sha256(path), 'bytes': path.stat().st_size}


def _resolve(path: str | Path, root: Path) -> Path:
    value = Path(path)
    return (value if value.is_absolute() else root / value).resolve()


def _validate_output_location(output: Path, protected: list[Path]) -> None:
    output = output.resolve()
    for source in protected:
        source = source.resolve()
        if output == source or output in source.parents or source in output.parents:
            raise ValueError(f'M3 output must be isolated from frozen input: {source}')
    if output.is_symlink():
        raise ValueError('M3 output cannot be a symbolic link')


def validate_assembly_output(output: Path, gate: dict) -> None:
    """Refuse output aliases/overwrites before the CLI starts RSS sampling."""
    paths = gate['_resolved_inputs']
    _validate_output_location(Path(output), [Path(paths[name]) for name in
        ('m1_directory', 'm2_source', 'm2_closeout', 'appearance_acceptance', 'assembly_spec')])
    resolved = Path(output).resolve()
    if resolved.exists():
        raise ValueError(f'M3 output already exists; refusing to overwrite: {resolved}')


def validate_assembly_gate(*, spec_path: Path, m2_source: Path, m2_closeout: Path,
                           appearance_acceptance: Path, project_root: Path | None = None) -> tuple[AssemblySpec, dict]:
    """Read and validate all M2 acceptance evidence without touching output."""
    from shoe_poc.acceptance import validate_m2_acceptance

    root = Path(project_root).resolve() if project_root else Path.cwd().resolve()
    spec_file = _resolve(spec_path, root)
    spec = AssemblySpec.load(spec_file)
    source, closeout, approval = (_resolve(p, root) for p in
                                  (m2_source, m2_closeout, appearance_acceptance))
    configured = {'m1_directory': _resolve(spec.m1_directory, root),
                  'm2_directory': _resolve(spec.m2_directory, root),
                  'm2_acceptance_report': _resolve(spec.m2_acceptance_report, root)}
    if configured['m2_directory'] != source or configured['m2_acceptance_report'] != approval:
        raise AssemblyGateError('M3 config M2 source/approval paths must match CLI-selected paths')
    try:
        binding = validate_m2_acceptance(source, closeout, approval, project_root=root)
    except Exception as error:
        raise AssemblyGateError('M2 acceptance gate is not READY: ' + str(error),
                                {'status': 'BLOCKED', 'M3_gate': 'BLOCKED', 'error': str(error)}) from error
    if binding.get('M3_gate') != 'READY':
        raise AssemblyGateError('M2 acceptance gate did not return READY',
                                {'status': 'BLOCKED', 'M3_gate': binding.get('M3_gate')})

    selected = binding.get('selected_lattice', {})
    approved_lattice = (source / 'lattice.stl').resolve()
    if Path(selected.get('path', '')).resolve() != approved_lattice:
        raise AssemblyGateError('Selected lattice path differs from the user-approved frozen M2 lattice', binding)
    actual = _binding(approved_lattice)
    if any(selected.get(key) != actual[key] for key in ('sha256', 'bytes')):
        raise AssemblyGateError('Selected lattice bytes differ from the user-approved M2 binding', binding)
    m1_directory = Path(binding['baseline']['directory']).resolve()
    if configured['m1_directory'] != m1_directory:
        raise AssemblyGateError('M3 config M1 directory differs from frozen M2 baseline')
    binding['_resolved_inputs'] = {
        'm2_source': str(source), 'm2_closeout': str(closeout),
        'appearance_acceptance': str(approval),
        'm1_directory': str(m1_directory), 'assembly_spec': str(spec_file),
        'selected_lattice': actual,
        'domain_field': _binding(source / 'domain_field.npz'),
        'domain_metadata': _binding(source / 'domain_metadata.json'),
    }
    return spec, binding


def assemble_shoe(*, spec_path: Path, m2_source: Path, m2_closeout: Path,
                  appearance_acceptance: Path, output: Path,
                  project_root: Path | None = None, gate_binding: dict | None = None) -> dict:
    """Build and export a topology-valid core/shell prototype after M2 READY.

    Upper hex holes and footbed access ports are deliberately not implemented.
    A valid prototype therefore remains PARTIAL and powder access is unverified.
    """
    started = time.perf_counter()
    root = Path(project_root).resolve() if project_root else Path.cwd().resolve()
    spec_file = _resolve(spec_path, root)
    # Re-read the gate immediately before touching output, even if the CLI ran
    # an earlier preflight before starting its RSS watcher.
    spec, gate = validate_assembly_gate(spec_path=spec_file, m2_source=m2_source,
        m2_closeout=m2_closeout, appearance_acceptance=appearance_acceptance,
        project_root=root)
    m2 = Path(gate['_resolved_inputs']['m2_source']).resolve()
    m1 = Path(gate['_resolved_inputs']['m1_directory']).resolve()
    output_path = _resolve(output, root)
    validate_assembly_output(output_path, gate)

    report = {'status': 'RUNNING', 'milestone': 3,
        'M3_gate': 'READY', 'm2_acceptance': gate,
        'operation': 'A = ((B - C) union L) intersect B; V = (B intersect C) - L',
        'configuration_sha256': spec.configuration_sha256(),
        'input_bindings': gate['_resolved_inputs'], 'repair_applied': False,
        'components_removed': 0, 'upper_hex_status': 'BLOCKED_NOT_IMPLEMENTED',
        'footbed_access_status': 'BLOCKED_NOT_IMPLEMENTED',
        'powder_removal_status': 'NOT_VERIFIED', 'manufacturing_status': 'NOT_VERIFIED',
        'global_self_intersections': 'NOT_VERIFIED'}
    output_path.mkdir(parents=True)
    try:
        from .core import assemble_core_shell, extract_core
        from .exports import export_assembly
        from .geometry import load_stl_exact, require_valid_mesh

        rules = _resolve(spec.manufacturing_rules_file, root)
        report['manufacturing_rules_path'] = str(rules)
        base_file, lattice_file = m1 / 'base_shoe.stl', m2 / 'lattice.stl'
        base_binding = gate['baseline']['files'].get('base_shoe.stl')
        if not base_binding or _binding(base_file)['sha256'] != base_binding['sha256']:
            raise AssemblyGateError('Frozen M1 base shoe STL changed after M2 gate validation', gate)
        base = load_stl_exact(base_file)
        lattice = load_stl_exact(lattice_file)
        policy = {'area_floor_mm2': spec.numerical_area_floor_mm2,
                  'volume_floor_mm3': spec.numerical_volume_floor_mm3,
                  'self_intersection_pair_budget': spec.self_intersection_pair_budget}
        input_reports = {
            'base': require_valid_mesh(base, require_single_body=True, **policy),
            'lattice': require_valid_mesh(lattice, require_single_body=False, **policy),
        }
        field_file = m2 / 'domain_field.npz'
        with zipfile.ZipFile(field_file) as archive:
            if 'field_mm.npy' not in archive.namelist():
                raise ValueError('Saved domain_field.npz omits field_mm')
            uncompressed = archive.getinfo('field_mm.npy').file_size
            if uncompressed > spec.maximum_core_grid_nodes * 8 + 4096:
                raise ValueError('Saved domain field exceeds the configured input allocation bound')
        with np.load(field_file, allow_pickle=False) as field:
            required = {'field_mm', 'origin_mm', 'spacing_mm'}
            if not required <= set(field.files):
                raise ValueError('Saved domain_field.npz lacks field_mm/origin_mm/spacing_mm XYZ records')
            domain_values = np.asarray(field['field_mm'])
            origin = np.asarray(field['origin_mm'], dtype=np.float64)
            spacing = float(np.asarray(field['spacing_mm']).item())
        if domain_values.ndim != 3 or domain_values.size > spec.maximum_core_grid_nodes:
            raise ValueError('Saved domain grid shape exceeds the configured XYZ node limit')
        domain_metadata = json.loads((m2 / 'domain_metadata.json').read_text())
        if (domain_metadata.get('axis_order') != 'XYZ'
                or domain_metadata.get('shape_xyz') != list(domain_values.shape)
                or not np.array_equal(np.asarray(domain_metadata.get('origin_mm'), dtype=float), origin)
                or float(domain_metadata.get('spacing_mm', -1)) != spacing):
            raise ValueError('Saved domain field grid differs from its accepted XYZ metadata')
        core_result = extract_core(domain_values, origin_mm=origin, spacing_mm=spacing,
            overlap_field_mm=spec.overlap_field_mm, max_memory_bytes=4 * 1024**3,
            area_floor_mm2=spec.numerical_area_floor_mm2,
            volume_floor_mm3=spec.numerical_volume_floor_mm3)
        assembled = assemble_core_shell(base, lattice, core_result.mesh,
            area_floor_mm2=spec.numerical_area_floor_mm2,
            volume_floor_mm3=spec.numerical_volume_floor_mm3,
            boolean_tolerance_mm=spec.numerical_distance_tolerance_mm,
            regional_x_bins=None)
        report.update(inputs=input_reports, core=core_result.metadata,
                      geometry=assembled.report, input_exact_indexing='coordinate identity only; all STL triangle records retained',
                      output_mesh={'vertices': len(assembled.mesh.vertices), 'triangles': len(assembled.mesh.faces),
                          'bounds_mm': assembled.mesh.bounds.tolist(), 'volume_mm3': float(assembled.mesh.volume)})
        report['lattice_component_participation'] = assembled.report['lattice_component_participation']
        report['regions'] = assembled.report['regions']
        report['global_self_intersections'] = assembled.report.get('global_self_intersections', 'NOT_VERIFIED')
        # Both pattern features remain blocked, so the correct deliverable is a
        # geometry prototype. It cannot be labeled complete M3 or manufacturing PASS.
        exported = export_assembly(assembled.mesh, output_path,
            self_intersection_pair_budget=spec.self_intersection_pair_budget,
            area_floor_mm2=spec.numerical_area_floor_mm2,
            volume_floor_mm3=spec.numerical_volume_floor_mm3,
            require_single_body=False)
        report['exports'] = exported
        authoritative = [row for name, row in exported.get('artifacts', {}).items()
                         if name.endswith(('.stl', '.3mf'))]
        if not authoritative or any(row.get('status') == 'FAIL' for row in authoritative):
            raise RuntimeError('An authoritative prototype export or reimport check failed; diagnostics are retained')
        # GLB uses float32 positions and may be too coarse for this high-detail
        # mesh. The engineering preview is therefore rendered from the exact
        # STL readback, never from a degraded GLB when its preflight fails.
        from .exports import read_ascii_stl64
        from .preview import render_assembly_preview
        preview_images = render_assembly_preview(read_ascii_stl64(output_path/'m3_core_shell_prototype.stl'),
            output_path/'views', source_stl=output_path/'m3_core_shell_prototype.stl',
            base_mesh=base)
        report['preview_images'] = preview_images
        report.update(status='PARTIAL',
            geometry_status=assembled.report.get('geometry_status', 'NOT_VERIFIED'),
            topology_status=assembled.report.get('topology_status', 'NOT_VERIFIED'),
            output_directory=str(output_path), duration_s=time.perf_counter()-started,
            m3_completion='PARTIAL_CORE_SHELL_PROTOTYPE',
            scope='Core/shell retention prototype only; upper hex and footbed void access are not implemented')
        from .manufacturing import manufacturing_report
        report['manufacturing'] = manufacturing_report(assembled.mesh, rules)
        (output_path / 'assembly_report.json').write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
        validation = {'milestone': 3, 'status': report['status'],
            'geometry_status': report['geometry_status'], 'topology_status': report['topology_status'],
            'shell_nesting_status': report.get('geometry', {}).get('shell_nesting_status', 'NOT_VERIFIED'),
            'global_self_intersections': report['global_self_intersections'],
            'shell_union_lattice': report['geometry'],
            'exports': report['exports'], 'manufacturing': report['manufacturing'],
            'upper_hex_status': report['upper_hex_status'],
            'footbed_access_status': report['footbed_access_status'],
            'powder_removal_status': report['powder_removal_status'],
            'physical_printing': 'NOT_TESTED', 'wear_testing': 'NOT_TESTED'}
        (output_path / 'validation_report.json').write_text(json.dumps(validation, indent=2, allow_nan=False) + '\n')
        manifest = {p.relative_to(output_path).as_posix(): {
            'sha256': _sha256(p), 'bytes': p.stat().st_size}
            for p in sorted(output_path.rglob('*')) if p.is_file() and p.name != 'artifact_manifest.json'}
        (output_path / 'artifact_manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
        return report
    except Exception as error:
        diagnostic = getattr(error, 'report', None)
        report.update(status='FAIL', output_directory=str(output_path), error=str(error),
                      diagnostics=diagnostic, duration_s=time.perf_counter()-started,
                      m3_completion='HALTED_WITH_DIAGNOSTICS')
        (output_path / 'assembly_report.json').write_text(json.dumps(report, indent=2, allow_nan=False, default=str) + '\n')
        raise
