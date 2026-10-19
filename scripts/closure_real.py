"""S5 on real assets with a stand-in cavity (until S4 / ease.py lands): envelope -> pose -> closure.

    SHOE_ASSETS_ROOT=~/Downloads uv run --extra fast python scripts/closure_real.py \\
        [--shoe dlp_solid_44_right] [--alpha 43] [--h 1.0] [--ease 1.0] [--out-dir DIR]

Stand-in cavity: phi_C = phi_last(posed) - ease, and every column inside the collar ring is opened
to the top of the grid (crude version of S4's collar through-cut; the real one uses z_T).
Writes closure_report.json, envelope.stl, cavity.stl (phi_C) and cavity_closed.stl (phi_C*) to --out-dir.
"""
import argparse
import json
import time
from pathlib import Path

import numpy as np

from shoe_designer import pose as pz
from shoe_designer.assets import load_asset, load_catalog
from shoe_designer.envelope import envelope
from shoe_designer.sdf import ALPHA_B_DEG, Grid, sdf_from_mesh, sdf_to_mesh
from shoe_designer.support_closure import closure


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shoe", default="dlp_solid_44_right")
    ap.add_argument("--closing", type=float, default=12.0)
    ap.add_argument("--h", type=float, default=1.0)
    ap.add_argument("--ease", type=float, default=1.0)
    ap.add_argument("--alpha", type=float, default=ALPHA_B_DEG, help="closure angle; 43 = D3 strict, 50 = relaxed")
    ap.add_argument("--headroom", type=float, default=45.0, help="extra grid height above the template (mm)")
    ap.add_argument("--wall", type=float, default=2.0)
    ap.add_argument("--out-dir", default=".")
    a = ap.parse_args()
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    cat = load_catalog()
    last = load_asset("sneaker_last", cat).mesh
    shoe = load_asset(a.shoe, cat).mesh
    # taller than the template: the ridge shell of the closure must fit under the top of the grid
    b = shoe.bounds
    grid = Grid.from_bounds(b[0], b[1] + [0, 0, a.headroom], a.h, a.alpha, margin=3 * max(a.h, Grid.dz_for(a.h, a.alpha)))
    env = envelope(shoe, h=a.h, grid=grid, closing_radius=a.closing)
    res = pz.fit_pose(env.phi, env.grid, env.mask_below(), last, ease=a.ease, wall=a.wall)
    posed = last.copy()
    posed.apply_transform(res.transform)

    t0 = time.perf_counter()
    phi_c = sdf_from_mesh(posed, grid, band=15.0) - np.float32(a.ease)
    # open every column inside the collar ring from its lowest cavity voxel to the top of the grid
    nx, ny, _ = grid.shape
    gx, gy = np.meshgrid(grid.origin[0] + np.arange(nx) * grid.h, grid.origin[1] + np.arange(ny) * grid.h, indexing="ij")
    ring = env.collar.inside(np.stack([gx.ravel(), gy.ravel()], axis=1)).reshape(nx, ny)
    inside = phi_c < 0
    opened = np.cumsum(inside, axis=2) > 0  # at or above the column's lowest cavity voxel
    phi_c = np.where(ring[:, :, None] & opened, np.float32(-1.0), phi_c).astype(np.float32)
    t_c = time.perf_counter() - t0

    t0 = time.perf_counter()
    cl = closure(phi_c, grid, t=a.wall, phi_e=env.phi, region=env.mask_below())
    t_cl = time.perf_counter() - t0
    mesh_c, _ = sdf_to_mesh(phi_c, grid)
    mesh_s, rep = sdf_to_mesh(cl.phi_c_star, grid)
    env.to_mesh()[0].export(out / "envelope.stl")
    mesh_c.export(out / "cavity.stl")
    mesh_s.export(out / "cavity_closed.stl")
    up = mesh_s.face_normals[:, 2]
    report = {"shoe": a.shoe, "params": vars(a), "pose": res.to_dict()["pose"], "seconds": {"phi_c": round(t_c, 1), "closure": round(t_cl, 1)},
              "closure": cl.report, "closed_mesh": {"watertight": bool(mesh_s.is_watertight), "faces": rep.n_faces, "components": rep.n_components,
              "overhang_area_mm2_below_collar": round(float(mesh_s.area_faces[(up > 0.7071) & (mesh_s.triangles_center[:, 2] < float(env.collar.rim[:, 2].min()) - 2)].sum()), 0), "area_mm2": round(float(mesh_s.area), 0)}}
    (out / "closure_report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False, default=float))
    print(json.dumps(report, indent=1, ensure_ascii=False, default=float))


if __name__ == "__main__":
    main()
