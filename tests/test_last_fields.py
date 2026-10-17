"""M0.3: last -> signed-distance sampled fields, footprint, height map, height surface.

Synthetic lasts only (tests/fixtures/synthetic_lasts.py). Truth values are closed-form, see
the fixture module. Runtime budget of the default tests is about one minute; the heavier
checks are marked ``slow``.
"""

from __future__ import annotations

import json
import os
import resource
import sys
import time
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from build123d import export_step
from fixtures.synthetic_lasts import (
    R_HEMI,
    heel_rise_last,
    hemisphere_last,
    hemisphere_surface_distance,
    rounded_last,
)
from graph_testutil import rng, single

from shoegen.graph import (
    Extrude2D,
    Graph,
    GraphBuilder,
    NpyDirectoryStore,
    Union,
    compile_graph,
    graph_hash,
)
from shoegen.last import (
    FieldParams,
    LastLoader,
    LastMeshError,
    LastProjection,
    build_last_fields,
    fields_cache_key,
    last_to_mesh,
    mesh_sdf_fn,
)
from shoegen.last.heightsheet import build_height_sheet
from shoegen.last.mesh import check_closed_solid

FINE_BAND_TOL = 0.05
SLOPE_LIMIT = np.tan(np.radians(60.0))


def mesh_of(synthetic, tmp_path: Path, tolerance=0.01, angular=0.05):
    """STEP export -> LastLoader -> last_to_mesh: the whole import path."""
    path = tmp_path / f"{synthetic.name}.step"
    export_step(synthetic.shape, path)
    loaded = LastLoader().load(path)
    return last_to_mesh(
        loaded.shape, tolerance, angular_tolerance=angular, source_sha256=loaded.source["sha256"]
    )


def truth_slope(height_fn, xy, step=0.25):
    ex, ey = np.array([step, 0.0]), np.array([0.0, step])
    gx = (height_fn(xy + ex) - height_fn(xy - ex)) / (2 * step)
    gy = (height_fn(xy + ey) - height_fn(xy - ey)) / (2 * step)
    return np.hypot(gx, gy)


def evaluate(node, store, points):
    return compile_graph(single(node), store).eval(points)


# ------------------------------------------------------------------ fixtures


@pytest.fixture(scope="module")
def work(tmp_path_factory):
    return tmp_path_factory.mktemp("last_fields")


@pytest.fixture(scope="module")
def small():
    return rounded_last(76.0, 40.0, 30.0, 8.0)


@pytest.fixture(scope="module")
def small_mesh(small, work):
    return mesh_of(small, work)


@pytest.fixture(scope="module")
def small_fields(small_mesh, work):
    t0 = time.perf_counter()
    lf = build_last_fields(small_mesh, work / "cache_small")
    lf.build_seconds = time.perf_counter() - t0
    return lf


@pytest.fixture(scope="module")
def hemi():
    return hemisphere_last()


@pytest.fixture(scope="module")
def hemi_mesh(hemi, work):
    return mesh_of(hemi, work)


@pytest.fixture(scope="module")
def hemi_proj(hemi_mesh):
    return LastProjection(hemi_mesh)


# ------------------------------------------------------------------ mesh


def test_mesh_from_step_is_closed_and_accurate(small, small_mesh):
    assert small_mesh.volume == pytest.approx(small.shape.volume, rel=2e-3)
    assert small_mesh.bbox_min == pytest.approx(small.bbox_min, abs=1e-3)
    assert small_mesh.bbox_max == pytest.approx(small.bbox_max, abs=1e-3)
    pts = rng(1).uniform(np.array(small.bbox_min) - 5, np.array(small.bbox_max) + 5, (20000, 3))
    truth = small.sdf(pts)
    near = np.abs(truth) < 6
    err = np.abs(mesh_sdf_fn(small_mesh)(pts) - truth)[near].max()
    assert err < 0.02, f"mesh deviates {err:.4f} mm from the exact solid"


def test_mesh_checks_reject_bad_meshes():
    v = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]], dtype=float)
    f = np.array([[0, 2, 1], [0, 1, 3], [1, 2, 3], [0, 3, 2]])  # outward tetrahedron
    check_closed_solid(v, f)
    with pytest.raises(LastMeshError, match="watertight"):
        check_closed_solid(v, f[:3])
    with pytest.raises(LastMeshError, match="orientation"):
        check_closed_solid(v, np.vstack([f[:3], f[3:][:, ::-1]]))
    with pytest.raises(LastMeshError, match="normals point inward"):
        check_closed_solid(v, f[:, ::-1])
    v2 = np.vstack([v, v + 10.0])
    f2 = np.vstack([f, f + 4])
    with pytest.raises(LastMeshError, match="2 connected components"):
        check_closed_solid(v2, f2)
    assert LastMeshError.exit_code == 4


