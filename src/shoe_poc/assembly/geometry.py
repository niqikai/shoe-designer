"""Independent, non-repairing mesh checks for material with enclosed voids.

A boundary shell is not a physical material component. Alternating nested
positive and negative shells represent material and void, respectively. No
Euler target, shell inversion, tolerance welding, or island removal is used.
"""
from __future__ import annotations

from pathlib import Path
import struct
import numpy as np
import trimesh
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree


class GeometryError(ValueError):
    def __init__(self, message, report=None):
        self.report = report or {}
        super().__init__(message)


def exact_index_triangles(triangles):
    """Losslessly index coordinates, retaining the order and count of all faces."""
    triangles = np.asarray(triangles, dtype=np.float64)
    if triangles.ndim != 3 or triangles.shape[1:] != (3, 3):
        raise GeometryError('Expected Nx3x3 triangle records')
    vertices, inverse = np.unique(triangles.reshape(-1, 3), axis=0, return_inverse=True)
    mesh = trimesh.Trimesh(vertices=vertices, faces=inverse.reshape(-1, 3), process=False)
    mesh.metadata['indexing'] = 'exact coordinate identity only; every triangle retained'
    return mesh


def cancel_opposite_duplicate_faces(mesh):
    """Cancel exact, opposite-oriented duplicate boundary faces algebraically.

    Such a pair contributes zero oriented boundary and zero volume. No vertex
    coordinate is moved; same-winding duplicates are retained for strict
    validation to reject.
    """
    faces = np.asarray(mesh.faces)
    if not len(faces):
        return mesh, {'opposite_duplicate_pairs_cancelled': 0, 'triangles_removed': 0,
                      'same_winding_duplicates_retained': 0}
    ordered = np.sort(faces, axis=1)
    _, inverse, counts = np.unique(ordered, axis=0, return_inverse=True, return_counts=True)
    duplicate_groups = np.flatnonzero(counts > 1)
    remove = np.zeros(len(faces), dtype=bool)
    canceled = 0
    same_winding_left = 0
    for group in duplicate_groups:
        ids = np.flatnonzero(inverse == group)
        local = faces[ids]
        # Distinct vertices are guaranteed by the caller's strict checks.
        parity = []
        for face in local:
            ranks = np.argsort(np.argsort(face))
            parity.append(-1 if sum(ranks[i] > ranks[j]
                                    for i in range(3) for j in range(i+1, 3)) % 2 else 1)
        positive, negative = ids[np.array(parity) == 1], ids[np.array(parity) == -1]
        pairs = min(len(positive), len(negative))
        if pairs:
            remove[np.r_[positive[:pairs], negative[:pairs]]] = True
            canceled += pairs
        same_winding_left += max(0, len(positive)-pairs) + max(0, len(negative)-pairs)
    if not canceled:
        return mesh, {'opposite_duplicate_pairs_cancelled': 0, 'triangles_removed': 0,
                      'same_winding_duplicates_retained': int(same_winding_left)}
    retained_triangles = np.asarray(mesh.triangles)[~remove]
    result = exact_index_triangles(retained_triangles)
    result.metadata['opposite_duplicate_face_cancellation'] = {
        'opposite_duplicate_pairs_cancelled': int(canceled),
        'triangles_removed': int(np.count_nonzero(remove)),
        'same_winding_duplicates_retained': int(same_winding_left),
        'coordinates_changed': False,
        'oriented_boundary_chain_preserved': True}
    return result, {'opposite_duplicate_pairs_cancelled': int(canceled),
                    'triangles_removed': int(np.count_nonzero(remove)),
                    'same_winding_duplicates_retained': int(same_winding_left)}


