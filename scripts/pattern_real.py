"""#13 on real assets: compose phi_shoe once (as build_real.py, SLS/MJF TPU: closure off), then apply patterns.

    SHOE_ASSETS_ROOT=~/Downloads uv run --extra fast --with matplotlib python scripts/pattern_real.py \\
        [--shoe dlp_solid_44_right] [--process config/process_sls_tpu.toml] [--h 1.0] [--out-dir reports/pattern] \\
        [--image my.png] [--only hex,voronoi,wave,image]

Writes baseline.stl and one STL per pattern, a side and a top render of each (shaded depth maps of the field),
pattern_report.json with the audit numbers. The output is derived from CC BY-NC-ND / unknown-source assets:
keep it out of git (reports/ is ignored). Without --image a test picture (rings, bars, a star) is drawn.
"""
import argparse
import dataclasses
import json
import time
from pathlib import Path

import numpy as np
import trimesh

from shoe_designer import audit, ease, pose as pz
from shoe_designer.assets import load_asset, load_catalog
from shoe_designer.compose import compose
from shoe_designer.envelope import envelope
from shoe_designer.pattern import PatternConfig, PatternMasks, PatternSpec, apply_pattern, export_checks
from shoe_designer.process import ProcessConfig, mesh_inner_shells, sealed_cavities
from shoe_designer.sdf import Grid, sdf_to_mesh
from shoe_designer.support_closure import closure

PATTERNS = {
    "hex": {"kind": "hex_perforation", "scale_mm": 10, "open_ratio": 0.35},
    "voronoi": {"kind": "voronoi_perforation", "scale_mm": 12, "open_ratio": 0.35, "seed": 7},
    "wave": {"kind": "wave_displacement", "scale_mm": 12, "depth_mm": 1.0, "seed": 3},
    "image": {"kind": "image", "image_use": "perforation", "zones": ["side"]},
}


def make_test_image(path: Path):
    """White canvas 3:1, black features >= 8 mm at ~250 mm width: ring (island inside), two bars, a star."""
    from PIL import Image, ImageDraw

    w, h = 900, 300
    im = Image.new("L", (w, h), 255)
    d = ImageDraw.Draw(im)
    d.ellipse((40, 40, 260, 260), fill=0)
    d.ellipse((100, 100, 200, 200), fill=255)  # ring: the inner disc is an island -> becomes part of the hole
    d.rectangle((310, 60, 400, 240), fill=0)
    d.rectangle((440, 60, 530, 240), fill=0)
    cx, cy, r1, r2 = 700, 150, 120, 50
    pts = [(cx + (r1 if i % 2 == 0 else r2) * np.sin(i * np.pi / 5), cy - (r1 if i % 2 == 0 else r2) * np.cos(i * np.pi / 5)) for i in range(10)]
    d.polygon(pts, fill=0)
    im.save(path)
    return str(path)


