"""S3 on real assets: envelope() (S2) -> fit_pose -> gap map.

    SHOE_ASSETS_ROOT=~/Downloads uv run python scripts/pose_real.py [--h 1.0] [--out report.json]
"""
import argparse
import json
import time

from shoe_designer import pose as pz
from shoe_designer.assets import load_asset, load_catalog
from shoe_designer.envelope import envelope

CELLS = ("all", "heel", "mid", "toe", "y+", "y-", "top", "sole", "side")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--h", type=float, default=1.0)
    ap.add_argument("--shoe", default="dlp_solid_43_right")
    ap.add_argument("--closing", type=float, default=0.0, help="closing radius for perforated templates")
    ap.add_argument("--weight-mode", default="fixed", choices=["fixed", "pose"])
    ap.add_argument("--pitch-max", type=float, default=5.0)
    ap.add_argument("--s-min", type=float, default=6.0)
    ap.add_argument("--out")
    a = ap.parse_args()
    cat = load_catalog()
    last = load_asset("sneaker_last", cat).mesh
    shoe = load_asset(a.shoe, cat).mesh
    t = time.perf_counter()
    env = envelope(shoe, h=a.h, closing_radius=a.closing)
    print(f"envelope {env.grid.shape} in {time.perf_counter() - t:.1f}s")
    res = pz.fit_pose(env.phi, env.grid, env.mask_below(), last, ease=1.0, wall=2.0, s_min=a.s_min,
                      pitch_max_deg=a.pitch_max, weight_mode=a.weight_mode)
    out = res.to_dict()
    print(json.dumps({k: out[k] for k in ("pose", "cost", "baseline_cost", "rms_deficit_mm", "flags", "seconds")}, indent=1))
    for name in ("baseline_gap", "gap"):
        print(name)
        for cell in CELLS:
            print(f"  {cell:6s}", out[name][cell])
    if a.out:
        with open(a.out, "w") as f:
            json.dump(out, f, indent=1)


if __name__ == "__main__":
    main()
