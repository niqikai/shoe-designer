"""Block-sparse mesh extraction from GeometryGraphs: acceptance of plan M0.6.

Single-block vs multi-block plans of the same shape share the global lattice, so their
canonical meshes must be bitwise equal; the multi-block plans put block faces, edges and
the 8-block corner through the surface. Skip vs no-skip and 1 vs 4 workers must not change
a single byte of the STL.
"""

from __future__ import annotations

import hashlib
import math

import numpy as np
import pytest
import trimesh

from shoegen.evaluate import BLOCK_CELLS, plan_blocks
from shoegen.graph import (
    Box,
    Capsule,
    GraphBuilder,
    HalfSpace,
    Intersect,
    RoundedBox,
    StrutLattice,
    Subtract,
    Union,
)
from shoegen.mesh import (
    STL_HEADER,
    MeshMemoryError,
    extract_mesh,
    manifold3d_check,
    read_stl,
    stl_bytes,
    validate_mesh,
    write_stl,
)
from shoegen.profile import load_profile
from shoegen.resources import RssWatchdog

# ---------------------------------------------------------------- shapes


def sphere_graph(c=(0.0, 0.0, 0.0), r=20.0):
    b = GraphBuilder()
    return b.build(b.add(RoundedBox(center=c, half_size=(r, r, r), radius=r), "sphere"))


def rounded_box_graph(c=(0.0, 0.0, 0.0)):
    b = GraphBuilder()
    return b.build(b.add(RoundedBox(center=c, half_size=(17.0, 11.0, 8.0), radius=3.0), "rbox"))


def capsules_graph(c=(0.0, 0.0, 0.0)):
    c = np.asarray(c)
    b = GraphBuilder()
    c1 = b.add(Capsule(a=tuple(c + (-14, -6, -2)), b=tuple(c + (14, 6, 2)), radius=5.0), "c1")
    c2 = b.add(Capsule(a=tuple(c + (-6, 14, -3)), b=tuple(c + (6, -14, 3)), radius=4.0), "c2")
    return b.build(b.add(Union(children=[c1, c2]), "caps"))


SHAPES = {
    "sphere": (sphere_graph, 21.0),
    "rounded_box": (rounded_box_graph, 18.0),
    "capsules": (capsules_graph, 20.0),
}


def _extract(graph, rmin, rmax, h, **kw):
    plan = plan_blocks(graph, None, rmin, rmax, h, skip=kw.pop("skip", True))
    return plan, extract_mesh(plan, workers=kw.pop("workers", 1), **kw)


def _trimesh_checks(mesh):
    tm = trimesh.Trimesh(mesh.vertices, mesh.faces, process=False)
    return tm.is_watertight, tm.is_winding_consistent, tm.euler_number, tm.volume


# ---------------------------------------------------------------- acceptance


def test_sphere_volume_error(eval_report):
    plan, res = _extract(sphere_graph((0.3, -0.2, 0.1)), (-25,) * 3, (25,) * 3, 1.0)
    exact = 4.0 / 3.0 * math.pi * 20.0**3
    err = res.validation.volume_mm3 / exact - 1.0
    assert abs(err) <= 0.01
    assert res.validation.ok and res.validation.euler_characteristic == 2
    wt, wc, euler, vol = _trimesh_checks(res.mesh)
    assert wt and wc and euler == 2 and vol == pytest.approx(res.validation.volume_mm3)
    eval_report(
        f"R=20 sphere at 1 mm: {res.mesh.n_faces} triangles, volume {res.validation.volume_mm3:.1f}"
        f" mm3 vs {exact:.1f} ({100 * err:+.3f} %), blocks={plan.n_crossing}"
    )


