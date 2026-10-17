"""Metric attribute, build-time checks (exit code 3) and sampled-data integrity."""

from __future__ import annotations

import numpy as np
import pytest
from graph_testutil import box_truth, rng, single, sphere_field

from shoegen.graph import (
    BOUND,
    EXACT,
    Box,
    Capsule,
    Graph,
    GraphBuilder,
    GraphValidationError,
    HalfSpace,
    Intersect,
    MemoryArrayStore,
    NodeMetric,
    NpyDirectoryStore,
    Offset,
    SampledDataError,
    ShellOutward,
    SmoothUnion,
    StrutLattice,
    Subtract,
    Union,
    compile_graph,
    graph_from_obj,
    loads_graph,
)

BOX = Box(center=(0.0, 0.0, 0.0), half_size=(10.0, 10.0, 10.0))
CAP = Capsule(a=(-5.0, 0.0, 0.0), b=(5.0, 0.0, 0.0), radius=2.0)


def _raises_exit3(fn):
    with pytest.raises(GraphValidationError) as info:
        fn()
    assert info.value.exit_code == 3
    return info.value


# ---------------------------------------------------------------- derivation


def test_metric_derivation_table():
    b = GraphBuilder()
    box, cap = b.add(BOX, "box"), b.add(CAP, "cap")
    names = {
        "u": Union(children=[box, cap]),
        "i": Intersect(children=[box, cap]),
        "s": Subtract(base=box, tool=cap),
        "su": SmoothUnion(children=[box, cap], k=1.0),
        "grow": Offset(child=box, distance=1.0),
        "shrink": Offset(child=box, distance=-1.0),
        "zero": Offset(child=box, distance=0.0),
        "shell": ShellOutward(child=box, thickness=1.0),
        "lat": StrutLattice(cell_mm=10.0, radius_mm=1.0),
    }
    for nid, node in names.items():
        b.add(node, nid)
    b.add(Subtract(base="s", tool="cap"), "s_of_s")  # base inside exact, tool outside exact
    b.add(Subtract(base="u", tool="cap"), "s_of_u")  # base inside bound
    b.add(Subtract(base="box", tool="i"), "s_tool_i")  # tool outside bound
    b.add(Union(children=["grow", "cap"]), "u_grow")
    b.add(ShellOutward(child="u", thickness=1.0), "shell_u")  # u: outside exact only
    b.add(Union(children=list(names) + ["s_of_s", "s_of_u", "s_tool_i", "u_grow", "shell_u"]), "r")
    g = b.build("r")
    expect = {
        "box": (EXACT, EXACT),
        "cap": (EXACT, EXACT),
        "u": (BOUND, EXACT),
        "i": (EXACT, BOUND),
        "s": (EXACT, BOUND),
        "su": (BOUND, BOUND),
        "grow": (BOUND, EXACT),
        "shrink": (EXACT, BOUND),
        "zero": (EXACT, EXACT),
        "shell": (BOUND, EXACT),
        "lat": (BOUND, BOUND),
        "s_of_s": (EXACT, BOUND),
        "s_of_u": (BOUND, BOUND),
        "s_tool_i": (BOUND, BOUND),
        "u_grow": (BOUND, EXACT),
        "shell_u": (BOUND, BOUND),
    }
    for nid, (ins, out) in expect.items():
        assert g.metric(nid) == NodeMetric(ins, out), nid


@pytest.mark.parametrize(
    ("child", "distance"),
    [
        (SmoothUnion(children=["box", "cap"], k=1.0), 1.0),
        (SmoothUnion(children=["box", "cap"], k=1.0), -1.0),
        (Intersect(children=["box", "cap"]), 0.5),  # outside of an intersection is a bound
        (Union(children=["box", "cap"]), -0.5),  # inside of a union is a bound
        (StrutLattice(cell_mm=10.0, radius_mm=1.0), 0.2),
    ],
)
def test_offset_of_bound_side_is_rejected(child, distance):
    b = GraphBuilder()
    b.add(BOX, "box")
    b.add(CAP, "cap")
    c = b.add(child, "c")
    off = b.add(Offset(child=c, distance=distance), "off")
    err = _raises_exit3(lambda: b.build(off, prune=True))
    assert "Offset" in str(err)


