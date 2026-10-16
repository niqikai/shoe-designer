"""Terminal coverage must belong to the principal lattice, not detached tips."""
from __future__ import annotations

import math
import numpy as np
from scipy import ndimage

from .lattice import _mesh


def sample_native_terminal_nodes(shoe, cavity, *, envelope, axes, guide, period_mm,
                                 computation_bounds_mm):
    """Measure native CAD distances at deterministic terminal grid sites."""
    from build123d import Compound, Vertex
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    from .lattice_coverage import NativeVerticalSoleSections
    sections = NativeVerticalSoleSections(shoe, cavity, upper_z=envelope.upper_z,
                                         z_min_mm=envelope.z_bounds[0])
    boundary = Compound(children=[*shoe.faces(), *cavity.faces()])
    xs = np.array([s['x_mm'] for s in guide])
    centers = np.array([(s['left_y_mm']+s['right_y_mm'])/2 for s in guide])
    halfwidths = np.array([(s['right_y_mm']-s['left_y_mm'])/2 for s in guide])
    bounds = np.asarray(computation_bounds_mm, float)
    spacing = float(axes[0][1]-axes[0][0])
    front = shoe.bounding_box().max.X
    selected_x = np.flatnonzero((axes[0] >= front-period_mm) & (axes[0] <= front)
                                & (axes[0] > bounds[0, 0]) & (axes[0] < bounds[1, 0]))
    rows = []
    for ix in selected_x:
        x = float(axes[0][ix])
        center, half = np.interp(x, xs, centers), np.interp(x, xs, halfwidths)
        for y_target in center+half*np.array([-.45, 0., .45]):
            iy = int(np.argmin(abs(axes[1]-y_target)))
            y = float(axes[1][iy])
            native = sections(x, y)
            z_indices = set()
            for low, high in native['sole_intervals_mm']:
                if high-low > spacing:
                    z_indices.update(int(np.argmin(abs(axes[2]-z))) for z in np.linspace(low+.05, high-.05, 7))
            for iz in sorted(z_indices):
                point = np.array([x, y, axes[2][iz]])
                inside = (np.all(point > bounds[0]) and np.all(point < bounds[1])
                          and any(a+1e-6 < point[2] < b-1e-6 for a, b in native['sole_intervals_mm']))
                distance = None
                if inside:
                    exact = BRepExtrema_DistShapeShape(Vertex(*point).wrapped, boundary.wrapped)
                    if not exact.IsDone() or not math.isfinite(exact.Value()):
                        raise RuntimeError('Independent terminal CAD boundary distance failed')
                    distance = min(float(exact.Value()), envelope.boundary_distance(point),
                                   float(np.min(point-bounds[0])), float(np.min(bounds[1]-point)))
                rows.append({'grid_index': [int(ix), iy, iz], 'point_mm': point.tolist(),
                    'inside_intended_material': bool(inside), 'minimum_boundary_distance_mm': distance})
    return rows


def principal_mesh_component(model):
    """Measure all actual closed surface components and select positive material."""
    import trimesh
    mesh = _mesh(model)
    if not len(mesh.faces) or not np.isfinite(mesh.vertices).all() or not mesh.is_watertight or not mesh.is_winding_consistent:
        raise ValueError('Principal component measurement requires a closed consistently oriented actual mesh')
    components = trimesh.graph.connected_components(mesh.face_adjacency,
        nodes=np.arange(len(mesh.faces)), min_len=1, engine='scipy')
    labels = np.empty(len(mesh.faces), dtype=np.int32)
    for label, faces in enumerate(components):
        labels[faces] = label
    triangles = mesh.triangles
    signed = np.einsum('ij,ij->i', triangles[:, 0],
                       np.cross(triangles[:, 1], triangles[:, 2]))/6
    volumes = np.bincount(labels, weights=signed, minlength=len(components))
    principal = int(np.argmax(volumes))
    if volumes[principal] <= 0:
        raise ValueError('No positive principal material component')
    faces = components[principal]
    vertices = mesh.vertices[np.unique(mesh.faces[faces])]
    detached = np.delete(volumes, principal)
    return mesh, faces, {
        'surface_component_count': len(components), 'principal_component_index': principal,
        'principal_volume_mm3': float(volumes[principal]),
        'principal_bounds_mm': [vertices.min(axis=0).tolist(), vertices.max(axis=0).tolist()],
        'all_component_bounds_mm': mesh.bounds.tolist(),
        'largest_detached_positive_volume_mm3': float(max(0., detached.max(initial=0.))),
        'positive_component_count': int(np.count_nonzero(volumes > 0)),
        'components_removed': 0}


