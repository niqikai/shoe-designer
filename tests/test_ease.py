import numpy as np
import pytest
import trimesh

from shoe_designer import ease
from shoe_designer.ease import EaseConfig, EaseField
from shoe_designer.envelope import build_cap
from shoe_designer.sdf import Grid, sample, sdf_to_mesh

DEFAULT = EaseConfig.from_toml()
UP, SIDE, DOWN = (0.0, 0.0, 1.0), (0.0, 1.0, 0.0), (0.0, 0.0, -1.0)
L = 276.7


def e_at(u, normal, cfg=DEFAULT):
    f = EaseField(cfg, (0.0, L))
    return float(f(np.array([[u * L, 0.0, 0.0]]), np.array([normal]))[0])


def cfg_of(**kw):
    raw = {"global": {"fdm_shrink_mm": 0.0, "transition_mm": 10.0, "smooth_mm": 0.0}, "base": {"value": 1.0}}
    raw.update(kw)
    return EaseConfig.from_dict(raw)


# --------------------------------------------------------------------------- zone numbers


@pytest.mark.parametrize("u,n,want", [
    (0.95, UP, 1.0), (0.95, SIDE, 1.0),         # toe
    (0.72, SIDE, 0.5),                          # metatarsal sides
    (0.50, UP, 2.5),                            # instep
    (0.10, SIDE, 0.3), (0.10, UP, 0.3),         # heel
    (0.28, SIDE, 0.5), (0.28, UP, 0.5),         # arch / waist: base
    (0.50, DOWN, 0.5), (0.95, DOWN, 0.5), (0.10, DOWN, 0.5),  # floor
    (0.50, SIDE, 0.5),                          # instep zone is up-facing only
])
def test_zone_values_plus_shrink(u, n, want):
    assert e_at(u, n) == pytest.approx(want + DEFAULT.fdm_shrink, abs=1e-9)


def test_insole_is_a_config_item():
    raw = {"base": {"value": 0.5}, "floor": {"insole_mm": 3.0},
           "zone": [{"name": "floor", "u": [0.0, 1.0], "faces": ["down"], "value": 0.5, "insole": True}]}
    cfg = EaseConfig.from_dict(raw)
    assert e_at(0.5, DOWN, cfg) == pytest.approx(3.5)
    assert e_at(0.5, SIDE, cfg) == pytest.approx(0.5)
    assert DEFAULT.insole_mm == 0.0


def test_config_rejects_bad_zone():
    with pytest.raises(ease.EaseError):
        EaseConfig.from_dict({"base": {"value": 1}, "zone": [{"name": "x", "u": [0.5, 0.2], "value": 1}]})
    with pytest.raises(ease.EaseError):
        EaseConfig.from_dict({"base": {"value": 1}, "zone": [{"name": "x", "u": [0, 1], "faces": ["left"], "value": 1}]})


# --------------------------------------------------------------------------- transitions


def test_transition_is_continuous_and_10mm_wide():
    cfg = cfg_of(zone=[{"name": "z", "u": [0.4, 0.6], "faces": ["up", "side"], "value": 3.0}])
    u = np.linspace(0, 1, 5001)
    f = EaseField(cfg, (0.0, L))
    n = np.tile(SIDE, (len(u), 1))
    e = f(np.column_stack([u * L, np.zeros_like(u), np.zeros_like(u)]), n)
    assert e.min() == pytest.approx(1.0) and e.max() == pytest.approx(3.0)
    # smoothstep over 10 mm: max slope 1.5 * delta / width, no jump anywhere
    slope = np.abs(np.diff(e)) / (np.diff(u) * L)
    assert slope.max() <= 1.5 * 2.0 / 10.0 + 1e-3
    # transition centred on the edge: halfway at the edge, flat 5 mm away from it
    x0 = 0.4 * L
    at = lambda x: float(f(np.array([[x, 0.0, 0.0]]), np.array([SIDE]))[0])
    assert at(x0) == pytest.approx(2.0, abs=1e-6)
    assert at(x0 - 5.01) == pytest.approx(1.0, abs=1e-3) and at(x0 + 5.01) == pytest.approx(3.0, abs=1e-3)


def test_normal_blend_is_continuous():
    f = EaseField(DEFAULT, (0.0, L))
    t = np.linspace(0, np.pi / 2, 2001)  # side -> up at the instep
    n = np.column_stack([np.zeros_like(t), np.cos(t), np.sin(t)])
    e = f(np.column_stack([np.full_like(t, 0.5 * L), np.zeros_like(t), np.zeros_like(t)]), n)
    assert e[0] == pytest.approx(0.5 + 0.2) and e[-1] == pytest.approx(2.5 + 0.2)
    assert np.abs(np.diff(e)).max() < 0.02 and (np.diff(e) >= -1e-9).all()


def test_zone_edges_at_the_ends_do_not_fade():
    assert e_at(1.0, UP) == pytest.approx(1.2) and e_at(0.0, UP) == pytest.approx(0.5)


