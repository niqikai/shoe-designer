"""S3 on real assets with a *stand-in* envelope (S2 not merged yet).

Stand-in: sdf_from_mesh of the solid shoe, then a 3D morphological closing (EDT dilate/erode,
radius ``--close``) to cover the old cavity, and M_below = z < ``--mouth`` mm. Numbers are
indicative only; rerun with the real ``envelope()`` once S2 lands.

    SHOE_ASSETS_ROOT=~/Downloads uv run python scripts/pose_real.py [--h 2.0] [--out report.json]
"""
import argparse
import json
import time

import numpy as np
from scipy import ndimage

from shoe_designer import pose as pz
from shoe_designer.assets import load_asset, load_catalog
from shoe_designer.sdf import Grid, sdf_from_mesh


def closed_envelope(shoe, h, radius):
    grid = Grid.for_mesh(shoe, h, margin_voxels=int(radius / h) + 3)
    phi = sdf_from_mesh(shoe, grid, band=6.0)
    occ = phi < 0
    sp = grid.spacing
    dil = ndimage.distance_transform_edt(~occ, sampling=sp) <= radius
    keep = ndimage.distance_transform_edt(dil, sampling=sp) > radius  # erosion of the dilation
    closed = occ | keep
    d_in = ndimage.distance_transform_edt(closed, sampling=sp)
    d_out = ndimage.distance_transform_edt(~closed, sampling=sp)
    return grid, (d_out - d_in).astype(np.float32)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--h", type=float, default=2.0)
    ap.add_argument("--close", type=float, default=45.0)
    ap.add_argument("--mouth", type=float, default=82.0)
    ap.add_argument("--shoe", default="dlp_solid_43_right")
    ap.add_argument("--pitch-max", type=float, default=5.0)
    ap.add_argument("--s-min", type=float, default=6.0)
    ap.add_argument("--out")
    a = ap.parse_args()
    cat = load_catalog()
    last = load_asset("sneaker_last", cat).mesh
    shoe = load_asset(a.shoe, cat).mesh
    t = time.perf_counter()
    grid, phi = closed_envelope(shoe, a.h, a.close)
    z = grid.origin[2] + np.arange(grid.shape[2]) * grid.dz
    mask = np.broadcast_to(z < a.mouth, grid.shape)
    print(f"envelope {grid.shape} in {time.perf_counter() - t:.1f}s")
    res = pz.fit_pose(phi, grid, mask, last, ease=1.0, wall=2.0, s_min=a.s_min, pitch_max_deg=a.pitch_max)
    out = res.to_dict()
    print(json.dumps({k: out[k] for k in ("pose", "cost", "baseline_cost", "rms_deficit_mm", "flags", "seconds")}, indent=1))
    for name in ("gap",):
        print(name)
        for cell, s in out[name].items():
            if "/" in cell or cell in ("all", "heel", "mid", "toe", "y+", "y-"):
                print(f"  {cell:12s}", s)
    if a.out:
        json.dump(out, open(a.out, "w"), indent=1)


if __name__ == "__main__":
    main()
