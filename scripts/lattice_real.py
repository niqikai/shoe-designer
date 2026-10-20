"""#14 on real assets: compose phi_shoe once (as pattern_real.py: SLS/MJF TPU, closure off), then build the lattices.

    SHOE_ASSETS_ROOT=~/Downloads uv run --extra fast --with matplotlib python scripts/lattice_real.py \\
        [--shoe dlp_solid_44_right] [--process config/process_sls_tpu.toml] [--lattice config/lattice.toml] \\
        [--only sole,knit,volume] [--out-dir reports/lattice] [--cell 6] [--kind bcc] [--no-cache]

Writes baseline.stl and one STL per form (sole.stl, knit.stl, volume.stl), side / bottom / section renders of each and
lattice_report.json (volumes, saved cm3 and grams at the placeholder TPU density, watertight / bodies / sealed cavities /
thin-material share, time, peak memory). The output is derived from CC BY-NC-ND / unknown-source assets: keep it out of
git (reports/ is ignored). The composed fields are cached in reports/lattice/cache.npz (compose takes ~25 s).
"""
import argparse
import dataclasses
import json
import resource
import sys
import time
import types
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from shoe_designer import lattice as L  # noqa: E402
from shoe_designer.process import ProcessConfig  # noqa: E402
from shoe_designer.sdf import Grid, sdf_to_mesh  # noqa: E402

PERF_BASE_CM3 = 685.75  # baseline mesh volume of dlp_solid_44_right + sneaker_last (SLS config, h = 1.0)


def rss_mb() -> float:
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return r / (1024 * 1024 if sys.platform == "darwin" else 1024)


def load_composed(a, proc):
    cache = Path(a.out_dir) / "cache.npz"
    if cache.exists() and not a.no_cache:
        d = np.load(cache)
        grid = Grid(tuple(float(v) for v in d["origin"]), float(d["h"]), float(d["dz"]), d["phi"].shape)
        return d["phi"], d["cav"], d["zt"], grid
    from pattern_real import compose_shoe
    from shoe_designer.ease import _column_xy

    comp, phi_cav, collar = compose_shoe(types.SimpleNamespace(shoe=a.shoe, h=a.h), proc)
    g = comp.grid
    zt = collar.z_t(_column_xy(g)).reshape(g.shape[:2])
    Path(a.out_dir).mkdir(parents=True, exist_ok=True)
    np.savez(cache, phi=comp.phi, cav=phi_cav, zt=zt, origin=g.origin, h=g.h, dz=g.dz)
    return comp.phi, phi_cav, zt, g


# ------------------------------------------------------------------------------------------------ renders
def render(phi, grid, title, path, bottom=True, section_y=None, section_z=None):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from pattern_real import depth_map, shade

    side = shade(depth_map(phi, grid, 1), (grid.dz, grid.h))
    up = shade(depth_map(phi, grid, 2, from_positive=False), (grid.h, grid.h))  # from below
    top = shade(depth_map(phi, grid, 2, from_positive=True), (grid.h, grid.h))
    ex = [grid.origin[0], grid.origin[0] + grid.shape[0] * grid.h]
    ey = [grid.origin[1], grid.origin[1] + grid.shape[1] * grid.h]
    ez = [grid.origin[2], grid.origin[2] + grid.shape[2] * grid.dz]
    fig, axs = plt.subplots(4, 1, figsize=(15, 20), gridspec_kw={"height_ratios": [1.0, 1.0, 1.0, 1.0]})
    axs[0].imshow(side.T, origin="lower", cmap="gray", vmin=0, vmax=1, extent=[*ex, *ez])
    axs[0].set_title(f"{title}: side view (from -y)")
    axs[1].imshow(top.T, origin="lower", cmap="gray", vmin=0, vmax=1, extent=[*ex, *ey])
    axs[1].set_title("top view")
    axs[2].imshow(up.T, origin="lower", cmap="gray", vmin=0, vmax=1, extent=[*ex, *ey])
    axs[2].set_title("view from below")
    iy = grid.shape[1] // 2 if section_y is None else int(round((section_y - grid.origin[1]) / grid.h))
    sec = (phi[:, iy, :] < 0).astype(float)
    axs[3].imshow(1 - sec.T, origin="lower", cmap="gray", vmin=0, vmax=1.2, extent=[*ex, *ez])
    axs[3].set_title(f"cross-section y = {grid.origin[1] + iy * grid.h:.1f} mm (solid = black)")
    for ax in axs:
        ax.set_aspect("equal")
    fig.tight_layout()
    fig.savefig(path, dpi=100)
    plt.close(fig)


