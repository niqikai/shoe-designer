"""Independent M2 mesh and bounded CAD checks, without M1 genus assumptions.

A passing sample is evidence at the reported sites, never a global containment,
self-intersection, wall-thickness, or manufacturing proof.
"""
from __future__ import annotations

from pathlib import Path
import time
from typing import Callable

import numpy as np
import trimesh
from scipy.spatial import cKDTree

from shoe_poc.reporting import utc_now
from .geometry import singular_vertex_links


def _mesh(value):
    if isinstance(value, (str, Path)):
        # STL repeats corner coordinates. Welding exact serialized coordinates is
        # necessary topology reconstruction, not hole filling or mesh repair.
        mesh = trimesh.load_mesh(value, process=False)
        if not isinstance(mesh, trimesh.Trimesh):
            raise ValueError("Expected one triangle mesh")
        mesh.merge_vertices(digits_vertex=12)
        return mesh
    if not isinstance(value, trimesh.Trimesh):
        raise ValueError("Expected a triangle mesh or STL path")
    return value


def validate_lattice_mesh(model, *, reference_mesh=None,
                          serialization_tolerance_mm: float = 5e-5) -> dict:
    """Check a closed lattice with any genus and any number of components.

    Every face component must have positive signed volume: an outward component
    cannot conceal an inverted component in the summed volume. Connectivity to
    a future shoe shell is a separate M3 requirement.
    """
    if not np.isfinite(serialization_tolerance_mm) or serialization_tolerance_mm <= 0:
        raise ValueError("Serialization tolerance must be finite and positive")
    started = time.perf_counter()
    mesh = _mesh(model)
    checks = []

    def check(name, passed, value, threshold):
        checks.append({"name": name, "status": "PASS" if passed else "FAIL",
                       "value": value, "threshold": threshold})

    nonempty = len(mesh.faces) > 0 and len(mesh.vertices) > 0
    finite = bool(np.isfinite(mesh.vertices).all())
    valid_indices = bool(mesh.faces.ndim == 2 and mesh.faces.shape[1] == 3
                         and (not len(mesh.faces) or (mesh.faces.min() >= 0
                              and mesh.faces.max() < len(mesh.vertices))))
    check("nonempty_triangle_mesh", nonempty, nonempty, True)
    check("finite_vertices", finite, finite, True)
    check("valid_face_indices", valid_indices, valid_indices, True)
    report = {"scope": "M2 closed lattice mesh; arbitrary genus and component count",
              "checked_at": utc_now(), "checks": checks,
              "manufacturing_status": "NOT_VERIFIED",
              "physical_connectivity_to_shoe": "NOT_VERIFIED",
              "physical_connectivity_note": "Lattice-to-shell fusion and fragment retention belong to M3; disconnected components are retained.",
              "limitations": ["Global triangle self-intersection is not verified.",
                              "No physical wall-thickness or manufacturing qualification is implied."]}
    if not (nonempty and finite and valid_indices):
        return {**report, "geometry_status": "FAIL", "runtime_seconds": time.perf_counter()-started}
    _, incidence = np.unique(np.sort(mesh.edges, axis=1), axis=0, return_counts=True)
    boundary = int(np.count_nonzero(incidence == 1))
    nonmanifold = int(np.count_nonzero(incidence > 2))
    check("boundary_edges", boundary == 0, boundary, 0)
    check("nonmanifold_edges", nonmanifold == 0, nonmanifold, 0)
    singular = singular_vertex_links(mesh)
    check("nonmanifold_vertex_links", not singular, len(singular), 0)
    check("consistent_winding", bool(mesh.is_winding_consistent), bool(mesh.is_winding_consistent), True)
    finite_areas = bool(np.isfinite(mesh.area_faces).all())
    check("finite_triangle_areas", finite_areas, finite_areas, True)
    degenerate = int(np.count_nonzero(mesh.area_faces <= 1e-12))
    check("zero_area_triangles", degenerate == 0, degenerate, 0)
    components = trimesh.graph.connected_components(mesh.face_adjacency,
                      nodes=np.arange(len(mesh.faces)), min_len=1, engine="scipy")
    triangles = mesh.triangles
    contributions = np.einsum("ij,ij->i", triangles[:, 0],
                             np.cross(triangles[:, 1], triangles[:, 2])) / 6.0
    component_volumes = np.asarray([contributions[indices].sum() for indices in components])
    check("positive_component_volume_mm3", bool(np.all(np.isfinite(component_volumes) & (component_volumes > 0))),
          {"minimum": float(component_volumes.min()), "nonpositive_count": int(np.count_nonzero(~np.isfinite(component_volumes) | (component_volumes <= 0)))}, "> 0 for every component")
    reference_report = None
    if reference_mesh is not None:
        reference = _mesh(reference_mesh)
        if not len(reference.vertices) or not np.isfinite(reference.vertices).all():
            raise ValueError("Reference mesh must have finite nonempty vertices")
        forward = cKDTree(reference.vertices).query(mesh.vertices, workers=1)[0]
        reverse = cKDTree(mesh.vertices).query(reference.vertices, workers=1)[0]
        max_error = float(max(forward.max(), reverse.max()))
        reference_report = {"coordinate_metric": "symmetric nearest-vertex distance",
                            "maximum_coordinate_error_mm": max_error,
                            "serialization_tolerance_mm": serialization_tolerance_mm,
                            "reference_triangles": len(reference.faces),
                            "reimported_triangles": len(mesh.faces)}
        check("STL_coordinate_roundtrip", max_error <= serialization_tolerance_mm,
              max_error, serialization_tolerance_mm)
        check("STL_triangle_count", len(mesh.faces) == len(reference.faces), len(mesh.faces), len(reference.faces))
    return {**report, "geometry_status": "FAIL" if any(c["status"] == "FAIL" for c in checks) else "PASS",
            "bounds_mm": mesh.bounds.tolist(), "extents_mm": mesh.extents.tolist(),
            "volume_mm3": float(component_volumes.sum()), "vertices": len(mesh.vertices),
            "triangles": len(mesh.faces), "euler_number": int(mesh.euler_number),
            "connected_components": len(components),
            "component_volume_range_mm3": [float(component_volumes.min()), float(component_volumes.max())],
            "singular_vertex_examples": singular[:30], "roundtrip": reference_report,
            "runtime_seconds": time.perf_counter()-started}


