import dataclasses

import numpy as np
import pytest
from scipy import ndimage

from shoe_designer import lattice as L
from shoe_designer.process import ProcessConfig, mesh_inner_shells, sealed_cavities
from shoe_designer.sdf import Grid

CFG = L.LatticeConfig.from_toml()
PROC = ProcessConfig.from_toml("process_sls_tpu.toml")


def axes_of(grid):
    return np.meshgrid(*[grid.origin[i] + np.arange(grid.shape[i]) * grid.spacing[i] for i in range(3)], indexing="ij")


def box_phi(grid, lo, hi):
    q = np.stack([np.maximum(a - p, p - b) for p, a, b in zip(axes_of(grid), lo, hi)])
    return (np.linalg.norm(np.maximum(q, 0), axis=0) + np.minimum(q.max(axis=0), 0)).astype(np.float32)


def scene(length=120.0, floor=18.0):
    """Box 'shoe' (length x 60 x 40) with a box cavity from z = floor up through the open top, side walls 3 mm."""
    grid = Grid.from_bounds((-6, -40, -4), (length + 6, 40, 60), 1.0)
    outer = box_phi(grid, (0, -30, 0), (length, 30, 40))
    cav = box_phi(grid, (3, -27, floor), (length - 3, 27, 80))
    phi = np.maximum(outer, -cav).astype(np.float32)
    phi[phi == 0] = 1e-3
    return grid, phi, cav


def fine_cfg(**sole):
    s = dataclasses.replace(CFG.sole, **sole)
    return dataclasses.replace(CFG, sole=s, voxel_mm=0.3)


@pytest.fixture(scope="module")
def built():
    grid, phi, cav = scene()
    cfg = fine_cfg()
    fine, fg, rep = L.sole_lattice(phi, cav, grid, cfg, PROC)
    return grid, phi, cav, cfg, fine, fg, rep


# ------------------------------------------------------------------------------------------------ parameter checks
def test_strut_diameter_below_limit_is_refused():
    L.check_radii([0.6, 0.8], 1.2, 0.3)  # exactly 1.2 mm is fine
    with pytest.raises(L.LatticeError, match="diameter"):
        L.check_radii([0.6, 0.55], 1.2, 0.3)


def test_voxel_must_resolve_the_thinnest_strut():
    L.check_voxel(0.3, 1.2)
    with pytest.raises(L.LatticeError, match="voxel"):
        L.check_voxel(1.0, 1.2)  # the pipeline's own h = 1 mm is far too coarse
    with pytest.raises(L.LatticeError, match="voxel"):
        L.check_radii([0.9], 1.2, 0.5)  # 1.8 mm strut: voxel must be <= 0.45
    with pytest.raises(L.LatticeError):
        L.check_voxel(float("nan"), 1.2)


def test_density_too_low_for_the_minimum_strut_is_refused_at_build_time():
    grid, phi, cav = scene()
    cfg = fine_cfg(density=((0.0, 0.05), (1.0, 0.05)))  # r ~ 0.3 mm at a 6 mm cell
    with pytest.raises(L.LatticeError, match="diameter"):
        L.sole_lattice(phi, cav, grid, cfg, PROC)


def test_process_wall_minimum_raises_the_floor():
    proc = dataclasses.replace(PROC, wall_min=1.8)
    grid, phi, cav = scene()
    with pytest.raises(L.LatticeError):
        L.sole_lattice(phi, cav, grid, fine_cfg(), proc)  # radii 0.67-0.96 -> some diameters < 1.8


def test_skin_and_window_limits_come_from_the_process():
    grid, phi, cav = scene()
    with pytest.raises(L.LatticeError, match="skin"):
        L.sole_lattice(phi, cav, grid, fine_cfg(skin_outsole_mm=1.0), PROC)
    with pytest.raises(L.LatticeError, match="window"):
        L.sole_lattice(phi, cav, grid, fine_cfg(outsole_hole_mm=3.0), PROC)


def test_enforced_opening_refuses_a_tight_lattice():
    grid, phi, cav = scene()
    cfg = dataclasses.replace(fine_cfg(), enforce_opening=True)
    with pytest.raises(L.LatticeError, match="opening"):
        L.sole_lattice(phi, cav, grid, cfg, PROC)  # BCC 6 mm: through opening ~2 mm < 5 mm


def test_config_loads_and_rejects_bad_files(tmp_path):
    assert CFG.sole.kind == "bcc" and CFG.voxel_mm <= CFG.min_diameter_mm / CFG.voxel_ratio + 1e-9
    bad = tmp_path / "bad.toml"
    bad.write_text("[strut]\nmin_diameter_mm = 1.2\n")
    with pytest.raises(L.LatticeError):
        L.LatticeConfig.from_toml(bad)


