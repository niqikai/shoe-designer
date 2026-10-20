"""#14 B-knit shape-freedom demo: build the variants of config/lattice_variants.toml on the cavity of sneaker_last.

    SHOE_ASSETS_ROOT=~/Downloads uv run --extra fast --with matplotlib python scripts/lattice_variants.py --all
    ... --variant v4_sole [--stl]          one variant (own process: its own peak memory)
    ... --overview                         assemble reports/lattice/variants/overview.png from the per-variant renders

The main template pipeline is only used to place the last and find the collar line (as lattice_real.py); the body is the
cavity offset (see shoe_designer.lattice_variants). Per variant: three renders (side, top, oblique), variants_report.json
entry (volume, grams at the placeholder 1.2 g/cm3, printable flag, time, peak memory, checks). STLs only with --stl (hundreds
of MB). Output derives from restricted assets: reports/ is ignored by git.
"""
import argparse
import dataclasses
import json
import resource
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

from shoe_designer import lattice as L
from shoe_designer import lattice_variants as V
from shoe_designer.process import ProcessConfig
from shoe_designer.sdf import Grid

OUT = Path("reports/lattice/variants")
VIEWS = {"side": ((0.0, -1.0, 0.0), (0.0, 0.0, 1.0)), "top": ((0.0, 0.0, 1.0), (0.0, 1.0, 0.0)), "oblique": ((-0.5, -0.78, 0.55), (0.0, 0.0, 1.0))}
PX = 0.4  # mm per pixel


def rss_mb() -> float:
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return r / (1024 * 1024 if sys.platform == "darwin" else 1024)


def load_prep(a):
    cache = Path("reports/lattice/cache2.npz")
    if not cache.exists():
        raise SystemExit("reports/lattice/cache2.npz missing: run the prep (see scripts/lattice_variants.py --prep)")
    d = np.load(cache)
    grid = Grid(tuple(float(v) for v in d["origin"]), float(d["h"]), float(d["dz"]), d["c0"].shape)
    return V.prepare(d["c0"], d["zt"], grid)


def prep_cache(a):
    """Run the template pipeline once: place the last (pose), build the cavity, keep the unopened phi_C and the collar height."""
    from shoe_designer import ease, pose as pz
    from shoe_designer.assets import load_asset, load_catalog
    from shoe_designer.envelope import envelope

    proc = ProcessConfig.from_toml(a.process)
    cat = load_catalog()
    last = load_asset("sneaker_last", cat).mesh
    shoe = load_asset(a.shoe, cat).mesh
    h, b = 1.0, shoe.bounds
    step = max(h, Grid.dz_for(h, proc.alpha_b_deg))
    grid0 = Grid.from_bounds(b[0], b[1] + [0, 0, 45.0], h, proc.alpha_b_deg, margin=3 * step)
    env = envelope(shoe, h=h, grid=grid0, closing_radius=12.0, band=14.0)
    cfg = dataclasses.replace(ease.EaseConfig.from_toml(None), fdm_shrink=proc.shrink_mm)
    field = ease.EaseField.for_last(last, cfg)
    fe = float(field(np.array([[last.bounds.mean(axis=0)[0], 0.0, last.bounds[0, 2]]]), np.array([[0.0, 0.0, -1.0]]))[0])
    res = pz.fit_pose(env.phi, env.grid, env.mask_below(), last, ease=field, wall=proc.wall, s_min=proc.sole_min + fe)
    lb = last.bounds
    corners = np.array([[x, y, z] for x in lb[:, 0] for y in lb[:, 1] for z in lb[:, 2]])
    pc = corners @ res.transform[:3, :3].T + res.transform[:3, 3]
    grid = ease.extend_grid(env.grid, pc.min(axis=0) - 5, np.maximum(pc.max(axis=0), [0, 0, float(env.collar.rim[:, 2].max()) + 45.0]) + 5)
    cav = ease.build_cavity(last, res.transform, env.collar, grid, cfg)
    c0 = (cav.phi_last - cav.ease_grid).astype(np.float32)  # phi_C before the collar is opened
    zt = env.collar.z_t(ease._column_xy(grid)).reshape(grid.shape[:2])
    np.savez("reports/lattice/cache2.npz", c0=c0, zt=zt, origin=grid.origin, h=grid.h, dz=grid.dz)