def _sample_indices(count, budget):
    return np.unique(np.linspace(0, count-1, min(count, budget), dtype=np.int64)) if count and budget else np.array([], dtype=np.int64)


def _boolean_mask(value, name, count):
    """Unknown native states must never become True through bool coercion."""
    result = np.asarray(value)
    if result.shape != (count,) or result.dtype.kind != "b":
        raise ValueError(f"CAD {name} must return exactly {count} Boolean classifications")
    return result


def validate_lattice_domain(model, *, classify_points: Callable,
                            max_samples: int = 4096, native_line_probe=None) -> dict:
    """Bounded point AND independent native line evidence against original CAD.

    Point callbacks must return complete Boolean inside_sole, inside_cavity and
    within_z_bounds arrays. Unknown states and omitted evidence cannot pass.
    The optional allowed mask must agree with those three classifications.
    Independent native lines are required for this report's sampled PASS; an
    omitted line callback leaves point evidence available as point_probe_status.
    No finite sampling result establishes global containment.
    """
    if isinstance(max_samples, bool) or not isinstance(max_samples, int) or not 2 <= max_samples <= 200_000:
        raise ValueError("CAD sample budget must be an integer in [2, 200000]")
    mesh = _mesh(model)
    vertex_indices = _sample_indices(len(mesh.vertices), (max_samples+1)//2)
    face_indices = _sample_indices(len(mesh.faces), max_samples//2)
    points = np.vstack([mesh.vertices[vertex_indices], mesh.vertices[mesh.faces[face_indices]].mean(axis=1)])
    report = {"scope": "Bounded independent CAD membership probes; not global containment proof",
              "method": "Deterministic uniformly indexed exported vertices and triangle centroids against native sole/cavity CAD, plus independent native line intervals",
              "maximum_sample_budget": max_samples,
              "coverage": {"total_vertices": len(mesh.vertices), "sampled_vertices": len(vertex_indices),
                           "total_faces": len(mesh.faces), "sampled_face_centroids": len(face_indices),
                           "all_vertices_checked": len(vertex_indices) == len(mesh.vertices),
                           "all_face_centroids_checked": len(face_indices) == len(mesh.faces)},
              "sample_count": len(points), "classification_errors": [],
              "global_containment_status": "NOT_VERIFIED"}
    try:
        if not len(mesh.faces) or not len(points) or not np.isfinite(points).all():
            raise ValueError("Domain probes require a nonempty mesh with finite coordinates")
        result = classify_points(points)
        inside = _boolean_mask(result["inside_sole"], "inside_sole", len(points))
        cavity = _boolean_mask(result["inside_cavity"], "inside_cavity", len(points))
        z_bounds = _boolean_mask(result["within_z_bounds"], "within_z_bounds", len(points))
        errors = list(result.get("classification_errors", []))
        allowed = inside & ~cavity & z_bounds
        if "allowed" in result:
            supplied = _boolean_mask(result["allowed"], "allowed", len(points))
            mismatches = np.flatnonzero(supplied != allowed)
            if len(mismatches):
                errors.append({"reason": "Combined allowed mask disagrees with sole/cavity/Z classifications",
                               "sample_indices": mismatches[:20].tolist(), "affected_samples": len(mismatches)})
        escaped, intrusion, outside_z = np.flatnonzero(~inside), np.flatnonzero(cavity), np.flatnonzero(~z_bounds)
        report.update({"outside_sole_count": len(escaped), "inside_cavity_count": len(intrusion),
                       "outside_z_bounds_count": len(outside_z),
                       "outside_sole_examples_mm": points[escaped[:20]].tolist(),
                       "inside_cavity_examples_mm": points[intrusion[:20]].tolist(),
                       "outside_z_bounds_examples_mm": points[outside_z[:20]].tolist(),
                       "classification_errors": errors,
                       "point_probe_status": "FAIL" if not allowed.all() else "NOT_VERIFIED" if errors else "PASS"})
    except Exception as error:
        report.update({"point_probe_status": "NOT_VERIFIED", "classification_errors": [{"reason": str(error), "affected_samples": len(points)}]})
    lines = {"geometry_status": "NOT_VERIFIED", "measurement_errors": ["Independent native line probe was not supplied"]}
    if native_line_probe is not None:
        try:
            lines = native_line_probe(mesh)
            if not isinstance(lines, dict):
                raise ValueError("Native line probe returned no structured evidence")
            if lines.get("geometry_status") == "PASS":
                complete = (lines.get("planned_lines", 0) > 0
                            and lines.get("measured_lines") == lines["planned_lines"]
                            and lines.get("occupied_lattice_lines", 0) > 0
                            and not lines.get("measurement_errors", []))
                if not complete:
                    raise ValueError("Native line probe claimed PASS without complete occupied-line coverage")
        except Exception as error:
            lines = {"geometry_status": "NOT_VERIFIED", "measurement_errors": [str(error)]}
    report["native_line_probes"] = lines
    statuses = [report["point_probe_status"], lines.get("geometry_status", "NOT_VERIFIED")]
    report["geometry_status"] = "FAIL" if "FAIL" in statuses else "PASS" if all(status == "PASS" for status in statuses) else "NOT_VERIFIED"
    return report


def validate_gyroid_residual(model, *, period_mm: float, threshold: float,
                              domain_field: Callable, cap_tolerance_mm: float,
                              residual_tolerance: float,
                              phase_origin_mm=(0., 0., 0.), max_samples: int = 200_000) -> dict:
    """Measure dimensionless analytic residual; separate clipped-domain caps.

    The returned residual is not a millimetre wall-thickness measurement.
    Boundary labels query the domain field but G is evaluated independently.
    """
    scalars = [period_mm, threshold, cap_tolerance_mm, residual_tolerance]
    if not np.isfinite(scalars).all() or min(scalars) <= 0 or threshold >= 1.5:
        raise ValueError("Residual parameters must be finite and positive")
    phase = np.asarray(phase_origin_mm, dtype=float)
    if phase.shape != (3,) or not np.isfinite(phase).all():
        raise ValueError("Gyroid phase must be three finite coordinates")
    if not isinstance(max_samples, int) or isinstance(max_samples, bool) or not 1 <= max_samples <= 2_000_000:
        raise ValueError("Residual sample budget must be an integer in [1, 2000000]")
    mesh = _mesh(model)
    indices = _sample_indices(len(mesh.vertices), max_samples)
    points = mesh.vertices[indices]
    if not np.isfinite(points).all():
        raise ValueError("Gyroid residual requires finite mesh coordinates")
    domain = np.asarray(domain_field(points), dtype=float)
    if domain.shape != (len(points),) or not np.isfinite(domain).all():
        raise ValueError("Domain residual classifier returned non-finite or incomplete values")
    with np.errstate(over="ignore", invalid="ignore"):
        xyz = (points-phase)*(2*np.pi/period_mm)
    if not np.isfinite(xyz).all():
        raise ValueError("Gyroid evaluation coordinates exceed finite numeric range")
    x, y, z = xyz.T
    values = np.sin(x)*np.cos(y)+np.sin(y)*np.cos(z)+np.sin(z)*np.cos(x)
    wall_field = np.abs(values)-threshold
    residual = np.abs(wall_field)
    cap = np.abs(domain) <= cap_tolerance_mm
    interior = ~cap

    def stats(mask):
        values = residual[mask]
        return {"sample_count": len(values), "maximum_absolute_residual": float(values.max()) if len(values) else None,
                "mean_absolute_residual": float(values.mean()) if len(values) else None,
                "p95_absolute_residual": float(np.quantile(values, .95)) if len(values) else None}

    interior_stats = stats(interior)
    invalid_caps = int(np.count_nonzero(cap & (wall_field > residual_tolerance)))
    outside_domain = int(np.count_nonzero(domain > cap_tolerance_mm))
    failed = invalid_caps > 0 or outside_domain > 0 or (interior.any() and interior_stats["maximum_absolute_residual"] > residual_tolerance)
    return {"geometry_status": "FAIL" if failed else "NOT_VERIFIED" if not interior.any() else "PASS",
            "method": "Independent analytic abs(G)-threshold on extracted physical-mm vertices",
            "units": "dimensionless", "threshold_is_wall_thickness_mm": False,
            "residual_tolerance_dimensionless": residual_tolerance,
            "cap_label_tolerance_mm": cap_tolerance_mm,
            "interior": interior_stats, "domain_caps": stats(cap),
            "cap_vertices_outside_gyroid_material": invalid_caps,
            "vertices_outside_domain_tolerance": outside_domain,
            "total_vertices": len(mesh.vertices), "sampled_vertices": len(indices),
            "maximum_sample_budget": max_samples,
            "all_vertices_checked": len(indices) == len(mesh.vertices),
            "limitation": "Interpolation residual and finite sampling do not establish physical wall thickness or global shape error."}


def _mesh_line_intervals(mesh, y, z, tolerance_mm):
    """Independent all-triangle X-line crossings, bounded by one triangle array.

    This does not consult the generator scalar grid or spatial acceleration.
    Duplicate hits at a triangulation edge count as one geometric crossing.
    """
    triangles = mesh.triangles
    a, b, c = triangles[:, 0], triangles[:, 1], triangles[:, 2]
    v, w = b-a, c-a
    determinant = v[:, 1]*w[:, 2]-v[:, 2]*w[:, 1]
    candidate = np.abs(determinant) > 1e-12
    v, w, a, d = v[candidate], w[candidate], a[candidate], determinant[candidate]
    dy, dz = y-a[:, 1], z-a[:, 2]
    beta = (dy*w[:, 2]-dz*w[:, 1])/d
    gamma = (v[:, 1]*dz-v[:, 2]*dy)/d
    hit = (beta >= -1e-10) & (gamma >= -1e-10) & (beta+gamma <= 1+1e-10)
    x = (a[:, 0]+beta*v[:, 0]+gamma*w[:, 0])[hit]
    sign = np.sign(d[hit])  # normal X: negative enters; positive exits.
    order = np.argsort(x)
    crossings = []
    for coordinate, direction in zip(x[order], sign[order]):
        if crossings and abs(coordinate-crossings[-1][0]) <= tolerance_mm:
            crossings[-1][1].add(int(direction))
        else:
            crossings.append([float(coordinate), {int(direction)}])
    depth, start, intervals = 0, None, []
    for coordinate, signs in crossings:
        change = (-1 if 1 in signs else 0)+(1 if -1 in signs else 0)
        next_depth = depth+change
        if next_depth < 0:
            raise ValueError("Line crosses an outward exit before entry; topology, orientation or tangency is ambiguous")
        if depth == 0 and next_depth > 0:
            start = coordinate
        if depth > 0 and next_depth == 0 and coordinate-start > tolerance_mm:
            intervals.append([start, coordinate])
        depth = next_depth
    if depth != 0:
        raise ValueError("Mesh line has an unmatched entry/exit")
    return intervals


def validate_native_line_containment(model, *, scanline: Callable,
                                      maximum_lines: int = 32,
                                      tolerance_mm: float = 2e-5) -> dict:
    """Compare actual STL material intervals against independent CAD X-lines.

    ``scanline(y,z)`` returns sole_intervals_mm and cavity_intervals_mm from
    native B-Rep intersections. Non-grid fractions avoid reusing generation
    scanline sites. All selected lines, empty lines and measurement failures
    remain in the report. Finite lines cannot prove global containment.
    """
    if isinstance(maximum_lines, bool) or not isinstance(maximum_lines, int) or not 1 <= maximum_lines <= 128:
        raise ValueError("Native line budget must be an integer in [1, 128]")
    if not np.isfinite(tolerance_mm) or tolerance_mm <= 0:
        raise ValueError("Line containment tolerance must be finite and positive")
    mesh = _mesh(model)
    if (not len(mesh.faces) or not len(mesh.vertices) or not np.isfinite(mesh.vertices).all()
            or not mesh.is_watertight or not mesh.is_winding_consistent):
        return {"geometry_status": "NOT_VERIFIED", "global_containment_status": "NOT_VERIFIED",
                "maximum_lines": maximum_lines, "planned_lines": 0, "measured_lines": 0,
                "occupied_lattice_lines": 0, "samples": [],
                "measurement_errors": ["Native interval probes require a finite, closed, consistently oriented input mesh"]}
    rows = int(np.ceil(np.sqrt(maximum_lines)))
    # Irrational-looking non-grid offsets are deterministic, not random.
    fractions = (np.arange(rows)+.38196601125)/rows
    sites = [(mesh.bounds[0, 1]+fy*mesh.extents[1],
              mesh.bounds[0, 2]+fz*mesh.extents[2]) for fy in fractions for fz in fractions][:maximum_lines]
    samples, errors = [], []
    for y, z in sites:
        record = {"y_mm": float(y), "z_mm": float(z)}
        try:
            intervals = _mesh_line_intervals(mesh, y, z, tolerance_mm)
            cad = scanline(float(y), float(z))
            sole = np.asarray(cad["sole_intervals_mm"], dtype=float)
            cavity = np.asarray(cad["cavity_intervals_mm"], dtype=float)
            # [] denotes no intervals; all nonempty arrays must be explicit N×2.
            sole = sole.reshape(0, 2) if sole.shape == (0,) else sole
            cavity = cavity.reshape(0, 2) if cavity.shape == (0,) else cavity
            if sole.ndim != 2 or cavity.ndim != 2 or sole.shape[1] != 2 or cavity.shape[1] != 2:
                raise ValueError("Native CAD intervals must have shape (N,2)")
            if not np.isfinite(sole).all() or not np.isfinite(cavity).all():
                raise ValueError("Native CAD intervals are nonfinite")
            if any(end <= start for start, end in [*sole, *cavity]):
                raise ValueError("Native CAD interval is reversed or empty")
            invalid, intrusions = [], []
            z_value = cad["within_z_bounds"]
            if not isinstance(z_value, (bool, np.bool_)):
                raise ValueError("Native line Z classification must be Boolean")
            z_allowed = bool(z_value)
            for lo, hi in intervals:
                if not z_allowed or not any(lo >= a-tolerance_mm and hi <= b+tolerance_mm for a, b in sole):
                    invalid.append([lo, hi])
                if any(min(hi, b)-max(lo, a) > tolerance_mm for a, b in cavity):
                    intrusions.append([lo, hi])
            record.update({"lattice_intervals_mm": intervals, "sole_intervals_mm": sole.tolist(),
                           "cavity_intervals_mm": cavity.tolist(), "within_z_bounds": z_allowed,
                           "outside_sole_intervals_mm": invalid,
                           "cavity_intrusion_intervals_mm": intrusions,
                           "status": "FAIL" if invalid or intrusions else "PASS"})
            samples.append(record)
        except Exception as error:
            errors.append({**record, "reason": str(error)})
    occupied = sum(bool(sample["lattice_intervals_mm"]) for sample in samples)
    return {"geometry_status": "FAIL" if any(sample["status"] == "FAIL" for sample in samples)
            else "NOT_VERIFIED" if errors or not occupied else "PASS",
            "scope": "Finite independent native CAD line containment and cavity exclusion; not a global proof",
            "global_containment_status": "NOT_VERIFIED",
            "maximum_lines": maximum_lines, "planned_lines": len(sites),
            "measured_lines": len(samples), "occupied_lattice_lines": occupied,
            "tolerance_mm": tolerance_mm, "samples": samples, "measurement_errors": errors}
