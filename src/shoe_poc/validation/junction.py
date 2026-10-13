"""Independent normals on actual final-CAD exterior edges in the sole band.

This finite check detects a corner; it does not certify global G1 continuity.
Its angle limit is an experimental numerical target for this M1 template.
"""
from __future__ import annotations

import math

import numpy as np

from shoe_poc.reporting import utc_now
from .surface import _LoadedSolidClassifier, _distance_to_shape

JUNCTION_ANGLE_LIMIT_DEGREES = 2.0
BOUNDARY_EXCLUSION_MM = 1e-5
SAMPLE_FRACTIONS = (.05, .25, .5, .75, .95)


def _oriented_normal_angle(first, second):
    """Preserve the sign: opposite exterior normals are a 180-degree turn."""
    a, b = np.asarray(first, dtype=float), np.asarray(second, dtype=float)
    lengths = np.linalg.norm(a)*np.linalg.norm(b)
    if not np.isfinite(lengths) or lengths <= 1e-15:
        raise ValueError("Undefined or non-finite CAD face normal")
    return float(math.degrees(math.acos(float(np.clip(np.dot(a, b)/lengths, -1., 1.)))))


def validate_junction(shape, cavity, opening, source_box, *, maximum_z_mm,
                      minimum_z_mm=None, angle_limit_degrees=JUNCTION_ANGLE_LIMIT_DEGREES):
    """Measure actual oriented adjacent faces, excluding real cutter boundaries.

    Every shared edge in the supplied transition band is considered, including
    stock/stock joins. Face geometry types are evidence, never an exclusion rule.
    ``Face.normal_at`` calls OCCT ``BRepGProp_Face.Normal`` with the oriented
    TopoDS face, so normals follow the final solid's face orientation. Positive
    volume and valid solid topology are prerequisites. No absolute dot is used.
    Errors and missing heel/middle/toe coverage prevent PASS.
    """
    from build123d import Edge, Face
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE
    from OCP.TopExp import TopExp
    from OCP.TopTools import TopTools_IndexedDataMapOfShapeListOfShape

    if not 0 <= angle_limit_degrees < 90:
        raise ValueError("Junction angle limit must be in [0, 90) degrees")
    lower = source_box.min.Z+2. if minimum_z_mm is None else float(minimum_z_mm)
    upper = float(maximum_z_mm)
    report = {
        "status": "NOT_VERIFIED", "geometry_status": "NOT_VERIFIED",
        "method": "Actual final B-Rep shared-edge samples with oriented adjacent-face normals",
        "checked_at": utc_now(), "angle_limit_degrees": float(angle_limit_degrees),
        "tolerance_scope": "Experimental M1 numerical shape target; not a manufacturing standard or global G1 certificate",
        "normal_orientation": "OCCT BRepGProp_Face.Normal on oriented final-solid faces; signed dot product",
        "sample_fractions": list(SAMPLE_FRACTIONS),
        "transition_z_band_mm": [lower, upper],
        "boundary_exclusion_distance_mm": BOUNDARY_EXCLUSION_MM,
        "exclusion_method": "Exact point-to-actual-cavity/opening CAD distance and solid classification",
        "records": [], "excluded_boundary_records": [], "measurement_errors": [],
        "regions": {}, "maximum_normal_angle_degrees": None,
        "exterior_sample_count": 0, "excluded_boundary_sample_count": 0,
        "measurement_error_count": 0,
        "limitations": ["Finite samples cannot establish global tangent or curvature continuity.",
                        "Visual/user appearance and physical performance remain unverified."],
    }
    if not shape.is_valid or len(shape.solids()) != 1 or shape.volume <= 0:
        report["status"] = report["geometry_status"] = "FAIL"
        report["measurement_errors"].append({"reason": "Final CAD is not a valid positive single solid"})
        report["measurement_error_count"] = 1
        return report
    classifiers = {
        name: tuple(_LoadedSolidClassifier(s) for s in tool.solids()) if tool is not None else ()
        for name, tool in (("cavity", cavity), ("opening", opening))
    }
    adjacency = TopTools_IndexedDataMapOfShapeListOfShape()
    TopExp.MapShapesAndAncestors_s(shape.wrapped, TopAbs_EDGE, TopAbs_FACE, adjacency)
    extent = float(source_box.size.X)

    def region_of(x):
        if x <= source_box.min.X+.08*extent:
            return "heel"
        if x >= source_box.max.X-.08*extent:
            return "toe"
        return "middle"

    for index in range(1, adjacency.Extent()+1):
        edge = Edge(adjacency.FindKey(index))
        box = edge.bounding_box()
        if box.max.Z < lower or box.min.Z > upper:
            continue
        faces = []
        for wrapped in adjacency.FindFromIndex(index):
            if not any(wrapped.IsSame(f.wrapped) for f in faces):
                faces.append(Face(wrapped))
        # A periodic seam belongs to a single face, not a two-face junction.
        if len(faces) == 1:
            continue
        if len(faces) != 2:
            report["measurement_errors"].append({"edge_index": index,
                "reason": "Edge does not have exactly two adjacent faces", "face_count": len(faces)})
            continue
        for fraction in SAMPLE_FRACTIONS:
            record = {"edge_index": index, "edge_fraction": fraction,
                      "edge_length_mm": float(edge.length),
                      "face_types": [face.geom_type.name for face in faces]}
            try:
                point = edge.position_at(fraction)
                if not lower <= point.Z <= upper:
                    continue
                record["point_mm"] = list(point)
                record["region"] = region_of(point.X)
                distances = {}
                for name, tool in (("cavity", cavity), ("opening", opening)):
                    distances[name] = (_distance_to_shape(tool, np.asarray(tuple(point)), classifiers[name])
                                       if tool is not None else None)
                record["cavity_distance_mm"] = distances["cavity"]
                record["opening_distance_mm"] = distances["opening"]
                boundaries = [name for name, distance in distances.items()
                              if distance is not None and distance <= BOUNDARY_EXCLUSION_MM]
                if boundaries:
                    record["excluded_as"] = boundaries
                    report["excluded_boundary_records"].append(record)
                    continue
                normals = [tuple(face.normal_at(point)) for face in faces]
                record["oriented_normals"] = [list(n) for n in normals]
                record["normal_angle_degrees"] = _oriented_normal_angle(*normals)
                record["status"] = "PASS" if record["normal_angle_degrees"] <= angle_limit_degrees else "FAIL"
                report["records"].append(record)
            except Exception as error:
                record["reason"] = f"{type(error).__name__}: {error}"
                report["measurement_errors"].append(record)
    for region in ("heel", "middle", "toe"):
        records = [r for r in report["records"] if r["region"] == region]
        maximum = max((r["normal_angle_degrees"] for r in records), default=None)
        status = ("FAIL" if any(r["status"] == "FAIL" for r in records)
                  else "PASS" if records else "NOT_VERIFIED")
        report["regions"][region] = {"geometry_status": status, "sample_count": len(records),
                                     "maximum_normal_angle_degrees": maximum}
    report["maximum_normal_angle_degrees"] = max(
        (r["normal_angle_degrees"] for r in report["records"]), default=None)
    states = [r["geometry_status"] for r in report["regions"].values()]
    status = ("FAIL" if "FAIL" in states else "NOT_VERIFIED"
              if report["measurement_errors"] or "NOT_VERIFIED" in states else "PASS")
    report["status"] = report["geometry_status"] = status
    report["exterior_sample_count"] = len(report["records"])
    report["excluded_boundary_sample_count"] = len(report["excluded_boundary_records"])
    report["measurement_error_count"] = len(report["measurement_errors"])
    return report