# ------------------------------------------------------------------------------------------------ the field itself
@pytest.mark.parametrize("kind,r", [("bcc", 0.8), ("bcc", 0.6), ("cubic", 0.8)])
def test_strut_radius_measured_equals_set(kind, r):
    h, a = 0.2, 6.0
    g = (np.arange(int(2 * a / h)) + 0.5) * h
    X, Y, Z = np.meshgrid(g, g, g, indexing="ij")
    solid = L.strut_distance(kind, X, Y, Z, a) < r
    dist = ndimage.distance_transform_edt(solid, sampling=h)
    # mid-point of a strut away from the nodes: bcc diagonal (a/4, a/4, a/4); cubic edge (0, 0, a/2)
    p = (a / 4,) * 3 if kind == "bcc" else (a, a, 1.5 * a)
    i = tuple(int(round(v / h - 0.5)) for v in p)
    assert abs(dist[i] - r) <= h  # <= 1 voxel


@pytest.mark.parametrize("kind,rho", [("bcc", 0.2), ("bcc", 0.4), ("cubic", 0.3)])
def test_density_matches_measured_volume_fraction(kind, rho):
    a = 6.0
    r = L.radius_for_density(kind, a, rho)
    h = 0.15
    g = (np.arange(int(3 * a / h)) + 0.5) * h
    X, Y, Z = np.meshgrid(g, g, g, indexing="ij")
    frac = float((L.strut_distance(kind, X, Y, Z, a) < r).mean())
    assert frac == pytest.approx(rho, abs=0.02)
    assert L.density_for_radius(kind, a, r) == pytest.approx(rho, abs=0.01)


def test_bcc_distance_equals_brute_force_over_all_struts():
    rng = np.random.default_rng(0)
    a = 6.0
    p = rng.uniform(0, 2 * a, (200, 3)).astype(np.float32)
    nodes = [(0.5 + i, 0.5 + j, 0.5 + k) for i in range(-1, 3) for j in range(-1, 3) for k in range(-1, 3)]
    best = np.full(len(p), np.inf)
    for c in nodes:
        c = np.array(c) * a
        for s in np.array([(sx, sy, sz) for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)]):
            e = c + s * a / 2
            d = e - c
            t = np.clip(((p - c) @ d) / (d @ d), 0, 1)
            best = np.minimum(best, np.linalg.norm(p - (c + t[:, None] * d), axis=1))
    assert np.allclose(L.strut_distance("bcc", p[:, 0], p[:, 1], p[:, 2], a), best, atol=1e-4)


def test_pore_report_orders_the_lattices():
    tight = L.pore_report("bcc", 6.0, 0.9, 0.25)
    open_ = L.pore_report("bcc", 8.0, 0.9, 0.25)
    assert 0 < tight["through_mm"] <= tight["inscribed_mm"]
    assert open_["through_mm"] > tight["through_mm"]


# ------------------------------------------------------------------------------------------------ the sole
def test_sole_is_one_watertight_body_without_sealed_cavities(built):
    grid, phi, cav, cfg, fine, fg, rep = built
    assert L.sealed(fine, fg)["count"] == 0
    mesh, er = L.to_mesh(fine, fg)
    assert mesh.is_watertight and mesh.body_count == 1
    assert mesh_inner_shells(mesh)["count"] == 0
    assert er.dropped_volume_mm3 == 0.0
    assert rep["zone_fill_fraction"] < 0.5  # the zone is lighter than solid


def test_sealed_pocket_would_be_detected_and_filled():
    grid = Grid.from_bounds((0, 0, 0), (20, 20, 20), 1.0)
    f = np.full(grid.shape, 1.0, np.float32)
    f[3:17, 3:17, 3:17] = -1.0
    f[8:12, 8:12, 8:12] = 1.0
    assert sealed_cavities(f, grid)["count"] == 1
    rep = L.fill_sealed(f, grid)
    assert rep["components"] == 1 and sealed_cavities(f, grid)["count"] == 0


def test_density_gradient_runs_from_heel_to_toe(built):
    grid, phi, cav, cfg, fine, fg, rep = built
    assert rep["foot_axis"]["toe"] == "+x" or rep["foot_axis"]["toe"] == "-x"
    x = fg.origin[0] + np.arange(fg.shape[0]) * fg.h
    k = int(round((8.0 - fg.origin[2]) / fg.dz))  # mid-core height of the box (core z 4 .. 16)
    solid = (fine[:, :, k - 6 : k + 6] < 0)[:, int(fg.shape[1] * 0.4) : int(fg.shape[1] * 0.6)]

    def frac(lo, hi):
        m = (x > lo) & (x < hi)
        return float(solid[m].mean())

    heel_lo, heel_hi = (8, 30) if rep["foot_axis"]["toe"] == "+x" else (90, 112)
    toe_lo, toe_hi = (90, 112) if rep["foot_axis"]["toe"] == "+x" else (8, 30)
    assert frac(heel_lo, heel_hi) > frac(toe_lo, toe_hi) + 0.03


