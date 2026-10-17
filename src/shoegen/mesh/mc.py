"""Per-block Marching Cubes with global edge identities (ADR 0005, plan M0.6).

Input is an M0.5 block: ``float32[65, 65, 65]`` samples at global lattice indices
``start + (a, b, c)``; any shape ``(n0, n1, n2)`` with every ``n >= 2`` works the same way.
The kernel processes the cubes ``[0, n0 - 1) x [0, n1 - 1) x [0, n2 - 1)``, so a 65^3 block
owns its 64^3 cubes and no cube is processed by two blocks.

Vertex identity
---------------
A vertex is the zero crossing on one lattice edge ``(i, j, k, axis)`` (``(i, j, k)`` the
global index of the edge's lower end). Its id packs the edge relative to a common
``id_origin`` (``BlockPlan.index_min``) into an int64::

    id = (i - i0) << 42 | (j - j0) << 22 | (k - k0) << 2 | axis      (20 bits per index)

so id order is (i, j, k, axis) lexicographic order, the same for any block layout of the
same lattice. Vertices of a block come out in increasing id order.

Vertex position
---------------
With ``v0``, ``v1`` the samples at the lower and upper end (float32 widened to float64)
and ``g`` the lower end's global index: ``t = v0 / (v0 - v1)``; the coordinate along
``axis`` is ``(float64(g[axis]) + t) * h`` and the other two are ``float64(g[.]) * h``
(the lattice coordinates of M0.5). Nothing depends on the block, so the same edge gives
bitwise the same vertex in every block. ``t`` is in ``[0, 1]``; it is exactly 1 when the
outside end is exactly 0 (zero is outside, ADR 0005), so vertices can land on lattice
points; several edges then share a coordinate, which the assembly step merges.

Near-zero samples
-----------------
A sample with ``|v| < h / 1024`` is treated as exactly 0, i.e. outside. Without this, a
plane through lattice points evaluated with rounding noise (values of about 1e-16 with
random signs, e.g. a tilted ``HalfSpace``) puts vertices on two neighbouring lattice
points and a third on the edge between them: a zero-area triangle. With the rule such a
plane becomes an exact-zero plane, whose coincident vertices merge cleanly. For a field
with Lipschitz constant about 1, every other vertex is then at least about ``h / 1024`` from
both ends of its edge, far above float32 resolution at shoe coordinates, so rounding
cannot create further coincidences. The surface moves by less than ``h / 1024``.

With ``quantize`` the coordinates are rounded to float32 (and kept as float64): binary STL
stores float32, so the mesh that is validated is the mesh that is written, including
vertices that only coincide after rounding. The rounding is a fixed function of the
float64 value and does not break block invariance.

Every kernel is ``njit(cache=True, nogil=True)`` without ``fastmath`` or ``parallel``: the
RSS watchdog thread must be able to run while a block is meshed (M0.6a).
"""

from __future__ import annotations

from typing import NamedTuple

import numpy as np
from numba import njit

from shoegen.mesh.tables import build_tables

ID_INDEX_BITS = 20
ID_INDEX_LIMIT = 1 << ID_INDEX_BITS
"""Relative lattice indices must lie in ``[0, 2**20)`` (262 m at 0.25 mm)."""
_SHIFT_I = 2 + 2 * ID_INDEX_BITS
_SHIFT_J = 2 + ID_INDEX_BITS
_SHIFT_K = 2

ZERO_SNAP_FRACTION = 2.0**-10
"""Samples with ``|v| < h / 1024`` are treated as exactly 0 (outside); see module doc."""

_JIT = {"cache": True, "nogil": True}


class BlockMesh(NamedTuple):
    """Marching-cubes output of one block.

    ``edge_ids`` (int64, strictly increasing), ``positions`` (float64 ``[nv, 3]``) and
    ``triangles`` (int32 ``[nt, 3]``, indices into this block's vertices, oriented with
    normals from inside to outside).
    """

    edge_ids: np.ndarray
    positions: np.ndarray
    triangles: np.ndarray

    @property
    def nbytes(self) -> int:
        return int(self.edge_ids.nbytes + self.positions.nbytes + self.triangles.nbytes)


def encode_edge_ids(rel_index: np.ndarray, axis: np.ndarray) -> np.ndarray:
    """Ids of edges with lower ends at ``rel_index`` (``(N, 3)``, relative to the id origin)."""
    r = np.asarray(rel_index, dtype=np.int64)
    a = np.asarray(axis, dtype=np.int64)
    if np.any(r < 0) or np.any(r >= ID_INDEX_LIMIT):
        raise ValueError("relative lattice index out of the 20-bit id range")
    return (r[:, 0] << _SHIFT_I) | (r[:, 1] << _SHIFT_J) | (r[:, 2] << _SHIFT_K) | a


