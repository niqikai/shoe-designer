"""S6 on real assets: envelope -> pose -> phi_C -> closure -> compose -> STL + audit + Gate verdict.

    SHOE_ASSETS_ROOT=~/Downloads uv run --extra fast python scripts/build_real.py \\
        [--shoe dlp_solid_44_right] [--alpha 43] [--h 1.0] [--sigma 8] [--out-dir reports/build]

Writes shoe.stl (the first printable body), envelope.stl, cavity_closed.stl and build_report.json to
--out-dir. The output is derived from CC BY-NC-ND / unknown-source assets: keep it out of git (reports/ is ignored).
The pose is fitted with s_min = sole + floor ease so the *cavity floor*, not the last, leaves --sole mm of sole.
"""
import argparse
import json
import resource
import sys
import time
from pathlib import Path

import numpy as np
import trimesh

from shoe_designer import audit, ease, pose as pz
from shoe_designer.assets import load_asset, load_catalog
from shoe_designer.compose import (SIGMA_MM, T_FILL_MM, WALL_MM, compose, export, gate_verdict, label_faces)
from shoe_designer.envelope import envelope
from shoe_designer.sdf import ALPHA_B_DEG, Grid, sample, sdf_to_mesh
from shoe_designer.support_closure import closure

SIN45 = float(np.sin(np.radians(45)))


def rss_mb() -> float:
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return r / (1024 * 1024 if sys.platform == "darwin" else 1024)