@pytest.mark.parametrize("shape", sorted(SHAPES))
@pytest.mark.parametrize("h", [1.0, 0.7])
def test_single_vs_multi_block_identical(shape, h, eval_report):
    make, reach = SHAPES[shape]
    g = make()
    # single block: the region fits into 64 cells
    lo = -(reach + 2 * h)
    plan1, single = _extract(g, (lo,) * 3, (-lo,) * 3, h)
    assert plan1.grid_shape == (1, 1, 1)
    # multi block: block faces at index_min + 64 cut through the middle of the shape
    gmid = math.floor(-0.3 / h)  # a lattice plane near the centre
    lo2 = (gmid - BLOCK_CELLS) * h
    plan2, multi = _extract(g, (lo2 + 0.01 * h,) * 3, (-lo,) * 3, h)
    assert plan2.grid_shape == (2, 2, 2) and plan2.n_crossing == 8
    assert multi.assembly.blocks_with_triangles == 8
    assert multi.assembly.shared_vertices > 0
    assert multi.mesh.equals(single.mesh), f"{shape} h={h}: single and 8-block meshes differ"
    v = multi.validation
    assert v.ok and v.euler_characteristic == 2 and v.n_components == 1
    assert stl_bytes(multi.mesh) == stl_bytes(single.mesh)
    eval_report(
        f"{shape} h={h}: {v.triangles} triangles, chi={v.euler_characteristic}, "
        f"{multi.assembly.shared_vertices} vertices welded across block faces; "
        f"single == 2x2x2 blocks"
    )


def test_eight_block_corner_no_cracks(eval_report):
    """A ball centred on the corner where 8 blocks meet, radius a whole number of voxels
    (the samples on the three junction lines at distance R are exactly 0), and a capsule
    along the body diagonal through the corner."""
    h = 0.5
    corner = 64 * h  # region from 0: blocks meet at (32, 32, 32)
    b = GraphBuilder()
    ball = b.add(RoundedBox(center=(corner,) * 3, half_size=(6.0,) * 3, radius=6.0), "ball")
    rod = b.add(
        Capsule(
            a=(corner - 9.0, corner - 8.0, corner - 9.5),
            b=(corner + 9.5, corner + 9.0, corner + 8.0),
            radius=2.2,
        ),
        "rod",
    )
    g = b.build(b.add(Union(children=[ball, rod]), "u"))
    plan, res = _extract(g, (0.0,) * 3, (2 * corner,) * 3, h)
    assert plan.grid_shape == (2, 2, 2) and res.assembly.blocks_with_triangles == 8
    v = res.validation
    assert v.ok and v.n_components == 1 and v.euler_characteristic == 2
    assert res.assembly.coincident_points > 0  # exact zeros on the junction lines
    # the same shape inside a single block
    plan1, single = _extract(g, (corner - 16,) * 3, (corner + 15,) * 3, h)
    assert plan1.grid_shape == (1, 1, 1)
    assert single.mesh.equals(res.mesh)
    wt, wc, euler, _ = _trimesh_checks(res.mesh)
    assert wt and wc and euler == 2
    eval_report(
        f"8-block corner: {v.triangles} triangles in 8 blocks, "
        f"{res.assembly.shared_vertices} welded, {res.assembly.coincident_points} exact-zero "
        f"points merged, open edges {v.open_edges}; equal to the single-block mesh"
    )


def _zeros_cases():
    b = GraphBuilder()
    box = b.add(Box(center=(0.0, 0.0, 0.0), half_size=(8.0, 6.0, 4.0)), "box")
    plane11 = b.add(HalfSpace(normal=(1.0, 1.0, 0.0), offset=0.0), "p11")
    plane111 = b.add(HalfSpace(normal=(1.0, 1.0, 1.0), offset=0.0), "p111")
    plane13 = b.add(HalfSpace(normal=(1.0, 3.0, 0.0), offset=0.0), "p13")
    return {
        "box_on_lattice": b.build(box, prune=True),
        "box_cut_x+y<=0": b.build(b.add(Intersect(children=[box, plane11]), "i1"), prune=True),
        "box_cut_x+y+z<=0": b.build(b.add(Intersect(children=[box, plane111]), "i2"), prune=True),
        # n.p at lattice points on this plane is +-4e-16 rounding noise (random signs)
        "box_cut_x+3y<=0_noisy": b.build(
            b.add(Intersect(children=[box, plane13]), "i3"), prune=True
        ),
        "box_minus_box": b.build(
            b.add(
                Subtract(
                    base=box,
                    tool=b.add(Box(center=(4.0, 3.0, 2.0), half_size=(4.0, 3.0, 2.0)), "t"),
                ),
                "s",
            ),
            prune=True,
        ),
    }