# ------------------------------------------------------------------ last SDF field


def test_fine_band_error(small, small_fields):
    lf = small_fields
    pts = rng(2).uniform(np.array(small.bbox_min) - 6, np.array(small.bbox_max) + 6, (150000, 3))
    truth = small.sdf(pts)
    band = np.abs(truth) <= 5.0
    got = evaluate(lf.sampled_field, lf.store, pts[band])
    err = np.abs(got - truth[band])
    away = small.medial_distance(pts[band]) > 1.0
    print(f"fine band: max {err.max():.4f} mm (away from kinks {err[away].max():.4f})")
    assert err[away].max() <= FINE_BAND_TOL
    assert err.max() <= 0.2  # kink limitation of trilinear interpolation is far smaller here


def test_coarse_region_error_and_far_field(small, small_fields):
    lf = small_fields
    g = lf.sampled_field.grid
    pts = rng(3).uniform(np.array(small.bbox_min) - 28, np.array(small.bbox_max) + 28, (80000, 3))
    truth = small.sdf(pts)
    coarse = (np.abs(truth) > 8.0) & (np.abs(truth) < 25.0)
    got = evaluate(lf.sampled_field, lf.store, pts[coarse])
    err = np.abs(got - truth[coarse])
    away = small.medial_distance(pts[coarse]) > 4.0
    print(f"coarse region: away from medial axis {err[away].max():.3f} mm, all {err.max():.3f} mm")
    assert err[away].max() <= 1.0
    assert np.all(np.sign(got) == np.sign(truth[coarse]))
    # beyond the coarse domain the value is the distance to the bbox: a lower bound
    lo = np.array(g.coarse_origin)
    hi = lo + (np.array(lf.store.get(g.coarse_sha256).shape) - 1) * g.coarse_h
    far = np.concatenate(
        [
            rng(4).uniform(hi + 0.5, hi + 40.0, (3000, 3)),
            rng(5).uniform(lo - 40.0, lo - 0.5, (3000, 3)),
            rng(6).uniform(lo - 40.0, hi + 40.0, (6000, 3)),
        ]
    )
    far = far[np.any((far < lo) | (far > hi), axis=1)]
    d_true = small.sdf(far)
    d_got = evaluate(lf.sampled_field, lf.store, far)
    assert np.all(d_got > 0)
    assert np.all(d_got <= d_true + 1e-6), "far field must never exceed the true distance"


def test_fine_band_lipschitz(small, small_fields):
    lf = small_fields
    r = rng(7)
    p = r.uniform(np.array(small.bbox_min) - 6, np.array(small.bbox_max) + 6, (120000, 3))
    p = p[np.abs(small.sdf(p)) < 5.0][:40000]
    d = r.normal(size=p.shape)
    d /= np.linalg.norm(d, axis=1, keepdims=True)
    step = np.exp(r.uniform(np.log(0.01), np.log(0.5), len(p)))
    q = p + d * step[:, None]
    inside = np.abs(small.sdf(q)) < 5.0
    p, q, step = p[inside], q[inside], step[inside]
    prog = compile_graph(single(lf.sampled_field), lf.store)
    ratio = np.abs(prog.eval(p) - prog.eval(q)) / step
    print(f"fine band Lipschitz: {ratio.max():.4f} over {len(p)} pairs")
    assert ratio.max() <= 1.05


# ------------------------------------------------------------------ footprint


def test_footprint_field_error(small, small_fields):
    lf = small_fields
    node = Extrude2D(field=lf.footprint, z_min=-200.0, z_max=200.0)
    lo = np.array(small.bbox_min[:2]) - 25
    hi = np.array(small.bbox_max[:2]) + 25
    xy = rng(8).uniform(lo, hi, (60000, 2))
    got = evaluate(node, lf.store, np.column_stack([xy, np.zeros(len(xy))]))
    err = np.abs(got - small.footprint_sdf(xy))
    away = small.footprint_medial_distance(xy) > 1.5  # bilinear limit at the skeleton kink
    print(f"footprint 2D: max error {err[away].max():.4f} mm, {err.max():.4f} mm incl. skeleton")
    assert err[away].max() <= 0.1
    assert err.max() <= 0.2
    assert err[small.footprint_sdf(xy) > -3.0].max() <= 0.02  # outline and exterior are tight
    assert lf.footprint.h == 0.5
    outline_margin = np.array(lf.footprint.bbox_min) - np.array(lf.footprint.origin)
    assert np.all(outline_margin >= 30.0 - 0.5)


