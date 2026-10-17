"""Bitwise-identical evaluation regardless of batch size, order and entry point (ADR 0005)."""

from __future__ import annotations

import numpy as np
import pytest
from graph_testutil import bits, disk_grid, height_field, rng, sphere_field

from shoegen.graph import (
    Box,
    Capsule,
    Extrude2D,
    GraphBuilder,
    HalfSpace,
    Intersect,
    MemoryArrayStore,
    NpyDirectoryStore,
    Offset,
    RoundedBox,
    ShellOutward,
    SmoothUnion,
    Subtract,
    Union,
    compile_graph,
)


@pytest.fixture(scope="module")
def program_and_store():
    store = MemoryArrayStore()
    b = GraphBuilder()
    last = b.add(sphere_field(store), "last")
    floor = b.add(height_field(store), "floor")
    outline = b.add(Extrude2D(field=disk_grid(store), z_min=-20.0, z_max=5.0, offset=3.0), "out")
    cavity = b.add(Offset(child=last, distance=1.0), "cavity")
    env = b.add(Subtract(base=b.add(Intersect(children=[outline, floor])), tool=cavity), "env")
    core = b.add(Offset(child=env, distance=-1.2), "core")
    box = b.add(Box(center=(0.0, 0.0, 0.0), half_size=(6.0, 4.0, 3.0)))
    rb = b.add(RoundedBox(center=(3.0, 0.0, -10.0), half_size=(5.0, 5.0, 2.0), radius=1.0))
    cap = b.add(Capsule(a=(-10.0, 0.0, -12.0), b=(10.0, 2.0, -12.0), radius=1.5))
    blob = b.add(SmoothUnion(children=[box, rb, cap], k=2.0))
    upper = b.add(
        Intersect(
            children=[
                b.add(ShellOutward(child=cavity, thickness=2.0)),
                b.add(HalfSpace(normal=(0.0, 0.0, 1.0), offset=15.0)),
            ]
        )
    )
    root = b.add(Union(children=[b.add(Intersect(children=[core, blob])), upper, env]), "shoe")
    graph = b.build(root)
    return compile_graph(graph, store), graph, store


def _points(n=6000):
    r = rng(31)
    return np.vstack(
        [r.uniform(-30.0, 30.0, (n // 2, 3)), r.uniform(-120.0, 120.0, (n - n // 2, 3))]
    )


def test_batch_size_and_order_do_not_change_bits(program_and_store):
    prog, _, _ = program_and_store
    p = _points()
    ref = prog.eval(p)
    assert np.all(np.isfinite(ref))

    for size in (1, 7, 64, 1000):
        if size == 1:
            got = np.concatenate([prog.eval(p[i : i + 1]) for i in range(600)])
            assert np.array_equal(bits(got), bits(ref[:600]))
            continue
        got = np.concatenate([prog.eval(p[i : i + size]) for i in range(0, len(p), size)])
        assert np.array_equal(bits(got), bits(ref)), size

    perm = rng(32).permutation(len(p))
    got = np.empty_like(ref)
    got[perm] = prog.eval(p[perm])
    assert np.array_equal(bits(got), bits(ref))
    assert np.array_equal(bits(prog.eval(p[::-1])[::-1]), bits(ref))


def test_eval_grid_matches_eval_points(program_and_store):
    prog, _, _ = program_and_store
    origin = (-31.25, -17.5, -22.0)
    h = 0.25
    start = (37, -5, 12)
    shape = (9, 11, 13)
    grid = prog.eval_grid(origin, h, start, shape)
    idx = np.indices(shape).reshape(3, -1).T + np.asarray(start)
    pts = np.asarray(origin) + idx.astype(np.float64) * h
    assert np.array_equal(bits(grid.reshape(-1)), bits(prog.eval(pts)))
    # A sub-block of the grid evaluated separately gives the same bits (shared block faces).
    sub = prog.eval_grid(origin, h, (start[0] + 4, start[1] + 3, start[2]), (5, 8, 13))
    assert np.array_equal(bits(sub), bits(grid[4:, 3:, :]))


def test_recompile_and_mmap_store_give_same_bits(program_and_store, tmp_path):
    prog, graph, store = program_and_store
    disk = NpyDirectoryStore(tmp_path)
    for sha in prog.array_shas:
        assert disk.put(np.asarray(store.get(sha))) == sha
    prog2 = compile_graph(graph, disk)
    p = _points(2000)
    assert np.array_equal(bits(prog2.eval(p)), bits(prog.eval(p)))
