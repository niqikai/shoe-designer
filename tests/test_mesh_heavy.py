"""Heavy run: the 80 x 80 x 40 mm BCC lattice block of M0.6a at 0.25 mm (slow).

    uv run pytest -m slow tests/test_mesh_heavy.py -s

Takes ``.scratch/heavy.lock`` (implementation plan 1.1; skipped if another heavy job
holds it) and runs under a 6 GB :class:`RssWatchdog`. Same shape as the spike
(``spikes/m0_6a/common.py``): ``StrutLattice(cell 10, r 1)`` ∩ box
``[0.07, 80.07]^2 x [0.07, 40.07]``, region ``(-1, -1, -1)..(81, 81, 41)``, so the numbers
compare with ``docs/spikes/m0.6a-meshing.md`` (Euler characteristic -2774 from manifold3d,
volume reference 46 440 mm3, scikit-image 3 827 628 unwelded triangles).
"""

from __future__ import annotations

import json
import resource
import sys
import time
from pathlib import Path

import pytest

from shoegen.evaluate import plan_blocks
from shoegen.graph import Box, GraphBuilder, Intersect, StrutLattice
from shoegen.mesh import extract_mesh, write_stl
from shoegen.resources import RssWatchdog

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / ".scratch" / "heavy.lock"
REFERENCE_VOLUME_MM3 = 46440.0


@pytest.fixture
def heavy_lock():
    LOCK.parent.mkdir(exist_ok=True)
    try:
        LOCK.mkdir()
    except FileExistsError:
        pytest.skip(f"{LOCK} is held by another heavy job")
    try:
        yield
    finally:
        LOCK.rmdir()


def _maxrss_mb(who: int) -> float:
    r = resource.getrusage(who).ru_maxrss
    return r / 2**20 if sys.platform == "darwin" else r / 2**10


def lattice_graph():
    b = GraphBuilder()
    lat = b.add(StrutLattice(cell_mm=10.0, radius_mm=1.0, origin=(0.0, 0.0, 0.0)), "lattice")
    box = b.add(Box(center=(40.07, 40.07, 20.07), half_size=(40.0, 40.0, 20.0)), "clip")
    return b.build(b.add(Intersect(children=[lat, box]), "block"))


@pytest.mark.slow
def test_lattice_block_80x80x40_at_0_25(heavy_lock, tmp_path, eval_report):
    plan = plan_blocks(lattice_graph(), None, (-1.0, -1.0, -1.0), (81.0, 81.0, 41.0), 0.25)
    runs = []
    with RssWatchdog(limit_bytes=6 * 2**30, interval_s=0.1) as wd:
        for i in range(2):
            t0 = time.perf_counter()
            res = extract_mesh(
                plan,
                workers=4,
                watchdog=wd,
                min_fragment_volume_mm3=1.0,
                check_manifold3d=(i == 0),
            )
            t_stl = time.perf_counter()
            sha = write_stl(res.mesh, tmp_path / f"lattice{i}.stl")
            t_end = time.perf_counter()
            runs.append((res, sha, t_stl - t0, t_end - t_stl))
            del res
        peak = wd.peak_bytes
    (r0, sha0, wall0, stl0), (_r1, sha1, wall1, _) = runs
    s = r0.summary
    v = r0.validation
    report = {
        "grid": list(plan.grid_shape),
        "blocks_sampled": plan.n_crossing,
        "triangles": s["triangles"],
        "vertices": s["vertices"],
        "components": s["components"],
        "fragments_removed": s["fragments_removed"],
        "fragment_volume_mm3": s["fragment_volume_mm3"],
        "euler": s["euler_characteristic"],
        "genus": s["genus"],
        "volume_mm3": s["volume_mm3"],
        "volume_vs_reference": s["volume_mm3"] / REFERENCE_VOLUME_MM3 - 1,
        "watertight": s["watertight"],
        "manifold": s["manifold"],
        "ok": s["ok"],
        "zero_area": v.zero_area_triangles,
        "coincident_vertices": v.coincident_vertices,
        "assembly": s["assembly"],
        "manifold3d": s["manifold3d"],
        "timings_s": s["timings_s"] | {"write_stl": stl0, "wall_run1": wall0, "wall_run2": wall1},
        "block_mesh_mb": s["block_mesh_bytes"] / 2**20,
        "peak_rss_tree_mb_watchdog": peak / 2**20,
        "maxrss_parent_mb": _maxrss_mb(resource.RUSAGE_SELF),
        "maxrss_children_mb": _maxrss_mb(resource.RUSAGE_CHILDREN),
        "stl_sha256": sha0,
        "stl_sha256_run2": sha1,
    }
    print(json.dumps(report, indent=2, default=str))
    assert v.ok and v.n_components == 1
    assert sha0 == sha1
    assert s["manifold3d"]["ok"] and s["manifold3d"]["genus"] == s["genus"]
    assert abs(report["volume_vs_reference"]) < 0.02
    eval_report(
        f"lattice 80x80x40 @0.25: {s['triangles']} triangles, chi={s['euler_characteristic']}, "
        f"volume {s['volume_mm3']:.0f} ({100 * report['volume_vs_reference']:+.2f} % vs "
        f"46440), total {s['timings_s']['total']:.1f} s, peak RSS {peak / 2**30:.2f} GiB"
    )
