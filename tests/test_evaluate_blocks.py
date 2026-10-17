"""Block-sparse evaluation: lattice, skip rule, bitwise determinism (ADR 0005, plan M0.5)."""

from __future__ import annotations

import hashlib
import math

import numpy as np
import psutil
import pytest
from graph_testutil import rng, sphere_field

from shoegen.evaluate import (
    BLOCK_CELLS,
    BLOCK_SAMPLES,
    CROSSING,
    INSIDE,
    OUTSIDE,
    SKIP_SAFETY_K,
    half_diagonal,
    iter_block_samples,
    lattice_points,
    plan_blocks,
    skip_threshold,
)
from shoegen.graph import (
    Box,
    Capsule,
    GraphBuilder,
    GraphValidationError,
    MemoryArrayStore,
    NpyDirectoryStore,
    Offset,
    SmoothUnion,
    Subtract,
    Union,
    compile_graph,
)
from shoegen.resources import RssWatchdog

H = 0.25
LOCAL = np.indices((BLOCK_SAMPLES,) * 3, dtype=np.int64).reshape(3, -1).T  # C order


def u32(a: np.ndarray) -> np.ndarray:
    return np.ascontiguousarray(a, dtype=np.float32).view(np.uint32)


# ---------------------------------------------------------------- test graphs


def sphere_case():
    b = GraphBuilder()
    c = (0.3, -0.7, 1.1)
    root = b.add(Capsule(a=c, b=c, radius=30.0), "sphere")
    return b.build(root), None, (-40.0, -40.0, -40.0), (40.0, 40.0, 40.0)


def capsules_case():
    b = GraphBuilder()
    c1 = b.add(Capsule(a=(-25.0, -12.0, -3.0), b=(22.0, 9.0, 4.0), radius=4.0))
    c2 = b.add(Capsule(a=(-20.0, 14.0, 2.0), b=(18.0, -15.0, -2.5), radius=3.0))
    c3 = b.add(Capsule(a=(5.0, -3.0, -8.0), b=(5.0, -3.0, 8.0), radius=2.5))
    c4 = b.add(Capsule(a=(-8.0, 6.0, 0.0), b=(12.0, 6.0, 0.0), radius=2.0))
    blob = b.add(SmoothUnion(children=[c3, c4], k=1.5))
    root = b.add(Union(children=[c1, c2, blob]), "capsules")
    return b.build(root), None, (-44.0, -36.0, -28.0), (44.0, 36.0, 28.0)


def box_sampled_case(store=None):
    store = MemoryArrayStore() if store is None else store
    b = GraphBuilder()
    box = b.add(Box(center=(0.0, 0.0, 0.0), half_size=(28.0, 24.0, 12.0)), "box")
    last = b.add(sphere_field(store), "last")
    root = b.add(Subtract(base=box, tool=last), "box_minus_last")
    return b.build(root), store, (-36.0, -36.0, -36.0), (36.0, 36.0, 36.0)


CASES = {"sphere": sphere_case, "capsules": capsules_case, "box_sampled": box_sampled_case}


def _summary_line(summary) -> str:
    s = summary
    return (
        f"blocks total={s.total_blocks} sampled={s.sampled_blocks} "
        f"skipped_inside={s.skipped_inside} skipped_outside={s.skipped_outside} "
        f"grid={s.grid_shape} h={s.voxel_mm} workers={s.workers} "
        f"plan={s.plan_seconds:.3f}s sample={s.sample_seconds:.3f}s (eval/wait {s.wait_seconds:.3f}s)"
        + (f" peak_rss={s.peak_rss_bytes / 2**20:.0f}MiB" if s.peak_rss_bytes else "")
    )


# ---------------------------------------------------------------- lattice and layout


def test_lattice_origin_layout_and_numbering():
    graph, store, rmin, rmax = capsules_case()
    rmin = (-30.1, -19.95, -12.3)
    rmax = (30.0, 20.2, 11.9)  # 241 x 161 x 97 cells -> 4 x 3 x 2 blocks
    plan = plan_blocks(graph, store, rmin, rmax, H)
    g0 = np.asarray(plan.index_min)
    assert np.all(g0 * H <= np.asarray(rmin)) and np.all((g0 + 1) * H > np.asarray(rmin))
    assert np.all(np.asarray(plan.index_max) * H >= np.asarray(rmax))
    # Not one block too many.
    assert np.all((np.asarray(plan.index_max) - BLOCK_CELLS) * H < np.asarray(rmax))
    assert plan.grid_shape == (4, 3, 2)
    assert plan.status.shape == plan.grid_shape == plan.centre_values.shape
    assert plan.n_crossing + plan.n_inside + plan.n_outside == plan.total_blocks

    blocks = list(plan.blocks())
    assert blocks == sorted(blocks)
    assert [plan.block_number(b) for b in blocks] == list(range(plan.total_blocks))
    assert plan.block_start((1, 0, 2)) == (g0[0] + 64, g0[1], g0[2] + 128)
    lo, hi = plan.block_bounds_mm((1, 0, 2))
    assert np.array_equal(lo, np.asarray(plan.block_start((1, 0, 2))) * H)
    assert np.array_equal(hi - lo, np.full(3, 64 * H))

    assert SKIP_SAFETY_K == 1.5
    assert skip_threshold(H) == 1.5 * (math.sqrt(3.0) * 32 * H + H)
    assert half_diagonal(H) == math.sqrt(3.0) * 32 * H


