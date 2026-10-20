"""#46 checkpoint 1: conformal flow-aligned yarn pattern on a patch of the forefoot upper, next to the #14 engine.

    SHOE_ASSETS_ROOT=~/Downloads uv run --extra fast --with matplotlib python scripts/lattice_conformal_patch.py --all
    ... --variant c2_flow          one variant (own process: its own peak memory)
    ... --board                    assemble reports/lattice/conformal/compare.png from the per-variant renders

Needs reports/lattice/cache2.npz (scripts/lattice_variants.py --prep). The patch is the upper between ``--u0`` and ``--u1`` (fraction
of the foot length from the heel) on a 6 mm solid base; everything else is air. Per variant: renders, a JSON report with the
measured rod diameter (skeleton of the binary voxels), window sizes, chart distortion, time and peak memory. Nothing is distributed.
"""
import argparse
import dataclasses
import json
import sys
import time
from pathlib import Path

import numpy as np
from scipy import ndimage

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lattice_variants as LV  # noqa: E402

from shoe_designer import lattice as L  # noqa: E402
from shoe_designer import lattice_conformal as C  # noqa: E402
from shoe_designer import lattice_variants as V  # noqa: E402
from shoe_designer.lattice_upper import foot_frame  # noqa: E402
from shoe_designer.process import ProcessConfig  # noqa: E402

OUT = Path("reports/lattice/conformal")
CONFIG = "config/lattice_conformal.toml"
VIEWS = {"side": ((0.0, -1.0, 0.0), (0.0, 0.0, 1.0)), "top": ((0.0, 0.0, 1.0), (0.0, 1.0, 0.0)), "oblique": ((0.5, -0.78, 0.55), (0.0, 0.0, 1.0)),
         "front": ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0))}