def test_lattice_stays_inside_the_skins(built):
    grid, phi, cav, cfg, fine, fg, rep = built
    s = cfg.sole
    # a column in the middle of the box: solid from the outsole to z = skin (outsole window holes aside), and below the floor
    x = fg.origin[0] + np.arange(fg.shape[0]) * fg.h
    ix = int(np.argmin(np.abs(x - 40.3)))
    iy = int(np.argmin(np.abs((fg.origin[1] + np.arange(fg.shape[1]) * fg.h) - 0.7)))
    z = fg.origin[2] + np.arange(fg.shape[2]) * fg.dz
    col = fine[ix, iy] < 0
    in_skin = (z > 0.2) & (z < s.skin_outsole_mm - 0.3)
    in_top = (z > 18.0 - s.skin_cavity_mm + 0.3) & (z < 17.7)
    # allow the 6 mm window holes: check a neighbouring column set instead of one point
    cols = fine[ix - 40 : ix + 40, iy - 40 : iy + 40] < 0
    assert cols[:, :, in_top].mean() > 0.9
    assert cols[:, :, in_skin].mean() > 0.8  # the powder windows take the rest
    del col


def test_powder_windows_pierce_only_the_outsole(built):
    grid, phi, cav, cfg, fine, fg, rep = built
    z = fg.origin[2] + np.arange(fg.shape[2]) * fg.dz
    k = int(np.argmin(np.abs(z - 1.0)))  # inside the outsole skin
    plane = fine[:, :, k] < 0
    box = plane[int(10 / fg.h) : int(110 / fg.h), int(15 / fg.h) : int(45 / fg.h)]
    lab, n = ndimage.label(~box)
    sizes = np.bincount(lab.ravel())[1:]
    assert n >= 3  # separate round holes
    assert (sizes[sizes > 200] * fg.h**2 / np.pi).max() ** 0.5 * 2 >= 5.0  # a hole diameter >= 5 mm somewhere


def test_deterministic(built):
    grid, phi, cav, cfg, fine, fg, rep = built
    again, _, rep2 = L.sole_lattice(phi, cav, grid, cfg, PROC)
    assert np.array_equal(fine, again)
    assert rep == rep2


def test_thin_material_flags_struts_below_the_limit():
    h, a = 0.2, 6.0
    g = (np.arange(int(2 * a / h)) + 0.5) * h
    X, Y, Z = np.meshgrid(g, g, g, indexing="ij")
    grid = Grid((0, 0, 0), h, h, X.shape)
    good = (L.strut_distance("bcc", X, Y, Z, a) - 0.8).astype(np.float32)  # 1.6 mm struts
    bad = (L.strut_distance("bcc", X, Y, Z, a) - 0.45).astype(np.float32)  # 0.9 mm struts
    assert L.thin_material(good, grid, 1.2)["thin_fraction"] < 0.02
    assert L.thin_material(bad, grid, 1.2)["thin_fraction"] > 0.5


def test_zone_stays_solid_where_the_sole_is_too_thin():
    grid, phi, cav = scene(floor=5.0)  # 5 mm under the cavity: no room for skins + a core
    fine, fg, rep = L.sole_lattice(phi, cav, grid, fine_cfg(), PROC)
    assert rep["columns_with_core"] == 0
    assert rep["zone_fill_fraction"] == 0 or rep["saved_cm3"] == 0
    assert fine is not None


def test_toe_direction_can_be_forced():
    grid, phi, cav = scene()
    a = L.SoleZone.build(phi, cav, grid, dataclasses.replace(CFG.sole, toe="+x"))
    b = L.SoleZone.build(phi, cav, grid, dataclasses.replace(CFG.sole, toe="-x"))
    assert a.axis["toe"] == "+x" and b.axis["toe"] == "-x"
    assert a.radius_at(0.0) == b.radius_at(0.0)
    with pytest.raises(L.LatticeError):
        L.foot_axis(cav, grid, "up")


# ------------------------------------------------------------------------------------------------ B. upper shell prototypes
def capsule_scene():
    """Foot-like cavity: a capsule along x (radius 20, floor at z = 14) with an opening chimney above; collar line z = 52."""
    grid = Grid.from_bounds((-30, -40, -6), (150, 40, 75), 1.0)
    X, Y, Z = axes_of(grid)
    ax, bx = 15.0, 105.0
    t = np.clip((X - ax) / (bx - ax), 0, 1)
    cap = np.sqrt((X - (ax + t * (bx - ax))) ** 2 + Y**2 + (Z - 34.0) ** 2) - 20.0
    chim = np.maximum(np.abs(Y) - 12.0, np.maximum(np.abs(X - 60.0) - 30.0, 40.0 - Z))
    cav = np.minimum(cap, chim).astype(np.float32)
    zt = np.full(grid.shape[:2], 52.0, np.float32)
    return grid, cav, zt


