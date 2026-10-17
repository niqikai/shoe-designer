"""Marching cubes kernel and assembly on dense sample arrays (plan M0.6).

The random fields are deliberately hostile: random signs (every ambiguous case), 40 %
exact zeros, signed zeros and tiny values. Every layout of blocks must give the same
canonical mesh, and every mesh must be closed, manifold, without coincident vertices and,
with the near-zero rule on, without zero-area triangles.
"""

from __future__ import annotations

import numpy as np
import pytest

from shoegen.mesh import (
    ZERO_SNAP_FRACTION,
    Mesh,
    canonicalize,
    decode_edge_ids,
    encode_edge_ids,
    mesh_from_samples,
    mesh_samples,
    validate_mesh,
)

START = (1000, -7, 33)
H = 0.3


def _field(kind: str, seed: int, n: int = 17) -> np.ndarray:
    rng = np.random.default_rng(seed)
    choices = {
        "sign": ([-1.0, 1.0], None),
        "zeros": ([-2.0, -1.0, 0.0, 1.0, 2.0], [0.15, 0.15, 0.4, 0.15, 0.15]),
        "signed_zeros": ([-1.0, 0.0, -0.0, 1.0], None),
        "tiny": ([-1.0, -1e-30, 1e-30, 0.0, 1.0, -1e-9, 1e-9], None),
        "tiny_f32": ([-1.0, -3e-8, 3e-8, 1.0, 0.5, -0.5], None),
    }[kind]
    v = rng.choice(choices[0], size=(n, n, n), p=choices[1]).astype(np.float32)
    v[0], v[-1], v[:, 0], v[:, -1], v[:, :, 0], v[:, :, -1] = (1.0,) * 6  # closed
    return v


@pytest.mark.parametrize("kind", ["sign", "zeros", "signed_zeros", "tiny", "tiny_f32"])
@pytest.mark.parametrize("quantize", [True, False])
def test_random_fields_closed_manifold_layout_independent(kind, quantize, eval_report):
    totals = np.zeros(4, dtype=np.int64)
    for seed in range(8):
        v = _field(kind, seed)
        ref, st = mesh_from_samples(v, START, H, quantize=quantize)
        for cells in (5, 7, 16):
            other, _ = mesh_from_samples(v, START, H, block_cells=cells, quantize=quantize)
            assert other.equals(ref), f"{kind} seed {seed}: {cells}-cell blocks differ"
        val = validate_mesh(ref)
        assert val.manifold, (kind, seed, val.to_dict(components=False))
        assert val.coincident_vertices == 0 and val.zero_area_triangles == 0
        assert val.unused_vertices == 0 and val.degenerate_triangles == 0
        assert np.array_equal(canonicalize(ref.vertices, ref.faces).faces, ref.faces)
        totals += [st.coincident_points, st.merged_points, st.displaced_points, st.merge_rounds]
    eval_report(
        f"{kind} quantize={quantize}: 8 fields, coincident points={totals[0]}, merged="
        f"{totals[1]}, displaced={totals[2]}, merge rounds={totals[3]}"
    )


@pytest.mark.parametrize("kind", ["tiny", "tiny_f32"])
def test_without_zero_snap_still_closed_manifold(kind):
    """The near-zero rule only removes zero-area slivers; the merge verification alone
    already keeps the mesh closed and manifold."""
    for seed in range(4):
        v = _field(kind, seed)
        ref, _ = mesh_from_samples(v, START, H, snap_zero=False)
        other, _ = mesh_from_samples(v, START, H, block_cells=5, snap_zero=False)
        assert other.equals(ref)
        val = validate_mesh(ref)
        assert val.manifold and val.coincident_vertices == 0


def test_edge_ids_and_positions_are_global():
    rng = np.random.default_rng(3)
    v = rng.normal(size=(9, 8, 7)).astype(np.float32)
    origin = np.array([990, -20, 30])
    bm = mesh_samples(v, START, origin, H, quantize=False)
    assert np.all(np.diff(bm.edge_ids) > 0)
    rel, axis = decode_edge_ids(bm.edge_ids)
    assert np.array_equal(encode_edge_ids(rel, axis), bm.edge_ids)
    g = rel + origin  # global index of the lower end
    loc = g - np.asarray(START)
    v0 = v[loc[:, 0], loc[:, 1], loc[:, 2]].astype(np.float64)
    up = loc + np.eye(3, dtype=np.int64)[axis]
    v1 = v[up[:, 0], up[:, 1], up[:, 2]].astype(np.float64)
    assert np.all((v0 < 0) != (v1 < 0))
    t = v0 / (v0 - v1)
    expect = g.astype(np.float64) * H
    expect[np.arange(len(t)), axis] = (g[np.arange(len(t)), axis].astype(np.float64) + t) * H
    assert np.array_equal(bm.positions.view(np.uint64), expect.view(np.uint64))
    q = mesh_samples(v, START, origin, H, quantize=True)
    assert np.array_equal(q.positions, expect.astype(np.float32).astype(np.float64))


def test_shared_face_vertices_bitwise_equal_between_blocks():
    rng = np.random.default_rng(4)
    v = rng.normal(size=(9, 6, 6)).astype(np.float32)
    origin = np.array(START)
    a = mesh_samples(v[:5], START, origin, H)
    b = mesh_samples(v[4:], np.asarray(START) + (4, 0, 0), origin, H)
    common, ia, ib = np.intersect1d(a.edge_ids, b.edge_ids, return_indices=True)
    rel, axis = decode_edge_ids(common)
    assert len(common) > 5 and np.all(rel[:, 0] == 4) and np.all(axis > 0)
    assert np.array_equal(a.positions[ia].view(np.uint64), b.positions[ib].view(np.uint64))


