"""Gate evidence for #9: S2 envelope -> S3 pose -> gap distribution, on the real assets.

    SHOE_ASSETS_ROOT=~/Downloads uv run --extra fast python scripts/gate_real.py \\
        [--shoe dlp_solid_43_right] [--closing 12] [--h 1.0] [--out report.json]

Without embreex the ray vote of ``envelope()`` takes tens of minutes; use ``--extra fast``.
The deficit is the extra distance (mm) the outer skin would have to be pushed out for the last
to have ``ease + wall`` of room everywhere below the collar (uniform ease/wall, no S4 field yet).
"""
import argparse
import json
import resource
import sys
import time

import numpy as np

from shoe_designer import pose as pz
from shoe_designer.assets import load_asset, load_catalog
from shoe_designer.envelope import envelope

CELLS = ("all", "heel", "mid", "toe", "y+", "y-", "heel/y+", "heel/y-", "mid/y+", "mid/y-", "toe/y+", "toe/y-",
         "top", "side", "sole")
THRESHOLDS = (0.5, 1.0, 2.0, 4.0)
NEEDS = {"collision (need 0)": 0.0, "wall 1.2 (t_min)": 1.2, "wall 2.0 (t)": 2.0}


def peak_rss_mb() -> float:
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return rss / (1024 * 1024 if sys.platform == "darwin" else 1024)


def distribution(gap: pz.GapMap) -> dict:
    d = gap.deficit[gap.counted]
    out = {"area_mm2": round(float(len(d) * gap.area_per_point), 0)}
    for q in (50, 90, 95, 99):
        out[f"p{q}"] = round(float(np.percentile(d, q)), 2)
    out["max"] = round(float(d.max()), 2)
    for t in THRESHOLDS:
        out[f"frac>{t}"] = round(float((d > t).mean()), 4)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shoe", default="dlp_solid_43_right")
    ap.add_argument("--closing", type=float, default=12.0, help="closing radius (mm); 12 for the perforated 43")
    ap.add_argument("--h", type=float, default=1.0)
    ap.add_argument("--ease", type=float, default=1.0)
    ap.add_argument("--wall", type=float, default=2.0)
    ap.add_argument("--s-min", type=float, default=6.0)
    ap.add_argument("--pitch-max", type=float, default=5.0)
    ap.add_argument("--n-starts", type=int, default=5)
    ap.add_argument("--out")
    a = ap.parse_args()

    cat = load_catalog()
    last = load_asset("sneaker_last", cat).mesh
    shoe = load_asset(a.shoe, cat).mesh

    t0 = time.perf_counter()
    env = envelope(shoe, h=a.h, closing_radius=a.closing)
    t_env = time.perf_counter() - t0
    rss_env = peak_rss_mb()
    mask = env.mask_below()

    t0 = time.perf_counter()
    res = pz.fit_pose(env.phi, env.grid, mask, last, ease=a.ease, wall=a.wall, s_min=a.s_min,
                      pitch_max_deg=a.pitch_max, n_starts=a.n_starts)
    t_fit = time.perf_counter() - t0

    out = {
        "shoe": a.shoe, "params": vars(a),
        "envelope": {"grid": list(env.grid.shape), "seconds": round(t_env, 1), "report": env.report},
        "pose": res.to_dict()["pose"],
        "flags": res.flags,
        "fit_seconds": round(t_fit, 2),
        "peak_rss_mb": round(peak_rss_mb()),
        "peak_rss_mb_after_envelope": round(rss_env),
        "baseline": {"gap": distribution(res.baseline), "cost": res.baseline_cost},
        "optimised": {"gap": distribution(res.gap), "cost": res.cost, "rms_deficit_mm": res.rms,
                      "cells": {c: res.gap.summary()[c] for c in CELLS}},
        "baseline_cells": {c: res.baseline.summary()[c] for c in CELLS},
        "starts": res.to_dict()["starts"],
    }
    # Same pose, other requirements: how much of the last is outside / thinner than t_min / t.
    w = res.gap.weight
    out["room"] = {}
    for name, need in NEEDS.items():
        g = pz.gap_map(env.phi, env.grid, mask, last, res.transform, ease=0.0, wall=need,
                       n_points=len(res.gap.points), weight=w)
        out["room"][name] = distribution(g)

    text = json.dumps(out, indent=1, ensure_ascii=False, default=float)
    if a.out:
        with open(a.out, "w") as f:
            f.write(text)
    g = out["optimised"]["gap"]
    print(f"{a.shoe}: envelope {t_env:.1f}s, fit {t_fit:.1f}s, peak RSS {out['peak_rss_mb']} MB")
    print("pose", {k: round(v, 2) for k, v in out["pose"].items()})
    print("flags", {k: out["flags"][k] for k in ("sole_active", "pitch_at_bound", "lift_at_cap", "mask_escape",
                                                  "n_starts_within_5pct", "n_starts", "worst_start_over_best")})
    print("gap before", out["baseline"]["gap"])
    print("gap after ", g)
    for name, d in out["room"].items():
        print(f"{name:20s}", {k: d[k] for k in ("p95", "max", "frac>0.5", "frac>1.0")})
    print(f"{'cell':8s} frac>0.5  p95   max  worst_at")
    for c in CELLS:
        s = out["optimised"]["cells"][c]
        print(f"{c:8s} {s.get('frac_over_0p5', 0):8.3f} {s.get('p95', 0):5.2f} {s.get('max', 0):5.2f}  {s.get('worst_at')}")


if __name__ == "__main__":
    main()
