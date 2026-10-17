"""Candidate B: manifold3d Manifold.level_set on a test shape (one process = one measurement).

    uv run python spikes/m0_6a/b_levelset.py --shape sphere --edge 1.0 --callback lookup

Prints one JSON object. ``--callback``:
  lookup    numba trilinear lookup into the M0.5 block samples (the realistic data path)
  analytic  numba analytic SDF of the shape (no block samples; sphere only)
  program   the real graph kernel called per point from Python (Program.eval on 1 point)
Environment ``ONECPU_N`` (+ DYLD_INSERT_LIBRARIES, see onecpu.c) fixes the TBB thread count.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

import numba
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import common
import manifold3d as m3d

from shoegen.evaluate import plan_blocks
from shoegen.graph import compile_graph
from shoegen.resources import RssWatchdog


def level_set_bounds(shape: common.Shape, margin: float):
    if shape.name == "lattice":
        x0, x1, y0, y1, z0, z1 = common.LATTICE_BOX
        return [x0 - margin, y0 - margin, z0 - margin, x1 + margin, y1 + margin, z1 + margin]
    c = np.asarray(common.SPHERE_CENTRE)
    half = 22.0 if shape.name == "sphere" else 24.0
    lo, hi = c - half, c + half
    return [*lo, *hi]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shape", choices=["sphere", "capsules", "lattice"], required=True)
    ap.add_argument("--edge", type=float, required=True, help="level_set edgeLength (mm)")
    ap.add_argument("--callback", choices=["lookup", "analytic", "program"], default="lookup")
    ap.add_argument("--workers", type=int, default=4, help="M0.5 sampling workers")
    ap.add_argument("--voxel", type=float, default=None)
    ap.add_argument("--margin", type=float, default=0.5)
    ap.add_argument("--watchdog-gb", type=float, default=None)
    ap.add_argument("--no-vertex-check", action="store_true")
    ap.add_argument("--save", default=None, help="save canonical mesh to this .npz")
    ap.add_argument("--tolerance", type=float, default=-1.0)
    a = ap.parse_args()

    shape = {
        "sphere": common.sphere_shape,
        "capsules": common.capsules_shape,
        "lattice": common.lattice_shape,
    }[a.shape]()
    if a.voxel:
        shape.voxel = a.voxel
    out: dict = {
        "shape": a.shape,
        "edge_mm": a.edge,
        "callback": a.callback,
        "voxel_mm": shape.voxel,
        "onecpu_n": common.thread_env(),
    }

    def new_wd():
        return RssWatchdog(int(a.watchdog_gb * 2**30), interval_s=0.1) if a.watchdog_gb else None

    wd = new_wd()  # phase 1: M0.5 sampling (+ packing); counts parent + workers
    if wd:
        wd.__enter__()
    wd2 = None
    try:
        t0 = time.perf_counter()
        plan = plan_blocks(shape.graph, None, shape.region_min, shape.region_max, shape.voxel)
        store = None
        if a.callback == "lookup":
            store, summ = common.build_store(plan, workers=a.workers, watchdog=wd)
            out["m05_sampling"] = {
                k: summ[k]
                for k in (
                    "total_blocks",
                    "sampled_blocks",
                    "skipped_inside",
                    "skipped_outside",
                    "samples_evaluated",
                    "sample_seconds",
                    "wait_seconds",
                    "workers",
                )
            }
            out["m05_total_seconds_incl_pack"] = summ["total_seconds"]
            out["store_mb"] = store.nbytes / 2**20
            f = common.make_lookup(store)
        elif a.callback == "analytic":
            c = np.asarray(common.SPHERE_CENTRE)
            R = common.SPHERE_R

            @numba.njit
            def f(x, y, z):
                return R - math.sqrt((x - c[0]) ** 2 + (y - c[1]) ** 2 + (z - c[2]) ** 2)

            f(0.0, 0.0, 0.0)
        else:
            prog = compile_graph(shape.graph)
            buf = np.zeros((1, 3))

            def f(x, y, z):
                buf[0, 0], buf[0, 1], buf[0, 2] = x, y, z
                return -float(prog.eval(buf)[0])

        out["prep_seconds"] = time.perf_counter() - t0
        if wd:
            out["rss_peak_sampling_phase_mb"] = wd.peak_bytes / 2**20
            wd.__exit__(None, None, None)
        out["rss_before_levelset_mb"] = common.current_rss_mb()
        wd2 = new_wd()  # phase 2: extraction only (parent; workers are gone)
        if wd2:
            wd2.__enter__()

        bounds = level_set_bounds(shape, a.margin)
        out["bounds"] = bounds
        # `Manifold` is lazy: force evaluation with num_tri()
        t1 = time.perf_counter()
        M = m3d.Manifold.level_set(f, bounds, a.edge, 0.0, a.tolerance)
        nt = M.num_tri()
        out["levelset_seconds"] = time.perf_counter() - t1
        out["rss_after_levelset_mb"] = common.current_rss_mb()
        out["maxrss_parent_after_levelset_mb"] = common.peak_rss_mb()  # kernel-tracked peak
        out["status"] = str(M.status())
        t2 = time.perf_counter()
        mesh = M.to_mesh64()
        out["to_mesh64_seconds"] = time.perf_counter() - t2
        if wd2:
            out["rss_peak_extraction_phase_mb"] = wd2.peak_bytes / 2**20
        verts = np.asarray(mesh.vert_properties, dtype=np.float64)[:, :3]
        tris = np.asarray(mesh.tri_verts, dtype=np.int64)
        out["manifold_volume"] = M.volume()
        out["manifold_genus"] = M.genus()
        out["manifold_num_tri"] = nt
        rep = common.mesh_report(verts, tris, vertex_check=not a.no_vertex_check)
        out["mesh"] = rep
        if shape.exact_volume:
            out["sphere_volume_error_pct"] = 100.0 * (rep["volume_mm3"] / shape.exact_volume - 1)
        out["hash"] = common.mesh_hash(verts, tris)
        import hashlib

        out["raw_order_hash"] = hashlib.sha256(
            np.ascontiguousarray(verts).tobytes() + np.ascontiguousarray(tris).tobytes()
        ).hexdigest()
        if a.save:
            v, t = common.canonical(verts, tris)
            np.savez(a.save, v=v, t=t)
        out["peak_rss_self_mb_incl_checks"] = common.peak_rss_mb()
        if wd2:
            out["rss_peak_incl_checks_mb"] = wd2.peak_bytes / 2**20
    finally:
        if wd2:
            wd2.__exit__(None, None, None)
    print(json.dumps(out))


if __name__ == "__main__":
    main()
