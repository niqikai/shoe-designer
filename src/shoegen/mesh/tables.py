"""Marching-cubes lookup tables, generated from a single face rule (plan M0.6).

The tables are not typed in by hand: they are derived at import time from the cube
geometry and one rule for ambiguous faces, so they are consistent by construction and
small enough to verify exhaustively (``tests/test_mesh_tables.py``).

Numbering
---------
Corner ``c = x + 2 y + 4 z`` for ``x, y, z`` in ``{0, 1}``. Edge ``e = 4 * axis + idx``
runs from its lower corner ``c0`` to ``c0 + 2**axis``; ``idx = u + 2 v`` where ``u, v``
are the corner bits of the two other axes in increasing axis order. A corner is *inside*
when its sample is ``< 0`` (``0`` and ``-0.0`` are outside, ADR 0005).

Face rule (why the mesh is closed and manifold)
-----------------------------------------------
Each of the 6 faces is walked counter-clockwise around its outward normal. A crossing
edge where the walk goes outside -> inside is an *entry*, inside -> outside an *exit*;
they alternate, so a face has 0, 2 or 4 crossings. The surface meets the face in
segments ``entry -> exit``; every entry is paired with the **previous** exit along the
walk. With 2 crossings there is nothing to choose. With 4 (the ambiguous face, diagonal
corners alike) this pairing cuts off the two *outside* corners: inside corners are
connected across an ambiguous face, outside corners are separated.

The rule reads only the four corner signs of the face, and the two cubes sharing a face
see the same signs and walk the face in opposite directions, so they produce the same
segments with opposite orientation. Every crossing edge is an entry on one of its two
faces in a cube and an exit on the other, so the segments of a cube close into directed
loops. Each loop is triangulated as a disk (vertices are the loop's edges, triangles
keep the loop's direction, so normals point from inside to outside).

A diagonal of a loop that joins two edges lying on a common cube face would also be a
candidate diagonal of the neighbouring cube across that face; if both cubes chose it,
that mesh edge would get four triangles. The triangulation therefore never uses such
"face-lying" diagonals (only loop sides may lie on a face); every other diagonal joins
two edges that only this cube contains. Under these conditions the triangles around a
lattice edge form a single fan and every mesh edge has exactly two triangles, so the
extracted surface is a closed, consistently oriented 2-manifold wherever the region
boundary is outside.

Why not the classic 256-case table or MC33: the classic table resolves ambiguous faces
differently for a configuration and its complement, which can leave holes; the
asymptotic decider / MC33 compare a face saddle value computed from four floats, which is
consistent too but needs a canonical evaluation order per face and many more cases. The
topology requirement here is watertight + manifold + bitwise reproducible, not agreement
with the trilinear interpolant inside a voxel; the sign-only rule meets it with the least
machinery. Its cost is that sub-voxel topology at ambiguous faces is fixed by the rule
(inside connected), which only matters for features about one voxel thick; the strut rule
``voxel <= 2 r / 4`` (M0.7) keeps struts four voxels across.

Within a loop, among the triangulations without face-lying diagonals the one with the
smallest total diagonal length (vertices at edge midpoints) is used; ties go to the
smallest split index, so the table is fixed.
"""

from __future__ import annotations

from functools import cache

import numpy as np

N_CORNERS = 8
N_EDGES = 12


def corner_offset(c: int) -> tuple[int, int, int]:
    return (c & 1, (c >> 1) & 1, (c >> 2) & 1)


def _corner_index(x: int, y: int, z: int) -> int:
    return x + 2 * y + 4 * z


def _build_edges() -> tuple[tuple[int, int, int], ...]:
    edges = []
    for axis in range(3):
        p, q = (a for a in range(3) if a != axis)
        for idx in range(4):
            off = [0, 0, 0]
            off[p] = idx & 1
            off[q] = idx >> 1
            c0 = _corner_index(*off)
            edges.append((c0, c0 + (1 << axis), axis))
    return tuple(edges)


EDGES = _build_edges()
"""``EDGES[e] = (c0, c1, axis)``."""