def measure_bottom_round(shape, source_box, spec):
    """Independent trimmed-face principal-curvature samples at the real bottom round."""
    from OCP.GeomLProp import GeomLProp_SLProps
    from OCP.BRepTools import BRepTools
    from OCP.BRepClass import BRepClass_FaceClassifier
    from OCP.TopAbs import TopAbs_IN
    from OCP.gp import gp_Pnt2d
    from shoe_poc.geometry.sole import _rocker
    profile = spec.sole.profile_parameters
    bottom = source_box.min.Z-spec.sole.height_mm
    target = profile.edge_radius_mm
    if target == 0:
        return {"geometry_status": "NOT_REQUIRED", "target_radius_mm": 0,
                "sample_count": 0, "records": [], "measurement_errors": [],
                "reason": "Configuration explicitly disables the bottom round"}
    records, errors = [], []
    for face in shape.faces():
        u0, u1, v0, v1 = BRepTools.UVBounds_s(face.wrapped)
        surface = face.geom_adaptor()
        for u in np.linspace(u0, u1, 11)[1:-1]:
            for v in np.linspace(v0, v1, 21)[1:-1]:
                if BRepClass_FaceClassifier(face.wrapped, gp_Pnt2d(u, v), 1e-7).State() != TopAbs_IN:
                    continue
                point = surface.Value(u, v)
                rise = point.Z()-(bottom+_rocker(point.X(), source_box, profile))
                if not target*.125 < rise < target*.875:
                    continue
                try:
                    props = GeomLProp_SLProps(surface, u, v, 2, 1e-7)
                    if not props.IsCurvatureDefined():
                        raise ValueError("Curvature undefined at actual round sample")
                    curvature = max(abs(props.MaxCurvature()), abs(props.MinCurvature()))
                    if curvature <= 1e-8:
                        raise ValueError("No nonzero round curvature at selected sample")
                    records.append({"point_mm": [point.X(), point.Y(), point.Z()],
                                    "radius_mm": 1/curvature})
                except Exception as error:
                    errors.append({"point_mm": [point.X(), point.Y(), point.Z()], "reason": str(error)})
    radii = [r["radius_mm"] for r in records]
    deviation = max((abs(radius-target) for radius in radii), default=None)
    status = ("FAIL" if deviation is not None and deviation > .02
              else "NOT_VERIFIED" if errors or not radii else "PASS")
    return {"geometry_status": status, "method": "Actual trim-classified CAD face principal curvature near the rocker floor",
            "target_radius_mm": target, "sample_count": len(records),
            "minimum_measured_radius_mm": min(radii, default=None),
            "maximum_measured_radius_mm": max(radii, default=None),
            "maximum_radius_deviation_mm": deviation, "numerical_radius_tolerance_mm": .02,
            "tolerance_scope": "Experimental M1 CAD numerical check, not a manufacturing tolerance",
            "measurement_errors": errors, "records": records,
            "limitations": ["Finite principal-curvature samples cannot certify all points or global curvature continuity."]}
