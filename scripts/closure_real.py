"""S5 on real assets: S2 envelope -> S3 pose (zoned ease) -> S4 cavity phi_C -> closure.

    SHOE_ASSETS_ROOT=~/Downloads uv run --extra fast python scripts/closure_real.py \\
        [--shoe dlp_solid_44_right] [--alpha 43] [--h 1.0] [--out-dir DIR]

phi_C comes from ``ease.build_cavity`` (config/ease_zones.toml, open through the collar). The grid is
made ``--headroom`` mm taller than the template so the closure shell fits under it. Writes
closure_report.json, envelope.stl, cavity.stl (phi_C) and cavity_closed.stl (phi_C*) to --out-dir.
"""
import argparse
import json
import time
from pathlib import Path

import numpy as np

from shoe_designer import ease, pose as pz
from shoe_designer.assets import load_asset, load_catalog
from shoe_designer.envelope import envelope
from shoe_designer.sdf import ALPHA_B_DEG, Grid, sdf_to_mesh
from shoe_designer.support_closure import closure


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shoe", default="dlp_solid_44_right")
    ap.add_argument("--closing", type=float, default=12.0)
    ap.add_argument("--h", type=float, default=1.0)
    ap.add_argument("--alpha", type=float, default=ALPHA_B_DEG, help="closure angle; 43 = D3 strict, 50 = relaxed")
    ap.add_argument("--headroom", type=float, default=45.0, help="extra grid height above the template (mm)")
    ap.add_argument("--wall", type=float, default=2.0)
    ap.add_argument("--config")
    ap.add_argument("--out-dir", default=".")
    a = ap.parse_args()
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    cat = load_catalog()
    last = load_asset("sneaker_last", cat).mesh
    shoe = load_asset(a.shoe, cat).mesh
    b = shoe.bounds
    grid0 = Grid.from_bounds(b[0], b[1] + [0, 0, a.headroom], a.h, a.alpha, margin=3 * max(a.h, Grid.dz_for(a.h, a.alpha)))
    env = envelope(shoe, h=a.h, grid=grid0, closing_radius=a.closing)
    field = ease.EaseField.for_last(last, a.config)
    res = pz.fit_pose(env.phi, env.grid, env.mask_below(), last, ease=field, wall=a.wall)

    t0 = time.perf_counter()
    lb = last.bounds
    corners = np.array([[x, y, z] for x in lb[:, 0] for y in lb[:, 1] for z in lb[:, 2]])
    pc = corners @ res.transform[:3, :3].T + res.transform[:3, 3]
    zt_max = float(env.collar.rim[:, 2].max())
    grid = ease.extend_grid(env.grid, pc.min(axis=0) - 5, np.maximum(pc.max(axis=0), [0, 0, zt_max + a.headroom]) + 5)
    cav = ease.build_cavity(last, res.transform, env.collar, grid, a.config)
    # envelope phi_E on the extended grid (+8 mm = its band, beyond the old grid)
    off = np.rint((np.asarray(env.grid.origin) - np.asarray(grid.origin)) / np.asarray(grid.spacing)).astype(int)
    phi_e = np.full(grid.shape, 8.0, np.float32)
    sl = tuple(slice(o, o + n) for o, n in zip(off, env.grid.shape))
    phi_e[sl] = env.phi
    region = env.collar.mask_below(grid)
    t_c = time.perf_counter() - t0

    t0 = time.perf_counter()
    cl = closure(cav.phi, grid, t=a.wall, phi_e=phi_e, region=region)
    t_cl = time.perf_counter() - t0

    mesh_c, _ = sdf_to_mesh(cav.phi, grid)
    mesh_s, rep = sdf_to_mesh(cl.phi_c_star, grid)
    zt = env.collar.z_t(np.stack(np.meshgrid(grid.origin[0] + np.arange(grid.shape[0]) * grid.h,
                                             grid.origin[1] + np.arange(grid.shape[1]) * grid.h, indexing="ij"), -1).reshape(-1, 2))
    # face classes below the collar: z of the face centre against z_T at its (x, y)
    ctr = mesh_s.triangles_center
    ix = np.clip(np.rint((ctr[:, 0] - grid.origin[0]) / grid.h).astype(int), 0, grid.shape[0] - 1)
    iy = np.clip(np.rint((ctr[:, 1] - grid.origin[1]) / grid.h).astype(int), 0, grid.shape[1] - 1)
    below = ctr[:, 2] < zt.reshape(grid.shape[:2])[ix, iy] - 2.0
    up = mesh_s.face_normals[:, 2]
    area_below = float(mesh_s.area_faces[below].sum())
    over = float(mesh_s.area_faces[below & (up > 0.7071)].sum())
    over8 = float(mesh_s.area_faces[below & (up > 0.80)].sum())
    env.to_mesh()[0].export(out / "envelope.stl")
    mesh_c.export(out / "cavity.stl")
    mesh_s.export(out / "cavity_closed.stl")
    report = {
        "shoe": a.shoe, "params": vars(a), "pose": res.to_dict()["pose"], "cavity": cav.report,
        "seconds": {"cavity": round(t_c, 1), "closure": round(t_cl, 1)}, "closure": cl.report,
        "closed_mesh": {"watertight": bool(mesh_s.is_watertight), "faces": rep.n_faces, "components": rep.n_components,
                        "area_below_collar_mm2": round(area_below), "overhang_gt45_mm2": round(over),
                        "overhang_gt45_frac": round(over / area_below, 5), "nz_gt_0p8_mm2": round(over8)},
    }
    (out / "closure_report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False, default=float))
    print(json.dumps(report["closure"] | report["closed_mesh"], indent=1, ensure_ascii=False, default=float))


if __name__ == "__main__":
    main()