def test_nonfinite_samples_rejected():
    v = np.ones((3, 3, 3), np.float32)
    v[1, 1, 1] = np.nan
    with pytest.raises(ValueError, match="NaN"):
        mesh_samples(v, (0, 0, 0), (0, 0, 0), 1.0)
    with pytest.raises(ValueError, match="id range"):
        mesh_samples(np.ones((3, 3, 3), np.float32), (0, 0, 0), (1, 0, 0), 1.0)


def _lattice(shape, start=(0, 0, 0)):
    idx = np.indices(shape).reshape(3, -1).T + np.asarray(start)
    return idx.astype(np.float64)


def test_hourglass_pinch_is_resolved():
    """|z| - max(|x|, |y|) / 2: the outside is a double cone around the z axis that pinches
    to the lattice point at the origin (value exactly 0). Merging the four coincident
    vertices there would join the upper and lower cone at one vertex; the merge is
    rejected and the vertices are moved off the point."""
    n = 13
    g = _lattice((n, n, n), (-6, -6, -6))
    x, y, z = g.T
    vals = (np.abs(z) - 0.5 * np.maximum(np.abs(x), np.abs(y))).reshape(n, n, n)
    vals = np.minimum(vals, 5.5 - np.max(np.abs(g), axis=1).reshape(n, n, n))
    vals = -vals  # inside: the solid around the cone, inside the cube [-5.5, 5.5]^3
    vals = vals.astype(np.float32)
    assert vals[6, 6, 6] == 0
    mesh, st = mesh_from_samples(vals, (-6, -6, -6), 1.0)
    val = validate_mesh(mesh)
    assert val.manifold and val.coincident_vertices == 0 and val.zero_area_triangles == 0
    assert st.displaced_points >= 1
    m5, _ = mesh_from_samples(vals, (-6, -6, -6), 1.0, block_cells=5)
    assert m5.equals(mesh)


def test_touching_spheres_stay_separate():
    """Two balls that touch exactly at a lattice point: the two sheets meet there, the
    coincident vertices are not merged into one, and the result is two closed shells."""
    n = 25
    g = _lattice((n, n, n), (-12, -12, -12))
    d1 = np.linalg.norm(g - (-5, 0, 0), axis=1) - 5
    d2 = np.linalg.norm(g - (5, 0, 0), axis=1) - 5
    vals = np.minimum(d1, d2).reshape(n, n, n).astype(np.float32)
    assert vals[12, 12, 12] == 0
    mesh, st = mesh_from_samples(vals, (-12, -12, -12), 1.0)
    val = validate_mesh(mesh)
    assert val.manifold and val.coincident_vertices == 0
    assert val.n_components == 2 and all(c.euler_characteristic == 2 for c in val.components)
    assert st.displaced_points >= 1


def test_plane_through_lattice_points_merges_cleanly():
    """x + y + z <= 0.5 * n: every lattice point on the plane is exactly 0 and has three
    inside neighbours; their three vertices coincide and merge into one (a clean triangle
    collapse), leaving no degenerate triangles."""
    n = 12
    g = _lattice((n, n, n))
    s = g.sum(axis=1)
    box = np.max(np.abs(g - 5.5), axis=1) - 4.6
    vals = np.maximum(s - 15.0, box).reshape(n, n, n).astype(np.float32)
    mesh, st = mesh_from_samples(vals, (0, 0, 0), 1.0)
    val = validate_mesh(mesh)
    assert val.ok, val.to_dict(components=False)
    assert st.coincident_points > 10 and st.merged_points == st.coincident_points
    assert st.displaced_points == 0 and st.degenerate_dropped > 0
    assert val.euler_characteristic == 2


def test_canonicalize_matches_pipeline_order():
    v = _field("zeros", 11)
    mesh, _ = mesh_from_samples(v, START, H)
    rng = np.random.default_rng(0)
    perm = rng.permutation(mesh.n_vertices)
    inv = np.empty_like(perm)
    inv[perm] = np.arange(len(perm))
    shuffled_faces = inv[mesh.faces][rng.permutation(mesh.n_faces)]
    rot = rng.integers(0, 3, size=len(shuffled_faces))
    shuffled_faces = np.stack([np.roll(f, r) for f, r in zip(shuffled_faces, rot, strict=True)])
    again = canonicalize(mesh.vertices[perm], shuffled_faces)
    assert again.equals(mesh)
    assert isinstance(again, Mesh)


def test_zero_snap_threshold():
    assert ZERO_SNAP_FRACTION == 2.0**-10
    v = np.full((2, 2, 2), 1.0, np.float32)
    v[0, 0, 0] = -0.9 * H * ZERO_SNAP_FRACTION  # snapped to 0: outside, no surface
    assert mesh_samples(v, (0, 0, 0), (0, 0, 0), H).triangles.shape[0] == 0
    v[0, 0, 0] = -1.1 * H * ZERO_SNAP_FRACTION
    assert mesh_samples(v, (0, 0, 0), (0, 0, 0), H).triangles.shape[0] == 1
