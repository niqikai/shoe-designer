import math

import numpy as np
import pytest
import trimesh

from shoe_designer import audit
from shoe_designer.compose import (
    GateThresholds,
    compose,
    export,
    gate_metrics,
    gate_verdict,
    label_faces,
    pool,
    round_intersection,
    skin_samples,
    smooth_push,
)
from shoe_designer.envelope import build_cap
from shoe_designer.process import (
    ProcessConfig,
    build_volume,
    channel_width,
    mesh_inner_shells,
    sealed_cavities,
)
from shoe_designer.sdf import Grid, sdf_from_mesh
from shoe_designer.support_closure import closure

SIN45 = math.sin(math.radians(45))
T, T_FILL = 2.0, 1.5
Z_COLLAR = 50.0


def axes_of(grid):
    return np.meshgrid(*[grid.origin[i] + np.arange(grid.shape[i]) * grid.spacing[i] for i in range(3)], indexing="ij")


def box_phi(grid, lo, hi):
    q = np.stack([np.maximum(l - p, p - u) for p, l, u in zip(axes_of(grid), lo, hi)])
    return (np.linalg.norm(np.maximum(q, 0), axis=0) + np.minimum(q.max(axis=0), 0)).astype(np.float32)


def rect_collar(x0, x1, y0, y1, z):
    xs = np.arange(x0, x1, 2.0)
    ys = np.arange(y0, y1, 2.0)
    xy = np.vstack([np.column_stack([xs, np.full_like(xs, y0)]), np.column_stack([np.full_like(ys, x1), ys]),
                    np.column_stack([xs[::-1] + 2.0, np.full_like(xs, y1)]), np.column_stack([np.full_like(ys, x0), ys[::-1] + 2.0])])
    return build_cap(np.column_stack([xy, np.full(len(xy), z)]), interior_step=2.0)


def scene(wall_x0=0.8, floor=5.0, alpha=43.0, h=1.0, kind="box", z0=-4.0, pocket=False):
    """Template with a thin wall at x = 0 (``wall_x0`` mm), a floor at ``floor`` mm and a chimney through the collar."""
    grid = Grid.from_bounds((-6, -40, z0), (106, 40, 80), h, alpha_deg=alpha)
    if kind == "box":
        phi_e = box_phi(grid, (0, -30, 0), (100, 30, Z_COLLAR))
        main = box_phi(grid, (wall_x0, -12, floor), (92, 12, 30))
    else:  # ellipsoid template, ellipsoid cavity whose -x end sits ``wall_x0`` inside the envelope
        def ell(c, a):
            m = trimesh.creation.icosphere(subdivisions=4)
            m.apply_scale(a)
            m.apply_translation(c)
            return sdf_from_mesh(m, grid, band=8.0)

        phi_e = ell((50, 0, 25), (50, 30, 25))
        # cavity: -x end at wall_x0, flat floor enforced by the sole guard
        main = ell((wall_x0 + 44, 0, 23), (44, 24, 17))
    chim = box_phi(grid, (30, -12, 15), (70, 12, 120))
    phi_c = np.minimum(main, chim)
    if pocket:  # a sealed void inside the wall
        phi_c = np.minimum(phi_c, box_phi(grid, (50, 17, 20), (60, 23, 25)))
    collar = rect_collar(30, 70, -12, 12, Z_COLLAR if kind == "box" else 40.0)
    below = collar.mask_below(grid)
    phi_e = np.clip(phi_e, -8, 8).astype(np.float32)
    cl = closure(phi_c, grid, t=T, phi_e=phi_e, region=below)
    return grid, phi_e, phi_c, cl, collar


def build(**kw):
    sc = {k: kw.pop(k) for k in ("wall_x0", "floor", "alpha", "h", "kind", "z0", "pocket") if k in kw}
    grid, phi_e, phi_c, cl, collar = scene(**sc)
    comp = compose(phi_e, phi_c, cl.phi_a, cl.phi_c_star, grid, collar, **kw)
    mesh, rep = export(comp)
    return grid, phi_e, phi_c, cl, collar, comp, mesh


