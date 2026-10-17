"""Exhaustive checks of the generated marching-cubes tables (plan M0.6)."""

from __future__ import annotations

import itertools

import numpy as np
import pytest

from shoegen.mesh import tables as T


def _corner_xyz(c: int) -> np.ndarray:
    return np.asarray(T.corner_offset(c), dtype=float)


def _mid(e: int) -> np.ndarray:
    c0, c1, _ = T.EDGES[e]
    return 0.5 * (_corner_xyz(c0) + _corner_xyz(c1))


def test_numbering():
    assert len(T.EDGES) == 12 and len(T.FACES) == 6
    for e, (c0, c1, axis) in enumerate(T.EDGES):
        assert c1 - c0 == 1 << axis and not (c0 >> axis) & 1
        assert e // 4 == axis
        assert len(T.EDGE_FACES[e]) == 2
    for f, cyc in enumerate(T.FACES):
        axis, side = divmod(f, 2)
        pts = [_corner_xyz(c) for c in cyc]
        assert all(p[axis] == side for p in pts)
        # counter-clockwise around the outward normal
        normal = np.zeros(3)
        normal[axis] = 1.0 if side else -1.0
        area = sum(np.cross(pts[i], pts[(i + 1) % 4]) for i in range(4))
        assert np.dot(area, normal) > 0


@pytest.mark.parametrize("config", range(256))
def test_loops_and_triangles(config):
    cross = T.crossing_edges(config)
    loops = T.cube_loops(config)
    assert sorted(e for lp in loops for e in lp) == cross
    segs = {a: b for f in range(6) for a, b in T.face_segments(config, f)}
    tris = T.cube_triangles(config)
    assert sum(len(lp) - 2 for lp in loops) == len(tris) <= 5
    for lp in loops:
        n = len(lp)
        for i in range(n):
            assert segs[lp[i]] == lp[(i + 1) % n], "loop step is not a face segment"
        lt = T.triangulate_loop(lp)
        # a disk bounded by the loop: sides once, diagonals twice with opposite direction
        directed = [(t[i], t[(i + 1) % 3]) for t in lt for i in range(3)]
        assert len(set(directed)) == len(directed)
        sides = {(lp[i], lp[(i + 1) % n]) for i in range(n)}
        for a, b in directed:
            if (a, b) in sides:
                continue
            assert (b, a) in directed, "interior edge without its twin"
            assert not T.share_face(a, b), "face-lying diagonal"
        assert sides <= set(directed)
        # orientation follows the loop: each triangle's corners come in loop order
        for t in lt:
            idx = [lp.index(e) for e in t]
            assert idx == sorted(idx)


def test_orientation_points_away_from_inside():
    # one inside corner: the triangle's normal points from the corner into the cube
    for c in range(8):
        (tri,) = T.cube_triangles(1 << c)
        p = [_mid(e) for e in tri]
        n = np.cross(p[1] - p[0], p[2] - p[0])
        assert np.dot(n, np.full(3, 0.5) - _corner_xyz(c)) > 0
        # the complement is the same triangle reversed
        (tri2,) = T.cube_triangles(255 ^ (1 << c))
        assert sorted(tri2) == sorted(tri)
        q = [_mid(e) for e in tri2]
        assert np.dot(np.cross(q[1] - q[0], q[2] - q[0]), n) < 0


def test_shared_face_segments_agree_between_neighbours():
    """Cube A sees face (axis, 1), cube B the same face as (axis, 0): for every sign pattern
    on the face and every pattern elsewhere, the segments are the same edge pairs with
    opposite direction. This is what makes the global mesh closed and oriented."""
    for axis in range(3):
        fa, fb = 2 * axis + 1, 2 * axis

        # map an edge of face fa in cube A to the edge of face fb in cube B (shift -1 on axis)
        def to_b(e, axis=axis):
            c0, c1, _ = T.EDGES[e]
            d0 = c0 - (1 << axis)
            d1 = c1 - (1 << axis)
            for e2, (x0, x1, _ax) in enumerate(T.EDGES):
                if {x0, x1} == {d0, d1}:
                    return e2
            raise AssertionError

        face_corners = T.FACES[fa]
        for signs in itertools.product((0, 1), repeat=4):
            for rest in range(16):
                ca = 0
                for c, s in zip(face_corners, signs, strict=True):
                    ca |= s << c
                cb = 0
                for c, s in zip(face_corners, signs, strict=True):
                    cb |= s << (c - (1 << axis))
                others_a = [c for c in range(8) if not (c >> axis) & 1]
                others_b = [c for c in range(8) if (c >> axis) & 1]
                for i, c in enumerate(others_a):
                    ca |= ((rest >> i) & 1) << c
                for i, c in enumerate(others_b):
                    cb |= ((rest >> i) & 1) << c
                sa = {(to_b(a), to_b(b)) for a, b in T.face_segments(ca, fa)}
                sb = set(T.face_segments(cb, fb))
                assert sb == {(b, a) for a, b in sa}, (axis, signs, rest)


def test_ambiguous_face_connects_inside():
    # face z=0 with corners 0 and 3 inside (diagonal): the outside corners 1 and 2 are cut off
    f = 4  # axis 2, side 0
    segs = T.face_segments((1 << 0) | (1 << 3), f)
    assert len(segs) == 2
    cut = []
    for a, b in segs:
        ca = set(T.EDGES[a][:2])
        cb = set(T.EDGES[b][:2])
        (corner,) = ca & cb
        cut.append(corner)
    assert sorted(cut) == [1, 2]


def test_tables_arrays():
    count, edges, info = T.build_tables()
    assert count.shape == (256,) and edges.shape == (256, 15) and info.shape == (12, 4)
    assert count[0] == count[255] == 0
    for c in range(256):
        tris = T.cube_triangles(c)
        assert count[c] == len(tris)
        assert edges[c, : 3 * len(tris)].tolist() == [e for t in tris for e in t]
        assert np.all(edges[c, 3 * len(tris) :] == -1)
    assert not count.flags.writeable
    assert T.loop_length_histogram() == {3: 144, 4: 66, 5: 72, 6: 52, 7: 24}
