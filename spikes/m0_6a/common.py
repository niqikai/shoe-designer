"""Shared helpers for the M0.6a meshing-library spike (evaluation only, not product code).

* test shapes as GeometryGraphs (sphere, two capsules, BCC lattice block);
* M0.5 block sampling -> sparse block store with a numba trilinear lookup (the thing a
  per-point-callback mesher has to be fed with);
* mesh checks (closed / edge-manifold / orientation / Euler / volume) written with numpy
  only, so they do not depend on the library under test;
* canonical form + sha256 of a triangle mesh (vertices lexicographic, triangles by index).
"""

from __future__ import annotations

import hashlib
import math
import os
import sys
import time
from dataclasses import dataclass

import numba
import numpy as np

from shoegen.evaluate import (
    BLOCK_SAMPLES,
    BlockPlan,
    iter_block_samples,
)
from shoegen.graph import (
    Box,
    Capsule,
    GraphBuilder,
    Intersect,
    RoundedBox,
    StrutLattice,
    Union,
)

# ------------------------------------------------------------------ shapes

SPHERE_R = 20.0
SPHERE_CENTRE = (30.0, 30.0, 30.0)
"""Offset so that the 64-cell block boundary cuts the sphere (multi-block case)."""


def sphere_graph(centre=SPHERE_CENTRE, r=SPHERE_R):
    b = GraphBuilder()
    root = b.add(RoundedBox(center=centre, half_size=(r, r, r), radius=r), "sphere")
    return b.build(root)


def capsules_graph(centre=SPHERE_CENTRE):
    c = np.asarray(centre)
    b = GraphBuilder()
    c1 = b.add(Capsule(a=tuple(c + (-14, -6, -2)), b=tuple(c + (14, 6, 2)), radius=5.0), "cap1")
    c2 = b.add(Capsule(a=tuple(c + (-6, 14, -3)), b=tuple(c + (6, -14, 3)), radius=4.0), "cap2")
    return b.build(b.add(Union(children=[c1, c2]), "union"))


LATTICE_BOX = (0.07, 80.07, 0.07, 80.07, 0.07, 40.07)
"""Solid [0.07, 80.07] x [0.07, 80.07] x [0.07, 40.07]. The 0.07 mm offset keeps the cut
faces off the 0.25 mm lattice, so no sample is exactly 0 (the zero-value rule is not what
this spike measures)."""


