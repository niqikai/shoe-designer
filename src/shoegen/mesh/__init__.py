"""Block-wise mesh extraction and welding (ADR 0005, plan M0.6).

Typical use::

    from shoegen.evaluate import plan_blocks
    from shoegen.mesh import extract_mesh, write_stl

    plan = plan_blocks(graph, store, region_min, region_max, voxel_mm=0.25)
    res = extract_mesh(plan, workers=4, watchdog=wd,
                       min_fragment_volume_mm3=profile.min_fragment_volume_mm3)
    res.validation.ok            # closed, manifold, volume > 0, no degenerate triangles
    write_stl(res.mesh, "output/shoe.stl")    # deterministic bytes
    report["mesh"] = res.summary              # counts, checks, timings

Pipeline: marching cubes per block in the sampling workers (:mod:`.mc`, tables from one
face rule in :mod:`.tables`), vertices identified by global lattice edge; welding by edge
id, canonical order, verified merging of coincident vertices (:mod:`.assemble`); fragment
removal (:mod:`.fragments`); numba checks (:mod:`.topology`, :func:`validate_mesh`);
binary STL (:mod:`.stl`); optional manifold3d cross-check in a subprocess
(:mod:`.manifold_check`).
"""

from shoegen.mesh.assemble import (
    AssemblyStats,
    MeshMemoryError,
    WeldError,
    assemble_blocks,
    estimate_assembly_bytes,
)
from shoegen.mesh.extract import MeshResult, extract_mesh, mesh_from_samples
from shoegen.mesh.fragments import FragmentStats, remove_fragments
from shoegen.mesh.manifold_check import estimate_manifold3d_bytes, manifold3d_check
from shoegen.mesh.mc import (
    ZERO_SNAP_FRACTION,
    BlockMesh,
    decode_edge_ids,
    encode_edge_ids,
    mesh_block_fn,
    mesh_samples,
)
from shoegen.mesh.model import (
    ComponentInfo,
    Mesh,
    MeshValidation,
    canonicalize,
    validate_mesh,
)
from shoegen.mesh.stl import STL_HEADER, read_stl, stl_bytes, write_stl

__all__ = [
    "STL_HEADER",
    "ZERO_SNAP_FRACTION",
    "AssemblyStats",
    "BlockMesh",
    "ComponentInfo",
    "FragmentStats",
    "Mesh",
    "MeshMemoryError",
    "MeshResult",
    "MeshValidation",
    "WeldError",
    "assemble_blocks",
    "canonicalize",
    "decode_edge_ids",
    "encode_edge_ids",
    "estimate_assembly_bytes",
    "estimate_manifold3d_bytes",
    "extract_mesh",
    "manifold3d_check",
    "mesh_block_fn",
    "mesh_from_samples",
    "mesh_samples",
    "read_stl",
    "remove_fragments",
    "stl_bytes",
    "validate_mesh",
    "write_stl",
]
