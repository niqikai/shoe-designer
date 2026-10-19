"""Gate gaps with the zoned ease field (S4) vs the uniform 1.0 mm ease of the #9 evidence, plus phi_C export.

    SHOE_ASSETS_ROOT=~/Downloads uv run --extra fast python scripts/ease_gate.py \\
        [--shoe dlp_solid_44_right] [--closing 12] [--out report.json] [--stl cavity.stl]

Same chain as gate_real.py (S2 envelope -> S3 pose -> gap map). Two runs on one envelope:
``uniform`` (ease 1.0, wall 2.0; reproduces the #9 numbers) and ``zoned`` (EaseField from
config/ease_zones.toml, incl. FDM shrink; wall 2.0), each with its own pose fit. Then phi_C is
built at the zoned pose (open through the collar) and optionally exported.
"""
import argparse
import json
import time

import numpy as np

from shoe_designer import ease, pose as pz
from shoe_designer.assets import load_asset, load_catalog
from shoe_designer.envelope import envelope
from shoe_designer.sdf import sdf_to_mesh

CELLS = ("all", "heel", "mid", "toe", "y+", "y-", "top", "side", "sole",
         "heel/y+", "heel/y-", "mid/y+", "mid/y-", "toe/y+", "toe/y-")


def dist(gap: pz.GapMap, sel=None) -> dict:
    m = gap.counted if sel is None else gap.counted & sel
    d = gap.deficit[m]
    if not len(d):
        return {}
    return {"area_mm2": round(float(len(d) * gap.area_per_point)), "p50": round(float(np.percentile(d, 50)), 2),
            "p95": round(float(np.percentile(d, 95)), 2), "max": round(float(d.max()), 2),
            "frac>0.5": round(float((d > 0.5).mean()), 4), "frac>2": round(float((d > 2).mean()), 4),
            "frac>4": round(float((d > 4).mean()), 4)}


def run(env, last, mask, ease_field, a):
    res = pz.fit_pose(env.phi, env.grid, mask, last, ease=ease_field, wall=a.wall, s_min=a.s_min,
                      pitch_max_deg=a.pitch_max, n_starts=a.n_starts)
    cells = res.gap.cells()
    out = {"pose": res.to_dict()["pose"], "flags": {k: res.flags[k] for k in ("sole_active", "pitch_at_bound", "lift_at_cap", "mask_escape", "n_starts_within_5pct", "n_starts")},
           "gap": dist(res.gap), "cells": {c: dist(res.gap, cells[c]) for c in CELLS},
           "room": {}}
    w = res.gap.weight
    for name, need in {"collision (need 0)": 0.0, "wall 1.2": 1.2}.items():
        g = pz.gap_map(env.phi, env.grid, mask, last, res.transform, ease=0.0, wall=need, n_points=len(res.gap.points), weight=w)
        out["room"][name] = dist(g)
    return out, res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shoe", default="dlp_solid_44_right")
    ap.add_argument("--closing", type=float, default=12.0)
    ap.add_argument("--h", type=float, default=1.0)
    ap.add_argument("--wall", type=float, default=2.0)
    ap.add_argument("--s-min", type=float, default=6.0)
    ap.add_argument("--pitch-max", type=float, default=5.0)
    ap.add_argument("--n-starts", type=int, default=5)
    ap.add_argument("--config")
    ap.add_argument("--out")
    ap.add_argument("--stl", help="export the cavity solid (phi_C < 0, open at the top) here")
    a = ap.parse_args()

    cat = load_catalog()
    last = load_asset("sneaker_last", cat).mesh
    shoe = load_asset(a.shoe, cat).mesh
    t0 = time.perf_counter()
    env = envelope(shoe, h=a.h, closing_radius=a.closing)
    mask = env.mask_below()
    t_env = time.perf_counter() - t0

    field = ease.EaseField.for_last(last, a.config)
    uni, _ = run(env, last, mask, 1.0, a)
    zon, res = run(env, last, mask, field, a)
    # zoned ease at the uniform-ease pose too (shows what the pose refit buys)
    out = {"shoe": a.shoe, "params": vars(a), "envelope_seconds": round(t_env, 1), "uniform": uni, "zoned": zon}

    t0 = time.perf_counter()
    b = last.bounds
    corners = np.array([[x, y, z] for x in b[:, 0] for y in b[:, 1] for z in b[:, 2]])
    pc = corners @ res.transform[:3, :3].T + res.transform[:3, 3]
    zt_max = float(env.collar.rim[:, 2].max())
    grid = ease.extend_grid(env.grid, pc.min(axis=0) - 5, np.maximum(pc.max(axis=0), [0, 0, zt_max + 6]) + 5)
    cav = ease.build_cavity(last, res.transform, env.collar, grid, a.config)
    out["cavity"] = dict(cav.report, seconds=round(time.perf_counter() - t0, 1))
    films = ease.find_films(cav.phi, cav.grid, env.collar, t_fill=1.2)
    out["cavity"]["film_columns_after"] = int(films.sum())
    if a.stl:
        mesh, rep = sdf_to_mesh(cav.phi, cav.grid)
        mesh.export(a.stl)
        out["cavity"]["export"] = {"watertight": bool(mesh.is_watertight), "faces": rep.n_faces, "components": rep.n_components,
                                    "dropped_mm3": rep.dropped_volume_mm3, "volume_mm3": round(float(mesh.volume))}
    text = json.dumps(out, indent=1, ensure_ascii=False, default=float)
    if a.out:
        open(a.out, "w").write(text)
    print(a.shoe, "envelope", f"{t_env:.1f}s")
    for k in ("uniform", "zoned"):
        print(k, "pose", out[k]["pose"], "\n  gap", out[k]["gap"])
        for n, d in out[k]["room"].items():
            print("  ", n, d)
    print("cavity", {k: v for k, v in out["cavity"].items() if k not in ("zones", "grid")})


if __name__ == "__main__":
    main()
