"""StrutLattice node (M0.7, ADR 0002): geometry, continuity, density, parameter check, hash."""

from __future__ import annotations

import itertools
import math

import numpy as np
import pytest
from graph_testutil import rng, single

from shoegen.graph import (
    Graph,
    GraphBuilder,
    GraphValidationError,
    StrutLattice,
    Union,
    compile_graph,
    loads_graph,
)
from shoegen.lattice import LatticeParameterError, check_lattice_params
from shoegen.profile import load_profile

ORIGIN = (0.3, -1.7, 2.2)
SIGNS = list(itertools.product((-1.0, 1.0), repeat=3))


def program(cell=10.0, r=0.75, k=None, origin=ORIGIN):
    return compile_graph(single(StrutLattice(cell_mm=cell, radius_mm=r, origin=origin, fillet_k=k)))


def reference(p: np.ndarray, cell, r, k, origin) -> np.ndarray:
    """Independent numpy evaluation: all 27 cells x 8 struts, no culling, plain logsumexp."""
    o = np.asarray(origin)
    half = 0.5 * cell
    base = np.floor((p - o) / cell)
    ds = []
    for off in itertools.product((-1, 0, 1), repeat=3):
        centre = o + (base + np.asarray(off) + 0.5) * cell
        for s in SIGNS:
            seg = half * np.asarray(s)  # centre -> corner
            q = p - centre
            t = np.clip(q @ seg / (seg @ seg), 0.0, 1.0)
            ds.append(np.linalg.norm(q - t[:, None] * seg, axis=1) - r)
    d = np.stack(ds)
    m = d.min(axis=0)
    return m - k * np.log(np.exp(-(d - m) / k).sum(axis=0))


# ---------------------------------------------------------------- geometry


def test_matches_reference_without_culling():
    g = rng(1)
    for cell, r, k in [(10.0, 0.75, 0.1125), (10.0, 2.5, 0.375), (7.0, 1.0, 1.0)]:
        p = program(cell, r, k)
        pts = np.asarray(ORIGIN) + g.uniform(-2 * cell, 2 * cell, (4000, 3))
        np.testing.assert_allclose(p.eval(pts), reference(pts, cell, r, k, ORIGIN), atol=1e-12)


def test_strut_axis_mid_segment_is_minus_r():
    cell, r = 10.0, 0.75
    p = program(cell, r)
    g = rng(2)
    o = np.asarray(ORIGIN)
    pts = []
    for _ in range(300):
        cell_idx = g.integers(-3, 4, 3)
        centre = o + (cell_idx + 0.5) * cell
        s = np.asarray(SIGNS[g.integers(8)])
        t = g.uniform(0.35, 0.65)  # away from the nodes
        pts.append(centre + t * 0.5 * cell * s)
    d = p.eval(np.asarray(pts))
    np.testing.assert_allclose(d, -r, atol=1e-9)


def test_node_centres_are_at_most_minus_r():
    cell, r = 10.0, 1.0
    p = program(cell, r)
    o = np.asarray(ORIGIN)
    g = rng(3)
    idx = g.integers(-3, 4, (50, 3))
    centres = o + (idx + 0.5) * cell
    corners = o + idx * cell
    for pts in (centres, corners):
        d = p.eval(pts)
        assert np.all(d <= -r)
        # eight equal struts meet at every node: -r - k ln 8
        np.testing.assert_allclose(d, -r - 0.15 * r * math.log(8.0), atol=1e-9)


def test_fillet_only_adds_material():
    cell, r = 10.0, 1.0
    g = rng(4)
    pts = np.asarray(ORIGIN) + g.uniform(-15, 15, (5000, 3))
    hard = program(cell, r, 1e-4).eval(pts)  # ~ min over capsules
    soft = program(cell, r, 0.15 * r).eval(pts)
    assert np.all(soft <= hard + 1e-12)
    # and a larger k adds more
    softer = program(cell, r, 0.5 * r).eval(pts)
    assert np.all(softer <= soft + 1e-12)


def test_far_from_struts_is_positive_and_periodic():
    cell, r = 10.0, 1.0
    p = program(cell, r)
    g = rng(5)
    pts = np.asarray(ORIGIN) + g.uniform(0, cell, (2000, 3))
    shifted = pts + np.array([cell, -2 * cell, 3 * cell])
    np.testing.assert_allclose(p.eval(shifted), p.eval(pts), atol=1e-9)
    # the face centre of a cell is the empty-most spot of BCC: positive
    face = np.asarray(ORIGIN) + np.array([0.0, 0.5, 0.5]) * cell
    assert p.eval(face[None])[0] > 0


def test_batch_and_order_independent_bitwise():
    p = program()
    g = rng(6)
    pts = np.asarray(ORIGIN) + g.uniform(-12, 12, (3000, 3))
    base = p.eval(pts)
    perm = g.permutation(len(pts))
    assert np.array_equal(p.eval(pts[perm]), base[perm])
    single_pt = np.array([p.eval(pts[i : i + 1])[0] for i in range(0, 3000, 97)])
    assert np.array_equal(single_pt, base[::97])