def run(a, name):
    specs = {s.name: s for s in C.load_specs(CONFIG)}
    v = specs[name]
    proc = ProcessConfig.from_toml(a.process)
    cfg = L.LatticeConfig.from_toml()
    OUT.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    prep = LV.load_prep(a)
    chk = V.check_variant(v, cfg, proc)
    body, aux = V.shell_body_v(prep, v)
    g = prep.grid
    xs_c = g.origin[0] + np.arange(g.shape[0]) * g.h
    ax = prep.axis
    uu = L.u_of_x(xs_c, ax)
    body[(uu < a.u0) | (uu > a.u1), :, :] = 1.0  # air outside the patch
    frame = foot_frame(prep.c0, prep.zt, g, v.pitch_mm)
    chart = C.FlowChart(prep, v, frame) if v.conformal else None
    t_chart = time.perf_counter() - t0
    holder = {}

    def layers(rs, fg):
        holder["rs"] = rs
        holder["knit"] = C.ConformalKnit(prep, v, aux, rs, frame, None, chart) if v.conformal else V.KnitLayer2(prep, v, aux, rs, frame, None)
        return [holder["knit"]]

    fine, fg, stats = L.build_fine(body, g, layers, v.voxel_mm, cfg.pad_mm)
    t_build = time.perf_counter() - t0
    rs, knit = holder["rs"], holder["knit"]
    # --- measurements on the fine grid
    h = fg.h
    solid = fine < 0
    c0f = np.empty(fine.shape, np.float32)
    for ix in range(fine.shape[0]):
        c0f[ix] = rs.plane(prep.c0, ix)
    X2, Y2 = np.meshgrid(rs.x, rs.y, indexing="ij")
    ci = [(X2 - g.origin[0]) / g.h, (Y2 - g.origin[1]) / g.h]
    zf = ndimage.map_coordinates(aux["floor"], ci, order=1, mode="nearest")
    zt = ndimage.map_coordinates(aux["zt_var"], ci, order=1, mode="nearest")
    z = rs.z[None, None, :]
    xu = L.u_of_x(rs.x, ax)[:, None, None]
    region = ((z > (zf + v.sole_margin_mm + 3.0)[:, :, None]) & (z < (zt - v.rim_mm - 3.0)[:, :, None]) & (c0f > 0) & (c0f < v.thickness_mm)
              & (xu > a.u0 + 0.04) & (xu < a.u1 - 0.04))
    rods = C.rod_diameters(solid, h, region, target=v.yarn_mm)
    if a.debug:
        from skimage.morphology import skeletonize

        edt = ndimage.distance_transform_edt(solid)
        sk = skeletonize(solid) & region
        dd = 2.0 * ndimage.maximum_filter(edt, size=3) * h
        thin = sk & (dd < v.yarn_mm - h)
        ii = np.argwhere(thin)
        zrel = (rs.z[ii[:, 2]] - zf[ii[:, 0], ii[:, 1]]) / np.maximum(zt[ii[:, 0], ii[:, 1]] - zf[ii[:, 0], ii[:, 1]], 1)
        off = c0f[thin] - 0.5 * v.thickness_mm
        print("thin n", len(ii), "zrel pct", np.percentile(zrel, [5, 25, 50, 75, 95]).round(2), "c0-T/2 pct", np.percentile(off, [5, 50, 95]).round(2),
              "thin d pct", np.percentile(dd[thin], [5, 50, 95]).round(2))
        allsk = np.argwhere(sk)
        zr_all = (rs.z[allsk[:, 2]] - zf[allsk[:, 0], allsk[:, 1]]) / np.maximum(zt[allsk[:, 0], allsk[:, 1]] - zf[allsk[:, 0], allsk[:, 1]], 1)
        print("all skeleton zrel pct", np.percentile(zr_all, [5, 25, 50, 75, 95]).round(2), "c0-T/2 pct all", np.percentile(c0f[sk] - 0.5 * v.thickness_mm, [5, 50, 95]).round(2))
    band = region & (np.abs(c0f - 0.5 * v.thickness_mm) < 0.75 * h)
    pores = C.shell_pores(solid, h, band)
    sealed = L.sealed(fine, fg)
    # --- chart distortion sigma
    xs = np.arange(ax["x_min"] + a.u0 * (ax["x_max"] - ax["x_min"]), ax["x_min"] + a.u1 * (ax["x_max"] - ax["x_min"]), 2.0) if ax["toe"] == "+x" else \
        np.arange(ax["x_max"] - a.u1 * (ax["x_max"] - ax["x_min"]), ax["x_max"] - a.u0 * (ax["x_max"] - ax["x_min"]), 2.0)
    th = np.linspace(-np.pi, np.pi, 192, endpoint=False)
    P = C.midsurface_points(prep, frame, xs, th, 0.5 * v.thickness_mm)
    r0 = frame["r0"]
    Jc = np.zeros((len(xs), len(th), 2, 2))
    if v.conformal:
        for i, xv in enumerate(xs):
            pp, p_th, p_x, tt, t_th, t_x = chart.pt_of(float(xv), th)
            _, _, psi_p, psi_t, phi_p, phi_t = chart.eval(pp, tt)
            # c = (phi, psi)(p, t); p = p(x, theta), t = t(x, theta)
            sa, ca = np.sin(chart.alpha_ref), np.cos(chart.alpha_ref)  # unit-speed orthogonal coordinates S = phi + psi sa, N = psi ca
            dS_p, dS_t, dN_p, dN_t = phi_p + sa * psi_p, phi_t + sa * psi_t, ca * psi_p, ca * psi_t
            Jc[i, :, 0, 0] = dS_p * p_x + dS_t * t_x
            Jc[i, :, 0, 1] = dS_p * p_th + dS_t * t_th
            Jc[i, :, 1, 0] = dN_p * p_x + dN_t * t_x
            Jc[i, :, 1, 1] = dN_p * p_th + dN_t * t_th
    else:  # #14 engine: c = (p = r0 theta, x)
        Jc[:, :, 0, 1] = r0
        Jc[:, :, 1, 0] = 1.0
    dist = C.chart_distortion(P, xs, th, Jc)
    zq = P[..., 2]
    zfl = ndimage.map_coordinates(aux["floor"], [(P[..., 0] - g.origin[0]) / g.h, (P[..., 1] - g.origin[1]) / g.h], order=1, mode="nearest")
    ztl = ndimage.map_coordinates(aux["zt_var"], [(P[..., 0] - g.origin[0]) / g.h, (P[..., 1] - g.origin[1]) / g.h], order=1, mode="nearest")
    ok = np.isfinite(zq) & (zq > zfl + v.sole_margin_mm + 3) & (zq < ztl - v.rim_mm - 3)
    ok[:2], ok[-2:] = False, False
    s1, s2 = dist["s1"][ok], dist["s2"][ok]
    aniso = s1 / np.maximum(s2, 1e-9)
    size = np.sqrt(s1 * s2)
    sig = {"anisotropy_median": float(np.median(aniso)), "anisotropy_p95": float(np.percentile(aniso, 95)), "anisotropy_max": float(aniso.max()),
           "size_median_mm_per_cell_unit": float(np.median(size)), "size_p05_over_median": float(np.percentile(size, 5) / np.median(size)),
           "size_p95_over_median": float(np.percentile(size, 95) / np.median(size)), "n": int(ok.sum())}
    res = {"name": name, "description": v.description, "check": chk, "label": "printable (voxel <= rod / 4)" if chk["printable"] else "preview (not printable)",
           "voxel_mm": v.voxel_mm, "rods_mm": rods, "rod_set_mm": v.yarn_mm, "pores_mm": pores, "sealed_cavities": sealed["count"], "sigma": sig,
           "chart_s": round(t_chart, 1), "build_s": round(t_build, 1), "fine_shape": list(fine.shape), "volume_cm3": round(stats.vol_final_mm3 / 1000, 2),
           "frame": {"n_around": frame["n_around"], "r0": round(frame["r0"], 2)}, "yarn_diameter_used_mm": [round(knit.r_min, 3), round(knit.r_max, 3)]}
    if chart is not None:
        res["flow"] = {"psi_p_range": [round(x, 3) for x in chart.psi_p_range], "phi_range_mm": [round(x, 1) for x in chart.phi_range]}
    views = LV.rasterize(LV.splat_views(fine, fg, 1, VIEWS))
    np.savez_compressed(OUT / f"{name}_views.npz", **{k: np.nan_to_num(x, nan=-1.0) for k, x in views.items()})
    res["total_s"] = round(time.perf_counter() - t0, 1)
    res["peak_rss_mb"] = round(LV.rss_mb())
    (OUT / f"{name}.json").write_text(json.dumps(res, indent=1, ensure_ascii=False, default=float))
    print(json.dumps({k: res[k] for k in ("name", "label", "rods_mm", "pores_mm", "sigma", "total_s", "peak_rss_mb", "sealed_cavities")}, default=float), flush=True)