@pytest.mark.parametrize("case", sorted(_zeros_cases()))
def test_exact_zeros_watertight_no_degenerate(case, eval_report):
    g = _zeros_cases()[case]
    h = 0.5
    # index_min = -68: the block faces at x, y, z = -2 mm cut through the box
    plan, res = _extract(g, (-34.0, -34.0, -34.0), (10.0, 10.0, 10.0), h)
    assert plan.grid_shape == (2, 2, 2)
    v = res.validation
    assert v.ok, v.to_dict(components=False)
    assert v.degenerate_triangles == 0 and v.zero_area_triangles == 0
    assert v.coincident_vertices == 0 and v.n_components == 1
    assert v.euler_characteristic == 2
    # exact zeros present: samples on the box faces are exactly 0
    plan_all = plan_blocks(g, None, (-34.0,) * 3, (10.0,) * 3, h, skip=False)
    samples = [plan_all.sample_block(blk) for blk in plan_all.blocks()]
    zeros = sum(int(np.count_nonzero(s == 0)) for s in samples)
    noisy = sum(int(np.count_nonzero((s != 0) & (np.abs(s) < 1e-12))) for s in samples)
    assert zeros > 100
    if case.endswith("noisy"):
        assert noisy > 20
    a = res.assembly
    eval_report(
        f"{case}: {zeros} samples exactly 0, {noisy} with |v| < 1e-12, "
        f"{a.coincident_points} coincident points "
        f"(merged {a.merged_points}, displaced {a.displaced_points}), "
        f"{a.degenerate_dropped} degenerate triangles dropped, {v.triangles} triangles, "
        f"volume {v.volume_mm3:.2f}"
    )


def test_workers_1_vs_4_identical_stl(tmp_path, eval_report):
    g = capsules_graph((0.4, 0.1, -0.3))
    plan = plan_blocks(g, None, (-22.0,) * 3, (22.0,) * 3, 0.5)
    assert plan.n_crossing >= 4
    r1 = extract_mesh(plan, workers=1)
    with RssWatchdog(limit_bytes=6 * 2**30, interval_s=0.1) as wd:
        r4 = extract_mesh(plan, workers=4, watchdog=wd)
    s1 = write_stl(r1.mesh, tmp_path / "w1.stl")
    s4 = write_stl(r4.mesh, tmp_path / "w4.stl")
    assert s1 == s4 == hashlib.sha256((tmp_path / "w1.stl").read_bytes()).hexdigest()
    assert r1.summary["mesh_sha256"] == r4.summary["mesh_sha256"]
    assert r4.summary["eval"]["workers"] == 4 and r4.summary["peak_rss_bytes"]
    assert r4.summary["eval"]["block_fn"] == "shoegen.mesh.mc.mesh_block_fn"
    eval_report(
        f"capsules h=0.5: {r4.mesh.n_faces} triangles, {plan.n_crossing} blocks, STL sha256 "
        f"{s1[:16]} for workers 1 and 4; peak RSS (4 workers) "
        f"{r4.summary['peak_rss_bytes'] / 2**20:.0f} MiB"
    )


