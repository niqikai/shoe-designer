"""Lipschitz <= 1 for analytic nodes and compositions; <= 1.05 for sampled fields (ADR 0001)."""

from __future__ import annotations

import numpy as np
import pytest
from graph_testutil import (
    SPHERE_C,
    SPHERE_R,
    disk_grid,
    height_field,
    plane_height_sdf,
    rng,
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

N_PAIRS = 40000


def _pairs(center, spread, seed, max_step=0.5, min_step=1e-4):
    r = rng(seed)
    p = r.uniform(-spread, spread, (N_PAIRS, 3)) + np.asarray(center, dtype=np.float64)
    direction = r.normal(size=(N_PAIRS, 3))
    direction /= np.linalg.norm(direction, axis=1, keepdims=True)
    step = np.exp(r.uniform(np.log(min_step), np.log(max_step), N_PAIRS))
    q = p + direction * step[:, None]
    return p, q


def _ratio(prog, p, q):
    return np.abs(prog.eval(p) - prog.eval(q)) / np.linalg.norm(p - q, axis=1)


def _composite():
    b = GraphBuilder()
    box = b.add(Box(center=(0.0, 0.0, 0.0), half_size=(12.0, 6.0, 4.0)), "box")
    rbox = b.add(RoundedBox(center=(4.0, 2.0, 3.0), half_size=(6.0, 6.0, 6.0), radius=2.0), "rb")
    cap = b.add(Capsule(a=(-10.0, -5.0, 0.0), b=(10.0, 5.0, 6.0), radius=1.5), "cap")
    hs = b.add(HalfSpace(normal=(0.2, -1.0, 0.5), offset=1.0), "hs")
    u = b.add(Union(children=[box, rbox, cap]), "u")
    i = b.add(Intersect(children=[u, hs]), "i")
    s = b.add(Subtract(base=i, tool=cap), "s")
    su = b.add(SmoothUnion(children=[s, rbox, cap], k=2.5), "su")
    shell = b.add(ShellOutward(child=u, thickness=1.0), "shell")
    grow = b.add(Offset(child=u, distance=0.7), "grow")
    bi = b.add(Intersect(children=[box, hs]), "bi")
    bs = b.add(Subtract(base=bi, tool=cap), "bs")
    shrink = b.add(Offset(child=bs, distance=-0.8), "shrink")
    root = b.add(Union(children=[su, shell, grow, shrink]), "root")
    return b.build(root)


ANALYTIC = {
    "box": Box(center=(0.0, 1.0, 0.0), half_size=(5.0, 3.0, 2.0)),
    "rounded_box": RoundedBox(center=(0.0, 0.0, 1.0), half_size=(5.0, 4.0, 3.0), radius=1.5),
    "capsule": Capsule(a=(-4.0, 0.0, 0.0), b=(4.0, 2.0, 1.0), radius=1.0),
    "halfspace": HalfSpace(normal=(1.0, 1.0, 1.0), offset=0.5),
}


@pytest.mark.parametrize("name", sorted(ANALYTIC))
def test_analytic_leaf_lipschitz(name):
    prog = compile_graph(single(ANALYTIC[name]))
    p, q = _pairs((0.0, 0.0, 0.0), 10.0, seed=sorted(ANALYTIC).index(name))
    assert _ratio(prog, p, q).max() <= 1.0 + 1e-9


def test_composition_lipschitz():
    graph = _composite()
    for nid in graph.order:
        prog = compile_graph(graph, root=nid)
        p, q = _pairs((0.0, 0.0, 0.0), 16.0, seed=11)
        assert _ratio(prog, p, q).max() <= 1.0 + 1e-9, nid


def test_sampled_field_lipschitz_in_band():
    store = MemoryArrayStore()
    prog = compile_graph(single(sphere_field(store)), store)
    p, q = _pairs(SPHERE_C, SPHERE_R + 6.0, seed=21, max_step=0.3)
    f = sphere_sdf(SPHERE_C, SPHERE_R)
    band = (np.abs(f(p)) <= 5.5) & (np.abs(f(q)) <= 5.5)
    assert band.sum() > 2000
    assert _ratio(prog, p[band], q[band]).max() <= 1.05


def test_height_surface_lipschitz_in_band():
    store = MemoryArrayStore()
    prog = compile_graph(single(height_field(store)), store)
    p, q = _pairs((0.0, 0.0, 2.0), 15.0, seed=22, max_step=0.3)
    band = (np.abs(plane_height_sdf(p)) <= 5.5) & (np.abs(plane_height_sdf(q)) <= 5.5)
    assert band.sum() > 2000
    assert _ratio(prog, p[band], q[band]).max() <= 1.05


def test_extrude2d_lipschitz():
    store = MemoryArrayStore()
    prog = compile_graph(single(Extrude2D(field=disk_grid(store), z_min=-3.0, z_max=4.0)), store)
    p, q = _pairs((2.0, 1.0, 0.0), 22.0, seed=23, max_step=0.3)
    assert _ratio(prog, p, q).max() <= 1.05