def ray_hits(mesh, origin, direction):
    loc, _, _ = mesh.ray.intersects_location([origin], [direction], multiple_hits=True)
    return np.sort(loc[:, np.argmax(np.abs(direction))] * np.sign(direction[np.argmax(np.abs(direction))]))


@pytest.fixture(scope="module")
def box():
    return build()


def test_round_intersection_keeps_thin_wall_and_rounds_corner():
    r = 1.0
    z = np.linspace(-3, 2, 5001)
    for thick in (1.2, 2.0, 3.0):
        # wall of thickness ``thick`` (a = lateral distance at its centre line) cut by the plane z = 0
        phi = round_intersection(np.full_like(z, -thick / 2), z, r)
        assert (phi < 0).any()  # the wall does not vanish
        assert z[phi < 0].max() >= -0.09  # the top edge drops by < 0.09 mm at the centre
        assert z[phi < 0].max() <= 0.0


def test_watertight_single_body_and_bed(box):
    *_, comp, mesh = box
    assert mesh.is_watertight and mesh.body_count == 1
    assert mesh.volume > 0
    assert mesh.bounds[0, 2] == pytest.approx(0.0, abs=0.05)  # cut at the bed, nothing below it
    assert comp.report["volume_mm3"]["shoe"] > 0


def test_thin_wall_is_patched(box):
    *_, comp, mesh = box
    hits = ray_hits(mesh, (-20.0, 0.0, 20.0), (1.0, 0.0, 0.0))
    assert len(hits) >= 2
    assert hits[1] - hits[0] >= T_FILL - 0.1  # was 0.8 mm
    assert comp.report["push"]["max_mm"] > 1.0  # the push did it
    assert comp.gate["need_fit_max_mm"] == pytest.approx(T - 0.8, abs=0.15)


def test_bed_plane_on_grid_nodes_stays_manifold():
    # nodes exactly at z = 0 (the cut plane) used to make marching cubes emit non-manifold edges
    *_, comp, mesh = build(z0=0.0)
    assert mesh.is_watertight and mesh.body_count == 1
    _, counts = np.unique(mesh.edges_sorted, axis=0, return_counts=True)
    assert (counts == 2).all()
    assert mesh.bounds[0, 2] == pytest.approx(0.0, abs=0.05)


def test_sealed_pocket_is_filled():
    *_, comp, mesh = build(pocket=True)
    assert comp.report["volume_mm3"]["sealed_pockets_filled"] > 100
    assert len(mesh.split(only_watertight=False)) == 1 and mesh.body_count == 1


def test_hard_fill_alone_reaches_t_fill():
    *_, comp, mesh = build(push=False)
    hits = ray_hits(mesh, (-20.0, 0.0, 20.0), (1.0, 0.0, 0.0))
    assert hits[1] - hits[0] >= T_FILL - 0.1
    assert comp.report["volume_mm3"]["hard_fill"] > 0
    assert comp.report["push"]["max_mm"] == 0


def test_wall_thickness_floor(box):
    *_, mesh = box
    w = audit.wall_thickness(mesh, n=6000)
    assert w["frac_thin"] <= 0.005
    assert w["p5"] >= 1.4


def test_sole_at_least_six_mm(box):
    *_, mesh = box
    for x, y in ((15.0, 0.0), (50.0, 5.0), (85.0, -5.0)):
        hits = ray_hits(mesh, (x, y, 20.0), (0.0, 0.0, -1.0))  # down from inside the cavity
        assert len(hits) >= 1
        assert 20.0 - hits[0] >= 6.0 - 0.15  # floor sat at 5 mm; the sole guard lifted it
    assert box[5].report["volume_mm3"]["sole_guard"] > 0