def board():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    names = [s.name for s in C.load_specs(CONFIG) if (OUT / f"{s.name}_views.npz").exists()]
    data = {n: np.load(OUT / f"{n}_views.npz") for n in names}
    cols = ["oblique", "side", "top", "front"]
    fig, axs = plt.subplots(len(names), len(cols), figsize=(4.5 * len(cols), 3.8 * len(names)))
    for i, n in enumerate(names):
        for j, c in enumerate(cols):
            im = data[n][c]
            axs[i, j].imshow(np.where(im < 0, 1.0, im * 0.85 + 0.1), cmap="gray", vmin=0, vmax=1)
            axs[i, j].axis("off")
            if i == 0:
                axs[i, j].set_title(c)
        axs[i, 0].text(0.0, 1.02, n, transform=axs[i, 0].transAxes, fontsize=12, fontweight="bold")
    fig.tight_layout()
    fig.savefig(OUT / "compare.png", dpi=80)


def main():
    import subprocess

    ap = argparse.ArgumentParser()
    ap.add_argument("--variant")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--board", action="store_true")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--u0", type=float, default=0.45)
    ap.add_argument("--u1", type=float, default=0.75)
    ap.add_argument("--process", default="config/process_sls_tpu.toml")
    a = ap.parse_args()
    if a.all:
        for s in C.load_specs(CONFIG):
            subprocess.run([sys.executable, __file__, "--variant", s.name, "--u0", str(a.u0), "--u1", str(a.u1)], check=False)
        board()
    elif a.board:
        board()
    else:
        run(a, a.variant)


if __name__ == "__main__":
    main()