def thickness_by_label(mesh: trimesh.Trimesh, labels: np.ndarray, n: int = 40000) -> dict:
    """Inward-ray wall thickness (as audit.wall_thickness) split by the source label of the sampled face."""
    pts, face = trimesh.sample.sample_surface(mesh, n, seed=audit.SEED)
    nrm = mesh.face_normals[face]
    origins = pts - nrm * audit.RAY_OFFSET_MM
    loc, ray, _ = mesh.ray.intersects_location(origins, -nrm, multiple_hits=False)
    d = np.full(n, np.nan)
    d[ray] = np.linalg.norm(loc - origins[ray], axis=1) + audit.RAY_OFFSET_MM
    lab = labels[face]
    out = {}
    for name in ["all"] + sorted(set(lab)):
        m = np.ones(n, bool) if name == "all" else lab == name
        v = d[m & ~np.isnan(d)]
        if len(v) < 20:
            continue
        out[name] = {"samples": int(len(v)), "min": round(float(v.min()), 2), "p1": round(float(np.percentile(v, 1)), 2),
                     "p5": round(float(np.percentile(v, 5)), 2), "median": round(float(np.median(v)), 2),
                     "frac_lt_1p2": round(float((v < audit.THIN_MM).mean()), 4), "frac_lt_1p4": round(float((v < 1.4).mean()), 4)}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shoe", default="dlp_solid_44_right")
    ap.add_argument("--closing", type=float, default=12.0, help="closing radius (mm), as closure_real.py")
    ap.add_argument("--h", type=float, default=1.0)
    ap.add_argument("--alpha", type=float, default=ALPHA_B_DEG, help="closure angle; 43 = D3 strict")
    ap.add_argument("--headroom", type=float, default=45.0)
    ap.add_argument("--wall", type=float, default=WALL_MM)
    ap.add_argument("--t-fill", type=float, default=T_FILL_MM)
    ap.add_argument("--sigma", type=float, default=SIGMA_MM)
    ap.add_argument("--sole", type=float, default=6.0, help="minimum sole under the cavity (D4)")
    ap.add_argument("--round-r", type=float, default=1.0)
    ap.add_argument("--band", type=float, default=14.0, help="phi_E exact band (mm); must exceed the largest push + 1.5")
    ap.add_argument("--no-push", action="store_true", help="skip level 2 (hard fill only)")
    ap.add_argument("--config")
    ap.add_argument("--out-dir", default="reports/build")
    a = ap.parse_args()
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    secs = {}
    t_all = time.perf_counter()

    def lap(name, t0):
        secs[name] = round(time.perf_counter() - t0, 1)
        print(f"[{name}] {secs[name]} s, peak RSS {rss_mb():.0f} MB", flush=True)

    cat = load_catalog()
    last = load_asset("sneaker_last", cat).mesh
    shoe = load_asset(a.shoe, cat).mesh
    b = shoe.bounds
    step = max(a.h, Grid.dz_for(a.h, a.alpha))
    grid0 = Grid.from_bounds(b[0], b[1] + [0, 0, a.headroom], a.h, a.alpha, margin=3 * step)

    t0 = time.perf_counter()
    env = envelope(shoe, h=a.h, grid=grid0, closing_radius=a.closing, band=a.band)
    lap("envelope", t0)
    field = ease.EaseField.for_last(last, a.config)
    floor_ease = float(field(np.array([[last.bounds.mean(axis=0)[0], 0.0, last.bounds[0, 2]]]), np.array([[0.0, 0.0, -1.0]]))[0])

    t0 = time.perf_counter()
    res = pz.fit_pose(env.phi, env.grid, env.mask_below(), last, ease=field, wall=a.wall, s_min=a.sole + floor_ease)
    lap("pose", t0)

    t0 = time.perf_counter()
    lb = last.bounds
    corners = np.array([[x, y, z] for x in lb[:, 0] for y in lb[:, 1] for z in lb[:, 2]])
    pc = corners @ res.transform[:3, :3].T + res.transform[:3, 3]
    zt_max = float(env.collar.rim[:, 2].max())
    grid = ease.extend_grid(env.grid, pc.min(axis=0) - 5, np.maximum(pc.max(axis=0), [0, 0, zt_max + a.headroom]) + 5)
    cav = ease.build_cavity(last, res.transform, env.collar, grid, a.config)
    off = np.rint((np.asarray(env.grid.origin) - np.asarray(grid.origin)) / np.asarray(grid.spacing)).astype(int)
    phi_e = np.full(grid.shape, a.band, np.float32)
    phi_e[tuple(slice(o, o + n) for o, n in zip(off, env.grid.shape))] = env.phi
    region = env.collar.mask_below(grid)
    lap("cavity", t0)

    t0 = time.perf_counter()
    cl = closure(cav.phi, grid, t=a.wall, phi_e=phi_e, region=region)
    lap("closure", t0)

    t0 = time.perf_counter()
    comp = compose(phi_e, cav.phi, cl.phi_a, cl.phi_c_star, grid, env.collar, wall=a.wall, t_fill=a.t_fill,
                   sigma=a.sigma, round_r=a.round_r, sole_min=a.sole, band_mm=a.band, push=not a.no_push)
    lap("compose", t0)

    t0 = time.perf_counter()
    mesh, erep = export(comp)
    lap("export", t0)
    mesh.export(out / "shoe.stl")
    env.to_mesh()[0].export(out / "envelope.stl")
    sdf_to_mesh(cl.phi_c_star, grid)[0].export(out / "cavity_closed.stl")

    # ----- audit of the exported STL
    t0 = time.perf_counter()
    labels = label_faces(mesh, comp, cav.phi, cl.phi_a, cl.phi_c_star, env.collar, wall=a.wall, t_fill=a.t_fill)
    area = mesh.area_faces
    lab_area = {n: round(float(area[labels == n].sum())) for n in sorted(set(labels))}
    cav_f = labels == "cavity"
    steep = cav_f & (mesh.face_normals[:, 2] < -SIN45) & (mesh.triangles_center[:, 2] > a.sole + 1.0)
    skin_total = float(area[np.isin(labels, ["skin", "hard_fill", "closure_shell"])].sum())
    fill_frac = float(area[labels == "hard_fill"].sum() / max(skin_total, 1e-9))
    ov = audit.overhangs(mesh)
    ov0 = audit.overhangs(shoe)
    thick = thickness_by_label(mesh, labels)
    geo = audit.geometry(mesh)
    parts = mesh.split(only_watertight=False)
    lap("audit", t0)

    # how far the final outer surface sits outside the template skin (push + fill + closure shell + collar rounding)
    outer = np.isin(labels, ["skin", "hard_fill", "closure_shell", "mixed"]) & (mesh.triangles_center[:, 2] > 1.0)
    ctr = mesh.triangles_center[outer]
    disp = sample(phi_e, grid, ctr)
    ao = area[outer]
    k = int(np.argmax(disp))
    xb = np.floor(ctr[:, 0] / 20.0).astype(int)
    displacement = {
        "max_mm": round(float(disp.max()), 2), "max_at": [round(float(v), 1) for v in ctr[k]],
        "p95_mm": round(float(np.percentile(disp, 95)), 2),
        "area_frac_over_0p5": round(float(ao[disp > 0.5].sum() / ao.sum()), 4),
        "area_frac_over_2": round(float(ao[disp > 2].sum() / ao.sum()), 4),
        "area_frac_over_4": round(float(ao[disp > 4].sum() / ao.sum()), 4),
        "by_x": [{"x_mm": float(b_ * 20), "max_mm": round(float(disp[xb == b_].max()), 2)} for b_ in np.unique(xb)],
    }
    # sole under the cavity: straight down from the floor faces (cavity faces looking up) to the bottom of the shoe
    fl = np.flatnonzero(cav_f & (mesh.face_normals[:, 2] > 0.9))
    fl = fl[:: max(1, len(fl) // 20000)]
    o = mesh.triangles_center[fl] - np.array([0.0, 0.0, 1e-3])
    loc, ray, _ = mesh.ray.intersects_location(o, np.tile([0.0, 0.0, -1.0], (len(o), 1)), multiple_hits=False)
    sole = o[ray, 2] - loc[:, 2]
    flat = loc[:, 2] < 1.0  # underside on the bed; elsewhere (toe spring) the "sole" is just the wall

    def sole_stats(v):
        return {"samples": int(len(v)), "min_mm": round(float(v.min()), 2), "p1_mm": round(float(np.percentile(v, 1)), 2),
                "median_mm": round(float(np.median(v)), 2), "frac_lt_6": round(float((v < a.sole).mean()), 4),
                "frac_lt_5p5": round(float((v < a.sole - 0.5).mean()), 4)}

    sole_report = {"on_bed": sole_stats(sole[flat]), "lifted_underside": sole_stats(sole[~flat]) if (~flat).any() else None}
    verdict = gate_verdict(comp.gate, fill_frac)
    final = {
        "shoe": a.shoe, "params": vars(a), "seconds": secs, "total_seconds": round(time.perf_counter() - t_all, 1),
        "peak_rss_mb": round(rss_mb()), "grid": {"shape": list(grid.shape), "h": grid.h, "dz": grid.dz, "voxels": grid.n_voxels},
        "pose": res.to_dict()["pose"], "pose_flags": res.flags, "floor_ease_mm": round(floor_ease, 3),
        "cavity": cav.report, "closure": cl.report, "compose": comp.report, "gate_metrics": comp.gate,
        "sole_under_cavity": sole_report, "verdict": verdict, "outer_displacement_vs_template": displacement,
        "export": {"components": erep.n_components, "dropped_volume_mm3": erep.dropped_volume_mm3, "faces": erep.n_faces,
                   "bodies_in_file": len(parts)},
        "geometry": geo,
        "face_area_by_label_mm2": lab_area,
        "hard_fill_area_frac_of_skin": round(fill_frac, 4),
        "cavity_overhang_gt45": {"area_mm2": round(float(area[steep].sum())), "frac_of_cavity_faces": round(float(area[steep].sum() / max(area[cav_f].sum(), 1e-9)), 5)},
        "overhangs_all": ov, "overhangs_template": ov0,
        "wall_thickness_by_label": thick,
        "bbox_mm": [round(float(v), 1) for v in mesh.extents],
        "bed_min_z": round(float(mesh.bounds[0, 2]), 3),
    }
    (out / "build_report.json").write_text(json.dumps(final, indent=1, ensure_ascii=False, default=float))
    print(json.dumps({k: final[k] for k in ("verdict", "gate_metrics", "geometry", "cavity_overhang_gt45", "hard_fill_area_frac_of_skin", "outer_displacement_vs_template")},
                     indent=1, ensure_ascii=False, default=float))
    print("wall:", json.dumps(thick["all"]), "| volumes", json.dumps(comp.report["volume_mm3"]))


if __name__ == "__main__":
    main()