def test_region_validation():
    graph, store, _, _ = sphere_case()
    with pytest.raises(ValueError):
        plan_blocks(graph, store, (0, 0, 0), (0, 1, 1), H)
    with pytest.raises(ValueError):
        plan_blocks(graph, store, (0, 0, 0), (1, 1, 1), 0.0)


# ---------------------------------------------------------------- values and skipping


@pytest.mark.parametrize("case", sorted(CASES))
def test_sampled_blocks_bitwise_and_skipped_blocks_uniform(case, eval_report):
    graph, store, rmin, rmax = CASES[case]()
    plan = plan_blocks(graph, store, rmin, rmax, H)
    ref = plan_blocks(graph, store, rmin, rmax, H, skip=False)
    assert ref.n_crossing == ref.total_blocks == plan.total_blocks
    assert ref.index_min == plan.index_min and ref.grid_shape == plan.grid_shape
    assert np.array_equal(ref.centre_values.view(np.uint64), plan.centre_values.view(np.uint64))
    assert plan.n_outside > 0, "test region should contain skippable blocks"
    if case == "sphere":
        assert plan.n_inside > 0

    prog = compile_graph(graph, store)
    stream = iter_block_samples(plan, workers=1)
    full_stream = iter_block_samples(ref, workers=1)
    min_abs_skipped = math.inf
    for block, full in full_stream:
        assert full.dtype == np.float32 and full.shape == (BLOCK_SAMPLES,) * 3
        assert full.flags.c_contiguous
        start = np.asarray(plan.block_start(block), dtype=np.int64)
        # Centre value of the plan is sample [32, 32, 32].
        assert u32(full[32, 32, 32]) == u32(np.float32(plan.centre_values[block]))
        st = plan.status[block]
        if st == CROSSING:
            got_block, vals = next(stream)
            assert got_block == block
            assert np.array_equal(u32(vals), u32(full)), "skip and no-skip differ"
            # Brute force through a different entry point: points from global indices.
            brute = prog.eval(lattice_points(start + LOCAL, H)).astype(np.float32)
            assert np.array_equal(u32(vals).ravel(), u32(brute)), "block != brute force"
            grid = prog.eval_grid((0.0, 0.0, 0.0), H, tuple(start), (BLOCK_SAMPLES,) * 3)
            assert np.array_equal(u32(vals), u32(grid.astype(np.float32)))
        else:
            inside = full < 0
            if st == INSIDE:
                assert inside.all(), f"skipped-inside block {block} has outside samples"
            else:
                assert st == OUTSIDE
                assert not inside.any(), f"skipped-outside block {block} has inside samples"
            min_abs_skipped = min(min_abs_skipped, float(np.min(np.abs(full))))
    assert next(stream, None) is None
    assert stream.summary.complete and full_stream.summary.complete
    assert stream.summary.blocks_yielded == plan.n_crossing
    assert stream.summary.boundary_inside_blocks == 0
    eval_report(
        f"{case}: {_summary_line(stream.summary)}; no-skip sample="
        f"{full_stream.summary.wait_seconds:.3f}s; min |d| in skipped blocks="
        f"{min_abs_skipped:.3f} mm"
    )


# ---------------------------------------------------------------- parallel determinism


def _stream_digest(stream) -> tuple[str, list]:
    h = hashlib.sha256()
    order = []
    for block, vals in stream:
        order.append(block)
        h.update(np.asarray(block, dtype=np.int64).tobytes())
        h.update(np.ascontiguousarray(vals).tobytes())
    return h.hexdigest(), order


