"""#46: whole-shoe builds of config/lattice_conformal.toml variants (name starts with w) on the cavity of sneaker_last.

    SHOE_ASSETS_ROOT=~/Downloads uv run --extra fast --with matplotlib python scripts/lattice_conformal_shoe.py --variant w1_flow [--stl]
    ... --board                 five-view boards and the comparison of the rendered variants

Needs reports/lattice/cache2.npz. Writes into reports/lattice/conformal_shoe/ (renders, JSON, STL only with --stl). Not for distribution.
"""
import argparse
import dataclasses
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lattice_variants as LV  # noqa: E402

from shoe_designer import lattice as L  # noqa: E402
from shoe_designer import lattice_conformal as C  # noqa: E402
from shoe_designer.process import ProcessConfig  # noqa: E402

OUT = Path("reports/lattice/conformal_shoe")
CONFIG = "config/lattice_conformal.toml"
CONFIG2 = "config/lattice_conformal_styles.toml"
VIEWS5 = {"side": ((0.0, -1.0, 0.0), (0.0, 0.0, 1.0)), "top": ((0.0, 0.0, 1.0), (0.0, 1.0, 0.0)), "back": ((-1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
          "oblique": ((-0.5, -0.78, 0.55), (0.0, 0.0, 1.0)), "bottom": ((0.0, 0.0, -1.0), (0.0, -1.0, 0.0)), "under": ((0.55, -0.6, -0.6), (0.0, 0.0, 1.0))}


def run(a, name):
    v = {s.name: s for s in (C.load_specs(CONFIG) + C.load_specs(CONFIG2))}[name]
    proc = ProcessConfig.from_toml(a.process)
    cfg = L.LatticeConfig.from_toml()
    OUT.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    prep = LV.load_prep(a)
    res = {"name": name, "description": v.description, "spec": dataclasses.asdict(v)}
    try:
        fine, fg, rep, aux = C.build_whole(prep, v, cfg, proc)
    except L.LatticeError as e:
        res.update({"status": "refused", "reason": str(e)})
        (OUT / f"{name}.json").write_text(json.dumps(res, indent=1, ensure_ascii=False, default=float))
        print(f"[{name}] REFUSED: {e}")
        return
    t_build = time.perf_counter() - t0
    rs = aux["rs"]
    region, band = C.yarn_zone_masks(prep, aux, v, fg, rs)
    solid = fine < 0
    rods = C.rod_diameters(solid, fg.h, region, target=v.yarn_mm)
    pores = C.shell_pores(solid, fg.h, band)
    sole_open = C.sole_cell_openings(prep, aux, aux['spec'], fg, rs, solid) if v.sole_mode == 'net' else None
    del solid, region, band
    raw = L.sealed(fine, fg)
    safety = L.fill_sealed(fine, fg) if raw["count"] else {"filled_mm3": 0.0, "components": 0, "largest_voxels": 0}
    mesh, er = L.to_mesh(fine, fg, 400.0)
    checks = L.export_checks(mesh)
    vol = float(mesh.volume)
    if a.stl:
        mesh.export(OUT / f"{name}.stl")
    res.update({"status": "ok", "form": rep, "build_s": round(t_build, 1), "faces": int(len(mesh.faces)), "volume_cm3": round(vol / 1000, 2),
                "grams_at_placeholder_density": round(vol / 1000 * cfg.tpu_density_g_cm3, 1), "extents_mm": [round(float(e), 1) for e in mesh.extents],
                "checks": checks, "sealed_raw": raw, "safety_fill": safety, "sealed_after": L.sealed(fine, fg),
                "rods_skeleton_mm": rods, "shell_windows_mm": pores, "sole_cells": sole_open, "label": "printable (voxel <= thinnest feature / 4)" if rep["printable"] else "preview (not printable)"})
    if not a.no_mesh_thickness:
        th = C.mesh_thickness(mesh, a.samples)
        d = th["d"]
        P3 = th["points"]
        from scipy import ndimage as ndi

        g = prep.grid
        c0p = ndi.map_coordinates(prep.c0, [(P3[:, 0] - g.origin[0]) / g.h, (P3[:, 1] - g.origin[1]) / g.h, (P3[:, 2] - g.origin[2]) / g.dz], order=1, mode="nearest")
        cx = [(P3[:, 0] - g.origin[0]) / g.h, (P3[:, 1] - g.origin[1]) / g.h]
        zfp, ztp = ndi.map_coordinates(aux["floor"], cx, order=1, mode="nearest"), ndi.map_coordinates(aux["zt_var"], cx, order=1, mode="nearest")
        inzone = (c0p > -0.5) & (c0p < v.thickness_mm + 0.5) & (P3[:, 2] > zfp + v.sole_margin_mm + 3) & (P3[:, 2] < ztp - v.rim_mm - 3)
        d = np.where(inzone, d, np.nan)  # only the yarn zone: the sole lattice struts are thinner by design
        ok = np.isfinite(d)
        res["mesh_thickness_mm"] = {"n": int(ok.sum()), "p01": float(np.nanpercentile(d, 1)), "p05": float(np.nanpercentile(d, 5)), "median": float(np.nanmedian(d)),
                                    "share_below_rod_minus_1_voxel": float((d[ok] < v.yarn_mm - fg.h).mean())}
        np.savez_compressed(OUT / f"{name}_thin.npz", pts=th["points"][ok & (d < v.yarn_mm - fg.h)], d=d[ok & (d < v.yarn_mm - fg.h)])
    del mesh
    views = LV.rasterize(LV.splat_views(fine, fg, 1, VIEWS5))
    np.savez_compressed(OUT / f"{name}_views.npz", **{k: np.nan_to_num(x, nan=-1.0) for k, x in views.items()})
    res["total_s"] = round(time.perf_counter() - t0, 1)
    res["peak_rss_mb"] = round(LV.rss_mb())
    (OUT / f"{name}.json").write_text(json.dumps(res, indent=1, ensure_ascii=False, default=float))
    print(f"[{name}] {res['label']} route={rep['route']} {res['total_s']} s vol {res['volume_cm3']} cm3 {res['grams_at_placeholder_density']} g "
          f"watertight={checks['watertight']} bodies={checks['bodies']} sealed_raw={raw['count']} after={res['sealed_after']['count']} rss {res['peak_rss_mb']} MB "
          f"rods={rods} mesh_thickness={res.get('mesh_thickness_mm')} cov={rep.get('coverage')}", flush=True)


def board(names):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    names = [n for n in names if (OUT / f"{n}_views.npz").exists()]
    data = {n: np.load(OUT / f"{n}_views.npz") for n in names}
    cols = ["oblique", "side", "top", "back", "bottom", "under"]
    fig, axs = plt.subplots(len(names), len(cols), figsize=(4.2 * len(cols), 3.2 * len(names)), squeeze=False)
    for i, n in enumerate(names):
        for j, c in enumerate(cols):
            im = data[n][c]
            axs[i, j].imshow(np.where(im < 0, 1.0, im * 0.85 + 0.1), cmap="gray", vmin=0, vmax=1)
            axs[i, j].axis("off")
            if i == 0:
                axs[i, j].set_title(c)
        axs[i, 0].text(0.0, 1.02, n, transform=axs[i, 0].transAxes, fontsize=12, fontweight="bold")
    fig.tight_layout()
    fig.savefig(OUT / "compare_" + "_".join(names) + ".png" if False else OUT / "compare.png", dpi=75)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant")
    ap.add_argument("--stl", action="store_true")
    ap.add_argument("--no-mesh-thickness", action="store_true")
    ap.add_argument("--samples", type=int, default=200000)
    ap.add_argument("--board", nargs="*")
    ap.add_argument("--process", default="config/process_sls_tpu.toml")
    a = ap.parse_args()
    if a.board is not None:
        board(a.board or [s.name for s in C.load_specs(CONFIG2)])
    else:
        run(a, a.variant)


if __name__ == "__main__":
    main()