def test_default_zones_are_continuous_along_the_last():
    f = EaseField(DEFAULT, (0.0, L))
    u = np.linspace(0, 1, 4001)
    for n in (UP, SIDE, DOWN):
        e = f(np.column_stack([u * L, 0 * u, 0 * u]), np.tile(n, (len(u), 1)))
        assert (np.abs(np.diff(e)) / (np.diff(u) * L)).max() <= 1.5 * 2.0 / 10.0 + 1e-3


# --------------------------------------------------------------------------- cavity on a box last

LAST_EXT = (80.0, 30.0, 20.0)


def box_last(extents=LAST_EXT):
    m = trimesh.creation.box(extents=extents)
    m.apply_translation(-m.bounds[0])  # x from 0, y from 0, z from 0
    m.apply_translation([0, -extents[1] / 2, 0])
    return m


def place(x0=10.0, lift=6.0):
    t = np.eye(4)
    t[:3, 3] = [x0, 0, lift]
    return t


def rect_collar(x0, x1, y0, y1, z):
    n = 20
    s = lambda a, b: np.linspace(a, b, n, endpoint=False)
    xy = np.vstack([np.column_stack([s(x0, x1), np.full(n, y0)]), np.column_stack([np.full(n, x1), s(y0, y1)]),
                    np.column_stack([s(x1, x0), np.full(n, y1)]), np.column_stack([np.full(n, x0), s(y1, y0)])])
    zz = z(xy) if callable(z) else np.full(len(xy), z)
    return build_cap(np.column_stack([xy, zz]), interior_step=2.0)


def grid_for(top, h=1.0):
    return Grid.from_bounds([-5, -30, -2], [110, 30, top], h, 43.0)


def uniform(e):
    return cfg_of(base={"value": e})


def test_phi_c_is_last_offset_by_ease_zoned():
    last = box_last()
    cfg = cfg_of(zone=[{"name": "top", "u": [0.0, 1.0], "faces": ["up"], "value": 2.0},
                       {"name": "floor", "u": [0.0, 1.0], "faces": ["down"], "value": 0.5}])
    collar = rect_collar(30, 70, -12, 12, 80.0)  # far above the last: no truncation involved
    grid = grid_for(90)
    cav = ease.build_cavity(last, place(), collar, grid, cfg)
    assert cav.collar.extruded_columns == 0
    top, bottom = 6.0 + 20.0, 6.0
    # zero crossings above the top face, below the floor, beside the side face (box: 15 mm half width)
    for x in (30.0, 50.0, 70.0):  # away from the box edges
        z = np.linspace(top, top + 4, 801)
        p = np.column_stack([np.full_like(z, x), np.zeros_like(z), z])
        v = sample(cav.phi, grid, p)
        assert z[np.argmax(v > 0)] == pytest.approx(top + 2.0, abs=0.15)
        z = np.linspace(bottom - 2, bottom, 801)
        p = np.column_stack([np.full_like(z, x), np.zeros_like(z), z])
        v = sample(cav.phi, grid, p)
        assert z[np.argmax(v < 0)] == pytest.approx(bottom - 0.5, abs=0.15)
        y = np.linspace(14, 20, 801)
        p = np.column_stack([np.full_like(y, x), y, np.full_like(y, 16.0)])
        v = sample(cav.phi, grid, p)
        assert y[np.argmax(v > 0)] == pytest.approx(15.0 + 1.0, abs=0.15)  # base 1.0 (side)


def test_far_field_sign_and_shrink():
    last = box_last()
    cfg = EaseConfig.from_dict({"global": {"fdm_shrink_mm": 0.2}, "base": {"value": 1.0}})
    cav = ease.build_cavity(last, place(), rect_collar(30, 70, -12, 12, 80.0), grid_for(90), cfg)
    assert cav.report["e_range_mm"] == [1.2, 1.2]
    g = cav.grid
    assert sample(cav.phi, g, np.array([[50.0, 0.0, 16.0]]))[0] < -5          # deep inside the last
    assert sample(cav.phi, g, np.array([[100.0, 25.0, 60.0]]))[0] > 5         # far away
    mesh, rep = sdf_to_mesh(cav.phi, g)
    assert mesh.is_watertight and rep.dropped_volume_mm3 == 0


# --------------------------------------------------------------------------- collar truncation


def cavity_top_at(cav, x, y=0.0, z0=0.0, z1=None):
    z1 = cav.grid.origin[2] + (cav.grid.shape[2] - 1) * cav.grid.dz if z1 is None else z1
    z = np.linspace(z0, z1, 4001)
    v = sample(cav.phi, cav.grid, np.column_stack([np.full_like(z, x), np.full_like(z, y), z]))
    open_ = v < 0
    return z[np.flatnonzero(open_)[-1]] if open_.any() else None


