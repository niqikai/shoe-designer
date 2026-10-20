"""#46 part C: outline function library (ranges, defaults, add-only, never entering the cavity)."""
import dataclasses

import numpy as np
import pytest

from shoe_designer import outline as O
from shoe_designer.lattice import u_of_x
from shoe_designer.lattice_conformal import ConformalSpec
from shoe_designer.lattice_variants import prepare, shell_body_v
from shoe_designer.sdf import Grid


def synthetic_prep():
    """A foot-like cavity: heel ball + forefoot ellipsoids with a waist, collar line 40 mm above the ground."""
    g = Grid((-10.0, -45.0, -5.0), 1.0, 1.0, (200, 90, 70))
    x = g.origin[0] + np.arange(g.shape[0])[:, None, None] * g.h
    y = g.origin[1] + np.arange(g.shape[1])[None, :, None] * g.h
    z = g.origin[2] + np.arange(g.shape[2])[None, None, :] * g.dz

    def ell(cx, cy, cz, a, b, c):
        return (np.sqrt(((x - cx) / a) ** 2 + ((y - cy) / b) ** 2 + ((z - cz) / c) ** 2) - 1.0) * min(a, b, c)

    c0 = np.minimum.reduce([ell(30, 0, 28, 30, 24, 24), ell(100, 0, 24, 45, 26, 22), ell(65, 0, 24, 18, 16, 18)]).astype(np.float32)
    zt = np.full(g.shape[:2], 40.0, np.float32)
    return prepare(c0, zt, g)


def spec(**kw):
    return ConformalSpec(thickness_mm=3.4, yarn_mm=2.4, sole_mm=14.0, **kw)


def test_defaults_inside_ranges_and_six_functions():
    assert set(O.PARAMS) >= {"toe", "heel", "sole_wall", "cradle", "collar_roll", "pull_tab"}
    assert set(O.KINDS["toe"]) == {"round", "square", "spring", "high"}
    shapes = [O.default(fn) for fn in O.PARAMS]
    assert O.validate(shapes) == []
    for fn, params in O.PARAMS.items():
        for k, (d, lo, hi) in params.items():
            assert lo <= d <= hi, (fn, k)


def test_out_of_range_refused_and_clamp_reports():
    with pytest.raises(O.OutlineError):
        O.validate([O.default("toe", dT_mm=99.0)])
    with pytest.raises(O.OutlineError):
        O.validate([{"fn": "nope"}])
    with pytest.raises(O.OutlineError):
        O.validate([O.default("toe", kind="pointy")])
    with pytest.raises(O.OutlineError):
        O.validate([{**O.default("heel"), "bogus": 1}])
    out, rep = O.clamp([O.default("sole_wall", flare_mm=50.0)])
    assert out[0]["flare_mm"] == O.PARAMS["sole_wall"]["flare_mm"][2]
    assert rep[0]["field"] == "sole_wall.flare_mm" and rep[0]["reason"] == "out_of_range"


@pytest.mark.parametrize("kind", ["round", "square", "spring", "high"])
def test_toe_kinds_only_add_and_stay_in_window(kind):
    p = synthetic_prep()
    base = spec()
    dT = O.dT_field(p, [O.default("toe", kind=kind)], base)
    assert dT.min() >= -1e-6
    if kind != "spring":
        assert dT.max() > 0.5
        xs = p.grid.origin[0] + np.arange(p.grid.shape[0]) * p.grid.h
        u = u_of_x(xs, p.axis)
        assert dT[u < 0.3].max() < 1e-3  # nothing at the heel end
    assert dT.max() <= 1.6 * 16.0 + 1e-3  # bounded by the range limits


def test_all_functions_never_enter_the_cavity():
    p = synthetic_prep()
    shapes = [O.default("toe", kind="high"), O.default("heel"), O.default("sole_wall"), O.default("cradle"), O.default("collar_roll")]
    s = O.apply_outline(spec(rim_mm=8.0), shapes)
    assert s.sole_flare_mm == pytest.approx(5.5) and s.sole_mm == 16.0 and s.rim_thick_mm == 3.0
    body, aux = shell_body_v(p, s)
    assert O.cavity_intrusion(body, p.c0) == 0
    assert (aux["T"] >= s.thickness_mm - 1e-4).all()  # the thickness never drops below the base: add-only


def test_cradle_only_raises_and_tab_is_outside_cavity():
    u = np.linspace(0, 1, 50)
    y = np.linspace(-40, 40, 81)
    r = O.cradle_raise(u[:, None], y[None, :], 40.0, 10.0, 0.55, 4.0)
    assert r.min() >= 0.0 and r[u < 0.4].max() < 4.5 and r[-1].max() > 9.0
    from shoe_designer.lattice_conformal import pull_tab_body

    p = synthetic_prep()
    s = O.apply_outline(spec(rim_mm=8.0), [O.default("pull_tab")])
    body, aux = shell_body_v(p, s)
    tab = pull_tab_body(p, s, body, aux["zt_var"])
    assert (tab < 0).any()
    assert ((tab < 0) & (p.c0 < 0)).sum() == 0