@pytest.mark.parametrize("case", ["capsules", "box_sampled_npy"])
def test_workers_1_and_4_bitwise_identical(case, tmp_path, eval_report):
    if case == "box_sampled_npy":
        graph, store, rmin, rmax = box_sampled_case(NpyDirectoryStore(tmp_path / "arrays"))
    else:
        graph, store, rmin, rmax = capsules_case()
    plan = plan_blocks(graph, store, rmin, rmax, H)
    s1 = iter_block_samples(plan, workers=1)
    d1, o1 = _stream_digest(s1)
    with RssWatchdog(limit_bytes=6 * 2**30, interval_s=0.1) as wd:
        s4 = iter_block_samples(plan, workers=4, watchdog=wd)
        d4, o4 = _stream_digest(s4)
    assert o1 == o4 == plan.sampled_blocks()
    assert d1 == d4
    assert s4.summary.complete and s4.summary.peak_rss_bytes
    assert s4.summary.max_in_flight == 8
    eval_report(f"{case}: sha256={d1[:16]} {_summary_line(s1.summary)}")
    eval_report(f"{case}: sha256={d4[:16]} {_summary_line(s4.summary)}")


def _pool_workers() -> list[psutil.Process]:
    out = []
    for p in psutil.Process().children(recursive=True):
        try:
            if "spawn_main" in " ".join(p.cmdline()):
                out.append(p)
        except psutil.Error:
            continue
    return out


def test_early_close_shuts_pool_down():
    graph, store, rmin, rmax = capsules_case()
    plan = plan_blocks(graph, store, rmin, rmax, H)
    assert plan.n_crossing >= 3
    with iter_block_samples(plan, workers=2, max_in_flight=1) as stream:
        first = next(stream)
        assert first[0] == plan.sampled_blocks()[0]
        assert 1 <= len(_pool_workers()) <= 2  # the pool starts workers on demand
    assert not stream.summary.complete
    assert stream.summary.blocks_yielded == 1
    _gone, alive = psutil.wait_procs(_pool_workers(), timeout=5)
    assert not alive


# ---------------------------------------------------------------- shared global lattice


def test_different_regions_share_global_samples(eval_report):
    graph, store, _, _ = capsules_case()
    prog = compile_graph(graph, store)
    regions = {
        "A": ((-44.0, -36.0, -28.0), (44.0, 36.0, 28.0)),
        "B": ((-21.3, -13.9, -9.1), (33.7, 24.2, 15.5)),
    }
    plans = {k: plan_blocks(graph, store, lo, hi, H) for k, (lo, hi) in regions.items()}
    assert plans["A"].index_min != plans["B"].index_min
    lo = np.maximum(plans["A"].index_min, plans["B"].index_min)
    hi = np.minimum(plans["A"].index_max, plans["B"].index_max)
    r = rng(5)
    probes = [r.integers(lo, hi + 1, size=(4000, 3))]
    # Probes on the block faces of both layouts (shared samples between neighbours).
    for p in plans.values():
        for axis in range(3):
            q = r.integers(lo, hi + 1, size=(1000, 3))
            planes = np.arange(p.index_min[axis], p.index_max[axis] + 1, BLOCK_CELLS)
            planes = planes[(planes >= lo[axis]) & (planes <= hi[axis])]
            q[:, axis] = r.choice(planes, size=len(q))
            probes.append(q)
    probes = np.unique(np.vstack(probes), axis=0)
    truth = u32(prog.eval(lattice_points(probes, H)).astype(np.float32))

    seen: dict[str, np.ndarray] = {}
    for name, plan in plans.items():
        hits = np.zeros(len(probes), dtype=np.int64)
        for block, vals in iter_block_samples(plan, workers=1):
            s = np.asarray(plan.block_start(block))
            m = np.all((probes >= s) & (probes <= s + BLOCK_CELLS), axis=1)
            loc = probes[m] - s
            got = u32(vals[loc[:, 0], loc[:, 1], loc[:, 2]])
            assert np.array_equal(got, truth[m]), f"{name} block {block} differs at probes"
            hits += m
        # A probe in a skipped block must have the recorded sign.
        bidx = (probes - np.asarray(plan.index_min)) // BLOCK_CELLS
        bidx = np.minimum(bidx, np.asarray(plan.grid_shape) - 1)
        status = plan.status[bidx[:, 0], bidx[:, 1], bidx[:, 2]]
        val = truth.view(np.float32)
        assert np.all(val[status == INSIDE] < 0) and np.all(val[status == OUTSIDE] >= 0)
        seen[name] = hits
    both = (seen["A"] > 0) & (seen["B"] > 0)
    shared_face = (seen["A"] > 1) | (seen["B"] > 1)
    assert both.sum() > 1000 and shared_face.sum() > 1000
    eval_report(
        f"{len(probes)} probes, {int(both.sum())} sampled in both regions, "
        f"{int(shared_face.sum())} on faces shared by >1 block; all bitwise equal"
    )