def lattice_graph():
    b = GraphBuilder()
    lat = b.add(StrutLattice(cell_mm=10.0, radius_mm=1.0, origin=(0.0, 0.0, 0.0)), "lattice")
    x0, x1, y0, y1, z0, z1 = LATTICE_BOX
    box = b.add(
        Box(
            center=((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2),
            half_size=((x1 - x0) / 2, (y1 - y0) / 2, (z1 - z0) / 2),
        ),
        "clip",
    )
    return b.build(b.add(Intersect(children=[lat, box]), "block"))


@dataclass
class Shape:
    name: str
    graph: object
    voxel: float
    region_min: tuple
    region_max: tuple
    exact_volume: float | None = None


def sphere_shape(h=1.0):
    c = np.asarray(SPHERE_CENTRE)
    return Shape(
        "sphere", sphere_graph(), h, tuple(c - 54), tuple(c + 22), 4 / 3 * math.pi * SPHERE_R**3
    )


def capsules_shape(h=1.0):
    c = np.asarray(SPHERE_CENTRE)
    return Shape("capsules", capsules_graph(), h, tuple(c - 54), tuple(c + 24))


def lattice_shape(h=0.25):
    return Shape("lattice", lattice_graph(), h, (-1.0, -1.0, -1.0), (81.0, 81.0, 41.0))


# ------------------------------------------------------------------ sparse block store


@dataclass
class BlockStore:
    """The crossing blocks of a plan packed into one array, plus lookup tables."""

    plan: BlockPlan
    packed: np.ndarray  # float32 [n, 65, 65, 65]
    block_map: np.ndarray  # int32 [nbx, nby, nbz]: index into packed, -1 = not sampled
    status: np.ndarray  # int8 same shape as block_map (-1 inside, 0 sampled, 1 outside)
    order: list  # block indices in packed order

    @property
    def nbytes(self) -> int:
        return int(self.packed.nbytes)


def build_store(plan: BlockPlan, workers: int = 1, watchdog=None) -> tuple[BlockStore, dict]:
    t0 = time.perf_counter()
    blocks = plan.sampled_blocks()
    packed = np.empty((len(blocks), BLOCK_SAMPLES, BLOCK_SAMPLES, BLOCK_SAMPLES), np.float32)
    bmap = np.full(plan.grid_shape, -1, np.int32)
    order = []
    with iter_block_samples(plan, workers=workers, watchdog=watchdog) as stream:
        for i, (blk, vals) in enumerate(stream):
            packed[i] = vals
            bmap[blk] = i
            order.append(blk)
        summary = stream.summary.to_dict()
    summary["total_seconds"] = time.perf_counter() - t0
    return BlockStore(plan, packed, bmap, np.asarray(plan.status), order), summary


def make_lookup(store: BlockStore):
    """Return an njit scalar function ``f(x, y, z) -> float`` = trilinear interpolation of
    the block samples, with the SDF sign convention flipped (positive inside) as manifold3d
    wants. Points in skipped blocks return +-BIG; points outside the block grid are outside."""
    g0 = np.asarray(store.plan.index_min, np.int64)
    h = float(store.plan.voxel_mm)
    nb = np.asarray(store.plan.grid_shape, np.int64)
    packed, bmap, status = store.packed, store.block_map, store.status
    big = 1.0e3

    @numba.njit(cache=False, fastmath=False)
    def f(x, y, z):
        ux = x / h - g0[0]
        uy = y / h - g0[1]
        uz = z / h - g0[2]
        n0 = nb[0] * 64
        n1 = nb[1] * 64
        n2 = nb[2] * 64
        if ux < 0.0 or uy < 0.0 or uz < 0.0 or ux > n0 or uy > n1 or uz > n2:
            return -big
        ix = min(int(math.floor(ux)), n0 - 1)  # noqa: RUF046
        iy = min(int(math.floor(uy)), n1 - 1)  # noqa: RUF046
        iz = min(int(math.floor(uz)), n2 - 1)  # noqa: RUF046
        bx = ix // 64
        by = iy // 64
        bz = iz // 64
        k = bmap[bx, by, bz]
        if k < 0:
            return big if status[bx, by, bz] < 0 else -big
        lx = ix - 64 * bx
        ly = iy - 64 * by
        lz = iz - 64 * bz
        fx = ux - ix
        fy = uy - iy
        fz = uz - iz
        b = packed[k]
        c00 = b[lx, ly, lz] * (1 - fx) + b[lx + 1, ly, lz] * fx
        c10 = b[lx, ly + 1, lz] * (1 - fx) + b[lx + 1, ly + 1, lz] * fx
        c01 = b[lx, ly, lz + 1] * (1 - fx) + b[lx + 1, ly, lz + 1] * fx
        c11 = b[lx, ly + 1, lz + 1] * (1 - fx) + b[lx + 1, ly + 1, lz + 1] * fx
        c0 = c00 * (1 - fy) + c10 * fy
        c1 = c01 * (1 - fy) + c11 * fy
        return -(c0 * (1 - fz) + c1 * fz)

    f(0.0, 0.0, 0.0)  # compile
    return f


# ------------------------------------------------------------------ mesh checks


def canonical(verts: np.ndarray, tris: np.ndarray):
    """Canonical form: vertices lexicographic by (x, y, z) (float64), each triangle rotated
    to start at its smallest vertex index (orientation kept), triangles sorted by index."""
    v = np.ascontiguousarray(verts, dtype=np.float64)
    t = np.asarray(tris, dtype=np.int64)
    order = np.lexsort((v[:, 2], v[:, 1], v[:, 0]))
    rank = np.empty(len(v), np.int64)
    rank[order] = np.arange(len(v))
    v = v[order]
    t = rank[t]
    start = np.argmin(t, axis=1)
    idx = (start[:, None] + np.arange(3)[None, :]) % 3
    t = np.take_along_axis(t, idx, axis=1)
    o = np.lexsort((t[:, 2], t[:, 1], t[:, 0]))
    return v, np.ascontiguousarray(t[o])


def mesh_hash(verts, tris) -> str:
    v, t = canonical(verts, tris)
    h = hashlib.sha256()
    h.update(v.tobytes())
    h.update(t.astype(np.int64).tobytes())
    return h.hexdigest()


def merge_by_coordinate(verts: np.ndarray, tris: np.ndarray):
    """Merge bitwise-equal vertex coordinates; drop triangles that become degenerate."""
    v = np.ascontiguousarray(verts, dtype=np.float64)
    uniq, inv = np.unique(v, axis=0, return_inverse=True)
    inv = inv.reshape(-1)
    t = inv[np.asarray(tris, dtype=np.int64)]
    ok = (t[:, 0] != t[:, 1]) & (t[:, 1] != t[:, 2]) & (t[:, 0] != t[:, 2])
    t = t[ok]
    used = np.unique(t)  # compact: drop vertices no triangle refers to
    remap = np.full(len(uniq), -1, np.int64)
    remap[used] = np.arange(len(used))
    return uniq[used], remap[t]


def mesh_report(verts: np.ndarray, tris: np.ndarray, vertex_check: bool = True) -> dict:
    """Topology and geometry checks with numpy only.

    ``closed_oriented_manifold``: every directed edge occurs exactly once and its reverse
    exactly once (each undirected edge is shared by exactly two triangles with opposite
    orientation). Vertex-manifoldness (single fan around a vertex) is checked separately.
    """
    v = np.asarray(verts, dtype=np.float64)
    t = np.asarray(tris, dtype=np.int64)
    n_v, n_t = len(v), len(t)
    if n_t == 0:
        return {"vertices": n_v, "triangles": 0, "closed_oriented_manifold": False}
    degenerate_idx = int(
        np.count_nonzero((t[:, 0] == t[:, 1]) | (t[:, 1] == t[:, 2]) | (t[:, 0] == t[:, 2]))
    )
    a, b, c = v[t[:, 0]], v[t[:, 1]], v[t[:, 2]]
    cross = np.cross(b - a, c - a)
    area2 = np.linalg.norm(cross, axis=1)
    degenerate_area = int(np.count_nonzero(area2 == 0.0))
    de = np.concatenate([t[:, [0, 1]], t[:, [1, 2]], t[:, [2, 0]]])
    key = de[:, 0] * n_v + de[:, 1]
    rkey = de[:, 1] * n_v + de[:, 0]
    uk, cnt = np.unique(key, return_counts=True)
    dup_directed = int(np.count_nonzero(cnt > 1))
    has_twin = np.isin(uk, rkey)
    n_untwinned = int(np.count_nonzero(~has_twin))
    closed = dup_directed == 0 and n_untwinned == 0
    # vertex manifoldness: for each vertex the incident triangles form one edge-connected fan
    vertex_nonmanifold = 0
    if closed and vertex_check:
        # count, per vertex, the number of directed edges leaving it vs the number of
        # connected "links": edges v->w and the next edge x->v ... use union-find per vertex
        # on triangles sharing an edge incident to the vertex.
        vertex_nonmanifold = _count_nonmanifold_vertices(t, n_v)
    used = np.zeros(n_v, bool)
    used[t.ravel()] = True
    n_used = int(used.sum())
    n_e = len(np.unique(np.minimum(de[:, 0], de[:, 1]) * n_v + np.maximum(de[:, 0], de[:, 1])))
    euler = n_used - n_e + n_t
    vol = float(np.sum(np.einsum("ij,ij->i", a, cross)) / 6.0)
    area = float(area2.sum() / 2)
    return {
        "vertices": n_v,
        "vertices_used": n_used,
        "triangles": n_t,
        "directed_edge_duplicates": dup_directed,
        "edges_without_twin": n_untwinned,
        "closed_oriented_manifold": bool(closed),
        "nonmanifold_vertices": int(vertex_nonmanifold) if vertex_check else None,
        "degenerate_tris_index": degenerate_idx,
        "degenerate_tris_zero_area": degenerate_area,
        "euler_characteristic": int(euler),
        "volume_mm3": vol,
        "area_mm2": area,
        "min_tri_area_mm2": float(area2.min() / 2),
        "bbox_min": v[used].min(0).tolist(),
        "bbox_max": v[used].max(0).tolist(),
    }


def _count_nonmanifold_vertices(t: np.ndarray, n_v: int) -> int:
    """Vertices whose incident triangles do not form a single edge-connected fan."""
    n_t = len(t)
    # for each (triangle, corner) record vertex; fan connectivity through shared edges
    # incident to the vertex: two triangles are adjacent at v if they share an edge (v, w).
    parent = np.arange(n_t * 3)

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    # corner id = 3 * tri + k. For corner (tri, k) at vertex v the two incident edges are
    # (v, next) and (prev, v). Corners at the same vertex joined when the edge is shared.
    # directed edge (a -> b) in tri i at corners (a: k, b: k+1); twin (b -> a) in tri j at
    # corners (b: m, a: m+1): join corner(i, k) with corner(j, m+1) [vertex a] and corner(i,
    # k+1) with corner(j, m) [vertex b].
    edge_of = {}
    for i in range(n_t):
        for k in range(3):
            edge_of[(int(t[i, k]), int(t[i, (k + 1) % 3]))] = (i, k)
    for (a, b), (i, k) in edge_of.items():
        tw = edge_of.get((b, a))
        if tw is None:
            continue
        j, m = tw
        for x, y in ((3 * i + k, 3 * j + (m + 1) % 3), (3 * i + (k + 1) % 3, 3 * j + m)):
            rx, ry = find(x), find(y)
            if rx != ry:
                parent[rx] = ry
    comp_per_vertex: dict[int, set] = {}
    for i in range(n_t):
        for k in range(3):
            comp_per_vertex.setdefault(int(t[i, k]), set()).add(find(3 * i + k))
    return sum(1 for s in comp_per_vertex.values() if len(s) > 1)


# ------------------------------------------------------------------ misc


def peak_rss_mb() -> float:
    import resource

    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return r / (1024 * 1024) if sys.platform == "darwin" else r / 1024


def current_rss_mb() -> float:
    import psutil

    return psutil.Process().memory_info().rss / 2**20


def heavy_lock_hint() -> str:
    return "mkdir .scratch/heavy.lock  (rmdir .scratch/heavy.lock when done)"


def thread_env() -> str:
    return os.environ.get("ONECPU_N", "default")