def load_stl_exact(path):
    """Read binary STL or ASCII vertex records without float32 ASCII truncation."""
    data = Path(path).read_bytes()
    if len(data) >= 84 and len(data) == 84 + 50 * struct.unpack_from('<I', data, 80)[0]:
        dtype = np.dtype([('normal', '<f4', (3,)), ('vertices', '<f4', (3, 3)), ('attribute', '<u2')])
        triangles = np.frombuffer(data, dtype=dtype, offset=84)['vertices'].astype(np.float64)
        encoding = 'binary_float32'
    else:
        rows = []
        for line in data.decode('ascii').splitlines():
            fields = line.split()
            if fields and fields[0].lower() == 'vertex':
                if len(fields) != 4:
                    raise GeometryError('Malformed ASCII STL vertex')
                rows.append([float(x) for x in fields[1:]])
        if not rows or len(rows) % 3:
            raise GeometryError('ASCII STL does not contain complete triangle records')
        triangles = np.asarray(rows, dtype=np.float64).reshape(-1, 3, 3)
        encoding = 'ascii_float64'
    mesh = exact_index_triangles(triangles)
    mesh.metadata['source_encoding'] = encoding
    return mesh


def _shell_accelerator(vertices, faces):
    import manifold3d as m
    # Manifold's pybind constructor requires writable C-order buffers; the
    # read-only arrays returned by NumPy views are rejected even when contiguous.
    solid = m.Manifold(m.Mesh64(np.array(vertices, dtype=np.float64, order='C', copy=True),
                                np.array(faces, dtype=np.uint64, order='C', copy=True)))
    if solid.status() != m.Error.NoError or solid.is_empty():
        raise GeometryError(f'Could not build exact shell containment accelerator: {solid.status()}')
    return solid


def _point_inside_shell(point, solid, bounds):
    """Accelerated parity query for one shell, with three independent rays.

    Generalized winding over every triangle per query made 800 tiny retained
    lattice shells cost billions of triangle evaluations against the enclosing
    body. Manifold's BVH-backed segment query gives the same inside/outside
    parity test without changing the supplied mesh.
    """
    point = np.asarray(point, dtype=np.float64)
    center = bounds.mean(axis=0)
    radius = max(float(np.linalg.norm(bounds[1]-bounds[0]))*4, 1.)
    directions = np.array([[1., .371, .193], [-.217, 1., .419], [.311, -.257, 1.]])
    inside_votes = []
    for direction in directions:
        endpoint = point + direction/np.linalg.norm(direction)*radius
        hits = solid.ray_cast(point, endpoint)
        inside_votes.append(len(hits) % 2 == 1)
    votes = sum(inside_votes)
    return votes >= 2, {'method': 'Manifold BVH ray parity (3 directions)', 'inside_votes': votes}


def _triangles_contact(first, second, tolerance):
    """Separating-axis test, including coplanar triangles and surface contact."""
    edges_a = np.roll(first, -1, axis=0)-first
    edges_b = np.roll(second, -1, axis=0)-second
    normal_a, normal_b = np.cross(edges_a[0], edges_a[1]), np.cross(edges_b[0], edges_b[1])
    axes = [normal_a, normal_b]
    axes.extend(np.cross(a, b) for a in edges_a for b in edges_b)
    axes.extend(np.cross(normal_a, edge) for edge in edges_a)
    axes.extend(np.cross(normal_b, edge) for edge in edges_b)
    for axis in axes:
        length = np.linalg.norm(axis)
        if length <= np.finfo(float).tiny:
            continue
        axis = axis/length
        a, b = first@axis, second@axis
        if a.max() < b.min()-tolerance or b.max() < a.min()-tolerance:
            return False
    return True