# ---------------------------------------------------------------- far-field switch


def _coarse_box(node, store):
    g = node.grid
    shape = store.get(g.coarse_sha256).shape
    lo = np.asarray(g.coarse_origin)
    return lo, lo + (np.asarray(shape) - 1) * g.coarse_h


def test_skipping_across_coarse_far_switch(eval_report):
    """The coarse -> bbox-distance switch of a sampled field jumps *down* (the far rule is
    a lower bound, not continuous with the coarse grid). Around a corner of the coarse box
    the jump exceeds the skip margin ``(k - 1) * half_diag + k * h``; skipping must still
    never miss a crossing, because the far region keeps the sign and ``|d| >= margin``."""
    store = MemoryArrayStore()
    field = sphere_field(store, coarse_margin_mm=10.0)
    b = GraphBuilder()
    last = b.add(field, "last")
    corner_graph = b.build(last)
    clo, chi = _coarse_box(field, store)
    # A capsule through the coarse-box corner, so crossing blocks sit on the switch too.
    cap = b.add(Capsule(a=tuple(chi - 14.0), b=tuple(chi + 14.0), radius=3.0), "cap")
    union_graph = b.build(b.add(Union(children=[last, cap]), "u"), prune=True)
    shell_graph = b.build(b.add(Offset(child=last, distance=4.5), "grown"), prune=True)

    rmin, rmax = tuple(chi - 24.0), tuple(chi + 24.0)
    margin = skip_threshold(H) - half_diagonal(H)
    hd = np.linalg.norm((LOCAL - 32) * H, axis=1).reshape((BLOCK_SAMPLES,) * 3)
    for name, graph in [
        ("last", corner_graph),
        ("last+capsule", union_graph),
        ("offset(last,4.5)", shell_graph),
    ]:
        plan = plan_blocks(graph, store, rmin, rmax, H)
        ref = plan_blocks(graph, store, rmin, rmax, H, skip=False)
        straddling = 0
        max_excess = -math.inf
        min_far = math.inf
        for block, full in iter_block_samples(ref, workers=1):
            st = plan.status[block]
            start = np.asarray(plan.block_start(block))
            pts = lattice_points(start + LOCAL, H)
            far = np.any((pts < clo) | (pts > chi), axis=1).reshape(full.shape)
            if far.any():
                min_far = min(min_far, float(np.min(full[far])))
            if st == CROSSING:
                continue
            inside = full < 0
            assert inside.all() if st == INSIDE else not inside.any(), (name, block)
            dc = abs(float(plan.centre_values[block]))
            max_excess = max(max_excess, float(np.max(dc - np.abs(full) - hd)))
            if far.any() and not far.all():
                straddling += 1
        if name != "last+capsule":  # the capsule itself reaches into the far region
            assert min_far > 0
        eval_report(
            f"{name}: total={plan.total_blocks} sampled={plan.n_crossing} "
            f"skipped_out={plan.n_outside} skipped_in={plan.n_inside}; skipped blocks "
            f"straddling the coarse/far switch={straddling}; max Lipschitz excess vs centre="
            f"{max_excess:.2f} mm (skip margin {margin:.2f} mm); min far-field d="
            f"{min_far:.2f} mm (far-field margin {plan.far_field_margins['last']:.2f} mm)"
        )
        if name == "last":
            # The jump is real and larger than the k-margin: safety comes from the sign.
            assert straddling > 0, "no skipped block straddles the coarse/far switch"
            assert max_excess > margin


def test_far_field_margin_must_exceed_offset_reach():
    store = MemoryArrayStore()
    field = sphere_field(store, coarse_margin_mm=2.0)
    b = GraphBuilder()
    last = b.add(field, "last")
    graph = b.build(b.add(Offset(child=last, distance=4.9), "grown"))
    lo, hi = _coarse_box(field, store)
    m = min(np.min(np.asarray(field.grid.bbox_min) - lo), np.min(hi - field.grid.bbox_max))
    assert m < 4.9
    with pytest.raises(GraphValidationError, match="offset reach"):
        plan_blocks(graph, store, (-10, -10, -10), (10, 10, 10), 0.5)
    ok = b.build(b.add(Offset(child=last, distance=m - 0.5), "ok"), prune=True)
    plan = plan_blocks(ok, store, (-10, -10, -10), (10, 10, 10), 0.5)
    assert plan.far_field_margins["last"] == pytest.approx(m)
