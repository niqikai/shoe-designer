"""#14: three looks after a concept board, built with the B-knit engine on sneaker_last (config/lattice_styles.toml).

    SHOE_ASSETS_ROOT=~/Downloads uv run --extra fast --with matplotlib python scripts/lattice_styles.py --all [--stl s1_honeycomb]
    ... --style s2_triangle        one style (own process, own peak memory)
    ... --overview                 per-style five-view boards and the side-by-side overview

Needs reports/lattice/cache2.npz (scripts/lattice_variants.py --prep). Writes into reports/lattice/styles/: per style the
five renders (side, top, back, oblique, bottom), a board, a JSON report; STL only for --stl. Not for distribution.
"""
import argparse
import dataclasses
import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lattice_variants as LV  # noqa: E402

from shoe_designer import lattice as L  # noqa: E402
from shoe_designer import lattice_variants as V  # noqa: E402
from shoe_designer.process import ProcessConfig  # noqa: E402

OUT = Path("reports/lattice/styles")
CONFIG = "config/lattice_styles.toml"
VIEWS5 = {"side": ((0.0, -1.0, 0.0), (0.0, 0.0, 1.0)), "top": ((0.0, 0.0, 1.0), (0.0, 1.0, 0.0)), "back": ((-1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
          "oblique": ((-0.5, -0.78, 0.55), (0.0, 0.0, 1.0)), "bottom": ((0.0, 0.0, -1.0), (0.0, -1.0, 0.0))}
TINT = {"s1_honeycomb": (0.93, 0.93, 0.90), "s2_triangle": (0.13, 0.13, 0.15), "s3_skeleton": (0.62, 0.78, 0.66)}
TITLE = {"s1_honeycomb": "Style 1: honeycomb flow", "s2_triangle": "Style 2: geometric triangles", "s3_skeleton": "Style 3: fluid skeleton"}


def rgb_of(img, tint):
    base = np.array(tint)
    shade = np.where(img < 0, 1.0, img)[..., None]
    out = np.clip(base * (0.35 + 0.9 * shade) if tint[0] < 0.3 else base * (0.45 + 0.6 * shade), 0, 1)
    out = np.where((img < 0)[..., None], 1.0, out)
    if tint[0] < 0.3:  # black rods: lift the shading so the form reads
        out = np.where((img < 0)[..., None], 1.0, np.clip(0.06 + 0.5 * shade * np.ones(3), 0, 1))
    return out


def run_style(a, name):
    specs = {s.name: s for s in V.load_variants(CONFIG)}
    v = specs[name]
    proc = ProcessConfig.from_toml(a.process)
    cfg = L.LatticeConfig.from_toml()
    OUT.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    prep = LV.load_prep(a)
    res = {"name": name, "description": v.description, "spec": dataclasses.asdict(v)}
    try:
        fine, fg, rep, aux = V.build_variant(prep, v, cfg, proc)
    except L.LatticeError as e:
        res.update({"status": "refused", "reason": str(e)})
        (OUT / f"{name}.json").write_text(json.dumps(res, indent=1, ensure_ascii=False, default=float))
        print(f"[{name}] REFUSED: {e}")
        return
    t_build = time.perf_counter() - t0
    raw = L.sealed(fine, fg)
    safety = L.fill_sealed(fine, fg) if raw["count"] else {"filled_mm3": 0.0, "components": 0, "largest_voxels": 0}
    mesh, er = L.to_mesh(fine, fg, 400.0)
    checks = L.export_checks(mesh)
    vol = float(mesh.volume)
    if a.stl:
        mesh.export(OUT / f"{name}.stl")
    # open-air hole sizes of the whole shoe: largest ball that fits in the air between the rods (outside the cavity), per mid-surface
    open_ = V_openings(fine, fg, prep, rep, v, aux, cfg)
    es = V.effective_sole(v, cfg)
    res.update({"status": "ok", "form": rep, "build_s": round(t_build, 1), "faces": int(len(mesh.faces)), "volume_cm3": round(vol / 1000, 2),
                "grams_at_placeholder_density": round(vol / 1000 * cfg.tpu_density_g_cm3, 1), "extents_mm": [round(float(e), 1) for e in mesh.extents],
                "checks": checks, "sealed_raw": raw, "safety_fill": safety, "sealed_after": L.sealed(fine, fg), "export_dropped_mm3": round(er.dropped_volume_mm3, 2),
                "label": "printable (voxel <= thinnest feature / 4)" if rep["printable"] else "preview (not printable)", "sole_effective": dataclasses.asdict(es),
                "shell_openings": open_})
    del mesh
    res["thin_material"] = L.thin_material(fine, fg, L.strut_min_mm(cfg, proc))
    views = LV.rasterize(LV.splat_views(fine, fg, 1 if v.voxel_mm <= 0.6 else 1, VIEWS5))
    np.savez_compressed(OUT / f"{name}_views.npz", **{k: np.nan_to_num(x, nan=-1.0) for k, x in views.items()})
    res["total_s"] = round(time.perf_counter() - t0, 1)
    res["peak_rss_mb"] = round(LV.rss_mb())
    (OUT / f"{name}.json").write_text(json.dumps(res, indent=1, ensure_ascii=False, default=float))
    print(f"[{name}] {res['label']} {res['total_s']} s vol {res['volume_cm3']} cm3 {res['grams_at_placeholder_density']} g watertight={checks['watertight']} "
          f"bodies={checks['bodies']} inner={checks['inner_shells']['count']} sealed_raw={raw['count']} after={res['sealed_after']['count']} "
          f"openings={open_} rss {res['peak_rss_mb']} MB", flush=True)


def V_openings(fine, fg, prep, rep, v, aux, cfg):
    """Hole sizes through the shell: reuse lattice_upper.shell_openings with this variant's collar / floor / thickness."""
    from shoe_designer.lattice_upper import UpperConfig, shell_openings

    u = UpperConfig(rim_mm=v.rim_mm, sole_margin_mm=v.sole_margin_mm)
    return shell_openings(fine, fg, prep.c0, prep.grid, aux["zt_var"], aux["floor"], u, v.thickness_mm, chunk=max(24, int(96 * 0.3 / fg.h)),
                          halo_mm=12.0 + v.thickness_mm, min_open_mm=cfg.min_opening_mm)


def boards():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    names = [n for n in TINT if (OUT / f"{n}_views.npz").exists()]
    data = {n: np.load(OUT / f"{n}_views.npz") for n in names}
    for n in names:
        fig, axs = plt.subplots(2, 3, figsize=(16, 8.5))
        fig.suptitle(TITLE[n], fontsize=15, fontweight="bold")
        for ax, k in zip(axs.ravel(), ["oblique", "side", "top", "back", "bottom"]):
            ax.imshow(rgb_of(data[n][k], TINT[n]))
            ax.set_title(k)
        axs.ravel()[-1].axis("off")
        for ax in axs.ravel():
            ax.axis("off")
        fig.tight_layout()
        fig.savefig(OUT / f"{n}_board.png", dpi=90)
        plt.close(fig)
    fig, axs = plt.subplots(len(names), 3, figsize=(16, 4.4 * len(names)))
    for i, n in enumerate(names):
        for j, k in enumerate(["oblique", "side", "bottom"]):
            axs[i, j].imshow(rgb_of(data[n][k], TINT[n]))
            axs[i, j].axis("off")
        axs[i, 0].set_title(TITLE[n], loc="left", fontsize=13, fontweight="bold")
    fig.tight_layout()
    fig.savefig(OUT / "overview_styles.png", dpi=80)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--style")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--overview", action="store_true")
    ap.add_argument("--stl", nargs="?", const="all", default="", help="write the STL of the selected style(s) (name or 'all')")
    ap.add_argument("--process", default="config/process_sls_tpu.toml")
    ap.add_argument("--config", default=CONFIG)
    a = ap.parse_args()
    names = [s.name for s in V.load_variants(CONFIG)]
    if a.all:
        for n in names:
            subprocess.run([sys.executable, __file__, "--style", n] + (["--stl"] if a.stl in ("all", n) else []), check=False)
        boards()
    elif a.overview:
        boards()
    else:
        a.stl = bool(a.stl)
        run_style(a, a.style)


if __name__ == "__main__":
    main()