def render_slices(phi, grid, path, zs, sec_x=None):
    """Horizontal slices (solid = black) at the given heights: the lattice core seen from above."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    ex = [grid.origin[0], grid.origin[0] + grid.shape[0] * grid.h]
    ey = [grid.origin[1], grid.origin[1] + grid.shape[1] * grid.h]
    fig, axs = plt.subplots(len(zs), 1, figsize=(15, 5.2 * len(zs)))
    for ax, z in zip(np.atleast_1d(axs), zs):
        k = int(round((z - grid.origin[2]) / grid.dz))
        ax.imshow(1 - (phi[:, :, k] < 0).T.astype(float), origin="lower", cmap="gray", vmin=0, vmax=1.2, extent=[*ex, *ey])
        ax.set_title(f"horizontal slice z = {z:.1f} mm (solid = black)")
        ax.set_aspect("equal")
    fig.tight_layout()
    fig.savefig(path, dpi=90)
    plt.close(fig)


# ------------------------------------------------------------------------------------------------ one form
def finish(name, phi_f, fg, rep, base_mesh_vol, out, cfg, proc, t0, t_build):
    sealed_raw = L.sealed(phi_f, fg)  # before the safety net: the design itself must leave none
    safety = L.fill_sealed(phi_f, fg) if sealed_raw["count"] else {"filled_mm3": 0.0, "components": 0}
    t1 = time.perf_counter()
    mesh, erep = L.to_mesh(phi_f, fg, 200.0)
    mesh.export(out / f"{name}.stl")
    t_mc = time.perf_counter() - t1
    res = {"build_s": round(t_build, 1), "mesh_s": round(t_mc, 1), "faces": int(len(mesh.faces)),
           "volume_cm3": round(float(mesh.volume) / 1000, 2), "baseline_cm3": round(base_mesh_vol / 1000, 2),
           "delta_cm3": round(float(mesh.volume - base_mesh_vol) / 1000, 2),
           "delta_pct": round(float((mesh.volume - base_mesh_vol) / base_mesh_vol * 100), 1),
           "grams_at_placeholder_density": round(float(mesh.volume) / 1000 * cfg.tpu_density_g_cm3, 1),
           "grams_saved": round(float(base_mesh_vol - mesh.volume) / 1000 * cfg.tpu_density_g_cm3, 1),
           "extents_mm": [round(float(e), 1) for e in mesh.extents], "export": {"components": erep.n_components, "dropped_mm3": round(erep.dropped_volume_mm3, 2)},
           "checks": L.export_checks(mesh), "sealed_field": sealed_raw, "sealed_filled": safety, "sealed_after": L.sealed(phi_f, fg)}
    t2 = time.perf_counter()
    res["thin_material"] = L.thin_material(phi_f, fg, L.strut_min_mm(cfg, proc))
    res["thin_s"] = round(time.perf_counter() - t2, 1)
    res["peak_rss_mb"] = round(rss_mb())
    res["total_s"] = round(time.perf_counter() - t0, 1)
    return mesh, res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shoe", default="dlp_solid_44_right")
    ap.add_argument("--process", default="config/process_sls_tpu.toml")
    ap.add_argument("--lattice", default=None)
    ap.add_argument("--h", type=float, default=1.0)
    ap.add_argument("--only", default="sole,knit,volume")
    ap.add_argument("--kind")
    ap.add_argument("--cell", type=float)
    ap.add_argument("--voxel", type=float)
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--no-render", action="store_true")
    ap.add_argument("--tag", default="", help="suffix for the output file names")
    ap.add_argument("--out-dir", default="reports/lattice")
    a = ap.parse_args()
    proc = ProcessConfig.from_toml(a.process)
    cfg = L.LatticeConfig.from_toml(a.lattice)
    sole = dataclasses.replace(cfg.sole, **{k: v for k, v in (("kind", a.kind), ("cell_mm", a.cell)) if v is not None})
    cfg = dataclasses.replace(cfg, sole=sole, voxel_mm=a.voxel or cfg.voxel_mm)
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    phi, cav, zt, grid = load_composed(a, proc)
    print(f"[load] {time.perf_counter() - t0:.0f} s  rss {rss_mb():.0f} MB", flush=True)
    base_mesh, _ = sdf_to_mesh(phi, grid)
    base_vol = float(base_mesh.volume)
    print(f"[baseline] {base_vol / 1000:.2f} cm3", flush=True)
    report = {"process": proc.name, "lattice_config": dataclasses.asdict(cfg), "baseline_cm3": round(base_vol / 1000, 2), "forms": {}}
    rpath = out / f"lattice_report{a.tag}.json"
    if rpath.exists():  # keep the forms of earlier runs (--only runs a subset)
        report["forms"].update(json.load(open(rpath)).get("forms", {}))
    prog = lambda i, n: print(f"   plane {i}/{n}  rss {rss_mb():.0f} MB", flush=True)  # noqa: E731
    for name in a.only.split(","):
        t1 = time.perf_counter()
        tag = name + a.tag
        if name == "sole":
            fine, fg, rep = L.sole_lattice(phi, cav, grid, cfg, proc, prog)
            rep["pores"] = L.check_sole_params(cfg, proc)
            zone = L.SoleZone.build(phi, cav, grid, cfg.sole)
            t2 = time.perf_counter()
            rep["powder_access"] = L.powder_access(fine, fg, phi, grid, L.zone_box(zone))
            rep["powder_access_s"] = round(time.perf_counter() - t2, 1)
            print("   powder access", json.dumps(rep["powder_access"]["balls"]), flush=True)
        else:
            from shoe_designer import lattice_upper as U

            fine, fg, rep = U.upper_shell(name, phi, cav, zt, grid, cfg, proc, prog)
        t_build = time.perf_counter() - t1
        mesh, res = finish(tag, fine, fg, rep, base_vol, out, cfg, proc, t1, t_build)
        res["form"] = rep
        report["forms"][tag] = res
        print(f"[{tag}] {res['total_s']} s  vol {res['volume_cm3']} cm3 ({res['delta_cm3']:+} cm3, {res['delta_pct']:+}%)  "
              f"saved {res['grams_saved']} g  watertight={res['checks']['watertight']} bodies={res['checks']['bodies']} "
              f"inner={res['checks']['inner_shells']['count']} sealed_raw={res['sealed_field']['count']} after={res['sealed_after']['count']} "
              f"thin={res['thin_material']['thin_fraction']}  rss {res['peak_rss_mb']} MB", flush=True)
        if not a.no_render:
            render(fine, fg, tag, out / f"{tag}.png")
            render_slices(fine, fg, out / f"{tag}_slices.png", [4.5, 6.0])
        del fine, mesh
        rpath.write_text(json.dumps(report, indent=1, ensure_ascii=False, default=float))


if __name__ == "__main__":
    main()