def _self_intersections(vertices, faces, pair_budget):
    """Bounded sphere/AABB broad phase, nonadjacent triangle SAT narrow phase.

    Pairs sharing an indexed vertex are excluded. Full coverage under this
    declared basic policy is distinct from a manufacturing self-contact proof.
    """
    report = {'status': 'NOT_VERIFIED', 'method': 'sphere/AABB filtered nonadjacent triangle SAT',
              'adjacency_policy': 'pairs sharing an indexed vertex excluded',
              'coplanar_contact_policy': 'nonadjacent contact is rejected',
              'pair_budget': int(pair_budget), 'checked_pairs': 0, 'intersections': [],
              'full_candidate_coverage': False}
    if pair_budget <= 0:
        report['reason'] = 'Configured pair budget is zero'
        return report
    triangles = vertices[faces]
    centers = triangles.mean(axis=1)
    radii = np.linalg.norm(triangles-centers[:, None], axis=2).max(axis=1)
    tree = cKDTree(centers)
    max_radius = float(radii.max())
    lower, upper = triangles.min(axis=1), triangles.max(axis=1)
    tol = np.finfo(float).eps*max(1., float(np.ptp(vertices, axis=0).max()))*32
    # The candidate bound is checked before constructing a potentially huge pair list.
    candidate_count = 0
    for start in range(0, len(faces), 8192):
        counts = tree.query_ball_point(centers[start:start+8192], radii[start:start+8192]+max_radius+tol,
                                       return_length=True)
        candidate_count += int(np.sum(counts)-len(counts))
        if candidate_count > 2*pair_budget:
            report['reason'] = 'Broad-phase candidate budget exceeded before full coverage'
            report['broad_phase_directed_candidate_lower_bound'] = candidate_count
            return report
    for i, center in enumerate(centers):
        for j in tree.query_ball_point(center, radii[i]+max_radius+tol):
            if j <= i or np.any(lower[i] > upper[j]+tol) or np.any(lower[j] > upper[i]+tol):
                continue
            if np.isin(faces[i], faces[j]).any():
                continue
            report['checked_pairs'] += 1
            if _triangles_contact(triangles[i], triangles[j], tol):
                report['intersections'].append([i, int(j)])
                if len(report['intersections']) >= 20:
                    report.update(status='FAIL', reason='Intersection evidence limit reached')
                    return report
    report.update(status='FAIL' if report['intersections'] else 'PASS', full_candidate_coverage=True)
    return report


def _vertex_links(faces, vertex_count, inverse):
    """Check that every vertex's link is one cycle using a sparse graph.

    Each triangle corner is a link node. Across each paired manifold edge, the
    two incident triangles are joined at both edge endpoints. Connected
    components then expose two shells that only touch at a shared vertex. This
    preserves the per-vertex connectivity test without millions of Python sets.
    """
    face_count = len(faces)
    edge_slots = np.argsort(inverse, kind='stable').reshape(-1, 2)
    first, second = edge_slots[:, 0], edge_slots[:, 1]
    f0, f1, s0, s1 = first//3, second//3, first%3, second%3
    row_parts, col_parts = [], []
    for offset in (0, 1):
        c0 = f0*3 + (s0+offset)%3
        # Consistently oriented manifold faces traverse a shared edge in
        # opposite directions, so the same geometric endpoint uses the
        # opposite local edge slot in the paired face.
        c1 = f1*3 + (s1+(1-offset))%3
        row_parts.extend((c0, c1))
        col_parts.extend((c1, c0))
    rows, cols = np.concatenate(row_parts), np.concatenate(col_parts)
    graph = coo_matrix((np.ones(len(rows), dtype=np.uint8), (rows, cols)),
                       shape=(face_count*3, face_count*3))
    _, labels = connected_components(graph, directed=False)
    vertex_ids = faces.ravel()
    order = np.lexsort((labels, vertex_ids))
    ordered_vertices, ordered_labels = vertex_ids[order], labels[order]
    split = ((ordered_vertices[1:] == ordered_vertices[:-1])
             & (ordered_labels[1:] != ordered_labels[:-1]))
    return np.unique(ordered_vertices[1:][split]).tolist()