def test_no_steep_cavity_overhang(box):
    grid, _, phi_c, cl, collar, comp, mesh = box
    lab = label_faces(mesh, comp, phi_c, cl.phi_a, cl.phi_c_star, collar)
    cav = lab == "cavity"
    assert cav.sum() > 1000
    steep = cav & (mesh.face_normals[:, 2] < -SIN45 - 1e-6) & (mesh.triangles_center[:, 2] > 7.0)  # not the sole guard
    # S5 leaves 1-voxel treads on the diagonal hips and at the ridge root (0.5% on the real last, §5.1 budget 0.2%);
    # a toy box has proportionally more hips, so the budget here is 2%. Without the closure the ceiling is ~30%.
    assert mesh.area_faces[steep].sum() <= 0.02 * mesh.area_faces[cav].sum()


def test_labels_cover_all_sources(box):
    grid, _, phi_c, cl, collar, comp, mesh = box
    lab = label_faces(mesh, comp, phi_c, cl.phi_a, cl.phi_c_star, collar)
    names = set(lab)
    assert {"cavity", "skin", "bed"} <= names
    assert "hard_fill" in names or "skin" in names


@pytest.mark.parametrize("alpha", [43.0, 50.0])
def test_alpha_is_a_parameter(alpha):
    *_, comp, mesh = build(alpha=alpha)
    assert comp.report["params"]["alpha_b_deg"] == pytest.approx(alpha, abs=0.01)
    assert mesh.is_watertight and mesh.body_count == 1


def test_ellipsoid_template():
    *_, comp, mesh = build(kind="ellipsoid", wall_x0=0.8)
    assert mesh.is_watertight and mesh.body_count == 1
    assert mesh.bounds[0, 2] == pytest.approx(0.0, abs=0.05)
    assert comp.gate["need_fit_max_mm"] > 0.5  # the cavity end pokes at the skin
    w = audit.wall_thickness(mesh, n=6000)
    assert w["frac_thin"] <= 0.01


def test_gate_zero_gap_is_usable():
    # generous walls everywhere: cavity 12 mm in from the skin
    grid = Grid.from_bounds((-6, -40, -4), (106, 40, 80), 1.0)
    phi_e = np.clip(box_phi(grid, (0, -30, 0), (100, 30, Z_COLLAR)), -8, 8)
    phi_c = np.minimum(box_phi(grid, (12, -12, 12), (88, 12, 30)), box_phi(grid, (30, -12, 15), (70, 12, 120)))
    collar = rect_collar(30, 70, -12, 12, Z_COLLAR)
    m = gate_metrics(skin_samples(phi_e, phi_c, grid, collar), T, T_FILL)
    assert m["need_fit_max_mm"] == 0 and m["need_fit_area_over_0p5_frac"] == 0
    assert gate_verdict(m)["verdict"] == "usable"


def test_gate_verdict_levels():
    base = {"need_fit_p95_mm": 1.0, "need_fit_max_mm": 2.0, "need_fit_area_over_0p5_frac": 0.05, "hard_fill_only_area_frac": 0.01}
    assert gate_verdict(base)["verdict"] == "usable"
    assert gate_verdict(base | {"need_fit_p95_mm": 2.5})["verdict"] == "reshape"
    assert gate_verdict(base | {"need_fit_max_mm": 4.1})["verdict"] == "not_applicable"
    assert gate_verdict(base | {"need_fit_area_over_0p5_frac": 0.2})["verdict"] == "reshape"
    assert gate_verdict(base | {"need_fit_area_over_0p5_frac": 0.31})["verdict"] == "not_applicable"
    # the push must not hide a bad fit: the no-push fill area counts
    assert gate_verdict(base | {"hard_fill_only_area_frac": 0.11}, residual_fill_frac=0.0)["verdict"] == "not_applicable"
    assert gate_verdict(base, th=GateThresholds(p95_ok=0.5))["verdict"] == "reshape"