_EDGE_BY_CORNERS = {frozenset((c0, c1)): e for e, (c0, c1, _) in enumerate(EDGES)}


def _build_faces() -> tuple[tuple[int, int, int, int], ...]:
    """Corner cycles of the 6 faces, counter-clockwise around the outward normal.

    Face ``2 * axis + side`` is the face ``coord[axis] == side``. With ``(p, q)`` the two
    other axes in cyclic order (``e_p x e_q = e_axis``) the cycle is (0,0) (1,0) (1,1)
    (0,1) in ``(p, q)`` for the ``+axis`` face and the reverse for the ``-axis`` face.
    """
    faces = []
    for axis in range(3):
        p, q = (axis + 1) % 3, (axis + 2) % 3
        for side in (0, 1):
            pq = [(0, 0), (1, 0), (1, 1), (0, 1)]
            if side == 0:
                pq = [(0, 0), (0, 1), (1, 1), (1, 0)]
            cyc = []
            for u, v in pq:
                off = [0, 0, 0]
                off[axis] = side
                off[p] = u
                off[q] = v
                cyc.append(_corner_index(*off))
            faces.append(tuple(cyc))
    return tuple(faces)


FACES = _build_faces()
"""``FACES[f]``: corner cycle of face ``f = 2 * axis + side``."""


def _face_edges(f: int) -> tuple[int, int, int, int]:
    cyc = FACES[f]
    return tuple(_EDGE_BY_CORNERS[frozenset((cyc[i], cyc[(i + 1) % 4]))] for i in range(4))


FACE_EDGES = tuple(_face_edges(f) for f in range(6))
"""``FACE_EDGES[f][i]`` joins corners ``FACES[f][i]`` and ``FACES[f][i + 1]``."""

EDGE_FACES = tuple(tuple(f for f in range(6) if e in FACE_EDGES[f]) for e in range(N_EDGES))
"""The two faces of each edge."""


def share_face(e1: int, e2: int) -> bool:
    """True when edges ``e1 != e2`` lie on a common cube face."""
    return bool(set(EDGE_FACES[e1]) & set(EDGE_FACES[e2]))


def is_inside(config: int, c: int) -> bool:
    return bool((config >> c) & 1)


def crossing_edges(config: int) -> list[int]:
    return [
        e for e, (c0, c1, _) in enumerate(EDGES) if is_inside(config, c0) != is_inside(config, c1)
    ]


def face_segments(config: int, f: int) -> list[tuple[int, int]]:
    """Directed segments ``(entry_edge, exit_edge)`` of the surface on face ``f``."""
    cyc = FACES[f]
    fe = FACE_EDGES[f]
    crossings: list[tuple[int, bool]] = []  # (edge, is_entry) in walk order
    for i in range(4):
        a, b = is_inside(config, cyc[i]), is_inside(config, cyc[(i + 1) % 4])
        if a != b:
            crossings.append((fe[i], b))
    m = len(crossings)
    segs = []
    for p, (e, entry) in enumerate(crossings):
        if entry:
            prev_exit, prev_is_entry = crossings[(p - 1) % m]
            assert not prev_is_entry
            segs.append((e, prev_exit))
    return segs


def cube_loops(config: int) -> list[list[int]]:
    """Directed loops of crossing edges; each starts at its smallest edge index."""
    nxt: dict[int, int] = {}
    for f in range(6):
        for a, b in face_segments(config, f):
            assert a not in nxt, "edge is an entry on two faces"
            nxt[a] = b
    cross = crossing_edges(config)
    assert sorted(nxt) == cross and sorted(nxt.values()) == cross
    loops = []
    seen: set[int] = set()
    for e in cross:
        if e in seen:
            continue
        loop = [e]
        seen.add(e)
        cur = nxt[e]
        while cur != e:
            loop.append(cur)
            seen.add(cur)
            cur = nxt[cur]
        loops.append(loop)
    return loops


def _edge_midpoint(e: int) -> np.ndarray:
    c0, c1, _ = EDGES[e]
    return 0.5 * (np.asarray(corner_offset(c0), float) + np.asarray(corner_offset(c1), float))