def validate_native_mesh(mesh, *, require_single_body=True, allow_empty=False,
                         area_floor_mm2=1e-12, volume_floor_mm3=1e-9,
                         self_intersection_pair_budget=2_000_000,
                         nesting_accelerator_face_budget=2_500_000):
    """Report strict native topology without modifying any vertex or triangle."""
    report = {'status': 'FAIL', 'geometry_status': 'FAIL', 'checks': {}, 'shells': [],
              'physical_body_count': None, 'repair_applied': False, 'components_removed': 0,
              'numerical_area_floor_mm2': float(area_floor_mm2),
              'numerical_volume_floor_mm3': float(volume_floor_mm3),
              'nesting_accelerator_face_budget': int(nesting_accelerator_face_budget),
              'manufacturing_status': 'NOT_VERIFIED'}
    checks = report['checks']
    vertices, faces = np.asarray(mesh.vertices), np.asarray(mesh.faces)
    report.update(vertices=len(vertices), triangles=len(faces))
    if mesh.metadata.get('exact_coordinate_reindexing'):
        report['exact_coordinate_reindexing'] = mesh.metadata['exact_coordinate_reindexing']
    if mesh.metadata.get('opposite_duplicate_face_cancellation'):
        report['opposite_duplicate_face_cancellation'] = mesh.metadata['opposite_duplicate_face_cancellation']
    checks['valid_array_shapes'] = (vertices.ndim == faces.ndim == 2 and vertices.shape[1:] == (3,)
                                    and faces.shape[1:] == (3,) and np.issubdtype(faces.dtype, np.integer))
    checks['finite_vertices'] = bool(np.isfinite(vertices).all())
    checks['nonempty'] = bool(len(faces)) or bool(allow_empty)
    if not all(checks.values()):
        return report
    if not len(faces):
        report.update(status='PASS', geometry_status='PASS', topology_status='PASS', physical_body_count=0, volume_mm3=0.,
                      bounds_mm=None, self_intersections={'status': 'PASS', 'full_candidate_coverage': True})
        return report
    checks['valid_indices'] = bool(faces.min() >= 0 and faces.max() < len(vertices))
    if not checks['valid_indices']:
        return report
    checks['all_vertices_referenced'] = len(np.unique(faces)) == len(vertices)
    checks['unique_vertex_coordinates'] = len(np.unique(vertices, axis=0)) == len(vertices)
    sorted_faces = np.sort(faces, axis=1)
    checks['no_collapsed_faces'] = bool(np.all(np.diff(sorted_faces, axis=1) > 0))
    unique_faces, face_inverse, face_counts = np.unique(sorted_faces, axis=0, return_inverse=True,
                                                        return_counts=True)
    checks['no_duplicate_faces'] = len(unique_faces) == len(faces)
    duplicate_groups = np.flatnonzero(face_counts > 1)
    report['duplicate_face_count'] = int(np.sum(face_counts[duplicate_groups]-1))
    report['duplicate_face_examples'] = []
    for group in duplicate_groups[:10]:
        ids = np.flatnonzero(face_inverse == group)
        report['duplicate_face_examples'].append({'face_ids': ids.tolist(),
            'triangles_mm': vertices[faces[ids]].tolist(),
            'winding_same': any(np.array_equal(np.roll(faces[ids[0]], k), faces[ids[1]])
                                for k in range(3))})
    triangles = vertices[faces]
    areas = np.linalg.norm(np.cross(triangles[:, 1]-triangles[:, 0], triangles[:, 2]-triangles[:, 0]), axis=1)*.5
    report['minimum_area_mm2'] = float(areas.min())
    checks['nondegenerate_triangles'] = bool(np.all(areas > area_floor_mm2))
    edges = faces[:, [[0, 1], [1, 2], [2, 0]]].reshape(-1, 2)
    undirected, inverse, counts = np.unique(np.sort(edges, axis=1), axis=0, return_inverse=True, return_counts=True)
    report.update(boundary_edges=int(np.count_nonzero(counts == 1)), nonmanifold_edges=int(np.count_nonzero(counts > 2)))
    report['nonmanifold_edge_examples'] = vertices[undirected[counts > 2][:10]].tolist()
    checks['manifold_edges'] = bool(np.all(counts == 2))
    directions = np.where(edges[:, 0] < edges[:, 1], 1, -1)
    checks['consistent_directed_edges'] = bool(np.all(np.bincount(inverse, weights=directions) == 0))
    if not all(checks.values()):
        return report
    singular = _vertex_links(faces, len(vertices), inverse)
    report['singular_vertices'] = singular[:100]
    report['singular_vertex_count'] = len(singular)
    checks['cyclic_vertex_links'] = not singular
    partners = np.argsort(inverse, kind='stable').reshape(-1, 2)//3
    graph = coo_matrix((np.ones(len(partners), dtype=np.uint8), (partners[:, 0], partners[:, 1])),
                       shape=(len(faces), len(faces)))
    count, labels = connected_components(graph, directed=False)
    shell_faces = [np.flatnonzero(labels == index) for index in range(count)]
    shell_triangles = [triangles[indices] for indices in shell_faces]
    shell_bounds = [np.array([t.min(axis=(0, 1)), t.max(axis=(0, 1))]) for t in shell_triangles]
    # Origin translation avoids large-coordinate cancellation for tiny closed shells.
    shell_volumes = []
    for shell, bounds in zip(shell_triangles, shell_bounds):
        t = shell-bounds.mean(axis=0)
        shell_volumes.append(float(np.einsum('ij,ij->i', t[:, 0], np.cross(t[:, 1], t[:, 2])).sum()/6))
    nesting_valid = True
    nesting_unverified = []
    containment_accelerators = {}
    for i, (indices, bounds, volume) in enumerate(zip(shell_faces, shell_bounds, shell_volumes)):
        point = shell_triangles[i][0].mean(axis=0)
        parents = []
        for j, other in enumerate(shell_bounds):
            if i != j and np.all(bounds[0] >= other[0]) and np.all(bounds[1] <= other[1]):
                # Large enclosing shells are imported once into a bounded BVH,
                # then queried for every enclosed shell. Exceeding this explicit
                # budget preserves uncertainty rather than silently skipping it.
                if len(shell_faces[j]) > nesting_accelerator_face_budget:
                    nesting_unverified.append({'shell_id': i, 'candidate_parent_id': j,
                                               'candidate_parent_triangles': len(shell_faces[j]),
                                               'reason': 'candidate parent exceeds the configured accelerator face budget'})
                    continue
                if j not in containment_accelerators:
                    try:
                        containment_accelerators[j] = _shell_accelerator(vertices, faces[shell_faces[j]])
                    except Exception as error:
                        nesting_unverified.append({'shell_id': i, 'candidate_parent_id': j,
                            'candidate_parent_triangles': len(shell_faces[j]),
                            'reason': 'accelerator construction failed', 'error': str(error)})
                        continue
                inside, query = _point_inside_shell(point, containment_accelerators[j], other)
                if inside:
                    parents.append(j)
        depth = len(parents)
        # A volume floor is a total-volume arithmetic tolerance, not a per-island
        # deletion rule. Tiny positive M2 fragments are still retained solid
        # components; nesting determines the expected sign, while zero/negative
        # signs are rejected without reorienting or dropping a shell.
        sign_valid = None if any(row['shell_id'] == i for row in nesting_unverified) else (
            volume > 0 if depth % 2 == 0 else volume < 0)
        if sign_valid is not None:
            nesting_valid &= sign_valid
        report['shells'].append({'id': i, 'face_count': len(indices), 'signed_volume_mm3': volume,
            'bounds_mm': bounds.tolist(), 'nesting_depth': depth, 'containing_shells': parents,
            'orientation_matches_nesting': sign_valid,
            'role': ('material_boundary' if volume > 0 else 'void_boundary')
                    if sign_valid is not None and sign_valid else
                    'positive_orientation' if volume > 0 else 'negative_orientation',
            'containment_method': 'AABB containment plus cached Manifold BVH ray parity at shell triangle centroid'})
    body_count = sum(volume > 0 for volume in shell_volumes)
    nesting_status = 'FAIL' if not nesting_valid else 'NOT_VERIFIED' if nesting_unverified else 'PASS'
    report.update(boundary_shell_count=count,
                  physical_body_count=body_count if not nesting_unverified else None,
                  positive_orientation_shell_count=body_count,
                  shell_nesting_status=nesting_status, shell_nesting_unverified=nesting_unverified,
                  volume_mm3=sum(shell_volumes), bounds_mm=np.array([vertices.min(axis=0), vertices.max(axis=0)]).tolist(),
                  euler_number=int(len(vertices)-len(undirected)+len(faces)))
    checks['valid_shell_nesting_and_orientation'] = (
        True if nesting_status == 'PASS' else False if nesting_status == 'FAIL' else None)
    checks['positive_material_volume'] = sum(shell_volumes) > volume_floor_mm3
    checks['physical_body_count'] = body_count == 1 if require_single_body else body_count > 0
    intersections = _self_intersections(vertices, faces, self_intersection_pair_budget)
    report['self_intersections'] = intersections
    # An incomplete broad phase is not evidence of a clean mesh. Keep the
    # strict geometry gate closed unless the checker completed its coverage.
    checks['no_detected_self_intersections'] = (
        True if intersections['status'] == 'PASS' else False if intersections['status'] == 'FAIL' else None)
    report['global_self_intersections'] = intersections['status']
    topology_checks = {name: value for name, value in checks.items()
                       if name not in ('no_detected_self_intersections', 'valid_shell_nesting_and_orientation')}
    topology_passed = all(value is True for value in topology_checks.values())
    complete = intersections['status'] == 'PASS' and nesting_status == 'PASS'
    status = ('FAIL' if not topology_passed or nesting_status == 'FAIL' or intersections['status'] == 'FAIL'
              else 'PASS' if complete else 'PARTIAL')
    report.update(status=status, geometry_status=status,
                  topology_status='PASS' if topology_passed else 'FAIL')
    return report


