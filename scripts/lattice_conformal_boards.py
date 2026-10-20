"""#46: five-view boards of the three concept styles (flow chart, net sole), the three-style overview and the comparison with the #14 versions.

    uv run --with matplotlib python scripts/lattice_conformal_boards.py

Needs reports/lattice/conformal_shoe/k*_views.npz (lattice_conformal_shoe.py) and reports/lattice/styles/s*_views.npz (lattice_styles.py, the #14 versions).
Labels are English so the fonts do not turn into boxes. Local only (derived from restricted assets).
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from lattice_styles import rgb_of  # noqa: E402

NEW = Path("reports/lattice/conformal_shoe")
OLD = Path("reports/lattice/styles")
OUT = NEW / "boards"
STYLES = [("k1_honeycomb", "s1_honeycomb", "Style 1: honeycomb flow", (0.93, 0.93, 0.90)), ("k2_triangle", "s2_triangle", "Style 2: geometric triangles", (0.13, 0.13, 0.15)),
          ("k3_skeleton", "s3_skeleton", "Style 3: fluid skeleton", (0.62, 0.78, 0.66))]
V5 = ["oblique", "side", "top", "back", "bottom"]


def load(p):
    return np.load(p) if Path(p).exists() else None


def info(name, d):
    f = d["form"]
    s = f"{d['grams_at_placeholder_density']:.0f} g (1.2 g/cm3 placeholder), {d['volume_cm3']:.0f} cm3, voxel {f['voxel_mm']} mm, {d['label'].split(' ')[0]}"
    if "lscm" in f:
        s += f"\nchart {f['route']}: anisotropy p95 {f['lscm']['anisotropy_p95']:.2f}, area factor {f['lscm']['area_factor_p05']:.2f}-{f['lscm']['area_factor_p95']:.2f}"
    return s


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    reps = {}
    for kn, sn, title, tint in STYLES:
        d = json.load(open(NEW / f"{kn}.json"))
        reps[kn] = d
        v = load(NEW / f"{kn}_views.npz")
        fig, axs = plt.subplots(2, 3, figsize=(17, 9))
        fig.suptitle(f"{title}   [{kn}]   (preview render, not a photo)", fontsize=15, fontweight="bold")
        for ax, k in zip(axs.ravel(), V5):
            ax.imshow(rgb_of(v[k], tint))
            ax.set_title(k)
        axs.ravel()[-1].axis("off")
        axs.ravel()[-1].text(0.0, 0.9, info(kn, d), fontsize=11, va="top", family="monospace")
        for ax in axs.ravel():
            ax.axis("off")
        fig.tight_layout()
        fig.savefig(OUT / f"{kn}_board.png", dpi=85)
        plt.close(fig)
    # overview
    fig, axs = plt.subplots(3, 3, figsize=(16, 12))
    for i, (kn, sn, title, tint) in enumerate(STYLES):
        v = load(NEW / f"{kn}_views.npz")
        for j, k in enumerate(["oblique", "side", "bottom"]):
            axs[i, j].imshow(rgb_of(v[k], tint))
            axs[i, j].axis("off")
        axs[i, 0].set_title(f"{title}  {reps[kn]['grams_at_placeholder_density']:.0f} g", loc="left", fontsize=13, fontweight="bold")
    fig.tight_layout()
    fig.savefig(OUT / "overview_new.png", dpi=75)
    plt.close(fig)
    # old vs new
    for kn, sn, title, tint in STYLES:
        o, n = load(OLD / f"{sn}_views.npz"), load(NEW / f"{kn}_views.npz")
        if o is None:
            continue
        od = json.load(open(OLD / f"{sn}.json"))
        fig, axs = plt.subplots(2, 5, figsize=(22, 8.5))
        for r, (v, lab) in enumerate([(o, f"#14 version ({od['grams_at_placeholder_density']:.0f} g)"), (n, f"#46 version ({reps[kn]['grams_at_placeholder_density']:.0f} g)")]):
            for j, k in enumerate(V5):
                axs[r, j].imshow(rgb_of(v[k], tint))
                axs[r, j].axis("off")
                if r == 0:
                    axs[r, j].set_title(k)
            axs[r, 0].text(0.0, 1.02, lab, transform=axs[r, 0].transAxes, fontsize=13, fontweight="bold")
        fig.suptitle(title, fontsize=15, fontweight="bold")
        fig.tight_layout()
        fig.savefig(OUT / f"{kn}_vs_14.png", dpi=70)
        plt.close(fig)
    # all old vs new overview
    fig, axs = plt.subplots(3, 4, figsize=(20, 11))
    for i, (kn, sn, title, tint) in enumerate(STYLES):
        o, n = load(OLD / f"{sn}_views.npz"), load(NEW / f"{kn}_views.npz")
        for j, (v, k) in enumerate([(o, "oblique"), (o, "bottom"), (n, "oblique"), (n, "bottom")]):
            if v is not None:
                axs[i, j].imshow(rgb_of(v[k], tint))
            axs[i, j].axis("off")
        axs[i, 0].set_title(f"{title}: #14 (left two) vs #46 (right two)", loc="left", fontsize=12, fontweight="bold")
    fig.tight_layout()
    fig.savefig(OUT / "overview_14_vs_46.png", dpi=65)


if __name__ == "__main__":
    main()