# ------------------------------------------------------------------------------------------------ renders (vertex splatting)
def splat_views(phi, grid, stride=1, views=None):
    from skimage import measure

    sub = np.pad(phi[::stride, ::stride, ::stride], 1, constant_values=1.0)
    sp = (grid.h * stride, grid.h * stride, grid.dz * stride)
    verts, _, normals, _ = measure.marching_cubes(sub, level=0.0, spacing=sp)
    verts = verts + np.asarray(grid.origin) - np.asarray(sp)
    out = {}
    for name, (cam, up) in (views or VIEWS).items():
        cam, up = np.asarray(cam, float), np.asarray(up, float)
        cam /= np.linalg.norm(cam)
        right = np.cross(up, cam)
        right /= np.linalg.norm(right)
        upv = np.cross(cam, right)
        front = normals @ cam > 0.0
        v, n = verts[front], normals[front]
        sx, sy, depth = v @ right, v @ upv, v @ cam
        lam = np.clip(n @ (cam * 0.6 + upv * 0.5 + right * -0.3) / 1.0, 0, 1)
        col = (0.22 + 0.78 * lam).astype(np.float32)
        out[name] = (sx, sy, depth, col)
    return out


def rasterize(views, size=None):
    imgs = {}
    for name, (sx, sy, depth, col) in views.items():
        x0, y0 = sx.min() - 3, sy.min() - 3
        w = int(np.ceil((sx.max() - x0 + 3) / PX))
        h = int(np.ceil((sy.max() - y0 + 3) / PX))
        ix, iy = ((sx - x0) / PX).astype(int), ((sy - y0) / PX).astype(int)
        zb = np.full((h, w), -np.inf, np.float32)
        img = np.full((h, w), np.nan, np.float32)
        order = np.argsort(depth)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                yy, xx = np.clip(iy + dy, 0, h - 1), np.clip(ix + dx, 0, w - 1)
                lin = yy * w + xx
                np.maximum.at(zb.reshape(-1), lin, depth.astype(np.float32))
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                yy, xx = np.clip(iy + dy, 0, h - 1), np.clip(ix + dx, 0, w - 1)
                lin = yy * w + xx
                ok = depth.astype(np.float32) >= zb.reshape(-1)[lin] - 1e-4
                sel = order[ok[order]]
                img.reshape(-1)[lin[sel]] = col[sel]
        imgs[name] = np.flipud(img)
    return imgs


def save_png(img, path):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rgb = np.dstack([np.where(np.isnan(img), 1.0, img * 0.85 + 0.1)] * 3)
    plt.imsave(path, np.clip(rgb, 0, 1))