def validate_terminal_coverage(model, *, field_mm, gyroid_values, origin_mm, spacing_mm,
                               expected_nodes, clearance_mm, approximation_tolerance_mm,
                               analytic_parameters=None, actual_level=0.):
    """Check independent exact-CAD-eligible nodes and connected terminal reach.

    Expected nodes are native CAD measurements at the exact sampling-grid nodes.
    Only nodes deeper than the declared numerical margin plus approximation
    tolerance demand domain coverage. No sparse fragment can establish reach of
    the principal material component. These finite probes are not a global proof.
    """
    field = np.asarray(field_mm)
    lattice = np.asarray(gyroid_values)
    if field.ndim != 3 or lattice.shape != field.shape or not np.isfinite(field).all() or not np.isfinite(lattice).all():
        raise ValueError('Terminal check requires finite matching domain and lattice fields')
    if (not all(math.isfinite(v) for v in (spacing_mm, clearance_mm, approximation_tolerance_mm))
            or spacing_mm <= 0 or clearance_mm < 0 or approximation_tolerance_mm < 0):
        raise ValueError('Invalid terminal measurement spacing or approximation tolerance')
    if not math.isfinite(actual_level) or actual_level > 0:
        raise ValueError('Terminal check needs the actual finite nonpositive extraction level')
    origin = np.asarray(origin_mm, float)
    if origin.shape != (3,) or not np.isfinite(origin).all():
        raise ValueError('Terminal grid origin must be finite XYZ')
    mask = field < 0
    labels, count = ndimage.label(mask)
    sizes = np.bincount(labels.ravel())
    sizes[0] = 0
    if not count:
        raise ValueError('Terminal domain has no negative material')
    principal_label = int(np.argmax(sizes))
    mesh, faces, component = principal_mesh_component(model)
    report = {'geometry_status': 'NOT_VERIFIED', 'global_coverage_status': 'NOT_VERIFIED',
        'scope': 'Independent native CAD node completeness plus principal domain and actual STL component terminal reach',
        'clearance_mm': float(clearance_mm), 'cell_diagonal_mm': math.sqrt(3)*spacing_mm,
        'approximation_tolerance_mm': approximation_tolerance_mm,
        'mesh': component, 'domain_component_count_6_neighbor': int(count),
        'domain_principal_node_count': int(sizes[principal_label]),
        'eligible_samples': [], 'missing_domain_samples': [], 'detached_domain_samples': [],
        'measurement_errors': [], 'terminal_planes': [], 'lattice_field_mismatch_planes': [],
        'limitations': ['Finite exact-CAD sites do not establish global completeness or minimum wall thickness.',
                       'A nonempty principal cross-section establishes connected reach, not full sectional density.',
                       'STL reach requires crossings on both nearby planes beyond float32 coordinate uncertainty; extensions smaller than that conservative margin do not pass.',
                       'No components were deleted, smoothed, or counted as connected merely by bounding-box overlap.']}
    threshold = clearance_mm+math.sqrt(3)*spacing_mm+8e-6+approximation_tolerance_mm
    for row in expected_nodes:
        raw_index = np.asarray(row['grid_index'])
        if raw_index.shape != (3,) or not np.isfinite(raw_index).all() or not np.all(raw_index == np.floor(raw_index)):
            report['measurement_errors'].append('Expected CAD point has an invalid grid index')
            continue
        index = tuple(int(v) for v in raw_index)
        point = origin+np.asarray(index)*spacing_mm
        if (any(i < 0 or i >= field.shape[d] for d, i in enumerate(index))
                or not np.allclose(point, row['point_mm'], rtol=0, atol=1e-8)):
            report['measurement_errors'].append('Expected CAD point does not identify the measured grid')
            continue
        distance = row.get('minimum_boundary_distance_mm')
        if (not isinstance(row.get('inside_intended_material'), bool)
                or (row.get('inside_intended_material') and distance is None)
                or (distance is not None and (not math.isfinite(distance) or distance < 0))):
            report['measurement_errors'].append('Expected CAD point has an unresolved native measurement')
            continue
        if not row.get('inside_intended_material') or distance is None or distance <= threshold:
            continue
        item = {'point_mm': point.tolist(), 'grid_index': list(index),
                'minimum_boundary_distance_mm': distance, 'domain_field_mm': float(field[index]),
                'in_principal_domain': bool(labels[index] == principal_label)}
        report['eligible_samples'].append(item)
        if field[index] >= 0:
            report['missing_domain_samples'].append(item)
        elif labels[index] != principal_label:
            report['detached_domain_samples'].append(item)
    if not report['eligible_samples']:
        report['measurement_errors'].append('No independent deep eligible toe samples')
    else:
        lo = min(row['grid_index'][0] for row in report['eligible_samples'])
        hi = max(row['grid_index'][0] for row in report['eligible_samples'])
        triangles_x = mesh.vertices[mesh.faces[faces], 0]
        triangle_lo, triangle_hi = triangles_x.min(axis=1), triangles_x.max(axis=1)
        for i in range(lo, hi+1):
            x = float(origin[0]+i*spacing_mm)
            expected = int(np.count_nonzero(lattice[i] < actual_level))
            maximum_field_error = None
            if analytic_parameters is not None:
                p = analytic_parameters
                k = 2*np.pi/p['period_mm']
                phase = np.asarray(p['phase_origin_mm'], float)
                xx = k*(x-phase[0])
                yy = k*(origin[1]+np.arange(field.shape[1])*spacing_mm-phase[1])
                zz = k*(origin[2]+np.arange(field.shape[2])*spacing_mm-phase[2])
                g = (np.sin(xx)*np.cos(yy)[:, None]+np.sin(yy)[:, None]*np.cos(zz)[None, :]
                     +np.sin(zz)[None, :]*np.cos(xx))
                independent = np.maximum(np.abs(g)-p['threshold'],
                    (field[i]+p['cap_inset_mm'])/spacing_mm)
                maximum_field_error = float(np.max(np.abs(independent-lattice[i])))
                if maximum_field_error > 2e-7:
                    report['lattice_field_mismatch_planes'].append({'x_mm': x,
                        'maximum_dimensionless_error': maximum_field_error})
                expected = int(np.count_nonzero(independent < actual_level-2e-7))
            in_domain = int(np.count_nonzero(labels[i] == principal_label))
            # MC triangles live inside individual cells. At an exact grid
            # plane they can meet along shared edges without any single face
            # strictly straddling it. Probe both nearby planes beyond the
            # serialized float32 coordinate uncertainty; a tangent cap has
            # no crossing on its outer side and cannot pass this test.
            delta = max(1e-5, 8*float(np.spacing(np.float32(max(1., abs(x))))))
            if delta >= spacing_mm/100:
                raise ValueError('STL coordinate precision is insufficient for terminal plane probes')
            left, right = x-delta, x+delta
            crossed_left = int(np.count_nonzero((triangle_lo < left) & (triangle_hi > left)))
            crossed_right = int(np.count_nonzero((triangle_lo < right) & (triangle_hi > right)))
            crossed = min(crossed_left, crossed_right)
            report['terminal_planes'].append({'x_mm': x, 'expected_lattice_negative_node_count': expected,
                'independent_field_maximum_error': maximum_field_error,
                'principal_domain_negative_node_count': in_domain,
                'principal_stl_crossing_triangle_count': crossed,
                'stl_probe_x_mm': [left, right],
                'stl_probe_delta_mm': delta,
                'principal_stl_left_crossing_triangle_count': crossed_left,
                'principal_stl_right_crossing_triangle_count': crossed_right,
                'status': 'FAIL' if in_domain == 0 or (expected > 0 and crossed == 0) else 'PASS'})
    failed_planes = [row for row in report['terminal_planes'] if row['status'] == 'FAIL']
    report['failed_terminal_planes'] = failed_planes
    report['eligible_sample_count'] = len(report['eligible_samples'])
    report['geometry_status'] = ('FAIL' if report['missing_domain_samples'] or report['detached_domain_samples'] or failed_planes or report['lattice_field_mismatch_planes']
        else 'NOT_VERIFIED' if report['measurement_errors'] else 'PASS')
    return report
