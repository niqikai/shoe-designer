"""Block-sparse mesh extraction: sample -> marching cubes -> weld -> fragments -> checks.

Where marching cubes runs: in the M0.5 workers, right after a block is sampled
(:func:`shoegen.evaluate.iter_block_results`). Only the compact block mesh (edge ids,
positions, triangles) is sent back, which for surfaces is about the size of the samples
or much less, and the parent never runs a per-block kernel; it only collects results in
block order and assembles once at the end. With ``workers=1`` the same function runs in
process. Either way the per-block results are bitwise the same, so the STL does not depend
on the worker count.

Memory: the parent holds the block meshes (proportional to the surface, not to the
region volume), then checks ``RSS + estimate_assembly_bytes`` against the budget
(``memory_limit_bytes``, default: the watchdog's limit) before allocating the assembly
arrays.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np
import psutil

from shoegen.evaluate import BLOCK_CELLS, DEFAULT_WORKERS, BlockPlan, iter_block_results
from shoegen.mesh.assemble import AssemblyStats, assemble_blocks
from shoegen.mesh.fragments import FragmentStats, remove_fragments
from shoegen.mesh.manifold_check import manifold3d_check
from shoegen.mesh.mc import ID_INDEX_LIMIT, ZERO_SNAP_FRACTION, mesh_block_fn, mesh_samples
from shoegen.mesh.model import Mesh, MeshValidation, validate_mesh
from shoegen.resources import RssWatchdog


@dataclass
class MeshResult:
    mesh: Mesh
    validation: MeshValidation
    assembly: AssemblyStats
    fragments: FragmentStats
    summary: dict
    """``report.json`` material: counts, checks, timings (timings are not deterministic)."""


def _check_id_range(plan: BlockPlan) -> None:
    extent = [n * BLOCK_CELLS + 1 for n in plan.grid_shape]
    if max(extent) > ID_INDEX_LIMIT:
        raise ValueError(
            f"block grid {plan.grid_shape} spans {max(extent)} samples on an axis; edge ids "
            f"allow at most {ID_INDEX_LIMIT}"
        )


def _genus(v: MeshValidation) -> int | None:
    if not v.manifold or v.n_components != len(v.components):
        return None
    return int(sum((2 - c.euler_characteristic) // 2 for c in v.components))


def extract_mesh(
    plan: BlockPlan,
    *,
    workers: int = DEFAULT_WORKERS,
    watchdog: RssWatchdog | None = None,
    min_fragment_volume_mm3: float = 0.0,
    quantize: bool = True,
    memory_limit_bytes: int | None = None,
    max_in_flight: int | None = None,
    check_manifold3d: bool = False,
) -> MeshResult:
    """Mesh the zero set of ``plan``'s graph (``d < 0`` inside).

    ``min_fragment_volume_mm3`` is normally ``profile.min_fragment_volume_mm3``.
    ``quantize`` rounds vertices to float32 so the validated mesh is the one STL stores.
    ``check_manifold3d`` adds manifold3d's verdict in a subprocess (memory permitting).
    The region must keep the solid off its outer faces (``summary["eval"]
    ["boundary_inside_blocks"] == 0``), otherwise the mesh is open there.
    """
    t0 = time.perf_counter()
    _check_id_range(plan)
    limit = memory_limit_bytes
    if limit is None and watchdog is not None:
        limit = watchdog.limit_bytes
    fn_args = (plan.index_min, plan.voxel_mm, bool(quantize), True)
    blocks = []
    with iter_block_results(
        plan, mesh_block_fn, fn_args, workers, max_in_flight=max_in_flight, watchdog=watchdog
    ) as stream:
        for _block, bm in stream:
            blocks.append(bm)
    eval_summary = stream.summary
    block_bytes = int(sum(b.nbytes for b in blocks))
    t1 = time.perf_counter()

    rss = psutil.Process().memory_info().rss
    mesh, ast = assemble_blocks(
        blocks,
        id_origin=plan.index_min,
        voxel_mm=plan.voxel_mm,
        quantize=quantize,
        memory_limit_bytes=limit,
        current_rss_bytes=rss,
    )
    del blocks
    t2 = time.perf_counter()
    mesh, fst = remove_fragments(mesh, min_fragment_volume_mm3)
    t3 = time.perf_counter()
    val = validate_mesh(mesh)
    t4 = time.perf_counter()
    m3d = manifold3d_check(mesh, memory_limit_bytes=limit) if check_manifold3d else None
    t5 = time.perf_counter()

    summary = {
        "triangles": mesh.n_faces,
        "vertices": mesh.n_vertices,
        "components": val.n_components,
        "fragments_removed": fst.removed,
        "fragment_volume_mm3": fst.removed_volume_mm3,
        "volume_mm3": val.volume_mm3,
        "area_mm2": val.area_mm2,
        "euler_characteristic": val.euler_characteristic,
        "genus": _genus(val),
        "watertight": val.watertight,
        "manifold": val.manifold,
        "ok": val.ok,
        "open_at_region_boundary": eval_summary.boundary_inside_blocks > 0,
        "voxel_mm": plan.voxel_mm,
        "vertex_precision": "float32" if quantize else "float64",
        "zero_snap_mm": plan.voxel_mm * ZERO_SNAP_FRACTION,
        "mesh_sha256": mesh.sha256(),
        "validation": val.to_dict(components=False),
        "shells": [c.__dict__ for c in val.components],
        "assembly": ast.to_dict(),
        "fragments": fst.to_dict(),
        "manifold3d": m3d,
        "eval": eval_summary.to_dict(),
        "block_mesh_bytes": block_bytes,
        "timings_s": {
            "sample_and_mc": t1 - t0,
            "mc_in_blocks": eval_summary.block_fn_seconds,
            "assemble": t2 - t1,
            "fragments": t3 - t2,
            "validate": t4 - t3,
            "manifold3d": t5 - t4,
            "total": t5 - t0,
        },
        "peak_rss_bytes": None if watchdog is None else watchdog.peak_bytes,
    }
    return MeshResult(mesh=mesh, validation=val, assembly=ast, fragments=fst, summary=summary)


def mesh_from_samples(
    vals: np.ndarray,
    start,
    voxel_mm: float,
    *,
    block_cells: int = BLOCK_CELLS,
    quantize: bool = True,
    snap_zero: bool = True,
) -> tuple[Mesh, AssemblyStats]:
    """Mesh a dense sample array (``vals[a, b, c]`` at global index ``start + (a, b, c)``)
    by cutting it into blocks of ``block_cells`` cubes that share their faces, exactly as
    the block-sparse path does. For tests and small dense fields."""
    v = np.ascontiguousarray(vals, dtype=np.float32)
    s = np.asarray(start, dtype=np.int64).reshape(3)
    if block_cells < 1:
        raise ValueError("block_cells must be >= 1")
    blocks = []
    for a in range(0, max(v.shape[0] - 1, 1), block_cells):
        for b in range(0, max(v.shape[1] - 1, 1), block_cells):
            for c in range(0, max(v.shape[2] - 1, 1), block_cells):
                sub = v[a : a + block_cells + 1, b : b + block_cells + 1, c : c + block_cells + 1]
                blocks.append(mesh_samples(sub, s + (a, b, c), s, voxel_mm, quantize, snap_zero))
    return assemble_blocks(blocks, id_origin=s, voxel_mm=voxel_mm, quantize=quantize)