def compose_shoe(a, proc):
    cat = load_catalog()
    last = load_asset("sneaker_last", cat).mesh
    shoe = load_asset(a.shoe, cat).mesh
    b = shoe.bounds
    step = max(a.h, Grid.dz_for(a.h, proc.alpha_b_deg))
    grid0 = Grid.from_bounds(b[0], b[1] + [0, 0, 45.0], a.h, proc.alpha_b_deg, margin=3 * step)
    env = envelope(shoe, h=a.h, grid=grid0, closing_radius=12.0, band=14.0)
    ease_cfg = dataclasses.replace(ease.EaseConfig.from_toml(None), fdm_shrink=proc.shrink_mm)
    field = ease.EaseField.for_last(last, ease_cfg)
    floor_ease = float(field(np.array([[last.bounds.mean(axis=0)[0], 0.0, last.bounds[0, 2]]]), np.array([[0.0, 0.0, -1.0]]))[0])
    res = pz.fit_pose(env.phi, env.grid, env.mask_below(), last, ease=field, wall=proc.wall, s_min=proc.sole_min + floor_ease)
    lb = last.bounds
    corners = np.array([[x, y, z] for x in lb[:, 0] for y in lb[:, 1] for z in lb[:, 2]])
    pc = corners @ res.transform[:3, :3].T + res.transform[:3, 3]
    zt_max = float(env.collar.rim[:, 2].max())
    grid = ease.extend_grid(env.grid, pc.min(axis=0) - 5, np.maximum(pc.max(axis=0), [0, 0, zt_max + 45.0]) + 5)
    cav = ease.build_cavity(last, res.transform, env.collar, grid, ease_cfg)
    off = np.rint((np.asarray(env.grid.origin) - np.asarray(grid.origin)) / np.asarray(grid.spacing)).astype(int)
    phi_e = np.full(grid.shape, 14.0, np.float32)
    phi_e[tuple(slice(o, o + n) for o, n in zip(off, env.grid.shape))] = env.phi
    cl = closure(cav.phi, grid, t=proc.wall, phi_e=phi_e, region=env.collar.mask_below(grid)) if proc.closure else None
    comp = compose(phi_e, cav.phi, cl.phi_a if cl else None, cl.phi_c_star if cl else None, grid, env.collar, wall=proc.wall,
                   t_fill=proc.t_fill, sole_min=proc.sole_min, band_mm=14.0, bed_cut=proc.bed_cut)
    return comp, (cl.phi_c_star if cl else cav.phi), env.collar


def depth_map(phi, grid, axis, from_positive=False):
    """Depth of the first solid voxel along ``axis`` (sub-voxel), as a masked array and the spacing of the other two axes."""
    solid = phi < 0
    if from_positive:
        phi, solid = np.flip(phi, axis), np.flip(solid, axis)
    first = solid.argmax(axis=axis)
    has = solid.any(axis=axis)
    idx = np.clip(first, 1, phi.shape[axis] - 1)
    ph_s = np.take_along_axis(phi, np.expand_dims(idx, axis), axis).squeeze(axis)
    ph_a = np.take_along_axis(phi, np.expand_dims(idx - 1, axis), axis).squeeze(axis)
    t = ph_a / np.maximum(ph_a - ph_s, 1e-6)
    depth = (idx - 1 + np.clip(t, 0, 1)) * grid.spacing[axis]
    return np.where(has, depth, np.nan)


def shade(depth, sp):
    gy, gx = np.gradient(np.nan_to_num(depth, nan=np.nanmax(depth)), *sp)
    n = np.stack([-gx, -gy, np.ones_like(gx)])
    n /= np.linalg.norm(n, axis=0)
    light = np.array([-0.4, 0.5, 0.75])
    light /= np.linalg.norm(light)
    lam = np.clip((n * light[:, None, None]).sum(axis=0), 0, 1)
    d = (depth - np.nanmin(depth)) / max(np.nanmax(depth) - np.nanmin(depth), 1e-9)
    return np.where(np.isnan(depth), np.nan, 0.25 + 0.6 * lam + 0.15 * (1 - d))


