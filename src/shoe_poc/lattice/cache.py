"""Strict replay of a hash-bound, previously completed native CAD domain.

Loading a cache does not perform new CAD classification or distance queries.
The caller must obtain ``baseline`` from the current accepted-input gate and
record this reuse separately from native sampling in the new execution report.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import zipfile

import numpy as np

from shoe_poc.lattice.domain import CAD_TOLERANCE_MM, SoleDomain, plan_grid
from shoe_poc.lattice.gyroid import positive_boundary
from shoe_poc.reporting import sha256
from shoe_poc.schemas.spec import LatticeDomainParameters


def _same_numeric(value, expected, name):
    try:
        actual = np.asarray(value, dtype=float)
        target = np.asarray(expected, dtype=float)
        valid = actual.shape == target.shape and np.isfinite(actual).all() and np.allclose(
            actual, target, rtol=0, atol=1e-8)
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise ValueError(f"Cached domain {name} does not match requested CAD domain")


def _positive_count(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"Cached domain {name} must be a positive integer")


def _check_archive(path, expected_shape):
    """Inspect the tiny NPY headers before allocating a decompressed 3D field."""
    limits = {"field_mm.npy": math.prod(expected_shape) * 4 + 4096,
              "origin_mm.npy": 4096, "spacing_mm.npy": 4096,
              "metadata_json.npy": 4 * 1024**2}
    with zipfile.ZipFile(path) as archive:
        if sorted(archive.namelist()) != sorted(limits):
            raise ValueError("Cached domain archive must contain exactly field, origin, spacing and metadata")
        for name, maximum in limits.items():
            if archive.getinfo(name).file_size > maximum:
                raise ValueError(f"Cached domain archive allocation exceeds expected {name} size")
        with archive.open("field_mm.npy") as member:
            version = np.lib.format.read_magic(member)
            if version == (1, 0):
                shape, _, dtype = np.lib.format.read_array_header_1_0(member)
            elif version == (2, 0):
                shape, _, dtype = np.lib.format.read_array_header_2_0(member)
            else:
                raise ValueError("Unsupported cached domain NPY version")
        if tuple(shape) != tuple(expected_shape) or dtype != np.dtype("float32"):
            raise ValueError("Cached domain field header shape/dtype differs from expected float32 xyz grid")


def load_certified_domain(directory: Path, *, baseline: dict, shape,
                          z_min_mm: float, z_max_mm: float,
                          settings: LatticeDomainParameters, spacing_mm: float,
                          max_voxels: int, max_memory_mb: float, envelope=None,
                          allow_refinement_seed: bool = False) -> SoleDomain:
    """Validate provenance, exact saved evidence and grid before replaying a field.

    No missing or stale evidence falls back to an unvalidated cache. No field is
    reconstructed, eroded, resampled, or otherwise changed by this function.
    """
    if not np.isfinite([z_min_mm, z_max_mm]).all() or z_min_mm >= z_max_mm:
        raise ValueError("Cached domain requires finite ordered sole Z bounds")
    if envelope is not None and not np.allclose(envelope.z_bounds, [z_min_mm, z_max_mm], rtol=0, atol=1e-8):
        raise ValueError('Cached domain curved envelope and global Z bounds disagree')
    box = shape.bounding_box()
    bounds = np.array([[box.min.X, box.min.Y, z_min_mm],
                       [box.max.X, box.max.Y, z_max_mm]], dtype=float)
    if settings.bounds_mm is not None:
        requested = np.asarray(settings.bounds_mm, dtype=float)
        bounds[0] = np.maximum(bounds[0], requested[0])
        bounds[1] = np.minimum(bounds[1], requested[1])
    # Must precede np.load or archive field allocation, even for a stale cache.
    plan = plan_grid(bounds, spacing_mm, max_voxels=max_voxels, max_memory_mb=max_memory_mb)
    directory = Path(directory).resolve()
    provenance = json.loads((directory / "domain_provenance.json").read_text())
    if provenance.get("baseline") != baseline:
        raise ValueError("Cached domain frozen baseline provenance changed")
    if not isinstance(provenance.get("run_id"), str) or not provenance["run_id"].strip():
        raise ValueError("Cached domain requires an identified original sampling run_id")
    parameters = provenance.get("parameters", {})
    expected_envelope = None if envelope is None else envelope.metadata
    if (settings.mode == 'curved_lower_foot') != (envelope is not None):
        raise ValueError('Cached domain mode requires the matching explicit envelope')
    if parameters.get('envelope') != expected_envelope:
        raise ValueError('Cached domain provenance envelope differs')
    _same_numeric(parameters.get("spacing_mm"), spacing_mm, "provenance spacing")
    _same_numeric(parameters.get("clearance_mm"), settings.clearance_mm, "provenance clearance")
    _same_numeric(parameters.get("sole_z_bounds_mm"), [z_min_mm, z_max_mm], "provenance sole Z bounds")
    if settings.bounds_mm is None:
        if parameters.get("bounds_mm", "missing") is not None:
            raise ValueError("Cached domain provenance computation bounds differ")
    else:
        _same_numeric(parameters.get("bounds_mm"), settings.bounds_mm, "provenance computation bounds")
    field_path, metadata_path = directory / "domain_field.npz", directory / "domain_metadata.json"
    for path, key in ((field_path, "domain_field_sha256"), (metadata_path, "domain_metadata_sha256")):
        if sha256(path) != provenance.get(key):
            raise ValueError(f"Cached domain hash changed: {path.name}")
    metadata = json.loads(metadata_path.read_text())
    saved_refinement = parameters.get('refinement')
    refinement_evidence = metadata.get('refinement')
    if saved_refinement is None:
        if refinement_evidence is not None:
            raise ValueError('Cached adaptive refinement has no bound provenance policy')
    else:
        from .refinement import REFINEMENT_VERSION
        if (not isinstance(refinement_evidence, dict)
                or refinement_evidence.get('policy') != saved_refinement
                or refinement_evidence.get('version') != REFINEMENT_VERSION
                or refinement_evidence.get('status') != 'COMPLETE'
                or refinement_evidence.get('query_budget_reached') is not False):
            raise ValueError('Cached adaptive refinement policy/evidence is incomplete or inconsistent')
    requested_refinement = None if settings.refinement is None else settings.refinement.model_dump(mode='json')
    if not allow_refinement_seed and saved_refinement != requested_refinement:
        raise ValueError('Cached domain refinement policy differs from requested policy')
    if metadata.get('envelope') != expected_envelope:
        raise ValueError('Cached domain metadata envelope differs')
    if metadata.get("axis_order") != "XYZ" or metadata.get("shape_xyz") != list(plan.shape):
        raise ValueError("Cached domain metadata axis order or grid shape differs")
    for key, expected in (("origin_mm", plan.origin_mm), ("spacing_mm", spacing_mm),
                          ("clearance_mm", settings.clearance_mm),
                          ("computation_bounds_mm", bounds), ("sole_z_bounds_mm", [z_min_mm, z_max_mm])):
        _same_numeric(metadata.get(key), expected, key)
    if metadata.get("voxel_count") != plan.voxel_count:
        raise ValueError("Cached domain voxel count differs from the requested grid")
    samples = metadata.get("independent_native_samples", {})
    _positive_count(samples.get("count"), "independent CAD sample count")
    if samples.get("failed_count") != 0 or samples.get("measurement_errors") != []:
        raise ValueError("Cached domain independent CAD evidence contains failed or incomplete measurements")
    points = np.asarray(samples.get("points_mm"), dtype=float)
    if points.shape != (samples["count"], 3) or not np.isfinite(points).all():
        raise ValueError("Cached domain independent CAD sample positions are incomplete")
    native = metadata.get("native_scanlines", {})
    _positive_count(native.get("line_count"), "native scanline count")
    if native.get("measurement_errors") != []:
        raise ValueError("Cached domain scanline evidence contains failed or incomplete measurements")
    certificate = metadata.get("certificate", {})
    _positive_count(certificate.get("query_count"), "exact CAD certificate query count")
    if certificate.get("anchor_classification_errors") != []:
        raise ValueError("Cached domain CAD certificate contains anchor classification errors")
    _same_numeric(certificate.get("cell_diagonal_reserve_mm"), math.sqrt(3) * spacing_mm,
                  "certificate full cell diagonal")
    _same_numeric(certificate.get("numeric_reserve_mm"), 8 * CAD_TOLERANCE_MM,
                  "certificate numerical reserve")
    _check_archive(field_path, plan.shape)
    with np.load(field_path, allow_pickle=False) as saved:
        if json.loads(str(saved["metadata_json"])) != metadata:
            raise ValueError("Cached domain NPZ and JSON metadata disagree")
        _same_numeric(saved["origin_mm"], plan.origin_mm, "NPZ origin")
        _same_numeric(saved["spacing_mm"], spacing_mm, "NPZ spacing")
        values = saved["field_mm"]
    if values.shape != plan.shape or values.dtype != np.float32 or not np.isfinite(values).all():
        raise ValueError("Cached domain field is not a finite float32 xyz grid")
    count = int(np.count_nonzero(values < 0))
    if not count or count != metadata.get("certified_negative_node_count") or not positive_boundary(values):
        raise ValueError("Cached domain field certificate count or positive padded boundary is invalid")
    return SoleDomain(plan=plan, occupancy=None, field_mm=values, metadata=metadata)
