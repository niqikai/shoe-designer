"""Core/shell assembly from the exact saved conservative domain samples."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import time
import numpy as np
import trimesh
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

from .geometry import GeometryError, require_valid_mesh, to_manifold, from_manifold


@dataclass
class CoreResult:
    mesh: trimesh.Trimesh
    metadata: dict


@dataclass
class AssemblyResult:
    mesh: trimesh.Trimesh
    base: object
    lattice: object
    core: object
    shell: object
    remaining_void: object
    report: dict


def _active_cells(values, level):
    count = 0
    for x in range(values.shape[0]-1):
        corners = [values[x+dx, dy:values.shape[1]-1+dy, dz:values.shape[2]-1+dz]
                   for dx in (0, 1) for dy in (0, 1) for dz in (0, 1)]
        lower, upper = np.minimum.reduce(corners), np.maximum.reduce(corners)
        count += int(np.count_nonzero((lower <= level) & (upper >= level) & (lower < upper)))
    return count


def extract_core(domain_values, *, origin_mm, spacing_mm, overlap_field_mm=.8,
                 cap_inset_mm=0., max_memory_bytes=4*1024**3,
                 area_floor_mm2=1e-12, volume_floor_mm3=1e-7):
    """Extract D<-overlap without resampling, smoothing, deleting, or repairing.

    Only a fixed three-step inward physical contour ladder is permitted. Every
    actual threshold and failed native mesh report is retained in metadata.
    This field is a conservative interior-ball field, not a CAD shell SDF.
    """
    from skimage.measure import marching_cubes
    values = np.asarray(domain_values)
    origin = np.asarray(origin_mm, dtype=np.float64)
    parameters = np.asarray([spacing_mm, overlap_field_mm, cap_inset_mm], dtype=float)
    if values.ndim != 3 or min(values.shape) < 3 or not np.isfinite(values).all():
        raise GeometryError('Core needs a finite saved 3D field with padded samples')
    if origin.shape != (3,) or not np.isfinite(origin).all() or not np.isfinite(parameters).all():
        raise GeometryError('Core parameters must be finite XYZ millimetres')
    guard = np.finfo(np.float32).eps*max(1., abs(float(values.min())), abs(float(values.max())))*16
    if spacing_mm <= 0 or cap_inset_mm < 0 or not cap_inset_mm+guard < overlap_field_mm < -values.min():
        raise GeometryError('Overlap must exceed cap inset plus guard and leave nonempty interior')
    metadata = {'algorithm': 'saved-domain-lewiner-field-mm-v1',
        'definition': 'offset of saved conservative domain field',
        'requested_overlap_field_mm': float(overlap_field_mm), 'cap_inset_mm': float(cap_inset_mm),
        'numerical_guard_mm': guard, 'axis_order': 'XYZ', 'origin_mm': origin.tolist(),
        'spacing_mm': float(spacing_mm), 'shape': list(values.shape),
        'source_field_dtype': str(values.dtype), 'vertices_dtype': 'float64',
        'marching_cubes_local_coordinate_dtype': 'float32',
        'repair_applied': False, 'components_removed': 0, 'attempts': []}
    for multiplier in (0, 16, 128):
        shift = multiplier*np.finfo(np.float32).eps*max(1., abs(float(values.min())), abs(float(values.max())))
        level = -float(overlap_field_mm)-shift
        if not values.min() < level < values.max():
            raise GeometryError('Core inward numerical contour emptied the interior', metadata)
        boundary = [values[0], values[-1], values[:, 0], values[:, -1], values[:, :, 0], values[:, :, -1]]
        if not all(np.all(side > level) for side in boundary):
            raise GeometryError('Core intersects saved grid boundary; no artificial cap is allowed', metadata)
        active = _active_cells(values, level)
        memory = int(values.size*48+active*1536)
        attempt = {'actual_domain_level_mm': level, 'actual_overlap_field_mm': -level,
                   'physical_inward_shift_mm': shift, 'float32_epsilon_multiplier': multiplier,
                   'active_cells': active, 'peak_memory_estimate_bytes': memory}
        metadata['attempts'].append(attempt)
        if memory > max_memory_bytes or max_memory_bytes <= 0:
            attempt.update(status='FAIL', reason='Meshing memory bound exceeded')
            raise GeometryError('Core meshing memory allocation bound exceeded', metadata)
        vertices, faces, _, _ = marching_cubes(values, level=level, spacing=(spacing_mm,)*3,
            gradient_direction='descent', method='lewiner', allow_degenerate=True)
        mesh = trimesh.Trimesh(vertices=vertices.astype(np.float64)+origin,
                               faces=faces.astype(np.int64), process=False)
        try:
            validation = require_valid_mesh(mesh, require_single_body=False,
                area_floor_mm2=area_floor_mm2, volume_floor_mm3=volume_floor_mm3)
        except GeometryError as exc:
            attempt.update(status='FAIL', validation=exc.report)
            # A contour change may resolve numerical coincident/tiny triangles;
            # failed geometry stays visible and no mesh is edited in place.
            continue
        attempt.update(status='PASS', validation=validation, triangles=len(faces), vertices=len(vertices))
        metadata.update(actual_domain_level_mm=level, actual_overlap_field_mm=-level,
                        status='PASS', validation=validation)
        return CoreResult(mesh=mesh, metadata=metadata)
    raise GeometryError('All bounded physical core contours failed strict validation', metadata)


def physical_components(solid, *, area_floor_mm2=1e-12, volume_floor_mm3=1e-7):
    """Group each positive shell with its immediate negative void boundaries.

    Manifold.decompose and trimesh.split describe connected boundary surfaces;
    neither is used as a shortcut for physical volume connectivity.
    """
    policy = dict(area_floor_mm2=area_floor_mm2, volume_floor_mm3=volume_floor_mm3)
    component_policy = {**policy, 'volume_floor_mm3': 0.}
    mesh = from_manifold(solid, require_single_body=False, **policy)
    report = require_valid_mesh(mesh, require_single_body=False, **policy)
    graph = coo_matrix((np.ones(len(mesh.face_adjacency)),
                      (mesh.face_adjacency[:, 0], mesh.face_adjacency[:, 1])), shape=(len(mesh.faces),)*2)
    _, labels = connected_components(graph, directed=False)
    components = []
    for shell in report['shells']:
        if shell['signed_volume_mm3'] <= 0:
            continue
        shell_ids = [shell['id']]
        shell_ids.extend(row['id'] for row in report['shells']
                         if row['signed_volume_mm3'] < 0 and shell['id'] in row['containing_shells']
                         and row['nesting_depth'] == shell['nesting_depth']+1)
        faces = np.asarray(mesh.faces)[np.isin(labels, shell_ids)]
        used, inverse = np.unique(faces, return_inverse=True)
        part_mesh = trimesh.Trimesh(vertices=mesh.vertices[used], faces=inverse.reshape(-1, 3), process=False)
        # This is a per-island validation, so the total residual-volume floor
        # must not reject a tiny but strictly positive retained M2 component.
        part = to_manifold(part_mesh, require_single_body=True, **component_policy)
        digest = hashlib.sha256(np.asarray(part_mesh.vertices, dtype='<f8').tobytes()
                                +np.asarray(part_mesh.faces, dtype='<i8').tobytes()).hexdigest()
        components.append((part, {'id': 'component-'+digest[:16], 'triangulation_sha256': digest,
            'boundary_shell_ids': shell_ids, 'volume_mm3': float(part.volume()),
            'bounds_mm': part_mesh.bounds.tolist()}))
    return components


def assemble_core_shell(base, lattice, core, *, area_floor_mm2=1e-12,
                        volume_floor_mm3=1e-7, regional_x_bins=None,
                        boolean_tolerance_mm=0.):
    """Return (B-C) union (L intersect B), the exact clipped form of the target set.

    The caller must enforce the hash-bound M2 READY gate before real-shoe use.
    This pure routine also supports synthetic geometry tests before that gate.
    """
    import manifold3d as m
    started = time.perf_counter()
    policy = dict(area_floor_mm2=area_floor_mm2, volume_floor_mm3=volume_floor_mm3)
    input_reports = {name: require_valid_mesh(mesh, require_single_body=name == 'base', **policy)
                     for name, mesh in [('base', base), ('lattice', lattice), ('core', core)]}
    B, L, C = [to_manifold(mesh, require_single_body=False,
                            boolean_tolerance_mm=boolean_tolerance_mm, **policy)
               for mesh in (base, lattice, core)]
    report = {'status': 'RUNNING',
              'operation': 'A = (B - C) union (L intersect B); set-equivalent to ((B - C) union L) intersect B',
              'coordinate_precision': 'Manifold Mesh64/to_mesh64 float64', 'inputs': input_reports,
              'boolean_tolerance_mm': float(boolean_tolerance_mm),
              'manifold_input_tolerances_mm': {'base': B.get_tolerance(), 'lattice': L.get_tolerance(),
                                               'core': C.get_tolerance()},
              'repair_applied': False, 'components_removed': 0, 'operations': {},
              'numerical_volume_floor_mm3': volume_floor_mm3,
              'directed_surface_distance': {'status': 'NOT_VERIFIED', 'reason': 'Set volume evidence is not a Hausdorff certificate'},
              'manufacturing_status': 'NOT_VERIFIED'}
    def checked(name, value, *, allow_empty=True, single=False, area_floor_mm2=None):
        check_policy = {**policy, **({'area_floor_mm2': area_floor_mm2}
                                     if area_floor_mm2 is not None else {})}
        try:
            result = from_manifold(value, require_single_body=single, allow_empty=allow_empty, **check_policy)
            report['operations'][name] = require_valid_mesh(result, require_single_body=single,
                                                            allow_empty=allow_empty, **check_policy)
            if area_floor_mm2 is not None:
                report['operations'][name]['configured_area_floor_mm2'] = area_floor_mm2
                report['operations'][name]['configured_area_floor_exceeded'] = (
                    report['operations'][name].get('minimum_area_mm2', float('inf'))
                    < policy['area_floor_mm2'])
        except GeometryError as exc:
            report['operations'][name] = exc.report
            report.update(status='FAIL', failed_operation=name)
            raise GeometryError('Assembly Boolean failed strict validation: '+name, report) from exc
        return value

    def volume_witness(name, value, *, require_below_floor=True,
                       purpose='set-identity residual volume witness only'):
        if value.status() != m.Error.NoError:
            raise GeometryError('Set-residual Boolean failed: '+name, report)
        signed_volume = float(value.volume())
        volume = abs(signed_volume)
        if not np.isfinite(volume):
            report['operations'][name] = {'status': 'FAIL',
                'purpose': 'set-identity residual volume witness only',
                'mesh_validation': 'NOT_PERFORMED', 'signed_volume_mm3': signed_volume,
                'absolute_volume_mm3': volume,
                'allowed_volume_mm3': float(volume_floor_mm3),
                'reason': 'native Manifold volume was non-finite',
                'surface_distance_status': 'NOT_VERIFIED'}
            raise GeometryError('Set-residual volume is not finite: '+name, report)
        within_floor = volume <= volume_floor_mm3
        status = ('PASS' if within_floor else 'FAIL') if require_below_floor else 'MEASURED'
        report['operations'][name] = {'status': status,
            'purpose': purpose, 'mesh_validation': 'NOT_PERFORMED',
            'signed_volume_mm3': signed_volume, 'absolute_volume_mm3': volume,
            'allowed_volume_mm3': float(volume_floor_mm3), 'surface_distance_status': 'NOT_VERIFIED'}
        if require_below_floor and not within_floor:
            raise GeometryError('Assembly set-identity residual exceeds volume tolerance: '+name, report)
        return value
    inside = checked('core_inside_base', C ^ B, allow_empty=False)
    outside = volume_witness('core_outside_base', C - B)
    S = checked('retained_shell', B-C, allow_empty=False)
    # This transient intersection is used only to report overlap volume; it is
    # not exported or fed into the assembled solid. Keep its topology checks,
    # while requiring strictly positive face area rather than imposing the
    # printable/output mesh's numerical area floor on an internal witness.
    overlap = checked('lattice_shell_overlap', L ^ S, allow_empty=False, area_floor_mm2=0.)
    clipped_lattice = checked('clipped_lattice', L ^ B, allow_empty=False)
    added = checked('added_lattice_material', clipped_lattice-S, allow_empty=False, area_floor_mm2=0.)
    # Clip first, then join with S. This is set-equivalent to clipping the raw
    # union, but avoids carrying out-of-base lattice fragments into the final
    # Boolean where they can create coincident shell edges.
    A = checked('assembled_before_holes', S+clipped_lattice,
                allow_empty=False, single=False, area_floor_mm2=0.)
    # B-A also contains the original wearer cavity above the sole. That is not
    # trapped Gyroid free space and must not count as powder-accessible volume.
    # Restrict this measurement to the core region, as defined by the saved
    # conservative domain: (B intersect C) minus the retained clipped lattice.
    V = checked('remaining_void_before_holes', inside-clipped_lattice, allow_empty=False)
    residuals = {
        'assembly_outside_base': volume_witness('assembly_outside_base', A-B),
        'void_outside_core': volume_witness('void_outside_core', V-C),
        'clipped_lattice_missing_from_assembly': volume_witness('clipped_lattice_missing_from_assembly', clipped_lattice-A),
        'assembly_missing_expected': volume_witness('assembly_missing_expected', (S+clipped_lattice)-A),
        'assembly_excess_vs_expected': volume_witness('assembly_excess_vs_expected', A-(S+clipped_lattice)),
        'void_missing_expected': volume_witness('void_missing_expected', (inside-clipped_lattice)-V),
        'void_excess_vs_expected': volume_witness('void_excess_vs_expected', V-(inside-clipped_lattice)),
    }
    volumes = {'core_volume_mm3': float(C.volume()), 'core_inside_base_volume_mm3': float(inside.volume()),
        'core_outside_base_volume_mm3': float(outside.volume()),
        'lattice_shell_overlap_volume_mm3': float(overlap.volume()),
        'added_lattice_material_volume_mm3': float(added.volume()),
        'remaining_void_volume_mm3': float(V.volume()), 'assembled_volume_mm3': float(A.volume())}
    report['volumes'] = volumes
    report['set_residual_volumes_mm3'] = {name: abs(float(solid.volume())) for name, solid in residuals.items()}
    report['symmetric_set_residual_volumes_mm3'] = {
        'assembly_vs_shell_union_clipped_lattice': float(residuals['assembly_missing_expected'].volume()+residuals['assembly_excess_vs_expected'].volume()),
        'remaining_void_vs_core_inside_base_minus_lattice': float(residuals['void_missing_expected'].volume()+residuals['void_excess_vs_expected'].volume())}
    conditions = {'core_inside_base': volumes['core_outside_base_volume_mm3'] <= volume_floor_mm3,
        'nonzero_lattice_shell_overlap': volumes['lattice_shell_overlap_volume_mm3'] > volume_floor_mm3,
        'nonzero_added_lattice_material': volumes['added_lattice_material_volume_mm3'] > volume_floor_mm3,
        'nonzero_remaining_void_before_holes': volumes['remaining_void_volume_mm3'] > volume_floor_mm3,
        'all_set_residuals_below_floor': all(abs(v) <= volume_floor_mm3 for v in report['set_residual_volumes_mm3'].values())}
    lattice_shells = input_reports['lattice'].get('shells', [])
    positive_lattice_shells = sum(row.get('signed_volume_mm3', 0) > 0 for row in lattice_shells)
    assembled_mesh_validation = report['operations']['assembled_before_holes']
    report['lattice_component_participation'] = {
        'status': 'NOT_VERIFIED',
        'input_boundary_shell_count': len(lattice_shells),
        'input_positive_orientation_shell_count': positive_lattice_shells,
        'assembled_boundary_shell_count': assembled_mesh_validation.get('boundary_shell_count'),
        'assembled_positive_orientation_shell_count': assembled_mesh_validation.get('positive_orientation_shell_count'),
        'assembled_physical_body_count': assembled_mesh_validation.get('physical_body_count'),
        'components_removed': 0,
        'reason': 'Aggregate overlap is measured; per-island connection is not inferred from aggregate volume.'}
    if regional_x_bins is None:
        edges = np.linspace(float(base.bounds[0, 0]), float(base.bounds[1, 0]), 5)
        regional_x_bins = {name: (float(edges[i]), float(edges[i+1]))
                           for i, name in enumerate(('heel', 'midfoot', 'forefoot', 'toe'))}
        report['regional_bin_definition'] = 'equal quarters of frozen base X bounds; caller may provide frozen-last bins'
    else:
        report['regional_bin_definition'] = 'explicit caller-provided X ranges in mm'
    bins = {}
    low, high = base.bounds[0]-1., base.bounds[1]+1.
    for name, (x0, x1) in regional_x_bins.items():
        if not np.isfinite([x0, x1]).all() or x1 <= x0:
            raise GeometryError('Invalid regional X bin', report)
        box = m.Manifold.cube([x1-x0, high[1]-low[1], high[2]-low[2]]).translate([x0, low[1], low[2]])
        row = {'x_range_mm': [x0, x1]}
        for label, solid in [('core', inside), ('lattice_shell_overlap', overlap), ('added_lattice_material', added), ('remaining_void', V)]:
            key = 'region_'+name+'_'+label
            intersection = volume_witness(key, solid ^ box, require_below_floor=False,
                purpose='regional partition volume measurement only')
            row[label+'_volume_mm3'] = abs(float(intersection.volume()))
        row['core_absent'] = row['core_volume_mm3'] <= volume_floor_mm3
        bins[name] = row
    geometry_reports = [*input_reports.values(), *report['operations'].values()]
    self_intersections_unverified = any(row.get('self_intersections', {}).get('status') == 'NOT_VERIFIED'
                                        for row in geometry_reports)
    nesting_unverified = any(row.get('shell_nesting_status') == 'NOT_VERIFIED'
                             for row in geometry_reports)
    partial_geometry = self_intersections_unverified or nesting_unverified
    report.update(regions=bins, checks=conditions, duration_s=time.perf_counter()-started,
                  topology_status='PASS' if all(conditions.values()) else 'FAIL',
                  geometry_status='PARTIAL' if partial_geometry and all(conditions.values()) else
                                  'PASS' if all(conditions.values()) else 'FAIL',
                  global_self_intersections='NOT_VERIFIED' if self_intersections_unverified else 'PASS',
                  shell_nesting_status='NOT_VERIFIED' if nesting_unverified else 'PASS',
                  status=('PARTIAL' if partial_geometry else 'PASS') if all(conditions.values()) else 'FAIL',
                  manufacturing_status='NOT_VERIFIED',
                  upper_hex_status='BLOCKED_NOT_IMPLEMENTED',
                  footbed_access_status='BLOCKED_NOT_IMPLEMENTED')
    if report['topology_status'] != 'PASS':
        raise GeometryError('Core/shell retention or set-identity verification failed', report)
    return AssemblyResult(from_manifold(A, require_single_body=False, **policy), B, L, C, S, V, report)