@pytest.fixture(scope="module")
def uppers():
    from shoe_designer import lattice_upper as U

    grid, cav, zt = capsule_scene()
    out = {}
    for form in U.FORMS:
        out[form] = U.upper_shell(form, cav, cav, zt, grid, dataclasses.replace(CFG, voxel_mm=0.3), PROC)
    return grid, cav, zt, out


@pytest.mark.parametrize("form", ["knit", "volume"])
def test_upper_prototype_is_one_watertight_body(uppers, form):
    grid, cav, zt, out = uppers
    fine, fg, rep = out[form]
    fine = fine.copy()
    pockets = L.fill_sealed(fine, fg)
    assert pockets["largest_voxels"] * fg.h**3 < 1.0  # < 1 mm^3: blips at a zone boundary, never a real cavity
    mesh, er = L.to_mesh(fine, fg)
    assert mesh.is_watertight and mesh.body_count == 1
    assert mesh_inner_shells(mesh)["count"] == 0
    assert er.dropped_volume_mm3 < 50
    assert rep["lattice_body_cm3"] < 0.8 * rep["solid_body_cm3"]  # lighter than the solid shell + sole it replaces


def test_knit_yarn_diameter_measured_equals_set(uppers):
    grid, cav, zt, out = uppers
    fine, fg, rep = out["knit"]
    k = rep["upper"]["yarn_diameter_mm"]
    solid = fine < 0
    # a yarn is a tube: distance to air inside it peaks at its radius (well away from crossings and the sole)
    sub = solid[:, :, int(round((34.0 - fg.origin[2]) / fg.dz)) : int(round((46.0 - fg.origin[2]) / fg.dz))]
    d = ndimage.distance_transform_edt(np.pad(sub, 2), sampling=fg.h)
    peak = np.percentile(d[d > 0.3], 99)
    assert abs(peak - 0.5 * k) <= 0.35 * k  # crossings are thicker; the tube radius is within a few voxels
    assert rep["openings"]["max_mm"] > 2.0  # the diamonds are open


def test_upper_forms_check_their_parameters():
    from shoe_designer import lattice_upper as U

    u = U.UpperConfig.from_lattice(CFG)
    U.check_upper("knit", u, CFG, PROC)
    U.check_upper("volume", u, CFG, PROC)
    with pytest.raises(L.LatticeError, match="diameter"):
        U.check_upper("knit", dataclasses.replace(u, knit=dataclasses.replace(u.knit, yarn_diameter_mm=1.0)), CFG, PROC)
    with pytest.raises(L.LatticeError, match="wall"):
        U.check_upper("volume", dataclasses.replace(u, volume=dataclasses.replace(u.volume, wall_mm=1.0)), CFG, PROC)
    with pytest.raises(L.LatticeError, match="cannot hold"):
        U.check_upper("volume", dataclasses.replace(u, volume=dataclasses.replace(u.volume, thickness_mm=3.0)), CFG, PROC)
    with pytest.raises(L.LatticeError):
        U.check_upper("tpms", u, CFG, PROC)


def test_offset_field_reaches_beyond_the_clamped_band():
    from shoe_designer import lattice_upper as U

    grid, cav, zt = capsule_scene()
    clamped = np.minimum(cav, 6.0)  # S4 clamps phi_C a few mm outside the cavity
    f = U.offset_field(clamped, grid)
    probe = tuple(np.array([60, 0, 34 - 20 - 7]) - np.array(grid.origin[:3]) + 0)  # 7 mm below the capsule bottom
    i = tuple(int(round(v / s)) for v, s in zip(probe, grid.spacing))
    assert f[i] == pytest.approx(7.0, abs=1.2)


def test_coupons_are_watertight_single_bodies_without_sealed_cavities():
    for cell in L.COUPON_CELLS:
        for d in L.COUPON_DIAMETERS:
            phi, grid, rep = L.coupon(cell, d, CFG, PROC)
            assert L.sealed(phi, grid)["count"] == 0
            mesh, er = L.to_mesh(phi, grid, 5.0)
            assert mesh.is_watertight and mesh.body_count == 1 and mesh_inner_shells(mesh)["count"] == 0
            assert rep["pores"]["through_mm"] > 0
    with pytest.raises(L.LatticeError):
        L.coupon(6.0, 1.0, CFG, PROC)  # 1.0 mm strut < 1.2 mm
    with pytest.raises(L.LatticeError):
        L.coupon(6.0, 1.6, CFG, PROC, hole_mm=4.0)  # window below the 5 mm opening
