"""Outline function library for the lattice shoe (#46 part C): composable shape functions for the thickness T(x, y, z) and the sole.

The outer skin of the lattice shoe is the cavity ``phi_C`` (the last + ease, never changed) offset outward by ``T(x, y, z)``; the
outline is what the user sees. Each function below is a *bounded, add-only* window: it can thicken the shell, flare or raise the sole,
or add a strap outside the collar, but never removes material from the cavity side, so the cavity is not entered (``T >= T0 >= 0``,
flare >= 0, the sole stays below the cavity floor, the tab stays outside the cavity; tested on a synthetic last and checked on the
built body by :func:`cavity_intrusion`).

========= ==================================================== ======================================
function  what                                                  effect on
========= ==================================================== ======================================
toe       round / square / spring (rocker) / high toe box       T bumps, sole rocker
heel      heel counter bulge + heel bevel                        T bump, sole bevel
sole_wall thick sole, flare and a curved (bulging) side wall     sole thickness / flare / bulge
cradle    forefoot rocker cradle (rises towards the toe, and     sole bottom height
          at the sides)
collar_roll  inward-rolled, rounded collar lip                   rim thickness / width
pull_tab  wide flat heel strap                                   added strap (box-section loop)
========= ==================================================== ======================================

Every parameter has ``(default, lo, hi)`` in :data:`PARAMS`; :func:`validate` refuses values outside the range (the build is refused
before any geometry is made) and :func:`clamp` returns the clamped values with a report that maps onto the #16 clamp report.
:func:`apply_outline` turns a list of functions into the fields of a ``ConformalSpec`` (thickness bumps and sole/collar/tab fields), so
the lattice builder needs no knowledge of the individual functions.
"""

from __future__ import annotations

import dataclasses
import math

import numpy as np

KINDS = {"toe": ("round", "square", "spring", "high")}

# name -> {param: (default, lo, hi)}
PARAMS = {
    "toe": {"dT_mm": (6.0, 0.0, 16.0), "u0": (0.80, 0.60, 0.95), "height_mm": (45.0, 20.0, 80.0), "spring_mm": (12.0, 0.0, 25.0)},
    "heel": {"dT_mm": (6.0, 0.0, 12.0), "height_mm": (50.0, 20.0, 80.0), "u1": (0.18, 0.08, 0.35), "bevel_mm": (4.0, 0.0, 10.0)},
    "sole_wall": {"sole_mm": (16.0, 10.0, 32.0), "flare_mm": (3.0, 0.0, 10.0), "bulge_mm": (2.5, 0.0, 8.0), "power": (1.5, 0.5, 3.0)},
    "cradle": {"rocker_mm": (10.0, 0.0, 25.0), "start_u": (0.55, 0.40, 0.80), "lateral_mm": (4.0, 0.0, 12.0)},
    "collar_roll": {"rim_mm": (10.0, 6.0, 16.0), "roll_mm": (3.0, 0.0, 6.0)},
    "pull_tab": {"width_mm": (16.0, 10.0, 24.0), "thick_mm": (2.6, 2.4, 5.0), "diameter_mm": (26.0, 18.0, 40.0)},
}


class OutlineError(ValueError):
    """An outline function has an unknown name / kind or a parameter outside its range."""


def default(fn: str, **over) -> dict:
    p = {k: v[0] for k, v in PARAMS[fn].items()}
    if fn == "toe":
        p["kind"] = "round"
    p.update(over)
    return {"fn": fn, **p}


def validate(shapes) -> list[str]:
    """Raise OutlineError for the first violation; returns [] when every parameter is inside its range."""
    for s in shapes:
        fn = s.get("fn")
        if fn not in PARAMS:
            raise OutlineError(f"unknown outline function {fn!r} (use one of {tuple(PARAMS)})")
        if fn == "toe" and s.get("kind", "round") not in KINDS["toe"]:
            raise OutlineError(f"toe kind must be one of {KINDS['toe']}, got {s.get('kind')!r}")
        extra = set(s) - set(PARAMS[fn]) - {"fn", "kind"}
        if extra:
            raise OutlineError(f"{fn}: unknown parameters {sorted(extra)}")
        for k, (d, lo, hi) in PARAMS[fn].items():
            val = float(s.get(k, d))
            if not (lo - 1e-9 <= val <= hi + 1e-9) or not math.isfinite(val):
                raise OutlineError(f"{fn}.{k} = {val:g} outside [{lo:g}, {hi:g}]")
    return []


def clamp(shapes):
    """Clamp every parameter into its range; returns (shapes, report) with one entry per clamped value (field, requested, used, reason)."""
    out, rep = [], []
    for s in shapes:
        fn = s.get("fn")
        if fn not in PARAMS:
            raise OutlineError(f"unknown outline function {fn!r}")
        t = dict(s)
        for k, (d, lo, hi) in PARAMS[fn].items():
            if k in t:
                v = float(t[k])
                u = min(max(v, lo), hi)
                if u != v:
                    rep.append({"field": f"{fn}.{k}", "requested": v, "used": u, "range": [lo, hi], "reason": "out_of_range"})
                t[k] = u
        out.append(t)
    return out, rep


def _p(s: dict, k: str) -> float:
    return float(s.get(k, PARAMS[s["fn"]][k][0]))