def render(phi, grid, title, path):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    side = shade(depth_map(phi, grid, 1), (grid.dz, grid.h))  # looking along +y from the -y side: image axes (x, z)
    top = shade(depth_map(phi, grid, 2, from_positive=True), (grid.h, grid.h))  # from above: axes (x, y)
    fig, axs = plt.subplots(2, 1, figsize=(14, 11), gridspec_kw={"height_ratios": [1.0, 1.15]})
    ex = [grid.origin[0], grid.origin[0] + grid.shape[0] * grid.h]
    axs[0].imshow(side.T, origin="lower", cmap="gray", vmin=0, vmax=1, extent=[*ex, grid.origin[2], grid.origin[2] + grid.shape[2] * grid.dz])
    axs[1].imshow(top.T, origin="lower", cmap="gray", vmin=0, vmax=1, extent=[*ex, grid.origin[1], grid.origin[1] + grid.shape[1] * grid.h])
    axs[0].set_title(f"{title}: side view (from -y)")
    axs[1].set_title("top view")
    for ax in axs:
        ax.set_aspect("equal")
        ax.set_facecolor("white")
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def audit_mesh(mesh, base_vol):
    th = audit.wall_thickness(mesh, 20000)
    return {"geometry": audit.geometry(mesh), "volume_cm3": round(float(mesh.volume) / 1000, 2),
            "volume_delta_cm3": round(float(mesh.volume - base_vol) / 1000, 2),
            "volume_delta_pct": round(float((mesh.volume - base_vol) / base_vol * 100), 1),
            "wall_thickness": th, "checks": export_checks(mesh), "extents_mm": [round(float(e), 1) for e in mesh.extents]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shoe", default="dlp_solid_44_right")
    ap.add_argument("--process", default="config/process_sls_tpu.toml")
    ap.add_argument("--h", type=float, default=1.0)
    ap.add_argument("--image")
    ap.add_argument("--wall-max", type=float, help="override perf_wall_max_mm (thickest wall that may be perforated)")
    ap.add_argument("--only", default=",".join(PATTERNS))
    ap.add_argument("--out-dir", default="reports/pattern")
    a = ap.parse_args()
    proc = ProcessConfig.from_toml(a.process)
    cfg = PatternConfig.from_toml()
    if a.wall_max:
        cfg = dataclasses.replace(cfg, perf_wall_max_mm=a.wall_max)
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    comp, phi_cav, collar = compose_shoe(a, proc)
    print(f"[compose] {time.perf_counter() - t0:.0f} s", flush=True)
    grid, phi = comp.grid, comp.phi
    masks = PatternMasks.from_composed(comp, phi_cav, collar, proc)
    base_mesh, _ = sdf_to_mesh(phi, grid)
    base_mesh.export(out / "baseline.stl")
    base_vol = float(base_mesh.volume)
    report = {"process": proc.name, "grid": {"shape": list(grid.shape), "h": grid.h}, "pattern_config": dataclasses.asdict(cfg),
              "baseline": audit_mesh(base_mesh, base_vol), "patterns": {}}
    report["baseline"]["sealed_field"] = sealed_cavities(phi, grid)
    print("baseline", json.dumps({k: report["baseline"][k] for k in ("volume_cm3", "extents_mm")}), flush=True)
    render(phi, grid, "baseline (no pattern)", out / "baseline.png")
    for name in a.only.split(","):
        raw = dict(PATTERNS[name])
        if name == "image":
            raw["image"] = a.image or make_test_image(out / "test_image.png")
        spec = PatternSpec.from_dict(raw)
        t0 = time.perf_counter()
        new, rep = apply_pattern(phi, grid, spec, masks, proc, cfg)
        mesh, erep = sdf_to_mesh(new, grid)
        mesh.export(out / f"{name}.stl")
        res = audit_mesh(mesh, base_vol)
        res["pattern"] = rep
        res["seconds"] = round(time.perf_counter() - t0, 1)
        report["patterns"][name] = res
        render(new, grid, name, out / f"{name}.png")
        wt = res["wall_thickness"]
        print(f"[{name}] {res['seconds']} s  vol {res['volume_cm3']} cm3 ({res['volume_delta_cm3']:+} cm3, {res['volume_delta_pct']:+}%)  "
              f"watertight={res['geometry']['watertight']} bodies={res['geometry']['bodies']} inner_shells={res['checks']['inner_shells']['count']} "
              f"sealed={rep['sealed_after']['count']} thin<1.2={wt['frac_thin']} min={wt['min_mm']}  "
              f"open_measured={rep.get('open_ratio_measured')} amp={rep.get('amplitude_max_mm')} cavity_invaded={rep['cavity_invaded_voxels']} "
              f"clamped={rep['clamped']} adj={rep['adjustments']}", flush=True)
    (out / "pattern_report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False, default=float))


if __name__ == "__main__":
    main()