def triangulate_loop(loop: list[int]) -> list[tuple[int, int, int]]:
    """Triangles ``(e_i, e_k, e_j)``, ``i < k < j`` in loop order, without face-lying
    diagonals; minimal total diagonal length, ties to the smallest split index."""
    n = len(loop)
    if n < 3:
        raise AssertionError("loop with fewer than 3 vertices")
    mids = [_edge_midpoint(e) for e in loop]

    def side(i: int, j: int) -> bool:
        return j == i + 1 or (i == 0 and j == n - 1)

    def allowed(i: int, j: int) -> bool:
        return side(i, j) or not share_face(loop[i], loop[j])

    def cost(i: int, j: int) -> float:
        if side(i, j):
            return 0.0
        d = mids[i] - mids[j]
        return float(np.sqrt(d @ d))

    inf = float("inf")
    best = [[inf] * n for _ in range(n)]
    split = [[-1] * n for _ in range(n)]
    for i in range(n - 1):
        best[i][i + 1] = 0.0
    for span in range(2, n):
        for i in range(n - span):
            j = i + span
            for k in range(i + 1, j):
                if not (allowed(i, k) and allowed(k, j)):
                    continue
                c = best[i][k] + best[k][j] + cost(i, k) + cost(k, j)
                if c < best[i][j]:
                    best[i][j] = c
                    split[i][j] = k
    if best[0][n - 1] == inf:
        raise AssertionError(f"loop {loop} has no triangulation without face-lying diagonals")
    tris: list[tuple[int, int, int]] = []

    def emit(i: int, j: int) -> None:
        if j - i < 2:
            return
        k = split[i][j]
        tris.append((loop[i], loop[k], loop[j]))
        emit(i, k)
        emit(k, j)

    emit(0, n - 1)
    tris.sort(key=lambda t: sorted(loop.index(e) for e in t))
    return tris


def cube_triangles(config: int) -> list[tuple[int, int, int]]:
    """All triangles of a configuration, loop by loop."""
    out: list[tuple[int, int, int]] = []
    for loop in cube_loops(config):
        out.extend(triangulate_loop(loop))
    return out


@cache
def build_tables() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """``(tri_count int8[256], tri_edges int8[256, 3 * max], edge_info int64[12, 4])``.

    ``tri_edges[config, 3 * t + s]`` is the cube edge of corner ``s`` of triangle ``t``
    (``-1`` padding); ``edge_info[e] = (dx, dy, dz, axis)`` of the edge's lower corner.
    """
    per = [cube_triangles(c) for c in range(256)]
    max_t = max(len(t) for t in per)
    count = np.zeros(256, dtype=np.int8)
    edges = np.full((256, 3 * max_t), -1, dtype=np.int8)
    for c, tris in enumerate(per):
        count[c] = len(tris)
        for t, tri in enumerate(tris):
            edges[c, 3 * t : 3 * t + 3] = tri
    info = np.zeros((N_EDGES, 4), dtype=np.int64)
    for e, (c0, _c1, axis) in enumerate(EDGES):
        info[e, :3] = corner_offset(c0)
        info[e, 3] = axis
    for a in (count, edges, info):
        a.flags.writeable = False
    return count, edges, info


def all_configs() -> range:
    return range(256)


def loop_length_histogram() -> dict[int, int]:
    hist: dict[int, int] = {}
    for c in all_configs():
        for loop in cube_loops(c):
            hist[len(loop)] = hist.get(len(loop), 0) + 1
    return dict(sorted(hist.items()))


__all__ = [
    "EDGES",
    "EDGE_FACES",
    "FACES",
    "FACE_EDGES",
    "N_CORNERS",
    "N_EDGES",
    "all_configs",
    "build_tables",
    "corner_offset",
    "crossing_edges",
    "cube_loops",
    "cube_triangles",
    "face_segments",
    "is_inside",
    "loop_length_histogram",
    "share_face",
    "triangulate_loop",
]