def test_footprint_hemisphere(hemi, hemi_proj):
    xy = rng(9).uniform(-80, 80, (60000, 2))
    err = np.abs(hemi_proj.footprint_sdf(xy) - hemi.footprint_sdf(xy))
    print(f"hemisphere footprint: max error {err.max():.4f} mm")
    assert err.max() <= 0.1


# ------------------------------------------------------------------ height map


def test_height_map_error(small, small_fields):
    hm = small_fields.height_map
    nx, ny = hm.values.shape
    ii, jj = np.meshgrid(np.arange(nx), np.arange(ny), indexing="ij")
    nodes = np.column_stack([ii.ravel(), jj.ravel()]) * hm.h + np.array(hm.origin)
    truth = small.height(nodes)
    ok = truth_slope(small.height, nodes) < SLOPE_LIMIT
    err = np.abs(hm.values.ravel().astype(np.float64) - truth)
    print(f"height map at nodes: max error {err[ok].max():.4f} mm on {ok.sum()} nodes")
    assert err[ok].max() <= 0.05
    # between nodes (bilinear), in cells whose four corners all have a true slope < 60 degrees
    node_ok = (truth_slope(small.height, nodes) < SLOPE_LIMIT).reshape(nx, ny)
    cell_ok = node_ok[:-1, :-1] & node_ok[1:, :-1] & node_ok[:-1, 1:] & node_ok[1:, 1:]
    xy = rng(11).uniform(
        np.array(hm.origin) + 5, np.array(hm.origin) + np.array([nx - 6, ny - 6]) * hm.h, (40000, 2)
    )
    cell = np.floor((xy - np.array(hm.origin)) / hm.h).astype(int)
    ok2 = cell_ok[cell[:, 0], cell[:, 1]]
    err2 = np.abs(hm.sample(xy) - small.height(xy))[ok2]
    print(f"height map bilinear: max error {err2.max():.4f} mm on {ok2.sum()} points")
    assert err2.max() <= 0.05
    # the exact function agrees too, and is continuous across the outline
    assert np.abs(hm.exact(xy) - small.height(xy))[ok2].max() <= 0.02


def test_height_map_hemisphere(hemi, hemi_proj):
    xy = rng(12).uniform(-70, 70, (60000, 2))
    truth = hemi.height(xy)
    ok = truth_slope(hemi.height, xy) < SLOPE_LIMIT
    err = np.abs(hemi_proj.height(xy) - truth)
    print(f"hemisphere height: max {err[ok].max():.4f} mm where slope < 60 deg")
    assert err[ok].max() <= 0.05
    outside = np.hypot(xy[:, 0], xy[:, 1]) > R_HEMI + 0.01
    assert np.abs(hemi_proj.height(xy[outside])).max() <= 0.01  # edge value 0, continuous


def test_heel_rise_variant(work):
    last = heel_rise_last()
    mesh = mesh_of(last, work)
    proj = LastProjection(mesh)
    pts = rng(13).uniform(np.array(last.bbox_min) - 5, np.array(last.bbox_max) + 5, (20000, 3))
    truth = last.sdf(pts)
    near = np.abs(truth) < 6
    assert np.abs(mesh_sdf_fn(mesh)(pts) - truth)[near].max() < 0.02
    xy = rng(14).uniform(-10, 135, (30000, 2))
    xy = xy[last.footprint_sdf(xy) < -0.1]
    assert np.abs(proj.footprint_sdf(xy) - last.footprint_sdf(xy)).max() <= 0.1
    xy = xy[:3000]
    truth_h = last.height(xy)
    ok = np.isfinite(truth_h) & (truth_slope(last.height, xy) < SLOPE_LIMIT)
    ok &= np.isfinite(truth_slope(last.height, xy))
    assert np.abs(proj.height(xy) - truth_h)[ok].max() <= 0.05
    # the bottom is 30 mm higher at the heel than at the toe
    z_heel = proj.height(np.array([[16.0, 0.0]]))[0]
    z_toe = proj.height(np.array([[110.0, 0.0]]))[0]
    assert z_heel - z_toe == pytest.approx(30.0 * (110.0 - 16.0) / 100.0, abs=3.0)
    assert z_heel > z_toe + 20.0


# ------------------------------------------------------------------ height surface