def require_valid_mesh(mesh, **kwargs):
    report = validate_native_mesh(mesh, **kwargs)
    if (report.get('topology_status', report['status']) != 'PASS'
            or report.get('self_intersections', {}).get('status') == 'FAIL'
            or report.get('shell_nesting_status') == 'FAIL'):
        raise GeometryError('Strict mesh validation failed: '+', '.join(k for k, v in report['checks'].items() if v is False), report)
    return report


def to_manifold(mesh, *, require_single_body=False, boolean_tolerance_mm=0., **kwargs):
    """Validate first; import native double precision with no merge vectors."""
    import manifold3d as m
    require_valid_mesh(mesh, require_single_body=require_single_body, **kwargs)
    solid = m.Manifold(m.Mesh64(np.asarray(mesh.vertices, dtype=np.float64),
                                 np.asarray(mesh.faces, dtype=np.uint64),
                                 tolerance=float(boolean_tolerance_mm)))
    if solid.status() != m.Error.NoError or solid.is_empty():
        raise GeometryError('Manifold Mesh64 import failed: '+str(solid.status()))
    return solid


def from_manifold(solid, *, require_single_body=False, allow_empty=False, **kwargs):
    import manifold3d as m
    if solid.status() != m.Error.NoError:
        raise GeometryError('Manifold operation failed: '+str(solid.status()))
    native = solid.to_mesh64()
    mesh = trimesh.Trimesh(vertices=np.asarray(native.vert_properties[:, :3], dtype=np.float64),
                           faces=np.asarray(native.tri_verts, dtype=np.int64), process=False)
    if len(mesh.faces):
        before_vertices, before_faces = len(mesh.vertices), len(mesh.faces)
        mesh = exact_index_triangles(np.asarray(mesh.triangles, dtype=np.float64))
        mesh.metadata['exact_coordinate_reindexing'] = {
            'method': 'coordinate identity only', 'vertices_before': before_vertices,
            'vertices_after': len(mesh.vertices), 'triangles_before': before_faces,
            'triangles_after': len(mesh.faces), 'triangles_removed': 0,
            'coordinates_changed': False}
        mesh, cancellation = cancel_opposite_duplicate_faces(mesh)
        if cancellation['opposite_duplicate_pairs_cancelled']:
            mesh.metadata['opposite_duplicate_face_cancellation'] = cancellation
    require_valid_mesh(mesh, require_single_body=require_single_body, allow_empty=allow_empty, **kwargs)
    return mesh
