"""The ``Mesh`` type, canonical ordering and mesh validation.

Canonical form (ADR 0005): vertices in lexicographic ``(x, y, z)`` order; every triangle
rotated so its smallest vertex index comes first (orientation kept); triangles in
lexicographic order. Two meshes with the same canonical form have bitwise equal arrays,
so equality and hashing need no tolerance.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field

import numpy as np
from numba import njit

from shoegen.mesh import topology as topo

_JIT = {"cache": True, "nogil": True}


def index_dtype(n: int) -> np.dtype:
    """int32 while every index fits, else int64."""
    return np.dtype(np.int32) if n < 2**31 - 1 else np.dtype(np.int64)


@dataclass(eq=False)
class Mesh:
    """Indexed triangle mesh: ``vertices`` float64 ``[V, 3]``, ``faces`` int32/int64
    ``[F, 3]`` oriented counter-clockwise seen from outside (normals point out)."""

    vertices: np.ndarray
    faces: np.ndarray

    def __post_init__(self) -> None:
        v = np.asarray(self.vertices)
        f = np.asarray(self.faces)
        if v.ndim != 2 or v.shape[1] != 3 or v.dtype != np.float64:
            raise ValueError(f"vertices must be float64 [V, 3], got {v.dtype} {v.shape}")
        if f.ndim != 2 or f.shape[1] != 3 or f.dtype not in (np.int32, np.int64):
            raise ValueError(f"faces must be int32/int64 [F, 3], got {f.dtype} {f.shape}")
        self.vertices = np.ascontiguousarray(v)
        self.faces = np.ascontiguousarray(f)

    @property
    def n_vertices(self) -> int:
        return int(self.vertices.shape[0])

    @property
    def n_faces(self) -> int:
        return int(self.faces.shape[0])

    def sha256(self) -> str:
        """Hash of the arrays as stored (vertices float64, faces as int64)."""
        h = hashlib.sha256()
        h.update(np.ascontiguousarray(self.vertices, dtype="<f8").tobytes())
        h.update(np.ascontiguousarray(self.faces, dtype="<i8").tobytes())
        return h.hexdigest()

    def equals(self, other: Mesh) -> bool:
        """Bitwise equality of vertices and faces."""
        return (
            self.vertices.shape == other.vertices.shape
            and self.faces.shape == other.faces.shape
            and np.array_equal(self.vertices.view(np.uint64), other.vertices.view(np.uint64))
            and np.array_equal(self.faces.astype(np.int64), other.faces.astype(np.int64))
        )

    def volume(self) -> float:
        return validate_mesh(self, fans=False).volume_mm3


# ---------------------------------------------------------------- canonical order


@njit(**_JIT)
def lexsort_rows_stable(keys, perm):
    """Stable lexicographic order of rows of ``keys`` (``[n, m]``), starting from the
    permutation ``perm`` (ties keep ``perm`` order). Least significant column first."""
    n, m = keys.shape
    out = perm.copy()
    col = np.empty(n, keys.dtype)
    for j in range(m - 1, -1, -1):
        for i in range(n):
            col[i] = keys[out[i], j]
        o = np.argsort(col, kind="mergesort")
        out = out[o]
    return out


@njit(**_JIT)
def rotate_min_first(tri):
    """Rotate each triangle so its smallest index is first (orientation kept), in place."""
    for t in range(tri.shape[0]):
        a = tri[t, 0]
        b = tri[t, 1]
        c = tri[t, 2]
        if b < a and b <= c:
            tri[t, 0] = b
            tri[t, 1] = c
            tri[t, 2] = a
        elif c < a and c < b:
            tri[t, 0] = c
            tri[t, 1] = a
            tri[t, 2] = b


@njit(**_JIT)
def sort_triangles(tri, nv):
    """Lexicographic order of (already rotated) triangles: bucket by first index, then
    insertion sort by (second, third) inside each bucket. Returns the order."""
    nt = tri.shape[0]
    off = np.zeros(nv + 1, np.int64)
    for t in range(nt):
        off[tri[t, 0] + 1] += 1
    for v in range(nv):
        off[v + 1] += off[v]
    fill = off[:-1].copy()
    order = np.empty(nt, np.int64)
    for t in range(nt):
        a = tri[t, 0]
        order[fill[a]] = t
        fill[a] += 1
    for v in range(nv):
        lo = off[v]
        hi = off[v + 1]
        for i in range(lo + 1, hi):
            x = order[i]
            j = i - 1
            while j >= lo:
                y = order[j]
                if tri[y, 1] > tri[x, 1] or (tri[y, 1] == tri[x, 1] and tri[y, 2] > tri[x, 2]):
                    order[j + 1] = y
                    j -= 1
                else:
                    break
            order[j + 1] = x
    return order


def canonicalize(vertices: np.ndarray, faces: np.ndarray) -> Mesh:
    """Canonical form of an arbitrary indexed mesh (unused vertices are dropped; ties
    between equal coordinates keep input order)."""
    v = np.ascontiguousarray(vertices, dtype=np.float64)
    f = np.ascontiguousarray(faces, dtype=np.int64)
    used = np.zeros(len(v), dtype=bool)
    used[f.ravel()] = True
    perm = lexsort_rows_stable(v, np.flatnonzero(used).astype(np.int64))
    rank = np.full(len(v), -1, dtype=np.int64)
    rank[perm] = np.arange(len(perm))
    t = rank[f]
    rotate_min_first(t)
    t = t[sort_triangles(t, len(perm))]
    dt = index_dtype(max(len(perm), 1))
    return Mesh(v[perm], t.astype(dt))


# ---------------------------------------------------------------- validation


@dataclass
class ComponentInfo:
    volume_mm3: float
    area_mm2: float
    vertices: int
    edges: int
    triangles: int
    euler_characteristic: int


@dataclass
class MeshValidation:
    """Numpy/numba checks of an indexed mesh (no external library).

    * ``watertight``: every undirected edge has exactly two half-edges, in opposite
      directions (closed and consistently oriented), and no triangle repeats a vertex;
    * ``manifold``: watertight and every vertex has a single fan of triangles;
    * Euler characteristic ``V - E + F`` over used vertices, per component and total.
    """

    vertices: int
    unused_vertices: int
    triangles: int
    edges: int
    open_edges: int
    nonmanifold_edges: int
    misoriented_edges: int
    nonmanifold_vertices: int
    degenerate_triangles: int
    zero_area_triangles: int
    coincident_vertices: int
    """Vertices whose coordinates equal another vertex's (excess count)."""
    euler_characteristic: int
    n_components: int
    volume_mm3: float
    area_mm2: float
    components: list[ComponentInfo] = field(default_factory=list)

    @property
    def watertight(self) -> bool:
        return (
            self.triangles > 0
            and self.open_edges == 0
            and self.nonmanifold_edges == 0
            and self.misoriented_edges == 0
            and self.degenerate_triangles == 0
        )

    @property
    def manifold(self) -> bool:
        return self.watertight and self.nonmanifold_vertices == 0

    @property
    def ok(self) -> bool:
        """Closed, manifold, positive volume, no degenerate triangles."""
        return self.manifold and self.volume_mm3 > 0 and self.zero_area_triangles == 0

    def to_dict(self, components: bool = True) -> dict:
        d = asdict(self)
        if not components:
            d.pop("components")
        d["watertight"] = self.watertight
        d["manifold"] = self.manifold
        d["ok"] = self.ok
        return d


