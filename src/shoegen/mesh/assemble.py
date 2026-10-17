"""Weld per-block marching-cubes output into one canonical mesh (ADR 0005, plan M0.6).

All heavy steps run in ``njit(nogil=True)`` kernels, so the RSS watchdog thread keeps
running during assembly.

1. **Weld by edge id.** Blocks are concatenated in block order and a stable sort by id
   joins the copies of an edge shared by two to eight blocks. The copies must have bitwise
   equal coordinates (same two samples, same global index); a mismatch raises
   :class:`WeldError`.
2. **Canonical vertex order.** Vertices are sorted by ``(x, y, z)``, ties by id. Id order is
   the (i, j, k, axis) order of the lattice, so the order does not depend on the block
   layout, on skipped blocks or on the worker count.
3. **Merge coincident vertices** (ADR 0005). A sample that is exactly 0 is outside, so the
   crossings on the edges from it to inside neighbours sit on the lattice point itself
   (the same happens when a sample is so small that the interpolation, or the float32
   rounding of ``quantize``, lands there). Coincident vertices can only meet at such a
   lattice point ``P``, and each lies on a different edge of ``P``. Vertices with equal
   coordinates that a (zero-length) mesh edge joins are merged into the one with the
   smallest rank, and triangles that then repeat a vertex are dropped.
4. **Verify every merge.** Merging is an edge collapse; it keeps the surface a manifold
   only when the collapse satisfies the link condition. That holds for the configurations
   a distance field produces (a plane through ``P`` gives a single vertex, an edge or a
   triangle of coincident vertices), but not for arbitrary sign patterns: random fields
   with many zeros produce merges that leave edges with four triangles, flaps (two
   triangles on the same three vertices) or a vertex whose triangles form two fans, and
   two sheets that touch at ``P`` leave coincident vertices that no edge joins. So after
   merging, a coincidence group is accepted only if it collapsed to one vertex and no
   triangle around it has a non-manifold or open edge, a second fan, a flap partner or
   zero area. Rejected groups are not merged: their vertices are moved off ``P`` along
   their own edges by ``delta = max(h / 1024, 4 ulp_float32)`` (at most 0.001 mm at
   1 mm voxels), which restores the marching-cubes topology there (closed and manifold by
   construction, see :mod:`shoegen.mesh.tables`). The check is repeated until no group is
   rejected; every round removes at least one group, so it terminates. The result has no
   coincident vertices and no degenerate triangles, and every decision depends only on
   the canonical mesh, so it is still layout-independent and deterministic.
5. **Compact and order triangles** canonically (see :mod:`shoegen.mesh.model`).

Memory: :func:`estimate_assembly_bytes` bounds the arrays allocated here; callers check
it against the RSS budget before concatenating (:class:`MeshMemoryError`, exit code 6).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass

import numpy as np
from numba import njit

from shoegen.mesh import topology as topo
from shoegen.mesh.mc import BlockMesh
from shoegen.mesh.model import (
    Mesh,
    index_dtype,
    lexsort_rows_stable,
    rotate_min_first,
    sort_triangles,
)
from shoegen.resources import EXIT_RESOURCE_LIMIT

_JIT = {"cache": True, "nogil": True}

BYTES_PER_RAW_VERTEX = 120
"""Upper bound of assembly memory per vertex as emitted by the blocks (before welding):
ids, positions, sort permutations, rank and inverse maps, canonical copies."""
BYTES_PER_TRIANGLE = 160
"""Upper bound per triangle: block-local and global index arrays, half-edge buckets,
twins, fan labels, triangle sort buffers and the output faces."""
DISPLACE_FRACTION = 2.0**-10
"""Rejected merges move vertices ``h / 1024`` (or 4 float32 ulps) off the lattice point."""
MAX_MERGE_ROUNDS = 10_000


class WeldError(RuntimeError):
    """The same lattice edge produced different coordinates in two blocks (a bug)."""


class MeshMemoryError(MemoryError):
    """The assembly would exceed the memory budget; exit code 6 like the RSS watchdog."""

    exit_code = EXIT_RESOURCE_LIMIT


def estimate_assembly_bytes(raw_vertices: int, triangles: int) -> int:
    return int(BYTES_PER_RAW_VERTEX * raw_vertices + BYTES_PER_TRIANGLE * triangles)


@dataclass
class AssemblyStats:
    blocks: int = 0
    blocks_with_triangles: int = 0
    raw_vertices: int = 0
    """Vertices as emitted by the blocks (edges on shared block faces counted per block)."""
    raw_triangles: int = 0
    welded_vertices: int = 0
    """Distinct edge ids."""
    shared_vertices: int = 0
    """Raw vertices joined to a copy from another block."""
    coincident_points: int = 0
    """Lattice points where two or more vertices coincided before merging."""
    merged_points: int = 0
    """Coincidence points whose vertices were merged (accepted merges)."""
    merged_vertices: int = 0
    """Vertices removed by accepted merges."""
    displaced_points: int = 0
    """Coincidence points whose merge was rejected (vertices moved off the point)."""
    displaced_vertices: int = 0
    degenerate_dropped: int = 0
    merge_rounds: int = 0
    vertices: int = 0
    triangles: int = 0
    memory_estimate_bytes: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------- kernels


@njit(**_JIT)
def _weld_ids(ids, pos):
    n = ids.shape[0]
    order = np.argsort(ids, kind="mergesort")
    nu = 0
    for r in range(n):
        if r == 0 or ids[order[r]] != ids[order[r - 1]]:
            nu += 1
    uids = np.empty(nu, np.int64)
    upos = np.empty((nu, 3), np.float64)
    uniq_of_raw = np.empty(n, np.int64)
    u = -1
    mismatch = 0
    for r in range(n):
        i = order[r]
        if u < 0 or ids[i] != uids[u]:
            u += 1
            uids[u] = ids[i]
            upos[u, 0] = pos[i, 0]
            upos[u, 1] = pos[i, 1]
            upos[u, 2] = pos[i, 2]
        elif pos[i, 0] != upos[u, 0] or pos[i, 1] != upos[u, 1] or pos[i, 2] != upos[u, 2]:
            mismatch += 1
        uniq_of_raw[i] = u
    return uids, upos, uniq_of_raw, mismatch


@njit(**_JIT)
def _welded_triangles(tris_local, tri_off, v_off, uniq_of_raw, out):
    for b in range(tri_off.shape[0] - 1):
        vo = v_off[b]
        for t in range(tri_off[b], tri_off[b + 1]):
            for s in range(3):
                out[t, s] = uniq_of_raw[vo + tris_local[t, s]]


@njit(**_JIT)
def _apply_rank(tri_w, rank, out):
    for t in range(tri_w.shape[0]):
        for s in range(3):
            out[t, s] = rank[tri_w[t, s]]


@njit(**_JIT)
def _coincidence_groups(cpos):
    """``grp[r]``: first rank of ``r``'s run of equal coordinates, or -1 if alone.
    Returns (grp, number of runs)."""
    nv = cpos.shape[0]
    grp = np.full(nv, -1, np.int64)
    runs = 0
    for r in range(1, nv):
        if (
            cpos[r, 0] == cpos[r - 1, 0]
            and cpos[r, 1] == cpos[r - 1, 1]
            and cpos[r, 2] == cpos[r - 1, 2]
        ):
            if grp[r - 1] < 0:
                grp[r - 1] = r - 1
                runs += 1
            grp[r] = grp[r - 1]
    return grp, runs


@njit(**_JIT)
def _merge(tri, grp):
    """Union equal-coordinate vertices joined by an edge; rewrite ``tri`` in place.
    Returns ``rep`` (representative = smallest rank of each merged set)."""
    nv = grp.shape[0]
    parent = np.arange(nv)
    nt = tri.shape[0]
    for t in range(nt):
        for s in range(3):
            a = tri[t, s]
            b = tri[t, (s + 1) % 3]
            if a != b and grp[a] >= 0 and grp[a] == grp[b]:
                topo._union_min(parent, a, b)
    rep = np.empty(nv, np.int64)
    for r in range(nv):
        rep[r] = topo._find(parent, r)
    for t in range(nt):
        for s in range(3):
            tri[t, s] = rep[tri[t, s]]
    return rep


@njit(**_JIT)
def _compact_rows(tri, keep):
    """Move kept rows to the front (order kept); returns the new count."""
    n = 0
    for t in range(tri.shape[0]):
        if keep[t]:
            if n != t:
                tri[n, 0] = tri[t, 0]
                tri[n, 1] = tri[t, 1]
                tri[n, 2] = tri[t, 2]
            n += 1
    return n


@njit(**_JIT)
def _nondegenerate(tri):
    nt = tri.shape[0]
    keep = np.empty(nt, np.bool_)
    for t in range(nt):
        a = tri[t, 0]
        b = tri[t, 1]
        c = tri[t, 2]
        keep[t] = a != b and b != c and a != c
    return keep


@njit(**_JIT)
def _rejected_groups(tri, cpos, grp, rep, twin, n_fans):
    """Coincidence groups whose merge is not clean (see module doc, step 4)."""
    nv = cpos.shape[0]
    nt = tri.shape[0]
    bad = np.zeros(nv, np.bool_)  # indexed by group id (= first rank of the run)
    # (a) a group that did not collapse to a single vertex
    first_rep = np.full(nv, -1, np.int64)
    for r in range(nv):
        g = grp[r]
        if g < 0:
            continue
        if first_rep[g] < 0:
            first_rep[g] = rep[r]
        elif rep[r] != first_rep[g]:
            bad[g] = True
    # (b) problem vertices: open / non-manifold / misoriented edges, fans != 1,
    #     zero-area triangles, flap partners
    prob = np.zeros(nv, np.bool_)
    for v in range(nv):
        if n_fans[v] > 1:
            prob[v] = True
    for t in range(nt):
        for s in range(3):
            if twin[3 * t + s] < 0:
                prob[tri[t, s]] = True
                prob[tri[t, (s + 1) % 3]] = True
        i0 = tri[t, 0]
        i1 = tri[t, 1]
        i2 = tri[t, 2]
        ux = cpos[i1, 0] - cpos[i0, 0]
        uy = cpos[i1, 1] - cpos[i0, 1]
        uz = cpos[i1, 2] - cpos[i0, 2]
        wx = cpos[i2, 0] - cpos[i0, 0]
        wy = cpos[i2, 1] - cpos[i0, 1]
        wz = cpos[i2, 2] - cpos[i0, 2]
        nx = uy * wz - uz * wy
        ny = uz * wx - ux * wz
        nz = ux * wy - uy * wx
        if nx == 0.0 and ny == 0.0 and nz == 0.0:
            prob[i0] = True
            prob[i1] = True
            prob[i2] = True
        # flap: the twin of a -> b lies in a triangle with the same third vertex
        for s in range(3):
            tw = twin[3 * t + s]
            if tw >= 0:
                t2 = tw // 3
                s2 = tw - 3 * t2
                if tri[t2, (s2 + 2) % 3] == tri[t, (s + 2) % 3]:
                    prob[i0] = True
                    prob[i1] = True
                    prob[i2] = True
    # (c) blame every merged group in the triangles around a problem vertex
    for t in range(nt):
        if prob[tri[t, 0]] or prob[tri[t, 1]] or prob[tri[t, 2]]:
            for s in range(3):
                g = grp[tri[t, s]]
                if g >= 0:
                    bad[g] = True
    return bad


@njit(**_JIT)
def _displace(upos, uids, perm, grp, bad, id_origin, h, quantize):
    """Move every vertex of a rejected group off its lattice point along its edge.
    Returns (points, vertices) displaced, or (-1, r) if vertex rank r is not at an end
    of its edge (a bug)."""
    nv = perm.shape[0]
    mask = (1 << 20) - 1
    npts = 0
    nverts = 0
    for r in range(nv):
        g = grp[r]
        if g < 0 or not bad[g]:
            continue
        if g == r:
            npts += 1
        u = perm[r]
        eid = uids[u]
        axis = eid & 3
        if axis == 0:
            gi = ((eid >> 42) & mask) + id_origin[0]
        elif axis == 1:
            gi = ((eid >> 22) & mask) + id_origin[1]
        else:
            gi = ((eid >> 2) & mask) + id_origin[2]
        lo = np.float64(gi) * h
        hi = np.float64(gi + 1) * h
        x = upos[u, axis]
        mag = max(abs(lo), abs(hi))
        ulp = np.float64(np.spacing(np.float32(mag)))
        delta = max(h * 0.0009765625, 4.0 * ulp)
        if quantize:
            qlo = np.float64(np.float32(lo))
            qhi = np.float64(np.float32(hi))
        else:
            qlo = lo
            qhi = hi
        if x == qlo:
            y = lo + delta
        elif x == qhi:
            y = hi - delta
        else:
            return -1, r
        if quantize:
            y = np.float64(np.float32(y))
        upos[u, axis] = y
        nverts += 1
    return npts, nverts


@njit(**_JIT)
def _compact(tri, cpos):
    """Drop unused vertices (order kept); rewrites ``tri`` in place."""
    nv = cpos.shape[0]
    used = np.zeros(nv, np.bool_)
    for t in range(tri.shape[0]):
        for s in range(3):
            used[tri[t, s]] = True
    new = np.full(nv, -1, np.int64)
    n = 0
    for v in range(nv):
        if used[v]:
            new[v] = n
            n += 1
    out = np.empty((n, 3), np.float64)
    for v in range(nv):
        if used[v]:
            out[new[v], 0] = cpos[v, 0]
            out[new[v], 1] = cpos[v, 1]
            out[new[v], 2] = cpos[v, 2]
    for t in range(tri.shape[0]):
        for s in range(3):
            tri[t, s] = new[tri[t, s]]
    return out


# ---------------------------------------------------------------- driver


def assemble_blocks(
    blocks: Sequence[BlockMesh],
    *,
    id_origin,
    voxel_mm: float,
    quantize: bool = True,
    memory_limit_bytes: int | None = None,
    current_rss_bytes: int | None = None,
) -> tuple[Mesh, AssemblyStats]:
    """Weld ``blocks`` (in block order) into a canonical :class:`Mesh`.

    ``id_origin``, ``voxel_mm`` and ``quantize`` must be those the blocks were meshed
    with. If ``memory_limit_bytes`` is given, :class:`MeshMemoryError` is raised before
    any large allocation when ``current_rss_bytes + estimate_assembly_bytes(...)``
    exceeds it.
    """
    st = AssemblyStats(blocks=len(blocks))
    nv_b = np.array([b.edge_ids.shape[0] for b in blocks], dtype=np.int64)
    nt_b = np.array([b.triangles.shape[0] for b in blocks], dtype=np.int64)
    st.blocks_with_triangles = int(np.count_nonzero(nt_b))
    st.raw_vertices = int(nv_b.sum())
    st.raw_triangles = int(nt_b.sum())
    st.memory_estimate_bytes = estimate_assembly_bytes(st.raw_vertices, st.raw_triangles)
    if memory_limit_bytes is not None:
        rss = 0 if current_rss_bytes is None else int(current_rss_bytes)
        if rss + st.memory_estimate_bytes > memory_limit_bytes:
            raise MeshMemoryError(
                f"mesh assembly needs about {st.memory_estimate_bytes / 2**30:.2f} GiB "
                f"({st.raw_vertices} vertices, {st.raw_triangles} triangles) on top of "
                f"{rss / 2**30:.2f} GiB in use; limit {memory_limit_bytes / 2**30:.2f} GiB"
            )
    if st.raw_triangles == 0:
        return Mesh(np.empty((0, 3), np.float64), np.empty((0, 3), np.int32)), st

    v_off = np.zeros(len(blocks) + 1, dtype=np.int64)
    v_off[1:] = np.cumsum(nv_b)
    t_off = np.zeros(len(blocks) + 1, dtype=np.int64)
    t_off[1:] = np.cumsum(nt_b)
    ids = np.concatenate([b.edge_ids for b in blocks])
    pos = np.concatenate([b.positions for b in blocks])
    local = np.concatenate([b.triangles for b in blocks])

    uids, upos, uniq_of_raw, mismatch = _weld_ids(ids, pos)
    if mismatch:
        raise WeldError(f"{mismatch} shared edge vertices differ between blocks")
    del ids, pos
    nv = int(uids.shape[0])
    st.welded_vertices = nv
    st.shared_vertices = st.raw_vertices - nv
    idt = index_dtype(max(nv, 3 * st.raw_triangles))
    tri_w = np.empty((st.raw_triangles, 3), dtype=idt)
    _welded_triangles(local, t_off, v_off, uniq_of_raw, tri_w)
    del local, uniq_of_raw

    origin = np.asarray(id_origin, dtype=np.int64).reshape(3)
    h = float(voxel_mm)
    tri = np.empty_like(tri_w)
    first_round = True
    for rnd in range(1, MAX_MERGE_ROUNDS + 1):
        st.merge_rounds = rnd
        perm = lexsort_rows_stable(upos, np.arange(nv, dtype=np.int64))
        rank = np.empty(nv, dtype=np.int64)
        rank[perm] = np.arange(nv, dtype=np.int64)
        cpos = upos[perm]
        tri = np.empty_like(tri_w)
        _apply_rank(tri_w, rank, tri)
        del rank
        grp, runs = _coincidence_groups(cpos)
        if first_round:
            st.coincident_points = int(runs)
            first_round = False
        if runs == 0:
            st.merged_points = 0
            st.merged_vertices = 0
            break
        rep = _merge(tri, grp)
        keep = _nondegenerate(tri)
        n = _compact_rows(tri, keep)
        dropped = int(tri.shape[0] - n)
        tri = tri[:n]
        twin, _edge_of, _counts = topo.edge_analysis(tri, nv)
        n_fans, _fan_of, _nf = topo.vertex_fans(tri, twin, nv)
        bad = _rejected_groups(tri, cpos, grp, rep, twin, n_fans)
        del twin, _edge_of, _fan_of
        if not bad.any():
            st.merged_points = int(runs)
            st.merged_vertices = int(np.count_nonzero(rep != np.arange(nv)))
            st.degenerate_dropped = dropped
            break
        pts, nverts = _displace(upos, uids, perm, grp, bad, origin, h, bool(quantize))
        if pts < 0:
            raise WeldError(f"coincident vertex (rank {nverts}) is not at a lattice point")
        st.displaced_points += int(pts)
        st.displaced_vertices += int(nverts)
    else:  # pragma: no cover - every round removes a group
        raise RuntimeError("coincident-vertex merging did not converge")

    verts = _compact(tri, cpos)
    rotate_min_first(tri)
    tri = tri[sort_triangles(tri, verts.shape[0])]
    faces = np.ascontiguousarray(tri.astype(index_dtype(verts.shape[0]), copy=False))
    st.vertices = int(verts.shape[0])
    st.triangles = int(faces.shape[0])
    return Mesh(verts, faces), st
