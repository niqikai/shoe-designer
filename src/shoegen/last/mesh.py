"""Last solid -> checked triangle mesh (plan M0.3).

``last_to_mesh`` tessellates a build123d shape, merges the vertices OpenCascade duplicates
per face, and refuses anything that is not a closed, consistently oriented, single-component
solid with outward normals: the signed-distance sign (winding number) and the footprint /
height-map extraction downstream rely on it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree

from shoegen.graph.arrays import array_sha256
from shoegen.last.errors import LastMeshError

MERGE_TOLERANCE_MM = 1e-6


@dataclass(frozen=True)
class LastMesh:
    """A closed triangle mesh in mm, faces counter-clockwise seen from outside."""

    vertices: np.ndarray  # (N, 3) float64
    faces: np.ndarray  # (M, 3) int64
    source_sha256: str
    """sha256 of the source file (or, when unknown, of the mesh content)."""
    tessellation: tuple[float, float]
    """(linear tolerance mm, angular tolerance rad) used to tessellate."""

    @property
    def bbox_min(self) -> tuple[float, float, float]:
        return tuple(float(v) for v in self.vertices.min(axis=0))  # type: ignore[return-value]

    @property
    def bbox_max(self) -> tuple[float, float, float]:
        return tuple(float(v) for v in self.vertices.max(axis=0))  # type: ignore[return-value]

    @property
    def volume(self) -> float:
        return float(_signed_volume(self.vertices, self.faces))

    @property
    def mesh_sha256(self) -> str:
        """Content hash of the vertex and face arrays (changes when the last is moved)."""
        h = hashlib.sha256()
        h.update(array_sha256(np.ascontiguousarray(self.vertices, dtype=np.float64)).encode())
        h.update(array_sha256(np.ascontiguousarray(self.faces, dtype=np.int64)).encode())
        return h.hexdigest()


def _signed_volume(v: np.ndarray, f: np.ndarray) -> float:
    a, b, c = v[f[:, 0]], v[f[:, 1]], v[f[:, 2]]
    return float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6.0)


def merge_vertices(vertices: np.ndarray, tol: float = MERGE_TOLERANCE_MM):
    """Cluster vertices closer than ``tol`` (transitively); return (unique, inverse)."""
    n = len(vertices)
    pairs = cKDTree(vertices).query_pairs(tol, output_type="ndarray")
    if len(pairs) == 0:
        return vertices.copy(), np.arange(n)
    graph = coo_matrix((np.ones(len(pairs)), (pairs[:, 0], pairs[:, 1])), shape=(n, n))
    count, label = connected_components(graph, directed=False)
    # representative = first vertex of each cluster (deterministic)
    first = np.full(count, n, dtype=np.int64)
    np.minimum.at(first, label, np.arange(n))
    return vertices[first], label


def check_closed_solid(vertices: np.ndarray, faces: np.ndarray) -> None:
    """Raise :class:`LastMeshError` unless the mesh is a closed, outward, single solid."""
    if len(faces) == 0:
        raise LastMeshError("last tessellation has no triangles")
    m = len(faces)
    nv = len(vertices)
    # Directed edges of every triangle: (a->b), (b->c), (c->a).
    ea = faces.reshape(-1)
    eb = np.roll(faces, -1, axis=1).reshape(-1)
    face_of_edge = np.repeat(np.arange(m), 3)
    lo = np.minimum(ea, eb)
    hi = np.maximum(ea, eb)
    key = lo * nv + hi
    order = np.argsort(key, kind="stable")
    ks = key[order]
    new_run = np.r_[True, ks[1:] != ks[:-1]]
    run_id = np.cumsum(new_run) - 1
    counts = np.bincount(run_id)
    n_open = int(np.count_nonzero(counts == 1))
    n_over = int(np.count_nonzero(counts > 2))
    if n_open:
        raise LastMeshError(
            f"last mesh is not watertight: {n_open} boundary edge(s) "
            "(gaps between faces after vertex merge)"
        )
    if n_over:
        raise LastMeshError(f"last mesh is non-manifold: {n_over} edge(s) shared by >2 faces")
    starts = np.flatnonzero(new_run)
    first = order[starts]
    second = order[starts + 1]
    # Each manifold edge is a pair of directed edges; they must run in opposite directions.
    same_dir = (ea[first] == ea[second]) & (eb[first] == eb[second])
    if np.any(same_dir):
        raise LastMeshError(
            f"last mesh has inconsistent face orientation at {int(same_dir.sum())} edge(s)"
        )
    # Single connected component through shared edges.
    f0 = face_of_edge[first]
    f1 = face_of_edge[second]
    graph = coo_matrix((np.ones(len(f0)), (f0, f1)), shape=(m, m))
    n_comp, _ = connected_components(graph, directed=False)
    if n_comp != 1:
        raise LastMeshError(f"last mesh has {n_comp} connected components, expected exactly 1")
    vol = _signed_volume(vertices, faces)
    if not vol > 0:
        raise LastMeshError(
            f"last mesh has non-positive volume ({vol:.3f} mm^3): normals point inward"
        )


def last_to_mesh(
    shape,
    tolerance: float = 0.01,
    *,
    angular_tolerance: float = 0.05,
    source_sha256: str | None = None,
) -> LastMesh:
    """Tessellate ``shape`` (chord tolerance ``tolerance`` mm) into a checked :class:`LastMesh`.

    OpenCascade meshes analytic spheres with a deviation far above ``tolerance`` unless
    ``angular_tolerance`` is small too (R = 50 mm: 0.05 mm deviation at 0.1 rad, 0.013 mm at
    0.05 rad, 0.005 mm at 0.03 rad); the default 0.05 keeps B-spline lasts light. A shape keeps
    its first triangulation, so call this once per freshly loaded shape.

    ``source_sha256`` is the sha256 of the STEP file (``LoadedLast.source["sha256"]``); when
    omitted, the content hash of the mesh stands in for it.
    """
    verts, tris = shape.tessellate(tolerance, angular_tolerance)
    if not len(verts) or not len(tris):
        raise LastMeshError("tessellation of the last produced no triangles")
    raw = np.array([[v.X, v.Y, v.Z] for v in verts], dtype=np.float64)
    faces = np.asarray(tris, dtype=np.int64).reshape(-1, 3)
    unique, inverse = merge_vertices(raw)
    faces = inverse[faces]
    # Triangles collapsed by the merge (sphere poles, seams) carry no area.
    keep = (
        (faces[:, 0] != faces[:, 1]) & (faces[:, 1] != faces[:, 2]) & (faces[:, 0] != faces[:, 2])
    )
    faces = faces[keep]
    # Drop vertices no triangle uses.
    used = np.zeros(len(unique), dtype=bool)
    used[faces.reshape(-1)] = True
    remap = np.cumsum(used) - 1
    unique = unique[used]
    faces = remap[faces]
    check_closed_solid(unique, faces)
    mesh = LastMesh(
        vertices=np.ascontiguousarray(unique),
        faces=np.ascontiguousarray(faces, dtype=np.int64),
        source_sha256="0" * 64,
        tessellation=(float(tolerance), float(angular_tolerance)),
    )
    sha = source_sha256 or mesh.mesh_sha256
    return LastMesh(mesh.vertices, mesh.faces, sha, mesh.tessellation)