def test_height_sheet_is_a_real_distance_with_offset(small, small_fields):
    """Above the flat part of the sole the distance to z = h + offset is just |z - h - offset|."""
    offset = 0.8
    hm = small_fields.height_map
    sheet = build_height_sheet(hm.exact, (-20, -35), (95, 35), offset)
    xy = rng(15).uniform([22, -8], [54, 8], (4000, 2))  # flat bottom: h = 0
    dz = rng(16).uniform(-5, 5, len(xy))
    p = np.column_stack([xy, dz + offset])
    assert np.abs(sheet.signed_distance(p) - dz).max() <= 0.02


def test_height_surface_field_hemisphere(hemi, hemi_mesh, work):
    """Steep outline edge of the R = 50 hemisphere: distance to the exact open surface."""
    params = FieldParams(sheet_margin_mm=8.0)
    cache = work / "cache_hemi"
    lf = build_last_fields(hemi_mesh, cache, params, use_cache=False)
    t0 = time.perf_counter()
    node = lf.height_surface(0.0)
    build = time.perf_counter() - t0
    r = rng(17)
    rho = r.uniform(25.0, 56.0, 60000)  # the rim and the plane beyond it (sheet reaches 58 mm)
    ang = r.uniform(0.0, 2 * np.pi, len(rho))
    inner = r.uniform(-45, 45, (60000, 2))
    xy = np.vstack([np.column_stack([rho * np.cos(ang), rho * np.sin(ang)]), inner])
    z = r.uniform(-52.0, 10.0, len(xy))
    p = np.column_stack([xy, z])
    truth = hemisphere_surface_distance(p)
    band = np.abs(truth) <= 5.0
    p, truth = p[band], truth[band]
    got = evaluate(node, lf.store, p)
    err = np.abs(got - truth)
    # SDF kink along the bisector of the rim crease: known trilinear limitation
    rho_p = np.hypot(p[:, 0], p[:, 1])
    r3 = np.hypot(rho_p, p[:, 2])
    d_arc = np.where(p[:, 2] <= 0, np.abs(r3 - R_HEMI), np.hypot(rho_p - R_HEMI, p[:, 2]))
    d_plane = np.where(rho_p >= R_HEMI, np.abs(p[:, 2]), np.hypot(R_HEMI - rho_p, p[:, 2]))
    kink = np.abs(d_arc - d_plane) / np.sqrt(2.0)
    away = kink > 0.75
    near_rim = np.abs(rho_p - R_HEMI) < 3.0
    print(
        f"height surface (hemisphere): {len(p)} points, build {build:.1f}s; "
        f"max error {err[away].max():.4f} mm away from the crease kink, "
        f"{err[away & near_rim].max():.4f} mm near the rim, {err.max():.4f} mm including it"
    )
    assert (away & near_rim).sum() > 1000
    assert err[away].max() <= FINE_BAND_TOL
    clear = np.abs(truth) > 0.05
    assert np.all(np.sign(got[clear & away]) == np.sign(truth[clear & away]))
    # asking for the same surface again is a cache hit
    t0 = time.perf_counter()
    again = lf.height_surface(0.0)
    assert time.perf_counter() - t0 < 0.5 and again == node  # cache hit


# ------------------------------------------------------------------ cache and graph


def test_cache_hit_skips_recompute(small_mesh, small_fields, work):
    t0 = time.perf_counter()
    again = build_last_fields(small_mesh, work / "cache_small")
    hit = time.perf_counter() - t0
    assert again.cache_hit and not small_fields.cache_hit
    assert again.sampled_field == small_fields.sampled_field
    assert again.footprint == small_fields.footprint
    assert again.height_map == small_fields.height_map
    assert hit < 1.0 < small_fields.build_seconds
    print(f"build {small_fields.build_seconds:.1f}s, cache hit {hit * 1000:.0f}ms")
    # different sampling parameters or a moved mesh are different keys
    key = fields_cache_key(small_mesh, FieldParams())
    assert (work / "cache_small" / f"{key}.json").is_file()
    assert key != fields_cache_key(small_mesh, FieldParams(coarse_margin_mm=32.0))
    moved = replace(small_mesh, vertices=small_mesh.vertices + [0.0, 0.0, 1.0])
    assert key != fields_cache_key(moved, FieldParams())
    files = sorted(p.name for p in (work / "cache_small").iterdir())
    assert any(f.endswith(".json") for f in files) and any(f.endswith(".npy") for f in files)
    arrays = [f for f in files if f.endswith(".npy")]
    assert all(len(f) == 64 + 4 for f in arrays)  # <sha256>.npy


