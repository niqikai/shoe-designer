"""Closed zero-set extraction in the sampling grid's physical xyz coordinates."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time

import numpy as np
import trimesh

from shoe_poc.lattice.gyroid import DEFAULT_MAX_MEMORY_BYTES, SampledField, positive_boundary
from shoe_poc.validation.geometry import singular_vertex_links


@dataclass
class LatticeMeshResult:
    mesh: trimesh.Trimesh
    metadata: dict


def _active_cells(values):
    count = 0
    for index in range(values.shape[0] - 1):
        corners = [values[index + dx, dy:values.shape[1] - 1 + dy,
                          dz:values.shape[2] - 1 + dz]
                   for dx in (0, 1) for dy in (0, 1) for dz in (0, 1)]
        lower = np.minimum.reduce(corners)
        upper = np.maximum.reduce(corners)
        count += int(np.count_nonzero((lower <= 0) & (upper >= 0) & (lower < upper)))
    return count


def mesh_integrity(mesh: trimesh.Trimesh) -> dict:
    """No topology target is imposed on a multi-cell gyroid or its components."""
    finite = bool(np.isfinite(mesh.vertices).all())
    if not finite or not len(mesh.faces):
        return {"status": "FAIL", "finite_vertices": finite, "nonempty": bool(len(mesh.faces))}
    incidence = np.bincount(mesh.edges_unique_inverse)
    components = len(trimesh.graph.connected_components(mesh.face_adjacency,
                     nodes=np.arange(len(mesh.faces)), min_len=1, engine="scipy"))
    singular = singular_vertex_links(mesh)
    checks = {"finite_vertices": finite, "nonempty": bool(len(mesh.faces)),
              "watertight": bool(mesh.is_watertight),
              "consistent_winding": bool(mesh.is_winding_consistent),
              "positive_volume": bool(mesh.volume > 0),
              "nondegenerate_triangles": bool(np.all(mesh.area_faces > 1e-12)),
              "manifold_edges": bool(np.all(incidence == 2)),
              "manifold_vertex_links": not singular}
    return {"status": "PASS" if all(checks.values()) else "FAIL", "checks": checks,
            "boundary_edges": int(np.count_nonzero(incidence == 1)),
            "nonmanifold_edges": int(np.count_nonzero(incidence > 2)),
            "singular_vertex_count": len(singular), "singular_vertices": singular[:20],
            "connected_components": components, "euler_number": int(mesh.euler_number),
            "component_policy": "all extracted components retained; connectivity reported, no forced single component",
            "volume_mm3": float(mesh.volume), "bounds_mm": mesh.bounds.tolist(),
            "vertices": len(mesh.vertices), "triangles": len(mesh.faces),
            "manufacturing_status": "NOT_VERIFIED"}


def extract_lattice_mesh(field: SampledField, *, max_memory_bytes=DEFAULT_MAX_MEMORY_BYTES) -> LatticeMeshResult:
    started = time.perf_counter()
    values = field.values
    if values.ndim != 3 or min(values.shape) < 3 or not np.isfinite(values).all():
        raise ValueError("Marching Cubes requires a finite three-dimensional scalar field")
    if not values.min() < 0 < values.max():
        raise ValueError("Scalar field must bracket a nonempty zero contour")
    if not positive_boundary(values):
        raise ValueError("Open scalar boundary: require strictly positive padded samples before extraction")
    if not np.isfinite(field.spacing_mm) or field.spacing_mm <= 0 or not np.isfinite(field.origin_mm).all():
        raise ValueError("Field origin and positive spacing must be finite millimetre coordinates")
    active = _active_cells(values)
    # Conservative active-cell reservation for native extraction, Trimesh,
    # edge sorting/adjacency and vertex-link checks; check before native MC.
    estimate = int(values.size * 48 + active * 1536)
    if max_memory_bytes <= 0 or estimate > max_memory_bytes:
        raise ValueError(f"Estimated field/meshing peak {estimate} bytes exceeds budget {max_memory_bytes}; reduce domain or increase spacing")
    from skimage.measure import marching_cubes
    vertices, faces, _, _ = marching_cubes(values, level=0.0,
        spacing=(field.spacing_mm,) * 3, gradient_direction="descent",
        method="lewiner", allow_degenerate=False)
    # MC returns local float32 positions. Add potentially large world offsets
    # only after promoting them, preserving submillimetre translation accuracy.
    vertices = vertices.astype(np.float64) + np.asarray(field.origin_mm, dtype=np.float64)
    mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
    # Negative material means MC descent's orientation can depend on library
    # convention. Reverse every triangle as a unit when necessary; no topology
    # repair, component dropping, vertex smoothing or cap patching occurs.
    if mesh.volume < 0:
        mesh.invert()
    integrity = mesh_integrity(mesh)
    if integrity["status"] != "PASS":
        raise ValueError(f"Extracted lattice mesh failed integrity checks: {integrity}")
    metadata = {"method": "scikit-image Lewiner Marching Cubes", "level": 0,
                "axis_order": "xyz", "coordinate_units": "mm",
                "spacing_mm": field.spacing_mm, "origin_mm": list(field.origin_mm),
                "active_cells": active, "peak_memory_estimate_bytes": estimate,
                "memory_estimate_model": "48 bytes/grid sample plus 1536 bytes/active cell for extraction and topology scratch; CLI RSS guard also required",
                "maximum_memory_bytes": int(max_memory_bytes),
                "elapsed_seconds": time.perf_counter() - started,
                "integrity": integrity, "mesh_repair_applied": False,
                "components_removed": 0, "field": field.metadata}
    return LatticeMeshResult(mesh, metadata)


def export_lattice(result: LatticeMeshResult, path: Path | str) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    result.mesh.export(path, file_type="stl")
    return path