@njit(**_JIT)
def _count_coincident(v, perm):
    n = 0
    for i in range(1, perm.shape[0]):
        a = perm[i - 1]
        b = perm[i]
        if v[a, 0] == v[b, 0] and v[a, 1] == v[b, 1] and v[a, 2] == v[b, 2]:
            n += 1
    return n


def validate_mesh(mesh: Mesh, *, fans: bool = True, max_components: int = 64) -> MeshValidation:
    """Topology and geometry of ``mesh`` (linear time except the coincidence count)."""
    v = mesh.vertices
    f = mesh.faces
    nv = mesh.n_vertices
    twin, edge_of, counts = topo.edge_analysis(f, nv)
    if fans:
        n_fans, _fan_of, _ = topo.vertex_fans(f, twin, nv)
    else:
        n_fans = np.ones(nv, dtype=np.int32)
    vlabel, tlabel, nc = topo.components(f, nv)
    fl, it = topo.component_measures(v, f, vlabel, tlabel, edge_of, n_fans, nc)
    used = int(np.count_nonzero(vlabel >= 0))
    perm = lexsort_rows_stable(v, np.arange(nv, dtype=np.int64))
    coincident = int(_count_coincident(v, perm))
    comps = [
        ComponentInfo(
            volume_mm3=float(fl[c, 0]),
            area_mm2=float(fl[c, 1]),
            vertices=int(it[c, 0]),
            edges=int(it[c, 1]),
            triangles=int(it[c, 2]),
            euler_characteristic=int(it[c, 0] - it[c, 1] + it[c, 2]),
        )
        for c in range(min(nc, max_components))
    ]
    return MeshValidation(
        vertices=nv,
        unused_vertices=nv - used,
        triangles=mesh.n_faces,
        edges=int(counts[0]),
        open_edges=int(counts[1]),
        nonmanifold_edges=int(counts[2]),
        misoriented_edges=int(counts[3]),
        nonmanifold_vertices=int(np.count_nonzero(n_fans > 1)),
        degenerate_triangles=int(it[:, 5].sum()),
        zero_area_triangles=int(it[:, 4].sum()),
        coincident_vertices=coincident,
        euler_characteristic=int(used - counts[0] + mesh.n_faces),
        n_components=int(nc),
        volume_mm3=float(fl[:, 0].sum()),
        area_mm2=float(fl[:, 1].sum()),
        components=comps,
    )
