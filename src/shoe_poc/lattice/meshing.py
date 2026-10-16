"""Closed zero-set extraction in the sampling grid's physical xyz coordinates."""
from __future__ import annotations

from dataclasses import dataclass
import gc
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


def _active_cells(values, level=0.0):
    count = 0
    for index in range(values.shape[0] - 1):
        corners = [values[index + dx, dy:values.shape[1] - 1 + dy,
                          dz:values.shape[2] - 1 + dz]
                   for dx in (0, 1) for dy in (0, 1) for dz in (0, 1)]
        lower = np.minimum.reduce(corners)
        upper = np.maximum.reduce(corners)
        count += int(np.count_nonzero((lower <= level) & (upper >= level) & (lower < upper)))
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


class LatticeExtractionError(ValueError):
    """An unsuccessful bounded contour ladder, retaining all attempt evidence."""

    def __init__(self, message, attempts):
        self.metadata = {"target_level": 0.0, "numerical_attempts": attempts,
                         "mesh_repair_applied": False, "components_removed": 0}
        super().__init__(message)

    def __reduce__(self):
        # Spawned parameter workers must return the original diagnostic error;
        # losing constructor arguments during unpickling breaks the whole pool.
        return type(self), (str(self), self.metadata['numerical_attempts'])


def _serialization_area_checks(mesh):
    """Check actual STL coordinate quantization without repairing triangles.

    Binary STL stores float32 coordinates. Work in bounded triangle slabs after
    quantizing vertices, avoiding a second complete triangle-array allocation.
    Full reimport topology checks remain the pipeline's independent validation.
    """
    with np.errstate(over="ignore", invalid="ignore"):
        serialized = mesh.vertices.astype(np.float32)
    finite = bool(np.isfinite(serialized).all())
    if not finite:
        return {"serialized_finite_vertices": False,
                "serialized_minimum_area_mm2": None,
                "serialized_degenerate_triangle_count": None}
    minimum, degenerate = float("inf"), 0
    for start in range(0, len(mesh.faces), 65536):
        triangles = serialized[mesh.faces[start:start+65536]].astype(np.float64)
        area = np.linalg.norm(np.cross(triangles[:, 1]-triangles[:, 0],
                                       triangles[:, 2]-triangles[:, 0]), axis=1)*.5
        minimum = min(minimum, float(area.min()))
        degenerate += int(np.count_nonzero(area <= 1e-12))
    return {"serialized_finite_vertices": finite,
            "serialized_minimum_area_mm2": minimum,
            "serialized_degenerate_triangle_count": degenerate}


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
    from skimage.measure import marching_cubes
    field_minimum, field_maximum = float(values.min()), float(values.max())
    scale = max(1.0, abs(field_minimum), abs(field_maximum))
    epsilon = float(np.finfo(np.float32).eps)
    attempts = []
    for multiplier in (0, 16, 128):
        inset = multiplier * epsilon * scale
        level = -inset if inset else 0.0
        if not field_minimum < level < field_maximum:
            attempts.append({"level": level, "inset_dimensionless": inset,
                             "status": "FAIL", "reason": "Conservative contour no longer brackets material"})
            raise LatticeExtractionError("Bounded numerical contour removed the valid zero crossing", attempts)
        active = _active_cells(values, level=level)
        # Reserve before EVERY native extraction, including numerical retries.
        estimate = int(values.size * 48 + active * 1536)
        if max_memory_bytes <= 0 or estimate > max_memory_bytes:
            attempts.append({"level": level, "inset_dimensionless": inset, "status": "FAIL",
                             "reason": "Meshing allocation budget exceeded", "peak_memory_estimate_bytes": estimate})
            raise LatticeExtractionError(
                f"Estimated field/meshing peak {estimate} bytes exceeds budget {max_memory_bytes}; reduce domain or increase spacing", attempts)
        vertices, faces, _, _ = marching_cubes(values, level=level,
            spacing=(field.spacing_mm,) * 3, gradient_direction="descent",
            method="lewiner", allow_degenerate=False)
        # Promote local float32 MC positions before adding physical origin.
        vertices = vertices.astype(np.float64) + np.asarray(field.origin_mm, dtype=np.float64)
        mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
        if mesh.volume < 0:
            mesh.invert()
        integrity = mesh_integrity(mesh)
        native_area = mesh.area_faces
        serialized = _serialization_area_checks(mesh)
        serialization_ok = serialized["serialized_finite_vertices"] and serialized["serialized_degenerate_triangle_count"] == 0
        passed = integrity["status"] == "PASS" and serialization_ok
        attempt = {"level": level, "inset_dimensionless": inset, "float32_eps_multiplier": multiplier,
                   "status": "PASS" if passed else "FAIL", "active_cells": active,
                   "minimum_area_mm2": float(native_area.min()),
                   "degenerate_triangle_count": int(np.count_nonzero(native_area <= 1e-12)),
                   "peak_memory_estimate_bytes": estimate, "integrity": integrity, **serialized}
        attempts.append(attempt)
        if passed:
            break
        # Numerical contour shifts cannot be used to excuse another topology,
        # winding, manifold, finite-coordinate or volume failure.
        other_checks = {name: passed for name, passed in integrity.get("checks", {}).items()
                        if name != "nondegenerate_triangles"}
        if not other_checks or not all(other_checks.values()):
            raise LatticeExtractionError(f"Extracted lattice mesh failed non-area integrity checks: {integrity}", attempts)
        if multiplier == 128:
            raise LatticeExtractionError("Extracted lattice mesh failed strict native/STL area checks after all bounded numerical contours", attempts)
        del mesh, vertices, faces, native_area
        gc.collect()
    threshold = field.metadata.get("threshold")
    metadata = {"method": "scikit-image Lewiner Marching Cubes", "level": level,
                "target_level": 0.0, "numerical_level_inset_dimensionless": inset,
                "effective_gyroid_threshold": float(threshold)-inset if threshold is not None else None,
                "numerical_domain_inset_mm": field.spacing_mm*inset,
                "numerical_field_scale": scale, "numerical_attempts": attempts,
                "numerical_contour_reason": (
                    "Zero target attempted first; only strict native/STL float32 area failures with all other integrity checks passing permit bounded inward 16/128-epsilon contours. "
                    "Original scalar field and parameters are unchanged; actual negative level is a conservative numerical shrink, not the exact requested zero contour. "
                    "All components of the chosen actual level are retained; no faces, components or vertices are repaired or removed."),
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