def test_skip_vs_no_skip_identical():
    g = sphere_graph((1.3, -0.4, 0.2), 9.0)
    rmin, rmax = (-30.0, -30.0, -30.0), (30.0, 30.0, 30.0)
    p_skip, r_skip = _extract(g, rmin, rmax, 0.4)
    p_all, r_all = _extract(g, rmin, rmax, 0.4, skip=False)
    assert p_skip.n_crossing < p_all.n_crossing == p_all.total_blocks
    assert r_skip.mesh.equals(r_all.mesh)
    assert r_skip.summary["mesh_sha256"] == r_all.summary["mesh_sha256"]


def _lattice_block(radius=1.5, extent=20.0, lo=0.5):
    b = GraphBuilder()
    lat = b.add(StrutLattice(cell_mm=10.0, radius_mm=radius), "lattice")
    hi = lo + extent
    c = (lo + hi) / 2
    box = b.add(Box(center=(c, c, c), half_size=(extent / 2,) * 3), "clip")
    return b.build(b.add(Intersect(children=[lat, box]), "block"))


def test_bcc_lattice_block(eval_report):
    """StrutLattice (cell 10, r 1.5) ∩ the box [0.5, 20.5]^3, whose faces lie on the lattice
    of both voxel sizes (exact zeros on the cut faces); 0.25 mm spans 2 x 2 x 2 blocks."""
    g = _lattice_block()
    out = {}
    for h in (0.5, 0.25):
        plan, res = _extract(g, (-1.0,) * 3, (22.0,) * 3, h, workers=4 if h < 0.5 else 1)
        v = res.validation
        assert v.ok, (h, v.to_dict(components=False))
        assert v.n_components == 1
        out[h] = (v.euler_characteristic, v.volume_mm3, v.triangles, plan.grid_shape)
    (e1, v1, t1, s1), (e2, v2, t2, s2) = out[0.5], out[0.25]
    assert e1 == e2, f"Euler characteristic differs: {e1} vs {e2}"
    assert abs(v1 / v2 - 1) < 0.03
    eval_report(
        f"BCC block 20 mm, r 1.5: chi={e1} (genus {(2 - e1) // 2}) at h=0.5 and 0.25; "
        f"volume {v1:.1f} / {v2:.1f} mm3; triangles {t1} / {t2}; grids {s1} / {s2}"
    )


def test_fragment_removal(eval_report):
    b = GraphBuilder()
    main = b.add(RoundedBox(center=(0.0, 0.0, 0.0), half_size=(8.0,) * 3, radius=8.0), "main")
    tiny = b.add(Capsule(a=(13.1, 0.2, 0.1), b=(13.1, 0.2, 0.1), radius=0.5), "tiny")
    shell = b.add(
        Subtract(
            base=b.add(Capsule(a=(0.1, 13.2, 0.1), b=(0.1, 13.2, 0.1), radius=1.0), "hb"),
            tool=b.add(Capsule(a=(0.1, 13.2, 0.1), b=(0.1, 13.2, 0.1), radius=0.55), "hc"),
        ),
        "hollow",
    )
    g = b.build(b.add(Union(children=[main, tiny, shell]), "all"))
    plan = plan_blocks(g, None, (-10.0,) * 3, (16.0,) * 3, 0.125)
    profile = load_profile("pbf-tpu-generic@0")
    keep_all = extract_mesh(plan, workers=1, min_fragment_volume_mm3=0.0)
    assert keep_all.validation.n_components == 4  # main, tiny, hollow outer + cavity
    vols = sorted(c.volume_mm3 for c in keep_all.validation.components)
    assert vols[0] < 0  # the cavity
    res = extract_mesh(plan, workers=1, min_fragment_volume_mm3=profile.min_fragment_volume_mm3)
    f = res.fragments
    assert f.threshold_mm3 == profile.min_fragment_volume_mm3 == 1.0
    assert f.removed == 1 and 0.3 < f.removed_volume_mm3 < 0.6 and f.cavities_removed == 0
    assert res.validation.n_components == 3 and res.validation.ok
    big = extract_mesh(plan, workers=1, min_fragment_volume_mm3=5.0)
    assert big.fragments.removed == 2 and big.fragments.cavities_removed == 1
    assert big.validation.n_components == 1 and big.validation.ok
    assert res.summary["fragments_removed"] == 1
    eval_report(
        f"fragments at threshold 1.0 mm3: removed {f.removed} ({f.removed_volume_mm3:.3f} mm3); "
        f"at 5.0: removed {big.fragments.removed} pieces + {big.fragments.cavities_removed} "
        f"cavity ({big.fragments.removed_volume_mm3:.3f} mm3)"
    )