def test_shell_of_bound_outside_is_rejected():
    b = GraphBuilder()
    i = b.add(Intersect(children=[b.add(BOX), b.add(CAP)]))
    _raises_exit3(lambda: b.build(b.add(ShellOutward(child=i, thickness=1.0))))


def test_rejected_from_json_too():
    text = (
        '{"graph_version":1,"root":"o","nodes":{'
        '"a":{"type":"Box","center":[0,0,0],"half_size":[1,1,1]},'
        '"b":{"type":"Box","center":[1,0,0],"half_size":[1,1,1]},'
        '"s":{"type":"SmoothUnion","children":["a","b"],"k":0.5},'
        '"o":{"type":"Offset","child":"s","distance":0.3}}}'
    )
    _raises_exit3(lambda: loads_graph(text))


# ---------------------------------------------------------------- inward offset = truth


def test_inward_offset_of_intersect_matches_truth():
    # box [-10,10]^3 cut by z <= 5, shrunk by 1 mm == box [-9,9]^2 x [-9,4].
    b = GraphBuilder()
    box = b.add(BOX)
    hs = b.add(HalfSpace(normal=(0.0, 0.0, 1.0), offset=5.0))
    root = b.add(Offset(child=b.add(Intersect(children=[box, hs])), distance=-1.0))
    g = b.build(root)
    assert g.metric(root).inside == EXACT
    p = rng(41).uniform(-14.0, 14.0, (40000, 3))
    d = compile_graph(g).eval(p)
    truth = box_truth(p, (0.0, 0.0, -2.5), (9.0, 9.0, 6.5))
    inside = truth < 0
    assert inside.sum() > 5000
    np.testing.assert_allclose(d[inside], truth[inside], rtol=0, atol=1e-12)
    assert np.all(d[~inside] >= -1e-12)
    assert np.all(d[~inside] <= truth[~inside] + 1e-12)  # outside: a bound


def test_inward_offset_of_subtract_matches_truth():
    # box minus a slab that removes z >= 5, shrunk by 1 mm.
    b = GraphBuilder()
    box = b.add(BOX)
    slab = b.add(Box(center=(0.0, 0.0, 10.0), half_size=(20.0, 20.0, 5.0)))
    root = b.add(Offset(child=b.add(Subtract(base=box, tool=slab)), distance=-1.0))
    g = b.build(root)
    p = rng(42).uniform(-14.0, 14.0, (40000, 3))
    d = compile_graph(g).eval(p)
    truth = box_truth(p, (0.0, 0.0, -2.5), (9.0, 9.0, 6.5))
    inside = truth < 0
    np.testing.assert_allclose(d[inside], truth[inside], rtol=0, atol=1e-12)


# ---------------------------------------------------------------- sampled-field band limit


def test_offset_on_sampled_field_limited_by_band():
    store = MemoryArrayStore()
    last = sphere_field(store)  # band 6 mm -> limit 5 mm

    def build(*ops):
        b = GraphBuilder()
        cur = b.add(last, "last")
        for op in ops:
            cur = b.add(op(cur))
        return b.build(cur)

    build(lambda c: Offset(child=c, distance=5.0))
    build(lambda c: Offset(child=c, distance=-5.0))
    _raises_exit3(lambda: build(lambda c: Offset(child=c, distance=5.5)))
    _raises_exit3(lambda: build(lambda c: Offset(child=c, distance=-5.5)))
    # Accumulated along the path: cavity (+1) then a 4.5 mm shell = 5.5 mm.
    _raises_exit3(
        lambda: build(
            lambda c: Offset(child=c, distance=1.0),
            lambda c: ShellOutward(child=c, thickness=4.5),
        )
    )
    g = build(
        lambda c: Offset(child=c, distance=1.0), lambda c: ShellOutward(child=c, thickness=2.0)
    )
    assert g.analysis.sampled_reach == {"last": 3.0}


def test_band_check_takes_max_over_shared_paths():
    store = MemoryArrayStore()
    b = GraphBuilder()
    last = b.add(sphere_field(store), "last")
    cav = b.add(Offset(child=last, distance=1.0), "cav")
    thin = b.add(ShellOutward(child=cav, thickness=1.0))
    thick = b.add(ShellOutward(child=cav, thickness=4.5))
    _raises_exit3(lambda: b.build(b.add(Union(children=[thin, thick]))))


