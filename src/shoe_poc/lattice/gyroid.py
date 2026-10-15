"""Bounded, millimetre-coordinate sampling of a dimensionless gyroid wall field.

Array axes are always x, y, z. Negative values represent material. The implicit
threshold is dimensionless; none of the sampling diagnostics certify wall mm.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
import time
from typing import Any

import numpy as np


DEFAULT_MAX_VOXELS = 10_000_000
DEFAULT_MAX_MEMORY_BYTES = 2 * 1024**3


@dataclass(frozen=True)
class GyroidParameters:
    period_mm: float
    threshold: float
    spacing_mm: float
    phase_origin_mm: tuple[float, float, float] = (0.0, 0.0, 0.0)

    def __post_init__(self):
        scalars = (self.period_mm, self.threshold, self.spacing_mm)
        if not all(math.isfinite(value) and value > 0 for value in scalars):
            raise ValueError("Gyroid period, threshold and spacing must be finite and positive")
        if self.threshold >= 1.5:
            raise ValueError("Gyroid threshold must be below 1.5 to retain a wall zero contour")
        if len(self.phase_origin_mm) != 3 or not np.isfinite(self.phase_origin_mm).all():
            raise ValueError("Gyroid phase origin must contain three finite millimetre coordinates")

    def resolution_diagnostics(self) -> dict:
        samples_per_period = self.period_mm / self.spacing_mm
        # This is a screening metric, not a measured or guaranteed wall thickness.
        transition_samples = self.threshold * self.period_mm / (math.pi * self.spacing_mm)
        return {
            "samples_per_period": samples_per_period,
            "nominal_wall_transition_samples": transition_samples,
            "minimum_samples_per_period": 12,
            "minimum_nominal_wall_transition_samples": 2,
            "feasible": samples_per_period >= 12 and transition_samples >= 2,
            "interpretation": "Aliasing screen only; threshold is dimensionless and wall thickness is not calibrated",
        }


@dataclass(frozen=True)
class SamplingGrid:
    origin_mm: tuple[float, float, float]
    shape: tuple[int, int, int]
    spacing_mm: float
    max_memory_bytes: int = DEFAULT_MAX_MEMORY_BYTES

    @classmethod
    def from_bounds(cls, bounds_mm, spacing_mm: float, *, padding_cells: int = 1,
                    max_voxels: int = DEFAULT_MAX_VOXELS,
                    max_memory_bytes: int = DEFAULT_MAX_MEMORY_BYTES):
        bounds = np.asarray(bounds_mm, dtype=float)
        if bounds.shape != (2, 3) or not np.isfinite(bounds).all() or not (bounds[1] > bounds[0]).all():
            raise ValueError("Sampling bounds must be two finite, strictly ordered xyz corners")
        if not math.isfinite(spacing_mm) or spacing_mm <= 0:
            raise ValueError("Sampling spacing must be finite and positive")
        if not isinstance(padding_cells, int) or padding_cells < 1:
            raise ValueError("At least one positive boundary padding cell is required")
        shape = tuple(int(math.ceil(value / spacing_mm)) + 1 + 2 * padding_cells
                      for value in bounds[1] - bounds[0])
        validate_grid_budget(shape, max_voxels=max_voxels, max_memory_bytes=max_memory_bytes)
        origin = tuple((bounds[0] - padding_cells * spacing_mm).tolist())
        return cls(origin, shape, spacing_mm, max_memory_bytes)

    @property
    def axes(self):
        return tuple(self.origin_mm[i] + np.arange(self.shape[i], dtype=np.float64) * self.spacing_mm
                     for i in range(3))


def validate_grid_budget(shape, *, max_voxels=DEFAULT_MAX_VOXELS,
                         max_memory_bytes=DEFAULT_MAX_MEMORY_BYTES) -> dict:
    if len(shape) != 3 or any(int(n) != n or n < 3 for n in shape):
        raise ValueError("Sampling grid must have at least three samples on each xyz axis")
    count = math.prod(int(n) for n in shape)
    # Live fields, CAD occupancy, EDT scratch, and slab temporaries. Native CAD
    # and mesh allocations are also monitored by the CLI process RSS budget.
    estimate = count * 48 + sum(shape) * 8
    if max_voxels <= 0 or count > max_voxels:
        raise ValueError(f"Sampling grid has {count} voxels; configured voxel cap is {max_voxels}")
    if max_memory_bytes <= 0 or estimate > max_memory_bytes:
        raise ValueError(f"Estimated field memory {estimate} bytes exceeds budget {max_memory_bytes}")
    return {"voxel_count": count, "field_peak_memory_estimate_bytes": estimate,
            "maximum_voxels": int(max_voxels), "maximum_memory_bytes": int(max_memory_bytes)}


def gyroid_values(points_mm, period_mm: float, phase_origin_mm=(0.0, 0.0, 0.0)):
    """Evaluate G at (..., 3) points without changing the physical xyz axes."""
    points = np.asarray(points_mm, dtype=np.float64)
    if points.shape[-1:] != (3,) or not np.isfinite(points).all():
        raise ValueError("Gyroid points must be finite xyz coordinates")
    if not math.isfinite(period_mm) or period_mm <= 0:
        raise ValueError("Gyroid period must be finite and positive")
    phase = np.asarray(phase_origin_mm, dtype=float)
    if phase.shape != (3,) or not np.isfinite(phase).all():
        raise ValueError("Gyroid phase origin must contain three finite xyz coordinates")
    p = (points - phase) * (2 * math.pi / period_mm)
    x, y, z = np.moveaxis(p, -1, 0)
    return np.sin(x) * np.cos(y) + np.sin(y) * np.cos(z) + np.sin(z) * np.cos(x)


def gyroid_wall_field(points_mm, period_mm: float, threshold: float,
                      phase_origin_mm=(0.0, 0.0, 0.0)):
    if not math.isfinite(threshold) or not 0 < threshold < 1.5:
        raise ValueError("Gyroid threshold must be finite, positive and below 1.5")
    return np.abs(gyroid_values(points_mm, period_mm, phase_origin_mm)) - threshold


@dataclass
class SampledField:
    values: np.ndarray
    origin_mm: tuple[float, float, float]
    spacing_mm: float
    metadata: dict[str, Any]

    @property
    def shape(self):
        return self.values.shape


def positive_boundary(values: np.ndarray) -> bool:
    return all(bool(np.all(face > 0)) for face in
               (values[0], values[-1], values[:, 0], values[:, -1], values[:, :, 0], values[:, :, -1]))


def sample_gyroid(parameters: GyroidParameters, grid, *, domain_signed_field: np.ndarray,
                  domain_metadata: dict | None = None, max_voxels=DEFAULT_MAX_VOXELS,
                  max_memory_bytes=DEFAULT_MAX_MEMORY_BYTES) -> SampledField:
    """Intersect gyroid walls and a conservative closed domain before meshing.

    ``grid`` may be a SamplingGrid or the domain module's GridPlan. The domain
    field is signed mm, with a positive outer boundary and a certified negative
    interior. A recorded tiny inward cap inset avoids grid-aligned zero plateaus;
    scaling by spacing makes max() operands dimensionless. Every resulting cap
    is part of the same intersected zero set.
    """
    started = time.perf_counter()
    axes = tuple(np.asarray(axis, dtype=np.float64) for axis in grid.axes)
    shape = tuple(len(axis) for axis in axes)
    budget = validate_grid_budget(shape, max_voxels=max_voxels, max_memory_bytes=max_memory_bytes)
    if not math.isclose(float(grid.spacing_mm), parameters.spacing_mm, rel_tol=1e-10):
        raise ValueError("Gyroid and domain grid sampling spacing must agree")
    for axis in axes:
        if not np.isfinite(axis).all() or not np.allclose(np.diff(axis), parameters.spacing_mm,
                                                        rtol=1e-9, atol=1e-9):
            raise ValueError("Domain axes must be increasing uniform millimetre coordinates")
    diagnostics = parameters.resolution_diagnostics()
    if not diagnostics["feasible"]:
        raise ValueError("Underresolved gyroid: require >=12 samples per period and >=2 nominal wall transition samples; "
                         "reduce spacing_mm (this screen does not certify physical wall thickness)")
    domain = np.asarray(domain_signed_field)
    if domain.shape != shape or domain.dtype.kind != "f" or not np.isfinite(domain).all():
        raise ValueError("Signed domain must be a finite floating xyz field matching the sampling grid")
    if not positive_boundary(domain):
        raise ValueError("Signed domain must have strictly positive padded boundary samples")
    values = np.empty(shape, dtype=np.float32)
    k = 2 * math.pi / parameters.period_mm
    coordinates = [(axes[i] - parameters.phase_origin_mm[i]) * k for i in range(3)]
    sx, sy, sz = (np.sin(axis) for axis in coordinates)
    cx, cy, cz = (np.cos(axis) for axis in coordinates)
    # Bound scratch allocations to a yz slab; no 3D coordinate meshgrid.
    # A domain zero exactly on a grid plane can create coincident cap sheets
    # in MC at grid edges. A small, explicit inward domain offset avoids this
    # zero plateau without extending material or repairing extracted triangles.
    cap_inset_mm = parameters.spacing_mm * 1e-3
    for index in range(shape[0]):
        slab = sx[index] * cy[:, None] + sy[:, None] * cz[None, :] + sz[None, :] * cx[index]
        np.maximum(np.abs(slab) - parameters.threshold,
                   (domain[index] + cap_inset_mm) / parameters.spacing_mm,
                   out=values[index])
    minimum, maximum = float(values.min()), float(values.max())
    if not minimum < 0 < maximum:
        raise ValueError("Clipped gyroid field has no valid zero contour; enlarge domain or revise parameters")
    origin = tuple(float(axis[0]) for axis in axes)
    metadata = {
        "schema_version": 1, "array_axis_order": "xyz", "coordinate_units": "mm",
        "field_sign": "negative material, positive void", "field_units": "dimensionless",
        "formula": "abs(sin(kx)*cos(ky)+sin(ky)*cos(kz)+sin(kz)*cos(kx))-threshold",
        "clipping_formula": "max(gyroid_wall_field, (conservative_domain_signed_mm+cap_inset_mm)/spacing_mm)",
        "cap_inset_mm": cap_inset_mm,
        "cap_inset_reason": "Conservative inward offset to avoid grid-aligned zero plateaus and coincident cap triangles",
        "boundary_strategy": "intersection implicit surface with strictly positive padded boundary; no mesh hole repair",
        "period_mm": parameters.period_mm, "threshold": parameters.threshold,
        "threshold_units": "dimensionless", "wall_thickness_mm": "NOT_CALIBRATED",
        "phase_origin_mm": list(parameters.phase_origin_mm), "sampling_origin_mm": list(origin),
        "spacing_mm": parameters.spacing_mm, "shape_xyz": list(shape),
        "sample_bounds_mm": [list(origin), [float(axis[-1]) for axis in axes]],
        "field_minimum": minimum, "field_maximum": maximum,
        "negative_sample_count": int(np.count_nonzero(values < 0)),
        "resolution": diagnostics, "domain": domain_metadata or {}, **budget,
        "sampling_elapsed_seconds": time.perf_counter() - started,
        "manufacturing_status": "NOT_VERIFIED",
    }
    return SampledField(values, origin, parameters.spacing_mm, metadata)


def save_field_npz(field: SampledField, path: Path | str) -> Path:
    """Store the actual clipped field and all data required to reconstruct xyz."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, values=field.values,
                        origin_mm=np.asarray(field.origin_mm, dtype=np.float64),
                        spacing_mm=np.asarray(field.spacing_mm, dtype=np.float64),
                        metadata_json=np.asarray(json.dumps(field.metadata, sort_keys=True)))
    return path