def test_thin_film_under_the_cap_is_opened_and_thick_roof_kept():
    last = box_last()  # top at z = 26; e = 1 -> cavity top at 27
    grid = grid_for(60)
    ring = lambda zt: rect_collar(30, 70, -12, 12, zt)
    # (a) cap 1.0 mm above the cavity top: sliver < t_fill -> the whole ring opens up through the grid top
    cav = ease.build_cavity(last, place(), ring(28.0), grid, uniform(1.0))
    assert cav.collar.extruded_columns == cav.collar.ring_columns > 0
    assert cavity_top_at(cav, 50.0) == pytest.approx(cav.grid.origin[2] + (grid.shape[2] - 1) * grid.dz, abs=0.1)
    assert not ease.find_films(cav.phi, grid, ring(28.0)).any()
    # (b) cap 2.0 mm above: a real 2 mm roof stays
    cav = ease.build_cavity(last, place(), ring(29.0), grid, uniform(1.0))
    assert cav.collar.extruded_columns == 0
    assert cavity_top_at(cav, 50.0) == pytest.approx(27.0, abs=0.1)
    assert cav.collar.min_gap_kept == pytest.approx(2.0, abs=0.2)
    assert not ease.find_films(cav.phi, grid, ring(29.0)).any()


def test_sloped_cap_threshold_height():
    last = box_last()
    grid = grid_for(60)
    zt = lambda xy: 27.0 + 0.1 * (xy[:, 0] - 30.0)  # 27 .. 31 across the ring, gap = 0 .. 4
    collar = rect_collar(30, 70, -12, 12, zt)
    raw = ease.build_cavity(last, place(), collar, grid, uniform(1.0))
    # columns with gap < 1.5 (x < 45) open, the rest keep a roof of 1.5 .. 4 mm
    assert cavity_top_at(raw, 36.0) > 55
    assert cavity_top_at(raw, 40.0) > 55
    assert cavity_top_at(raw, 52.0) == pytest.approx(27.0, abs=0.1)
    assert cavity_top_at(raw, 66.0) == pytest.approx(27.0, abs=0.1)
    assert raw.collar.max_gap_extruded < 1.5 <= raw.collar.min_gap_kept + 0.15
    # neighbours of opened columns shift the sub-voxel crossing by a few 0.1 mm; the audit limit is t_min = 1.2
    assert not ease.find_films(raw.phi, grid, collar, t_fill=1.2).any()
    # without the rule there are slivers: phi_L - e alone, same grid
    before = (raw.phi_last - raw.ease_grid).astype(np.float32)
    assert ease.find_films(before, grid, collar, t_fill=1.2).any()


def test_last_standing_in_the_mouth_goes_through():
    # a post taller than the collar: every column of the post is open through the top, no cap film at z_T
    last = trimesh.util.concatenate([box_last(), trimesh.creation.box(extents=(20, 20, 50), transform=trimesh.transformations.translation_matrix([10, 0, 25]))])
    grid = grid_for(75)
    collar = rect_collar(12, 28, -9, 9, 50.0)
    cav = ease.build_cavity(last, place(), collar, grid, uniform(1.0))
    # box post occupies x 10..30 (posed), top z = 56 > z_T = 50
    assert cav.collar.through_columns > 0
    top = cav.grid.origin[2] + (grid.shape[2] - 1) * grid.dz
    assert cavity_top_at(cav, 20.0) == pytest.approx(top, abs=0.1)
    assert not ease.find_films(cav.phi, grid, collar).any()
    mesh, _ = sdf_to_mesh(cav.phi, grid)
    assert mesh.is_watertight


def test_columns_outside_the_ring_are_untouched():
    last = box_last()
    grid = grid_for(60)
    collar = rect_collar(30, 50, -8, 8, 28.0)
    cav = ease.build_cavity(last, place(), collar, grid, uniform(1.0))
    assert cavity_top_at(cav, 25.0) > 55   # 5 mm outside the rim: inside the 10 mm reach, opened
    assert cavity_top_at(cav, 38.0, y=14.0) > 55
    assert cavity_top_at(cav, 62.0) == pytest.approx(27.0, abs=0.1)
    assert cavity_top_at(cav, 85.0) == pytest.approx(27.0, abs=0.1)  # well outside ring + 10 mm
    assert cavity_top_at(cav, 85.0, y=10.0) == pytest.approx(27.0, abs=0.1)


def test_grid_must_cover_collar_height():
    last = box_last()
    with pytest.raises(ease.EaseError):
        ease.build_cavity(last, place(), rect_collar(30, 70, -12, 12, 50.0), grid_for(51), uniform(1.0))


def test_extend_grid_is_aligned():
    g = Grid.from_bounds([0, 0, 0], [50, 40, 30], 1.0, 43.0)
    g2 = ease.extend_grid(g, [-3.2, -1, -5], [60, 41, 70])
    off = (np.asarray(g.origin) - np.asarray(g2.origin)) / np.asarray(g.spacing)
    assert np.allclose(off, np.round(off)) and (off >= 0).all()
    assert g2.h == g.h and g2.dz == g.dz
    hi = np.asarray(g2.origin) + (np.asarray(g2.shape) - 1) * np.asarray(g2.spacing)
    assert (hi >= [60, 41, 70]).all() and (np.asarray(g2.origin) <= [-3.2, -1, -5]).all()