def test_lattice_inside_a_union_graph():
    b = GraphBuilder()
    a = b.add(StrutLattice(cell_mm=10.0, radius_mm=1.0), "a")
    c = b.add(StrutLattice(cell_mm=10.0, radius_mm=1.0, origin=(5.0, 5.0, 5.0)), "c")
    g = b.build(b.add(Union(children=[a, c]), "u"))
    pts = rng(7).uniform(-10, 10, (500, 3))
    d = compile_graph(g).eval(pts)
    pa = compile_graph(single(StrutLattice(cell_mm=10.0, radius_mm=1.0))).eval(pts)
    assert np.all(d <= pa)


# ---------------------------------------------------------------- Lipschitz and continuity


@pytest.mark.parametrize("r, k", [(0.75, None), (2.5, None), (1.0, 1.0)])
def test_lipschitz_on_random_close_pairs(r, k):
    cell = 10.0
    p = program(cell, r, k)
    g = rng(8)
    o = np.asarray(ORIGIN)
    n = 60000
    a = o + g.uniform(-1.5 * cell, 1.5 * cell, (n, 3))
    # a third of the pairs sit right at nodes, where the smooth union bends most
    nodes = o + np.round(g.uniform(-1.5, 1.5, (n // 3, 3))) * cell
    nodes[: n // 6] += 0.5 * cell
    a[: n // 3] = nodes + g.normal(0, 0.8 * r, (n // 3, 3))
    scale = g.choice([1e-6, 1e-3, 0.05, 0.5], n)[:, None]
    dirs = g.normal(size=(n, 3))
    b = a + dirs / np.linalg.norm(dirs, axis=1)[:, None] * scale * g.uniform(0.1, 1, (n, 1))
    dist = np.linalg.norm(a - b, axis=1)
    jump = np.abs(p.eval(a) - p.eval(b))
    assert np.all(jump <= (1.0 + 1e-9) * dist + 1e-13)


@pytest.mark.parametrize("r", [0.75, 2.5])
def test_continuous_across_cell_faces_edges_and_corners(r):
    cell = 10.0
    p = program(cell, r)
    g = rng(9)
    o = np.asarray(ORIGIN)
    eps = 1e-7
    worst = 0.0
    for axes in ([0], [1], [2], [0, 1], [0, 2], [1, 2], [0, 1, 2]):
        idx = g.integers(-2, 3, (400, 3))
        pts = o + (idx + g.uniform(0, 1, (400, 3))) * cell
        for ax in axes:
            pts[:, ax] = o[ax] + idx[:, ax] * cell  # exactly on the grid plane
        lo, hi = pts.copy(), pts.copy()
        lo[:, axes] -= eps
        hi[:, axes] += eps
        # make sure the two sides really are in different cells
        cl = np.floor((lo - o) / cell)
        ch = np.floor((hi - o) / cell)
        assert np.all(cl[:, axes] != ch[:, axes])
        worst = max(worst, np.abs(p.eval(lo) - p.eval(hi)).max())
    assert worst <= 1e-6


# ---------------------------------------------------------------- relative density
#
# BCC strut lattice, cell edge a, strut radius r. Each cell holds 8 struts of length
# L = sqrt(3) a / 2 (centre -> corner) and "2 nodes" worth of junction (one at the centre,
# one at the 8 corners shared between cells; both have 8 struts arriving along the 8 body
# diagonals). Cutting each strut at its midpoint gives each junction 8 half-struts of length
# L/2, so
#
#     V_cell = 2 * V_node(S = L/2),   V_node(S) = 8 pi r^2 S - c r^3,
#
# where c r^3 is the volume the 8 cylinders (with the ball of radius r at the node, the
# capsule end) share. c was integrated numerically for the idealised node (union of 8
# half-cylinders along (+-1,+-1,+-1)/sqrt 3 and the ball |p| <= r, voxel integration with
# h = r/50, S = 4r and 6r, Richardson-extrapolated): c = 20.0 +- 0.2. Hence
#
#     rho = 4 sqrt(3) pi (r/a)^2 - 2 c (r/a)^3.
#
# The first term is the usual uncorrected 8 pi r^2 L / a^3 (e.g. Ashby, "The properties of
# foams and lattices", Phil. Trans. R. Soc. A 364 (2006), for strut-dominated cells); the
# second term removes the double-counted node volume. The fillet (k = 0.15 r) adds a few
# percent of material on top of this formula.

NODE_OVERLAP_C = 20.0


def analytic_density(r: float, a: float) -> float:
    x = r / a
    return 4.0 * math.sqrt(3.0) * math.pi * x**2 - 2.0 * NODE_OVERLAP_C * x**3


def sampled_density(r: float, h: float, cell: float = 10.0) -> float:
    """Fraction of voxel centres (one full period, aligned with the lattice) inside."""
    p = program(cell, r, origin=(0.0, 0.0, 0.0))
    n = round(cell / h)
    c = (np.arange(n) + 0.5) * h
    yz = np.stack(np.meshgrid(c, c, indexing="ij"), -1).reshape(-1, 2)
    inside = 0
    for x in c:
        pts = np.column_stack([np.full(len(yz), x), yz])
        inside += int((p.eval(pts) < 0).sum())
    return inside / n**3


def test_relative_density_monotone_in_r_and_matches_bcc_formula():
    radii = [0.75, 1.0, 1.5, 2.0]
    dens = [sampled_density(r, 0.25) for r in radii]
    assert all(b > a for a, b in itertools.pairwise(dens))
    # r = 0.75 has only 6 voxels across a strut: the 0.25 mm count is too coarse to hold
    # 10 % (it is checked at 0.1 mm below); the others are within 10 % at 0.25 mm.
    for r, d in zip(radii[1:], dens[1:], strict=True):
        ref = analytic_density(r, 10.0)
        assert abs(d - ref) <= 0.10 * ref, (r, d, ref)


def test_relative_density_fine_voxels_thin_struts():
    d = sampled_density(0.75, 0.1)
    ref = analytic_density(0.75, 10.0)
    assert abs(d - ref) <= 0.05 * ref, (d, ref)


# ---------------------------------------------------------------- build-time parameter check

PBF = "pbf-tpu-generic@0"  # min_strut_diameter_mm = 1.5


def lattice_graph(r, cell=10.0):
    return single(StrutLattice(cell_mm=cell, radius_mm=r))


def test_parameter_check_passes_at_the_limits():
    profile = load_profile(PBF)
    check_lattice_params(lattice_graph(0.75), profile, 0.375)  # 2r == min, voxel == 2r/4
    check_lattice_params(lattice_graph(1.0), PBF, 0.25)  # profile id accepted


def test_parameter_check_rejects_thin_strut_with_exit_3():
    with pytest.raises(LatticeParameterError) as e:
        check_lattice_params(lattice_graph(0.7), load_profile(PBF), 0.05)
    assert e.value.exit_code == 3
    assert isinstance(e.value, GraphValidationError)
    assert "min_strut_diameter_mm" in str(e.value) and "'n'" in str(e.value)


def test_parameter_check_rejects_coarse_voxel_with_exit_3():
    with pytest.raises(LatticeParameterError) as e:
        check_lattice_params(lattice_graph(1.0), load_profile(PBF), 0.51)  # > 2/4
    assert e.value.exit_code == 3
    assert "voxel" in str(e.value)


def test_parameter_check_covers_every_lattice_node():
    b = GraphBuilder()
    good = b.add(StrutLattice(cell_mm=10.0, radius_mm=1.0), "good")
    bad = b.add(StrutLattice(cell_mm=10.0, radius_mm=0.5), "bad")
    g = b.build(b.add(Union(children=[good, bad]), "u"))
    with pytest.raises(LatticeParameterError, match="'bad'") as e:
        check_lattice_params(g, PBF, 0.125)
    assert "'good'" not in str(e.value)


def test_parameter_check_ignores_graphs_without_lattice_and_rejects_bad_voxel():
    from shoegen.graph import Box

    g = single(Box(center=(0, 0, 0), half_size=(1, 1, 1)))
    check_lattice_params(g, PBF, 100.0)
    with pytest.raises(LatticeParameterError):
        check_lattice_params(g, PBF, 0.0)


# ---------------------------------------------------------------- graph hash


def test_fillet_none_equals_explicit_default_in_hash():
    r = 0.8
    a = single(StrutLattice(cell_mm=10.0, radius_mm=r))
    b = single(StrutLattice(cell_mm=10.0, radius_mm=r, fillet_k=0.15 * r))
    assert a.nodes["n"].fillet_k == 0.15 * r
    assert a.graph_hash() == b.graph_hash()
    assert a.canonical_json() == b.canonical_json()
    c = single(StrutLattice(cell_mm=10.0, radius_mm=r, fillet_k=0.2 * r))
    assert c.graph_hash() != a.graph_hash()
    # the JSON carries the resolved value and round-trips to the same hash
    assert loads_graph(a.canonical_json()).graph_hash() == a.graph_hash()
    # same program either way
    pts = rng(10).uniform(-10, 10, (200, 3))
    assert np.array_equal(compile_graph(a).eval(pts), compile_graph(b).eval(pts))


def test_graph_json_without_fillet_k_resolves_it():
    text = (
        '{"graph_version": 1, "root": "n", "nodes": {"n": '
        '{"type": "StrutLattice", "cell_mm": 10.0, "radius_mm": 1.0}}}'
    )
    g = loads_graph(text)
    assert g.nodes["n"].fillet_k == 0.15
    assert isinstance(g, Graph)