def test_stl_format_and_roundtrip(tmp_path):
    _, res = _extract(capsules_graph(), (-21.0,) * 3, (21.0,) * 3, 1.0)
    mesh = res.mesh
    sha = write_stl(mesh, tmp_path / "m.stl")
    data = (tmp_path / "m.stl").read_bytes()
    assert data == stl_bytes(mesh) and len(data) == 84 + 50 * mesh.n_faces
    assert hashlib.sha256(data).hexdigest() == sha
    header, normals, tris = read_stl(tmp_path / "m.stl")
    assert header == STL_HEADER
    # vertices are float32-exact, so the STL stores them without loss
    assert np.array_equal(tris.astype(np.float64), mesh.vertices[mesh.faces])
    n64 = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0]).astype(np.float64)
    n64 /= np.linalg.norm(n64, axis=1, keepdims=True)
    assert np.all(np.einsum("ij,ij->i", n64, normals) > 0.999)
    # welding the STL by coordinate gives back exactly the mesh
    tm = trimesh.load(tmp_path / "m.stl")
    assert tm.is_watertight and len(tm.vertices) == mesh.n_vertices


def test_memory_guard_raises_before_assembly():
    plan = plan_blocks(sphere_graph(), None, (-25,) * 3, (25,) * 3, 1.0)
    with pytest.raises(MeshMemoryError) as ei:
        extract_mesh(plan, workers=1, memory_limit_bytes=10**6)
    assert ei.value.exit_code == 6


def test_manifold3d_cross_check(eval_report):
    _, res = _extract(_lattice_block(), (-1.0,) * 3, (22.0,) * 3, 0.5)
    m = manifold3d_check(res.mesh, memory_limit_bytes=6 * 2**30)
    assert m["ok"] and m["status"] == "NoError"
    assert m["triangles"] == res.mesh.n_faces
    assert m["genus"] == (2 - res.validation.euler_characteristic) // 2
    assert m["volume_mm3"] == pytest.approx(res.validation.volume_mm3, rel=1e-9)
    skipped = manifold3d_check(res.mesh, memory_limit_bytes=1)
    assert "skipped" in skipped
    eval_report(f"manifold3d (subprocess): {m['status']}, genus {m['genus']}, {m['seconds']:.3f} s")


def test_validation_detects_defects():
    _, res = _extract(sphere_graph(), (-25,) * 3, (25,) * 3, 2.0)
    mesh = res.mesh
    holed = type(mesh)(mesh.vertices, mesh.faces[1:])
    v = validate_mesh(holed)
    assert not v.watertight and v.open_edges == 3
    flipped = mesh.faces.copy()
    flipped[0] = flipped[0, ::-1]
    v = validate_mesh(type(mesh)(mesh.vertices, flipped))
    assert not v.watertight and v.misoriented_edges == 3
    doubled = np.vstack([mesh.faces, mesh.faces[:1]])
    v = validate_mesh(type(mesh)(mesh.vertices, doubled))
    assert v.nonmanifold_edges == 3 and not v.manifold
    inverted = type(mesh)(mesh.vertices, mesh.faces[:, ::-1].copy())
    v = validate_mesh(inverted)
    assert v.watertight and v.volume_mm3 < 0 and not v.ok