def test_push_covers_need_everywhere():
    grid, phi_e, phi_c, cl, collar = scene()
    s = skin_samples(phi_e, phi_c, grid, collar)
    pooled = pool(s)
    for sigma in (4.0, 8.0):
        p = smooth_push(pooled, sigma)
        assert (p >= pooled.need - 1e-9).all()  # strict P >= need, the point of the max filter
        assert p[pooled.bed].max() == 0
        assert p[~pooled.below].max() == 0


# --------------------------------------------------------------------------- process: closure off, bed cut off


def test_closure_off_has_no_shell_and_keeps_cavity():
    grid, phi_e, phi_c, cl, collar = scene()
    on = compose(phi_e, phi_c, cl.phi_a, cl.phi_c_star, grid, collar)
    off = compose(phi_e, phi_c, None, None, grid, collar)
    assert off.report["volume_mm3"]["closure_shell"] == 0
    assert on.report["volume_mm3"]["shoe"] < off.report["volume_mm3"]["shoe"] - 1000  # the closure carves the ridge out
    assert off.report["params"]["closure"] is False
    mesh, _ = export(off)
    lab = label_faces(mesh, off, phi_c, None, None, collar)
    assert "closure_shell" not in set(lab)
    # the cavity ceiling is flat again: closure-off keeps the exact phi_C (a big downward-facing area)
    flat = (mesh.face_normals[:, 2] < -0.99) & (lab == "cavity")
    assert mesh.area_faces[flat].sum() > 500
    with pytest.raises(Exception):
        compose(phi_e, phi_c, cl.phi_a, None, grid, collar)  # closure fields come as a pair


def test_bed_cut_off_does_not_cut_the_underside():
    def run(cut):
        grid = Grid.from_bounds((-6, -40, -8), (106, 40, 80), 1.0)
        phi_e = np.clip(box_phi(grid, (0, -30, -3), (100, 30, Z_COLLAR)), -8, 8)  # sole reaches 3 mm below z = 0
        phi_c = np.minimum(box_phi(grid, (10, -12, 8), (90, 12, 30)), box_phi(grid, (30, -12, 15), (70, 12, 120)))
        collar = rect_collar(30, 70, -12, 12, Z_COLLAR)
        comp = compose(phi_e, phi_c, None, None, grid, collar, bed_cut=cut)
        return comp, export(comp)[0]

    comp_on, m_on = run(True)
    comp_off, m_off = run(False)
    assert m_on.bounds[0, 2] == pytest.approx(0.0, abs=0.05)
    assert m_off.bounds[0, 2] == pytest.approx(-3.0, abs=0.1)
    assert m_off.is_watertight and m_off.body_count == 1
    lab = label_faces(m_off, comp_off, np.zeros(comp_off.phi.shape, np.float32) + 5, None, None, rect_collar(30, 70, -12, 12, Z_COLLAR))
    assert "bed" not in set(lab)


# --------------------------------------------------------------------------- process: powder and build volume


def test_sealed_cavity_check_counts_and_measures():
    grid = Grid.from_bounds((-5, -20, -5), (60, 20, 40), 1.0)
    solid = box_phi(grid, (0, -15, 0), (50, 15, 30))
    pocket = box_phi(grid, (20, -4, 10), (30, 4, 18))
    phi = np.maximum(solid, -pocket)
    r = sealed_cavities(phi, grid)
    assert r["count"] == 1
    assert r["volume_mm3"] == pytest.approx(10 * 8 * 8 * 1.0, rel=0.35)
    assert sealed_cavities(solid, grid)["count"] == 0
    # open it with a 6 mm tunnel along y: no longer sealed
    tunnel = box_phi(grid, (22, -20, 12), (28, 0, 16))
    assert sealed_cavities(np.maximum(phi, -tunnel), grid)["count"] == 0


