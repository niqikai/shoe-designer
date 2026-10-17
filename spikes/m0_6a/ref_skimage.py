"""Speed reference only (not a candidate): scikit-image marching_cubes on M0.5 blocks.

    uv run --with scikit-image python spikes/m0_6a/ref_skimage.py [--voxel 0.25]

Per-block extraction of the lattice block (no welding: skimage does not expose edge ids).
Times are the sum over all crossing blocks, single thread, plus the first single block.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import common
import skimage
from skimage.measure import marching_cubes

from shoegen.evaluate import plan_blocks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--voxel", type=float, default=0.25)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--watchdog-gb", type=float, default=None)
    a = ap.parse_args()
    from shoegen.resources import RssWatchdog

    wd = RssWatchdog(int(a.watchdog_gb * 2**30), interval_s=0.1) if a.watchdog_gb else None
    if wd:
        wd.__enter__()
    sh = common.lattice_shape(a.voxel)
    plan = plan_blocks(sh.graph, None, sh.region_min, sh.region_max, a.voxel)
    store, summ = common.build_store(plan, workers=a.workers, watchdog=wd)
    # first block: the one in the middle of the grid
    mid = store.order[len(store.order) // 2]
    k = int(store.block_map[mid])
    vol = np.asarray(store.packed[k], dtype=np.float32)
    ts = []
    for _ in range(5):
        t = time.perf_counter()
        v, f, _n, _ = marching_cubes(vol, level=0.0, spacing=(a.voxel,) * 3)
        ts.append(time.perf_counter() - t)
    one = {
        "block": list(mid),
        "best_of_5_s": round(min(ts), 4),
        "vertices": len(v),
        "triangles": len(f),
    }
    tot_t = 0.0
    tot_tri = 0
    for k in range(len(store.order)):
        vol = np.asarray(store.packed[k])
        if not (vol.min() < 0.0 < vol.max()):
            continue
        t = time.perf_counter()
        v, f, _n, _ = marching_cubes(vol, level=0.0, spacing=(a.voxel,) * 3)
        tot_t += time.perf_counter() - t
        tot_tri += len(f)
    peak = wd.peak_bytes / 2**20 if wd else None
    print(
        json.dumps(
            {
                "peak_rss_mb_parent_plus_workers": peak,
                "skimage": skimage.__version__,
                "voxel_mm": a.voxel,
                "m05_sampling": {
                    x: summ[x]
                    for x in ("sampled_blocks", "sample_seconds", "workers", "samples_evaluated")
                },
                "single_block": one,
                "all_blocks_seconds_single_thread": round(tot_t, 3),
                "all_blocks_triangles_no_weld": tot_tri,
            }
        )
    )


if __name__ == "__main__":
    main()