def test_nodes_compile_into_one_graph(small, small_fields):
    lf = small_fields
    b = GraphBuilder()
    last = b.add(lf.sampled_field, "last")
    foot = b.add(Extrude2D(field=lf.footprint, z_min=-3.0, z_max=0.0), "foot")
    root = b.add(Union(children=[last, foot]), "u")
    graph: Graph = b.build(root)
    h1 = graph_hash(graph)
    assert h1 == graph_hash(graph)
    prog = compile_graph(graph, lf.store)
    pts = rng(18).uniform(np.array(small.bbox_min) - 4, np.array(small.bbox_max) + 4, (5000, 3))
    ref = np.minimum(
        evaluate(lf.sampled_field, lf.store, pts),
        evaluate(Extrude2D(field=lf.footprint, z_min=-3.0, z_max=0.0), lf.store, pts),
    )
    assert np.array_equal(prog.eval(pts), ref)
    # sampling metadata is part of the hash: a node with another sampling dict hashes differently
    changed = lf.sampled_field.model_copy(update={"sampling": {"kind": "other"}})
    b2 = GraphBuilder()
    root2 = b2.add(changed, "last")
    assert graph_hash(b2.build(root2)) != graph_hash(single(lf.sampled_field))
    # arrays come from an NpyDirectoryStore opened with mmap
    arr = NpyDirectoryStore(lf.store.root).get(lf.sampled_field.grid.coarse_sha256)
    assert isinstance(arr, np.memmap)


# ------------------------------------------------------------------ other lasts, real last


def test_hemisphere_sdf_field(hemi, hemi_mesh, work):
    lf = build_last_fields(hemi_mesh, work / "cache_hemi_full")
    pts = rng(19).uniform(np.array(hemi.bbox_min) - 6, np.array(hemi.bbox_max) + 6, (200000, 3))
    truth = hemi.sdf(pts)
    band = np.abs(truth) <= 5.0
    got = evaluate(lf.sampled_field, lf.store, pts[band])
    q = pts[band]
    # the 90 degree top rim (rho = 50, z = 30) is a sharp edge: the exterior distance bends
    # faster than a 0.5 mm trilinear cell can follow within ~1.5 mm of it
    rim = np.hypot(np.hypot(q[:, 0], q[:, 1]) - R_HEMI, q[:, 2] - 30.0)
    away = (hemi.medial_distance(q) > 1.0) & (rim > 1.5)
    err = np.abs(got - truth[band])
    print(f"hemisphere SDF: fine band {err[away].max():.4f} (incl. kinks/edge {err.max():.4f})")
    assert err[away].max() <= FINE_BAND_TOL


def test_heel_rise_sdf_field(work):
    last = heel_rise_last()
    lf = build_last_fields(mesh_of(last, work), work / "cache_heel")
    pts = rng(20).uniform(np.array(last.bbox_min) - 6, np.array(last.bbox_max) + 6, (200000, 3))
    truth = last.sdf(pts)
    band = np.abs(truth) <= 5.0
    got = evaluate(lf.sampled_field, lf.store, pts[band])
    away = last.medial_distance(pts[band]) > 1.0
    assert np.abs(got - truth[band])[away].max() <= FINE_BAND_TOL


def _peak_rss_mb() -> float:
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return rss / (1024 * 1024) if sys.platform == "darwin" else rss / 1024


@pytest.mark.real_last
def test_real_last_report():
    root = Path(__file__).resolve().parents[1]
    env = os.environ.get("SHOEGEN_REAL_LAST")  # read in place, never copied
    files = [Path(env)] if env else sorted((root / "input" / "lasts").glob("*.st*p"))
    if not files:
        pytest.skip("no real last in input/lasts/ and SHOEGEN_REAL_LAST not set")
    # derived arrays stay under output/ (gitignored; plan 1.4), not in the temp directory
    cache = root / "output" / "pytest_real_last_fields"
    loaded = LastLoader().load(files[0])
    t0 = time.perf_counter()
    mesh = last_to_mesh(loaded.shape, source_sha256=loaded.source["sha256"])
    lf = build_last_fields(mesh, cache)
    elapsed = time.perf_counter() - t0
    g = lf.sampled_field.grid
    bricks = lf.store.get(g.fine_bricks_sha256)
    sizes = {p.name[:8]: p.stat().st_size for p in sorted(Path(lf.store.root).glob("*.npy"))}
    print(
        json.dumps(
            {
                "triangles": len(mesh.faces),
                "bricks": int(bricks.shape[0]),
                "array_bytes_total": sum(sizes.values()),
                "seconds": round(elapsed, 1),
                "peak_rss_mb": round(_peak_rss_mb()),
            }
        )
    )
    assert bricks.shape[0] > 0