@pytest.mark.parametrize("width", [4.0, 8.0])
def test_channel_width_finds_the_bottleneck(width):
    grid = Grid.from_bounds((-5, -30, -5), (80, 30, 50), 1.0)
    solid = box_phi(grid, (0, -25, 0), (70, 25, 40))
    room = box_phi(grid, (10, -15, 5), (50, 15, 35))
    hole = box_phi(grid, (50, -width / 2, 15), (75, width / 2, 15 + width))  # tunnel through the +x wall
    phi = np.maximum(solid, -np.minimum(room, hole))
    core = (room < 0)
    r = channel_width(phi, grid, core, min_channel=5.0)
    assert r["width_mm"] == pytest.approx(width, abs=1.8)
    assert r["ok"] == (width >= 5.0)


def test_mesh_inner_shells():
    outer = trimesh.creation.box(extents=(30, 30, 30))
    assert mesh_inner_shells(outer)["count"] == 0
    inner = trimesh.creation.box(extents=(10, 10, 10))
    inner.invert()
    r = mesh_inner_shells(trimesh.util.concatenate([outer, inner]))
    assert r["count"] == 1 and r["volume_mm3"] == pytest.approx(1000.0)


def test_build_volume_against_machines():
    rng = np.random.default_rng(0)
    # a 290 x 100 slab turned 45 degrees about z: aligned bbox ~275 x 275, rotated envelope 290 x 100
    pts = np.array([[x, y, z] for x in (0, 290) for y in (0, 100) for z in (0, 110)], float)
    c, s = math.cos(math.radians(45)), math.sin(math.radians(45))
    pts[:, :2] = pts[:, :2] @ np.array([[c, s], [-s, c]])
    machines = {"big": (340.0, 340.0, 600.0), "mid": (320.0, 320.0, 450.0), "small": (220.0, 220.0, 320.0), "low": (340.0, 340.0, 100.0)}
    r = build_volume(pts, machines, gap_mm=5.0, min_xy_mm=295.0)
    assert r["bbox_mm"][0] == pytest.approx(275.8, abs=0.2)
    ma = r["rotated"]["min_area"]
    assert sorted([ma["x_mm"], ma["y_mm"]]) == pytest.approx([100.0, 290.0], abs=1.5)  # the tight rectangle
    assert r["rotated"]["min_longer_side"]["x_mm"] == pytest.approx(275.8, abs=0.5)  # as given (45 deg) is the smallest square-ish fit
    m = r["machines"]
    assert m["big"]["aligned"] and m["mid"]["aligned"] and m["mid"]["rotated"]
    assert not m["small"]["aligned"] and not m["small"]["rotated"] and not m["small"]["meets_min_xy"]
    assert m["big"]["meets_min_xy"] and not m["low"]["aligned"]  # 110 + 5 mm tall does not fit in 100 mm
    # clearance counts: 290 + 5 > 292
    r2 = build_volume(np.array([[x, y, z] for x in (0, 290) for y in (0, 50) for z in (0, 10)], float), {"m": (292.0, 292.0, 100.0)}, 5.0)
    assert not r2["machines"]["m"]["aligned"]


def test_process_config_files():
    sls = ProcessConfig.from_toml("process_sls_tpu.toml")
    assert (sls.wall, sls.wall_min, sls.sole_min) == (2.0, 1.2, 6.0)
    assert sls.closure is False and sls.bed_cut is False and sls.shrink_mm == 0.0
    assert sls.min_channel_mm == 5.0 and sls.min_xy_mm == 295.0
    assert set(sls.machines) == {"eos_p396", "large_cn", "eos_p110"}
    fdm = ProcessConfig.from_toml("process_fdm_tpu.toml")
    assert fdm.closure and fdm.bed_cut and fdm.alpha_b_deg == 43.0
    with pytest.raises(Exception):
        ProcessConfig.from_toml("nonexistent.toml")