def decode_edge_ids(ids: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Inverse of :func:`encode_edge_ids`: ``(rel_index (N, 3), axis (N,))``."""
    ids = np.asarray(ids, dtype=np.int64)
    mask = ID_INDEX_LIMIT - 1
    rel = np.stack(
        [(ids >> _SHIFT_I) & mask, (ids >> _SHIFT_J) & mask, (ids >> _SHIFT_K) & mask], 1
    )
    return rel, ids & 3


@njit(**_JIT)
def _mc_kernel(samples, g0, rel0, h, snap, quantize, tri_count, tri_edges, edge_info):
    n0, n1, n2 = samples.shape
    vals = np.empty((n0, n1, n2), np.float32)
    for a in range(n0):
        for b in range(n1):
            for c in range(n2):
                x = samples[a, b, c]
                if not np.isfinite(x):
                    return (
                        np.empty(0, np.int64),
                        np.empty((0, 3), np.float64),
                        np.empty((0, 3), np.int32),
                        1,
                    )
                vals[a, b, c] = np.float32(0.0) if abs(np.float64(x)) < snap else x

    # ---- vertices: one per crossing edge, in (a, b, c, axis) order = increasing id
    vmap = np.full((n0, n1, n2, 3), -1, np.int32)
    nv = 0
    for a in range(n0):
        for b in range(n1):
            for c in range(n2):
                ins = vals[a, b, c] < 0
                if a + 1 < n0 and (vals[a + 1, b, c] < 0) != ins:
                    vmap[a, b, c, 0] = nv
                    nv += 1
                if b + 1 < n1 and (vals[a, b + 1, c] < 0) != ins:
                    vmap[a, b, c, 1] = nv
                    nv += 1
                if c + 1 < n2 and (vals[a, b, c + 1] < 0) != ins:
                    vmap[a, b, c, 2] = nv
                    nv += 1
    ids = np.empty(nv, np.int64)
    pos = np.empty((nv, 3), np.float64)
    for a in range(n0):
        for b in range(n1):
            for c in range(n2):
                v0 = np.float64(vals[a, b, c])
                for axis in range(3):
                    q = vmap[a, b, c, axis]
                    if q < 0:
                        continue
                    if axis == 0:
                        v1 = np.float64(vals[a + 1, b, c])
                    elif axis == 1:
                        v1 = np.float64(vals[a, b + 1, c])
                    else:
                        v1 = np.float64(vals[a, b, c + 1])
                    t = v0 / (v0 - v1)
                    gi = g0[0] + a
                    gj = g0[1] + b
                    gk = g0[2] + c
                    x = np.float64(gi) * h
                    y = np.float64(gj) * h
                    z = np.float64(gk) * h
                    if axis == 0:
                        x = (np.float64(gi) + t) * h
                    elif axis == 1:
                        y = (np.float64(gj) + t) * h
                    else:
                        z = (np.float64(gk) + t) * h
                    if quantize:
                        x = np.float64(np.float32(x))
                        y = np.float64(np.float32(y))
                        z = np.float64(np.float32(z))
                    pos[q, 0] = x
                    pos[q, 1] = y
                    pos[q, 2] = z
                    ids[q] = (
                        ((rel0[0] + a) << 42) | ((rel0[1] + b) << 22) | ((rel0[2] + c) << 2) | axis
                    )

    # ---- triangles, cube by cube in (a, b, c) order
    nt = 0
    for a in range(n0 - 1):
        for b in range(n1 - 1):
            for c in range(n2 - 1):
                cfg = 0
                for corner in range(8):
                    if vals[a + (corner & 1), b + ((corner >> 1) & 1), c + ((corner >> 2) & 1)] < 0:
                        cfg |= 1 << corner
                nt += tri_count[cfg]
    tris = np.empty((nt, 3), np.int32)
    t = 0
    for a in range(n0 - 1):
        for b in range(n1 - 1):
            for c in range(n2 - 1):
                cfg = 0
                for corner in range(8):
                    if vals[a + (corner & 1), b + ((corner >> 1) & 1), c + ((corner >> 2) & 1)] < 0:
                        cfg |= 1 << corner
                for m in range(tri_count[cfg]):
                    for s in range(3):
                        e = tri_edges[cfg, 3 * m + s]
                        tris[t, s] = vmap[
                            a + edge_info[e, 0],
                            b + edge_info[e, 1],
                            c + edge_info[e, 2],
                            edge_info[e, 3],
                        ]
                    t += 1
    return ids, pos, tris, 0


def mesh_samples(
    vals: np.ndarray,
    start,
    id_origin,
    voxel_mm: float,
    quantize: bool = True,
    snap_zero: bool = True,
) -> BlockMesh:
    """Marching cubes on one block of samples (see module doc).

    ``vals``: float32 ``(n0, n1, n2)`` at global indices ``start + (a, b, c)``;
    ``id_origin``: global index that edge ids are relative to (the same for every block of
    one extraction, normally ``BlockPlan.index_min``). ``snap_zero=False`` disables the
    near-zero rule (tests only).
    """
    v = np.ascontiguousarray(vals, dtype=np.float32)
    if v.ndim != 3 or min(v.shape) < 2:
        raise ValueError(f"samples must be 3-D with every dimension >= 2, got {v.shape}")
    g0 = np.asarray(start, dtype=np.int64).reshape(3)
    o = np.asarray(id_origin, dtype=np.int64).reshape(3)
    rel0 = g0 - o
    if np.any(rel0 < 0) or np.any(rel0 + np.asarray(v.shape) > ID_INDEX_LIMIT):
        raise ValueError(
            f"block at {tuple(g0)} (shape {v.shape}) is outside the 20-bit id range of "
            f"origin {tuple(o)}"
        )
    h = float(voxel_mm)
    if not (np.isfinite(h) and h > 0):
        raise ValueError(f"voxel_mm must be finite and > 0, got {voxel_mm!r}")
    snap = h * ZERO_SNAP_FRACTION if snap_zero else 0.0
    count, edges, info = build_tables()
    ids, pos, tris, status = _mc_kernel(v, g0, rel0, h, snap, bool(quantize), count, edges, info)
    if status != 0:
        raise ValueError("samples contain NaN or infinity")
    return BlockMesh(ids, pos, tris)


def mesh_block_fn(vals, start, id_origin, voxel_mm, quantize, snap_zero=True) -> BlockMesh:
    """Block function for :func:`shoegen.evaluate.iter_block_results` (runs in the worker
    that sampled the block, so only the compact mesh crosses the process boundary)."""
    return mesh_samples(vals, start, id_origin, voxel_mm, quantize, snap_zero)
