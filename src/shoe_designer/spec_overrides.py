"""``--set key=value`` overrides for the lattice shoe specs (form B: ConformalSpec, form B-knit: VariantSpec).

Keys are the spec fields. Values are JSON (numbers, booleans, lists such as ``raise_pts``) or a bare string. The override goes through
the spec's own ``from_dict`` (same validation as the toml loader). Numbers with a design range in :data:`RANGES` are clamped and reported
in the shape of the intent / outline clamp report; limits the engine enforces itself (yarn below the strut minimum, thickness below the
thickest yarn, voxel too coarse for the thinnest strut) stay refusals (``check_variant``), with the engine's message.
"""

from __future__ import annotations

import dataclasses
import json
import math
from dataclasses import fields

# key -> (lo, hi): design-space clamp. Not listed = no clamp (the engine's own checks still apply).
RANGES = {
    "pitch_mm": (4.0, 40.0), "yarn_mm": (0.5, 8.0), "thickness_mm": (1.0, 12.0), "rim_mm": (0.0, 25.0), "rim_thick_mm": (0.0, 8.0),
    "sole_mm": (8.0, 40.0), "sole_flare_mm": (0.0, 12.0), "rocker_mm": (0.0, 30.0), "rocker_start": (0.4, 0.9), "heel_bevel_mm": (0.0, 12.0),
    "tread_depth_mm": (0.0, 4.0), "helix_slope": (0.3, 2.0), "jitter": (0.0, 0.5), "elongate": (0.5, 3.0), "fillet_mm": (0.0, 8.0),
    "ring_factor": (0.5, 2.0), "voxel_mm": (0.2, 1.0), "heel_counter_h_mm": (0.0, 80.0), "toe_cap_h_mm": (0.0, 60.0),
    "heel_loop_mm": (0.0, 60.0), "heel_loop_tube_mm": (3.0, 10.0), "sole_margin_mm": (1.0, 8.0),
    "flow_cross_deg": (-30.0, 30.0), "flow_max_deg": (10.0, 80.0), "sole_wall_mm": (1.2, 8.0), "sole_wall_rod_mm": (1.2, 8.0),
    "sole_pitch_mm": (6.0, 40.0), "sole_ring_mm": (0.0, 12.0), "sole_pad_mm": (1.2, 6.0), "tab_w_mm": (0.0, 24.0), "tab_t_mm": (2.4, 5.0),
    "tab_d_mm": (18.0, 40.0),
}


class OverrideError(ValueError):
    pass


def parse_set(items) -> dict:
    """``["pitch_mm=10", "raise_pts=[[0,45],[1,0]]"]`` -> {key: value}. A later duplicate wins."""
    out = {}
    for it_ in items or []:
        if "=" not in it_:
            raise OverrideError(f"--set expects key=value, got {it_!r}")
        k, raw = it_.split("=", 1)
        k = k.strip()
        try:
            v = json.loads(raw, parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)))
        except ValueError:
            v = raw  # bare string, e.g. style=hex
        out[k] = v
    return out


def valid_keys(spec) -> dict:
    return {f.name: f for f in fields(spec) if f.name not in ("name", "description")}


def describe(spec) -> list[str]:
    """One line per key: name, type, current default, range."""
    lines = []
    for k in valid_keys(spec):
        cur = getattr(spec, k)
        if k in ("bumps", "outline"):
            cur = [dict(x) for x in cur]
        rng = RANGES.get(k)
        lines.append(f"{k:24} {type(getattr(spec, k)).__name__:6} default {json.dumps(cur, ensure_ascii=False)}" + (f"  range [{rng[0]}, {rng[1]}]" if rng else ""))
    return lines


def _coerce(key: str, value, current):
    if isinstance(current, bool):
        if not isinstance(value, bool):
            raise OverrideError(f"{key}: expected true/false, got {value!r}")
        return value
    if isinstance(current, (int, float)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise OverrideError(f"{key}: expected a number, got {value!r}")
        if isinstance(current, int):
            if float(value) != int(value):
                raise OverrideError(f"{key}: expected an integer, got {value!r}")
            return int(value)
        return float(value)
    if isinstance(current, str):
        if not isinstance(value, str):
            raise OverrideError(f"{key}: expected a string, got {value!r}")
        return value
    if isinstance(current, tuple):
        if not isinstance(value, list):
            raise OverrideError(f"{key}: expected a JSON list, got {value!r}")
        return value
    return value


def apply_overrides(spec, overrides: dict):
    """(new spec, applied {key: value}, clamp lines). Raises :class:`OverrideError` (unknown key, bad type, spec validation)."""
    keys = valid_keys(spec)
    bad = [k for k in overrides if k not in keys]
    if bad:
        raise OverrideError(f"unknown parameter(s) {bad}; valid keys: {', '.join(keys)}")
    raw = {f.name: getattr(spec, f.name) for f in fields(spec)}
    raw["bumps"] = [dict(b) for b in spec.bumps]
    if hasattr(spec, "outline"):
        raw["outline"] = [dict(o) for o in spec.outline]
    applied, clamps = {}, []
    for k, v in overrides.items():
        v = _coerce(k, v, getattr(spec, k))
        if k in RANGES and not isinstance(v, (bool, list)):
            lo, hi = RANGES[k]
            used = min(max(v, lo), hi)
            if used != v:
                clamps.append(f"{k}: {v} -> {used} (range [{lo}, {hi}])")
                v = used
        raw[k] = v
        applied[k] = v
    try:
        new = type(spec).from_dict(raw)
    except (TypeError, ValueError, KeyError) as e:
        if type(e).__name__ == "LatticeError":
            raise OverrideError(str(e)) from None
        raise OverrideError(f"invalid value: {type(e).__name__}: {e}") from None
    return dataclasses.replace(new, name=spec.name, description=spec.description), applied, clamps
