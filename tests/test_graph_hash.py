"""Canonical JSON and graph_hash (ADR 0001)."""

from __future__ import annotations

import copy
import json
import math

import numpy as np
from graph_testutil import disk_grid, height_field, sphere_field

from shoegen.graph import (
    GRAPH_VERSION,
    Box,
    Capsule,
    Extrude2D,
    GraphBuilder,
    HalfSpace,
    Intersect,
    MemoryArrayStore,
    Offset,
    RoundedBox,
    SampledField,
    ShellOutward,
    SmoothUnion,
    StrutLattice,
    Subtract,
    Union,
    dump_graph,
    graph_from_obj,
    load_graph,
    loads_graph,
)


def _graph(store=None):
    store = store or MemoryArrayStore()
    b = GraphBuilder()
    last = b.add(sphere_field(store), "last")
    floor = b.add(height_field(store), "floor")
    outline = b.add(Extrude2D(field=disk_grid(store), z_min=-20.0, z_max=5.0, offset=3.0), "out")
    box = b.add(Box(center=(0.1, 0.2, 0.3), half_size=(6.0, 4.0, 3.0)), "box")
    rb = b.add(RoundedBox(center=(3.0, 0.0, -10.0), half_size=(5.0, 5.0, 2.0), radius=1.0), "rb")
    cap = b.add(Capsule(a=(-10.0, 0.0, -12.0), b=(10.0, 2.0, -12.0), radius=1.5), "cap")
    hs = b.add(HalfSpace(normal=(0.0, 0.0, 1.0), offset=15.0), "hs")
    lat = b.add(StrutLattice(cell_mm=10.0, radius_mm=1.0, origin=(0.5, 0.5, 0.5)), "lat")
    cav = b.add(Offset(child=last, distance=1.0), "cav")
    env = b.add(Subtract(base=b.add(Intersect(children=[outline, floor]), "ef"), tool=cav), "env")
    shell = b.add(ShellOutward(child=cav, thickness=2.0), "shell")
    su = b.add(SmoothUnion(children=[box, rb, cap], k=2.0), "su")
    root = b.add(Union(children=[env, shell, su, hs, lat]), "root")
    return b.build(root), store


def test_graph_version_is_one():
    g, _ = _graph()
    assert GRAPH_VERSION == 1
    assert json.loads(g.canonical_json())["graph_version"] == 1


def test_round_trip_keeps_hash(tmp_path):
    g, _ = _graph()
    h = g.graph_hash()
    assert len(h) == 64
    assert loads_graph(g.canonical_json()).graph_hash() == h
    assert loads_graph(g.canonical_json(indent=2)).graph_hash() == h
    path = tmp_path / "graph.json"
    dump_graph(g, path)
    g2 = load_graph(path)
    assert g2 == g
    assert g2.graph_hash() == h
    # Re-serialising is a fixed point.
    assert g2.canonical_json() == g.canonical_json()


def test_canonical_json_is_sorted_and_compact():
    g, _ = _graph()
    text = g.canonical_json()
    assert " " not in text and "\n" not in text
    obj = json.loads(text)
    assert list(obj) == sorted(obj)
    assert list(obj["nodes"]) == sorted(obj["nodes"])


def test_insertion_order_does_not_change_hash():
    g, _ = _graph()
    obj = g.to_canonical_obj()
    reversed_nodes = dict(reversed(list(obj["nodes"].items())))
    g2 = graph_from_obj({"root": obj["root"], "nodes": reversed_nodes, "graph_version": 1})
    assert g2.graph_hash() == g.graph_hash()


def test_floats_round_trip_exactly():
    awkward = [0.1 + 0.2, 1.0 / 3.0, 1e-300, 123456789.123456789, math.nextafter(2.0, 3.0)]
    for v in awkward:
        b = GraphBuilder()
        r = b.add(Box(center=(v, -v, 0.0), half_size=(1.0, 1.0, 1.0)), "b")
        g = b.build(r)
        g2 = loads_graph(g.canonical_json())
        assert g2.nodes["b"].center[0] == v
        assert g2.graph_hash() == g.graph_hash()


def _leaf_paths(obj, prefix=()):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _leaf_paths(v, (*prefix, k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _leaf_paths(v, (*prefix, i))
    else:
        yield prefix, obj


def _set(obj, path, value):
    for k in path[:-1]:
        obj = obj[k]
    obj[path[-1]] = value


def _perturb(value):
    if isinstance(value, bool):
        return not value
    if isinstance(value, float):
        return math.nextafter(value, math.inf)
    if isinstance(value, int):
        return value + 1
    if isinstance(value, str) and len(value) == 64:
        return ("0" if value[0] != "0" else "1") + value[1:]
    return None


def test_changing_any_parameter_changes_hash():
    g, _ = _graph()
    base = json.loads(g.canonical_json())
    h0 = g.graph_hash()
    checked = 0
    for path, value in _leaf_paths(base["nodes"]):
        if path[-1] == "type" or "children" in path or path[-1] in {"child", "base", "tool"}:
            continue
        new = _perturb(value)
        if new is None:
            continue
        obj = copy.deepcopy(base)
        _set(obj["nodes"], path, new)
        assert graph_from_obj(obj).graph_hash() != h0, path
        checked += 1
    assert checked > 60
    # Structure participates too: swapping children order, renaming the root.
    obj = copy.deepcopy(base)
    obj["nodes"]["root"]["children"].reverse()
    assert graph_from_obj(obj).graph_hash() != h0


def test_array_sha_participates_in_hash():
    store = MemoryArrayStore()
    node = sphere_field(store)
    coarse = np.array(store.get(node.grid.coarse_sha256))
    coarse[0, 0, 0] += np.float32(0.001)
    new_sha = store.put(coarse)
    assert new_sha != node.grid.coarse_sha256
    node2 = SampledField(
        source_sha256=node.source_sha256,
        sampling=node.sampling,
        grid=node.grid.model_copy(update={"coarse_sha256": new_sha}),
    )
    b1, b2 = GraphBuilder(), GraphBuilder()
    g1 = b1.build(b1.add(node, "last"))
    g2 = b2.build(b2.add(node2, "last"))
    assert g1.graph_hash() != g2.graph_hash()
    # The source file hash and sampling parameters count as well.
    node3 = node.model_copy(update={"sampling": {**node.sampling, "r": 20.5}})
    b3 = GraphBuilder()
    assert b3.build(b3.add(node3, "last")).graph_hash() != g1.graph_hash()