def bumps_of(s: dict) -> list[dict]:
    """The thickness bumps (``lattice_variants.thickness_field`` format) of one function: windows in u, height above the cavity floor and
    a normal weight. A bump only adds."""
    fn = s["fn"]
    if fn == "toe":
        kind, dT, u0, h = s.get("kind", "round"), _p(s, "dT_mm"), _p(s, "u0"), _p(s, "height_mm")
        if kind == "round":  # a fuller, rounder toe box all around
            return [{"dT": dT, "u": [u0, 1.2], "normal": "all", "z": [-5.0, 0.8 * h], "ramp": 0.10, "ramp_z": 6.0}]
        if kind == "square":  # blunt: thickness concentrated on the front normal over the whole height
            return [{"dT": 1.6 * dT, "u": [u0 + 0.05, 1.25], "normal": "front", "z": [-5.0, h], "ramp": 0.05, "ramp_z": 4.0},
                    {"dT": 0.5 * dT, "u": [u0, 1.2], "normal": "side", "z": [-5.0, 0.7 * h], "ramp": 0.08, "ramp_z": 6.0}]
        if kind == "high":  # tall toe box: thickness on the top normal
            return [{"dT": 1.5 * dT, "u": [u0 - 0.1, 1.15], "normal": "top", "z": [10.0, 2.0 * h], "ramp": 0.12, "ramp_z": 8.0}]
        return []  # spring: sole only
    if fn == "heel":
        return [{"dT": _p(s, "dT_mm"), "u": [-0.2, _p(s, "u1")], "normal": "back", "z": [0.0, _p(s, "height_mm")], "ramp": 0.06, "ramp_z": 8.0}]
    return []


def sole_fields(s: dict) -> dict:
    """ConformalSpec fields set by one function (sole, collar, tab)."""
    fn = s["fn"]
    if fn == "toe" and s.get("kind") == "spring":
        return {"rocker_mm": _p(s, "spring_mm"), "rocker_start": _p(s, "u0")}
    if fn == "heel":
        return {"heel_bevel_mm": _p(s, "bevel_mm")}
    if fn == "sole_wall":
        return {"sole_mm": _p(s, "sole_mm"), "sole_flare_mm": _p(s, "flare_mm") + _p(s, "bulge_mm"), "sole_bulge_mm": _p(s, "bulge_mm"), "sole_bulge_power": _p(s, "power")}
    if fn == "cradle":
        return {"sole_rocker2_mm": _p(s, "rocker_mm"), "sole_rocker2_start": _p(s, "start_u"), "sole_cradle_mm": _p(s, "lateral_mm")}
    if fn == "collar_roll":
        return {"rim_mm": _p(s, "rim_mm"), "rim_thick_mm": _p(s, "roll_mm")}
    if fn == "pull_tab":
        return {"tab_w_mm": _p(s, "width_mm"), "tab_t_mm": _p(s, "thick_mm"), "tab_d_mm": _p(s, "diameter_mm"), "heel_loop_mm": 0.0}
    return {}


def apply_outline(spec, shapes):
    """Return ``spec`` with the outline functions applied (validated first): bumps appended, sole/collar/tab fields set. Order matters only for
    the fields two functions both set (the later wins); bumps add."""
    validate(shapes)
    bumps = list(spec.bumps)
    kw = {}
    for s in shapes:
        bumps += [tuple(sorted(b.items())) for b in bumps_of(s)]
        kw.update(sole_fields(s))
    return dataclasses.replace(spec, bumps=tuple(bumps), **kw)


def dT_field(prep, shapes, base_spec) -> np.ndarray:
    """T(x, y, z) increments of the functions alone on the coarse grid (composable, no base thickness): sum of the bumps."""
    from .lattice_variants import thickness_field

    spec = dataclasses.replace(base_spec, bumps=tuple(tuple(sorted(b.items())) for s in shapes for b in bumps_of(s)), thickness_mm=0.5, rim_thick_mm=0.0,
                               heel_counter_h_mm=0.0, toe_cap_h_mm=0.0)
    return thickness_field(prep, spec) - np.float32(0.5)


# ----------------------------------------------------------------------------------------------- sole / strap geometry
def smooth(t):
    t = np.clip(t, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def cradle_raise(u, y, halfwidth, rocker2, start, lateral):
    """Extra bottom height (>= 0): rises towards the toe (``rocker2``) and, over the forefoot, towards the sides (``lateral`` at the edge)."""
    r = rocker2 * smooth((u - start) / max(1.0 - start, 1e-6)) ** 1.5
    w = smooth((u - 0.25) / 0.3) * smooth((1.0 - u) / 0.15 + 0.4)
    return r + lateral * w * np.clip(np.abs(y) / np.maximum(halfwidth, 1.0), 0.0, 1.0) ** 2


def inset_profile(t, bulge, power):
    """Inward inset of the sole side wall at relative height t (0 = bottom edge, 1 = top of the wall): ``bulge`` at the bottom edge, 0 from
    about 70 % of the height up: the wall bulges outwards and is rounded under (thick, curved side wall)."""
    return bulge * (1.0 - smooth(np.asarray(t, float) / 0.7)) ** max(power, 1e-3)


def tab_sdf(X, Y, Z, cx, cz, R, w, t, r_round=0.8):
    """Box-section loop (strap) in the y-z plane around (cz): radius R (centre line), thickness t, width w along x, rounded corners."""
    q = np.sqrt(Y * Y + (Z - cz) ** 2) - R
    a = np.stack([np.abs(q) - 0.5 * t + r_round, np.abs(X - cx) - 0.5 * w + r_round], axis=0)
    return np.sqrt((np.maximum(a, 0.0) ** 2).sum(axis=0)) + np.minimum(np.maximum(a[0], a[1]), 0.0) - r_round


def cavity_intrusion(body: np.ndarray, c0: np.ndarray, tol_mm: float = 0.0) -> int:
    """Number of coarse voxels that are solid in ``body`` and inside the cavity (``phi_C < -tol``). Must be 0."""
    return int(((body < 0) & (c0 < -tol_mm)).sum())