# ---------------------------------------------------------------- structure


def test_unknown_reference_cycle_unreachable_and_bad_documents():
    box = {"type": "Box", "center": [0, 0, 0], "half_size": [1, 1, 1]}
    unknown = {"root": "u", "nodes": {"u": {"type": "Union", "children": ["missing"]}}}
    assert "unknown" in str(_raises_exit3(lambda: graph_from_obj(unknown)))
    cycle = {
        "root": "a",
        "nodes": {
            "a": {"type": "Union", "children": ["b", "x"]},
            "b": {"type": "Offset", "child": "a", "distance": 1.0},
            "x": box,
        },
    }
    assert "cycle" in str(_raises_exit3(lambda: graph_from_obj(cycle)))
    self_loop = {"root": "a", "nodes": {"a": {"type": "Offset", "child": "a", "distance": 1.0}}}
    _raises_exit3(lambda: graph_from_obj(self_loop))
    unreachable = {"root": "x", "nodes": {"x": box, "y": box}}
    assert "reachable" in str(_raises_exit3(lambda: graph_from_obj(unreachable)))
    _raises_exit3(lambda: graph_from_obj({"root": "nope", "nodes": {"x": box}}))
    _raises_exit3(lambda: graph_from_obj({"root": "x", "nodes": {"x": {**box, "extra": 1}}}))
    _raises_exit3(lambda: graph_from_obj({"root": "x", "nodes": {"x": {"type": "Sphere"}}}))
    _raises_exit3(lambda: graph_from_obj({"root": "x", "nodes": {"x": box}, "graph_version": 2}))
    _raises_exit3(lambda: loads_graph("{not json"))
    bad_radius = {"type": "RoundedBox", "center": [0, 0, 0], "half_size": [1, 1, 1], "radius": 2}
    _raises_exit3(lambda: graph_from_obj({"root": "x", "nodes": {"x": bad_radius}}))
    nan_box = {"type": "Box", "center": [float("nan"), 0, 0], "half_size": [1, 1, 1]}
    _raises_exit3(lambda: graph_from_obj({"root": "x", "nodes": {"x": nan_box}}))


def test_shared_node_compiles_once():
    b = GraphBuilder()
    box = b.add(BOX, "box")
    g = b.build(b.add(Union(children=[box, b.add(Offset(child=box, distance=-1.0))])))
    prog = compile_graph(g)
    assert prog.node_ids.count("box") == 1
    assert prog.code.shape[0] == 3


def test_strut_lattice_is_a_stub_until_m07():
    node = StrutLattice(cell_mm=10.0, radius_mm=1.0)
    g = single(node)
    assert g.metric("n") == NodeMetric(BOUND, BOUND)
    with pytest.raises(NotImplementedError):
        compile_graph(g)


# ---------------------------------------------------------------- sampled data integrity


def test_sampled_array_hash_is_verified(tmp_path):
    store = MemoryArrayStore()
    last = sphere_field(store)
    g = Graph(root="last", nodes={"last": last})
    compile_graph(g, store)

    # Missing store / missing array.
    _raises_exit3(lambda: compile_graph(g))
    _raises_exit3(lambda: compile_graph(g, MemoryArrayStore()))

    # Content does not match the reference.
    bad = MemoryArrayStore()
    for sha in last.grid.array_shas():
        arr = np.array(store.get(sha))
        if sha == last.grid.coarse_sha256:
            arr.reshape(-1)[0] += np.float32(1.0)
        bad.put_unchecked(sha, arr)
    err = _raises_exit3(lambda: compile_graph(g, bad))
    assert isinstance(err, SampledDataError)

    # Same check for the mmap-backed directory store, after tampering with a file.
    disk = NpyDirectoryStore(tmp_path)
    for sha in last.grid.array_shas():
        disk.put(np.asarray(store.get(sha)))
    compile_graph(g, disk)
    path = disk.path_for(last.grid.coarse_sha256)
    arr = np.load(path)
    arr.reshape(-1)[-1] -= np.float32(0.5)
    np.save(path, arr)
    assert isinstance(_raises_exit3(lambda: compile_graph(g, disk)), SampledDataError)
