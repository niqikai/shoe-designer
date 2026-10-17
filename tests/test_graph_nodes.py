"""Ground truth for every node type (plan M0.4 acceptance)."""

from __future__ import annotations

import numpy as np
import pytest
from graph_testutil import (
    DISK_C,
    DISK_R,
    SPHERE_C,
    SPHERE_R,
    box_truth,
    capped_cylinder_truth,
    capsule_truth,
    disk_grid,
    halfspace_truth,
    height_field,
    plane_height_sdf,
    rng,
    rounded_box_truth,
    single,
    sphere_field,
    sphere_sdf,
)

from shoegen.graph import (
    Box,
    Capsule,
    Extrude2D,
    GraphBuilder,
    HalfSpace,
    Intersect,
    MemoryArrayStore,
    Offset,
    RoundedBox,
    ShellOutward,
    SmoothUnion,
    Subtract,
    Union,
    compile_graph,
)

TOL = 1e-12


def _pts(n=20000, lo=-30.0, hi=30.0, seed=1):
    return rng(seed).uniform(lo, hi, (n, 3))


@pytest.mark.parametrize(
    ("node", "truth"),
    [
        (
            Box(center=(1.0, -2.0, 0.5), half_size=(10.0, 4.0, 7.0)),
            lambda p: box_truth(p, (1.0, -2.0, 0.5), (10.0, 4.0, 7.0)),
        ),
        (
            RoundedBox(center=(-1.0, 2.0, 3.0), half_size=(12.0, 6.0, 8.0), radius=3.0),
            lambda p: rounded_box_truth(p, (-1.0, 2.0, 3.0), (12.0, 6.0, 8.0), 3.0),
        ),
        (
            RoundedBox(center=(0.0, 0.0, 0.0), half_size=(5.0, 5.0, 5.0), radius=5.0),
            lambda p: np.linalg.norm(p, axis=1) - 5.0,
        ),
        (
            Capsule(a=(-8.0, 1.0, 2.0), b=(9.0, -3.0, 5.0), radius=2.5),
            lambda p: capsule_truth(p, (-8.0, 1.0, 2.0), (9.0, -3.0, 5.0), 2.5),
        ),
        (
            Capsule(a=(3.0, 3.0, 3.0), b=(3.0, 3.0, 3.0), radius=4.0),
            lambda p: np.linalg.norm(p - 3.0, axis=1) - 4.0,
        ),
        (
            HalfSpace(normal=(1.0, 2.0, -2.0), offset=1.5),
            lambda p: halfspace_truth(p, (1.0, 2.0, -2.0), 1.5),
        ),
    ],
    ids=["box", "rounded_box", "rounded_box_sphere", "capsule", "capsule_degenerate", "halfspace"],
)
def test_analytic_leaf_matches_closed_form(node, truth):
    p = _pts()
    d = compile_graph(single(node)).eval(p)
    np.testing.assert_allclose(d, truth(p), rtol=0, atol=TOL)


def test_operators_match_closed_form():
    p = _pts(seed=2)
    a_c, a_h = (0.0, 0.0, 0.0), (10.0, 8.0, 6.0)
    b_a, b_b, b_r = (-12.0, 0.0, 0.0), (12.0, 3.0, 2.0), 4.0
    da = box_truth(p, a_c, a_h)
    db = capsule_truth(p, b_a, b_b, b_r)

    b = GraphBuilder()
    ia = b.add(Box(center=a_c, half_size=a_h), "a")
    ib = b.add(Capsule(a=b_a, b=b_b, radius=b_r), "b")
    cases = {
        b.add(Union(children=[ia, ib]), "u"): np.minimum(da, db),
        b.add(Intersect(children=[ia, ib]), "i"): np.maximum(da, db),
        b.add(Subtract(base=ia, tool=ib), "s"): np.maximum(da, -db),
        b.add(Offset(child=ia, distance=1.5), "o"): da - 1.5,
        b.add(ShellOutward(child=ia, thickness=2.0), "sh"): np.maximum(da - 2.0, -da),
    }
    k = 3.0
    hh = np.maximum(k - np.abs(da - db), 0.0) / k
    cases[b.add(SmoothUnion(children=[ia, ib], k=k), "su")] = np.minimum(da, db) - hh**2 * k / 4
    root = b.add(Union(children=list(cases)), "root")
    graph = b.build(root)
    for nid, truth in cases.items():
        d = compile_graph(graph, root=nid).eval(p)
        np.testing.assert_allclose(d, truth, rtol=0, atol=TOL, err_msg=nid)


def test_smooth_union_bounds():
    # smin <= min, deviates by at most k/4, equals min where |a - b| >= k.
    p = _pts(seed=3)
    b = GraphBuilder()
    ia = b.add(Box(center=(0.0, 0.0, 0.0), half_size=(5.0, 5.0, 5.0)))
    ib = b.add(Box(center=(8.0, 0.0, 0.0), half_size=(5.0, 5.0, 5.0)))
    su = b.add(SmoothUnion(children=[ia, ib], k=2.0))
    g = b.build(su)
    d = compile_graph(g).eval(p)
    da = compile_graph(g, root=ia).eval(p)
    db = compile_graph(g, root=ib).eval(p)
    m = np.minimum(da, db)
    assert np.all(d <= m)
    assert np.all(m - d <= 0.5 + 1e-12)
    far = np.abs(da - db) >= 2.0
    assert np.array_equal(d[far], m[far])