# ------------------------------------------------------------------------------------------------ one variant
def run_variant(a, name):
    specs = {s.name: s for s in V.load_variants(a.config)}
    v = specs[name]
    if a.voxel:
        v = dataclasses.replace(v, voxel_mm=a.voxel, preview=a.voxel > 0.3 or v.preview)
    proc = ProcessConfig.from_toml(a.process)
    cfg = L.LatticeConfig.from_toml(a.lattice)
    OUT.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    prep = load_prep(a)
    res = {"name": name, "description": v.description, "spec": dataclasses.asdict(v)}
    try:
        fine, fg, rep, aux = V.build_variant(prep, v, cfg, proc)
    except L.LatticeError as e:
        res.update({"status": "refused", "reason": str(e)})
        print(f"[{name}] REFUSED: {e}", flush=True)
        (OUT / f"{name}.json").write_text(json.dumps(res, indent=1, ensure_ascii=False, default=float))
        return
    t_build = time.perf_counter() - t0
    sealed_raw = L.sealed(fine, fg)
    safety = L.fill_sealed(fine, fg) if sealed_raw["count"] else {"filled_mm3": 0.0, "components": 0, "largest_voxels": 0}
    mesh, er = L.to_mesh(fine, fg, 200.0)
    checks = L.export_checks(mesh)
    vol = float(mesh.volume)
    res.update({"status": "ok", "form": rep, "build_s": round(t_build, 1), "faces": int(len(mesh.faces)), "volume_cm3": round(vol / 1000, 2),
                "grams_at_placeholder_density": round(vol / 1000 * cfg.tpu_density_g_cm3, 1), "extents_mm": [round(float(e), 1) for e in mesh.extents],
                "checks": checks, "sealed_raw": sealed_raw, "sealed_after": L.sealed(fine, fg), "safety_fill": safety,
                "export_dropped_mm3": round(er.dropped_volume_mm3, 2), "label": "printable (voxel <= strut/4)" if rep["printable"] else "preview (not printable)"})
    res["thin_material"] = L.thin_material(fine, fg, L.strut_min_mm(cfg, proc))
    if a.stl:
        mesh.export(OUT / f"{name}.stl")
    del mesh
    views = rasterize(splat_views(fine, fg))
    np.savez_compressed(OUT / f"{name}_views.npz", **{k: np.nan_to_num(v_, nan=-1.0) for k, v_ in views.items()})
    for k, img in views.items():
        save_png(img, OUT / f"{name}_{k}.png")
    res["total_s"] = round(time.perf_counter() - t0, 1)
    res["peak_rss_mb"] = round(rss_mb())
    (OUT / f"{name}.json").write_text(json.dumps(res, indent=1, ensure_ascii=False, default=float))
    print(f"[{name}] {res['label']}  {res['total_s']} s  vol {res['volume_cm3']} cm3  {res['grams_at_placeholder_density']} g  watertight={checks['watertight']} "
          f"bodies={checks['bodies']} inner={checks['inner_shells']['count']} sealed_raw={sealed_raw['count']} after={res['sealed_after']['count']} "
          f"yarn_d={rep['measured']['yarn_diameter_used_min_mm']}..{rep['measured']['yarn_diameter_used_max_mm']}  rss {res['peak_rss_mb']} MB", flush=True)


def overview(names):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    data = {n: np.load(OUT / f"{n}_views.npz") for n in names if (OUT / f"{n}_views.npz").exists()}
    names = list(data)
    cols = ["side", "top", "oblique"]
    ratio = [max(data[n][c].shape[1] for n in names) / max(data[n][c].shape[0] for n in names) for c in cols]
    fig, axs = plt.subplots(len(names), 3, figsize=(15, 3.1 * len(names)), gridspec_kw={"width_ratios": [1.9, 1.9, 1.5]})
    for i, n in enumerate(names):
        for j, c in enumerate(cols):
            img = data[n][c]
            ax = axs[i, j]
            rgb = np.dstack([np.where(img < 0, 1.0, img * 0.85 + 0.1)] * 3)
            ax.imshow(np.clip(rgb, 0, 1))
            ax.axis("off")
            if j == 0:
                ax.set_title(n, loc="left", fontsize=12, fontweight="bold")
    fig.tight_layout()
    fig.savefig(OUT / "overview.png", dpi=80)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--overview", action="store_true")
    ap.add_argument("--prep", action="store_true", help="build reports/lattice/cache2.npz from the assets (needs SHOE_ASSETS_ROOT)")
    ap.add_argument("--stl", action="store_true", help="also write the STL of the selected variant(s) (hundreds of MB)")
    ap.add_argument("--stl-only", default="", help="with --all: comma list of variants that get an STL")
    ap.add_argument("--voxel", type=float, help="override the voxel size; > 0.3 marks the variant 'preview (not printable)'")
    ap.add_argument("--config", default=None)
    ap.add_argument("--lattice", default=None)
    ap.add_argument("--process", default="config/process_sls_tpu.toml")
    ap.add_argument("--shoe", default="dlp_solid_44_right")
    a = ap.parse_args()
    if a.prep:
        prep_cache(a)
        return
    names = [s.name for s in V.load_variants(a.config)]
    if a.all:
        stl = set(filter(None, a.stl_only.split(",")))
        for n in names:
            cmd = [sys.executable, __file__, "--variant", n, "--process", a.process] + (["--stl"] if n in stl else []) + (["--voxel", str(a.voxel)] if a.voxel else [])
            subprocess.run(cmd, check=False)
        overview(names)
        return
    if a.overview:
        overview(names)
        return
    run_variant(a, a.variant)


if __name__ == "__main__":
    main()