def test_shell_outward_thickness():
    # Walk along a face normal: material exactly for 0 <= d_child <= t.
    b = GraphBuilder()
    box = b.add(Box(center=(0.0, 0.0, 0.0), half_size=(10.0, 10.0, 10.0)))
    sh = b.add(ShellOutward(child=box, thickness=2.0))
    z = np.linspace(-15.0, 15.0, 3001)
    p = np.column_stack([np.zeros_like(z), np.zeros_like(z), z])
    d = compile_graph(b.build(sh)).eval(p)
    inside = d <= 0
    assert np.allclose(np.abs(z[inside]).min(), 10.0) and np.allclose(np.abs(z[inside]).max(), 12.0)


def test_extrude2d_matches_capped_cylinder():
    store = MemoryArrayStore()
    grid = disk_grid(store)
    z0, z1 = -4.0, 7.0
    node = Extrude2D(field=grid, z_min=z0, z_max=z1)
    p = rng(4).uniform(-30.0, 30.0, (20000, 3))
    p[:, :2] += DISK_C
    d = compile_graph(single(node), store).eval(p)
    truth = capped_cylinder_truth(p, DISK_C, DISK_R, z0, z1)
    # Away from the axis (where the 2D cone tip is not representable by bilinear samples)
    # and inside the 2D grid, the only error is the bilinear sampling error of |xy| - R.
    rho = np.linalg.norm(p[:, :2] - DISK_C, axis=1)
    sel = (rho > 3.0) & (np.abs(p[:, :2] - DISK_C).max(axis=1) < DISK_R + 10.0)
    assert np.abs(d - truth)[sel].max() < 0.01
    # Exact formula: outside is exact too, also off the z-slab corners.
    corner = sel & (truth > 0) & (np.abs(p[:, 2] - 1.5) > 5.5) & (rho > DISK_R)
    assert corner.sum() > 100
    assert np.abs(d - truth)[corner].max() < 0.01
    # Beyond the 2D grid the value is a lower bound of the true distance.
    far = np.abs(p[:, :2] - DISK_C).max(axis=1) > DISK_R + 10.0
    assert np.all(d[far] <= truth[far] + 1e-9)


def test_extrude2d_offset_grows_outline():
    store = MemoryArrayStore()
    node = Extrude2D(field=disk_grid(store), z_min=-1.0, z_max=1.0, offset=3.0)
    p = np.array([[DISK_C[0] + DISK_R + 3.0 + 0.5, DISK_C[1], 0.0]])
    d = compile_graph(single(node), store).eval(p)
    assert abs(d[0] - 0.5) < 0.01


def test_sampled_field_two_levels():
    store = MemoryArrayStore()
    node = sphere_field(store)
    prog = compile_graph(single(node), store)
    c = np.asarray(SPHERE_C)
    r = rng(5)
    p = np.vstack([r.uniform(-30.0, 30.0, (30000, 3)), r.uniform(-90.0, 90.0, (30000, 3))]) + c
    d = prog.eval(p)
    truth = sphere_sdf(SPHERE_C, SPHERE_R)(p)
    band = np.abs(truth) <= node.grid.narrow_band_mm
    assert band.sum() > 1000
    assert np.abs(d - truth)[band].max() <= 0.05  # M0.3 criterion
    g = node.grid
    coarse = store.get(g.coarse_sha256)
    chi = np.asarray(g.coarse_origin) + (np.asarray(coarse.shape) - 1) * g.coarse_h
    in_coarse = np.all((p >= g.coarse_origin) & (p <= chi), axis=1)
    # Excluding the sphere centre, where the SDF has a kink that 2 mm trilinear samples
    # cannot represent (error up to ~sqrt(3)/2 * coarse_h there).
    smooth = np.linalg.norm(p - c, axis=1) > 4.0
    assert np.abs(d - truth)[in_coarse & ~band & smooth].max() <= 1.0
    far = ~in_coarse
    assert far.sum() > 1000
    assert np.all(d[far] <= truth[far])
    assert np.all(d[far] > 0)


def test_height_surface_field_in_band():
    store = MemoryArrayStore()
    node = height_field(store)
    p = rng(6).uniform(-20.0, 20.0, (20000, 3))
    p[:, 2] = 0.3 * p[:, 0] + 0.1 * p[:, 1] + 2.0 + rng(7).uniform(-5.0, 5.0, len(p))
    d = compile_graph(single(node), store).eval(p)
    truth = plane_height_sdf(p)
    # Trilinear interpolation reproduces a linear field; only float32 rounding remains.
    assert np.abs(d - truth).max() < 1e-4
    # Far below the surface the sign stays negative.
    below = np.array([[0.0, 0.0, -200.0], [300.0, 0.0, -100.0]])
    assert np.all(compile_graph(single(node), store).eval(below) < 0)
