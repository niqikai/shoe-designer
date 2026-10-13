"""Independent, bounded B-Rep surface probes; sampling cannot certify minima.

Points and normals come from the trimmed CAD last, never from the generator's
section rings or declared offset distances. Intersections use the final shoe
B-Rep. The fixed fit tolerances are experimental M1 numerical targets, not TPU
manufacturing limits. Every excluded or failed-to-measure site remains visible.
"""
from __future__ import annotations

import math

import numpy as np

from shoe_poc.reporting import utc_now

FIT_ERROR_MM = 0.25
THICKNESS_ERROR_MM = 0.35
MAX_SAMPLE_SITES = 512
CAD_TOLERANCE_MM = 1e-6


def _stats(values, target):
    if not values:
        return {"count": 0, "minimum_mm": None, "maximum_mm": None,
                "mean_mm": None, "maximum_absolute_deviation_mm": None}
    return {"count": len(values), "minimum_mm": min(values), "maximum_mm": max(values),
            "mean_mm": float(np.mean(values)),
            "maximum_absolute_deviation_mm": max(abs(v - target) for v in values)}


def _shape_quality(shape):
    from build123d import GeomType
    faces = shape.faces()
    area = sum(f.area for f in faces)
    planar_area = sum(f.area for f in faces if f.geom_type == GeomType.PLANE)
    return {"topology_valid": bool(shape.is_valid), "solid_count": len(shape.solids()),
            "face_count": len(faces), "surface_area_mm2": float(area),
            "planar_face_area_fraction": float(planar_area / area) if area else None,
            "scope": "Topology and face-type descriptors only; smoothness and appearance require visual review."}


def _candidate_sites(last):
    """Classify UV candidates against trims before evaluating exact surface normals."""
    from OCP.BRepClass import BRepClass_FaceClassifier
    from OCP.TopAbs import TopAbs_IN
    from OCP.gp import gp_Pnt2d
    from OCP.BRepTools import BRepTools
    faces = last.faces()
    # Center sites first ensure small faces are represented before subdivision.
    uv_sites = [(0.5, 0.5)] + [(u, v) for u in (0.2, 0.5, 0.8)
                              for v in (0.2, 0.5, 0.8) if (u, v) != (0.5, 0.5)]
    sites, uncovered = [], set(range(len(faces)))
    for u, v in uv_sites:
        for face_index, face in enumerate(faces):
            a, b, c, d = BRepTools.UVBounds_s(face.wrapped)
            classifier = BRepClass_FaceClassifier(face.wrapped, gp_Pnt2d(a+u*(b-a), c+v*(d-c)),
                                                  CAD_TOLERANCE_MM)
            if classifier.State() != TopAbs_IN:
                continue
            try:
                point, normal = tuple(face.position_at(u, v)), tuple(face.normal_at(u, v))
                if not all(math.isfinite(n) for n in (*point, *normal)):
                    continue
            except (ValueError, RuntimeError):
                continue
            sites.append((face_index, point, normal))
            uncovered.discard(face_index)
    # Select the first available site for every sampled face, then distribute the
    # remaining budget across subdivisions instead of silently ignoring a tail.
    first, extra, seen = [], [], set()
    for site in sites:
        if site[0] not in seen:
            first.append(site)
            seen.add(site[0])
        else:
            extra.append(site)
    if len(first) > MAX_SAMPLE_SITES:
        selected = [first[i] for i in np.linspace(0, len(first)-1, MAX_SAMPLE_SITES, dtype=int)]
    else:
        budget = MAX_SAMPLE_SITES-len(first)
        selected = first + ([extra[i] for i in np.linspace(0, len(extra)-1, min(budget, len(extra)), dtype=int)]
                            if budget and extra else [])
    covered = {s[0] for s in selected}
    return selected, {"candidate_count": len(sites), "planned_site_count": len(selected),
                      "maximum_site_count": MAX_SAMPLE_SITES,
                      "last_face_count": len(faces), "sampled_face_count": len(covered),
                      "faces_without_valid_uv_site": sorted(uncovered),
                      "faces_omitted_by_budget": sorted(seen-covered)}


def _ray_hits(intersector, point, direction, length):
    from OCP.gp import gp_Dir, gp_Lin, gp_Pnt
    intersector.Perform(gp_Lin(gp_Pnt(*point), gp_Dir(*direction)), 0.0, length)
    if not intersector.IsDone():
        raise ValueError("CAD line intersector did not complete")
    hits = sorted(float(intersector.WParameter(i)) for i in range(1, intersector.NbPnt()+1))
    unique = []
    for distance in hits:
        if distance >= -CAD_TOLERANCE_MM and (not unique or distance-unique[-1] > 1e-5):
            unique.append(max(0.0, distance))
    return unique


class _LoadedSolidClassifier:
    """Reuse one native classifier for an unchanged B-Rep within one probe run."""
    def __init__(self, shape):
        from OCP.BRepClass3d import BRepClass3d_SolidClassifier
        self.classifier = BRepClass3d_SolidClassifier(shape.wrapped)

    def inside(self, point):
        from OCP.TopAbs import TopAbs_IN, TopAbs_ON
        from OCP.gp import gp_Pnt
        self.classifier.Perform(gp_Pnt(*point), CAD_TOLERANCE_MM)
        return self.classifier.State() in (TopAbs_IN, TopAbs_ON)


def _inside(shape, point):
    return _LoadedSolidClassifier(shape).inside(point)


def _distance_to_shape(shape, point, solid_classifiers=None):
    from build123d import Vertex
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    # DistShapeShape detects an interior vertex for a Solid, but a Compound of
    # that same solid can return distance to its boundary instead. Classify the
    # actual member solids first so material membership remains independent of
    # the wrapper returned by a Boolean/import operation. Faces and empty shapes
    # have no solids and retain the usual geometric distance behavior.
    if solid_classifiers is None:
        solid_classifiers = tuple(_LoadedSolidClassifier(solid) for solid in shape.solids())
    if any(classifier.inside(point) for classifier in solid_classifiers):
        return 0.0
    distance = BRepExtrema_DistShapeShape(Vertex(*point).wrapped, shape.wrapped)
    if not distance.IsDone():
        raise ValueError("CAD distance to opening did not complete")
    # At this point the vertex lies outside every solid member.
    return float(distance.Value())


def validate_surface_samples(last_shape, build, spec) -> dict:
    """Return JSON-safe actual directional measurements and explicit blind spots.

    ``build`` supplies final ``shape`` and actual ``opening`` cutter. Geometry
    status FAIL means an executed check failed; PARTIAL means a planned site or
    a face could not be measured. PASS only describes executed, covered probes.
    The overall status remains PARTIAL because global minima, self-intersections
    and visual/user acceptance are not established by a finite sample set.
    """
    from OCP.IntCurvesFace import IntCurvesFace_ShapeIntersector
    checks = []
    checked_at = utc_now()

    def check(name, status, value, threshold, source="Independent B-Rep surface sampling, M1 experimental fit policy"):
        checks.append({"name": name, "status": status, "value": value,
                       "threshold": threshold, "source": source, "checked_at": checked_at})

    quality = {"last": _shape_quality(last_shape), "shoe": _shape_quality(build.shape),
               "cavity": _shape_quality(build.cavity), "opening": _shape_quality(build.opening)}
    valid = all(q["topology_valid"] and q["solid_count"] == 1 for q in quality.values())
    check("valid_single_solid_breps", "PASS" if valid else "FAIL", quality, "valid, one solid each")
    report = {"method": "Trim-classified CAD last UV points and outward face normals; exact B-Rep line intersections with final shoe and midpoint solid classification",
              "scope": "Sampled outward-normal clearance and directional thickness; not global shortest-distance wall thickness or manufacturing qualification",
              "targets_mm": {"clearance": spec.upper.clearance_mm, "thickness": spec.upper.thickness_mm},
              "tolerances_mm": {"clearance_absolute_error": FIT_ERROR_MM,
                                "thickness_absolute_error": THICKNESS_ERROR_MM,
                                "cad_intersection": CAD_TOLERANCE_MM},
              "quality_descriptors": quality, "samples": [], "excluded_samples": [],
              "unmeasurable_regions": [], "checks": checks,
              "sampled_clearance_mm": _stats([], spec.upper.clearance_mm),
              "sampled_thickness_mm": _stats([], spec.upper.thickness_mm)}
    if valid:
        # Loading the same source or shoe for each point dominated measured
        # runtime. Classifier state is refreshed by Perform for every query;
        # keep these native objects call-local so another build cannot reuse a
        # stale shape. The solid-member contract also covers Compound wrappers.
        last_classifier = _LoadedSolidClassifier(last_shape)
        shoe_classifier = _LoadedSolidClassifier(build.shape)
        opening_classifiers = tuple(_LoadedSolidClassifier(s) for s in build.opening.solids())
        sole_classifiers = tuple(_LoadedSolidClassifier(s) for s in build.sole.solids())
        sites, coverage = _candidate_sites(last_shape)
        report["coverage"] = coverage
        box = last_shape.bounding_box()
        report["exclusion_policy"] = {
            "sole_connection_below_z_mm": box.min.Z + max(3.0, spec.upper.thickness_mm+1.0),
            "sole_junction_distance_mm": 1.0,
            "opening_rim_distance_mm": spec.upper.clearance_mm + spec.upper.thickness_mm + 1.0,
            "description": "Exclude sole junction and points within or near the actual opening cutter; record each excluded point. No generator section data is used."}
        ray_length = math.sqrt(sum(v*v for v in build.shape.bounding_box().size)) + 10.0
        intersector = IntCurvesFace_ShapeIntersector()
        intersector.Load(build.shape.wrapped, CAD_TOLERANCE_MM)
        clearances, thicknesses = [], []
        for face_index, point, normal in sites:
            entry = {"face_index": face_index, "point_mm": list(point), "outward_normal": list(normal)}
            try:
                if point[2] <= report["exclusion_policy"]["sole_connection_below_z_mm"]:
                    entry["reason"] = "sole_connection"
                    report["excluded_samples"].append(entry)
                    continue
                if _distance_to_shape(build.opening, point, opening_classifiers) <= report["exclusion_policy"]["opening_rim_distance_mm"]:
                    entry["reason"] = "opening_removed_surface_or_rim"
                    report["excluded_samples"].append(entry)
                    continue
                p, n = np.asarray(point), np.asarray(normal)
                # Verify orientation against the actual solid rather than assuming
                # every face's parameterization has an outward derivative normal.
                if not last_classifier.inside(p-n*1e-3) or last_classifier.inside(p+n*1e-3):
                    raise ValueError("last normal is tangent, ambiguous, or not outward")
                hits = _ray_hits(intersector, point, normal, ray_length)
                boundaries = sorted(set([0.0, *hits, ray_length]))
                intervals = [(a, b) for a, b in zip(boundaries, boundaries[1:])
                             if b-a > 1e-5 and shoe_classifier.inside(p+n*((a+b)/2))]
                if not intervals:
                    raise ValueError("outward ray found no shoe material away from excluded opening/sole regions")
                a, b = intervals[0]
                if b >= ray_length-1e-5:
                    raise ValueError("outward material interval has no closed exit")
                # The sole can rise above the last base. Exclude the actual
                # material interval where it intersects or approaches the CAD
                # sole, rather than declaring a tall fixed-height blind band.
                if any(_distance_to_shape(build.sole, p+n*t, sole_classifiers) <= report["exclusion_policy"]["sole_junction_distance_mm"]
                       for t in (a+1e-4, (a+b)/2, b-1e-4)):
                    entry["reason"] = "actual_sole_or_junction_material_interval"
                    entry["material_intervals_mm"] = [[float(a), float(b)] for a, b in intervals]
                    report["excluded_samples"].append(entry)
                    continue
                clearance, thickness = float(a), float(b-a)
                clearances.append(clearance)
                thicknesses.append(thickness)
                entry.update({"clearance_mm": clearance, "thickness_mm": thickness,
                              "clearance_deviation_mm": clearance-spec.upper.clearance_mm,
                              "thickness_deviation_mm": thickness-spec.upper.thickness_mm,
                              "material_intervals_mm": [[float(a), float(b)] for a, b in intervals],
                              "status": "PASS" if (abs(clearance-spec.upper.clearance_mm) <= FIT_ERROR_MM
                                        and abs(thickness-spec.upper.thickness_mm) <= THICKNESS_ERROR_MM) else "FAIL"})
                report["samples"].append(entry)
            except (ValueError, RuntimeError) as exc:
                entry["reason"] = str(exc)
                report["unmeasurable_regions"].append(entry)
        report["sampled_clearance_mm"] = _stats(clearances, spec.upper.clearance_mm)
        report["sampled_thickness_mm"] = _stats(thicknesses, spec.upper.thickness_mm)
        for name, values, target, tolerance in (
            ("sampled_normal_clearance", clearances, spec.upper.clearance_mm, FIT_ERROR_MM),
            ("sampled_directional_thickness", thicknesses, spec.upper.thickness_mm, THICKNESS_ERROR_MM)):
            passed = values and all(abs(value-target) <= tolerance for value in values)
            check(name, "PASS" if passed else "FAIL" if values else "NOT_VERIFIED",
                  _stats(values, target), {"target_mm": target, "absolute_error_mm": tolerance})
        incomplete = (report["unmeasurable_regions"] or coverage["faces_without_valid_uv_site"]
                      or coverage["faces_omitted_by_budget"] or not clearances)
        check("planned_surface_probe_coverage", "NOT_VERIFIED" if incomplete else "PASS",
              {"measured_sites": len(clearances), "excluded_sites": len(report["excluded_samples"]),
               "unmeasurable_sites": len(report["unmeasurable_regions"]), **coverage},
              "At least one measurement; all selected non-excluded sites measurable; every face represented")
        # This independent Boolean uses the actual cutter, not the assembler's
        # stored overlap metric. A capped collar produces positive common volume.
        common = build.shape.intersect(build.opening)
        overlap = float(sum(s.volume for s in common.solids())) if common is not None else 0.0
        check("opening_cutter_unobstructed", "PASS" if overlap <= 0.01 else "FAIL", overlap,
              {"maximum_material_overlap_mm3": 0.01})
        common = build.cavity.intersect(build.opening)
        entry_volume = float(sum(s.volume for s in common.solids())) if common is not None else 0.0
        check("opening_reaches_cavity", "PASS" if entry_volume > 1.0 else "FAIL", entry_volume,
              {"minimum_entry_common_volume_mm3": 1.0})
        common = build.shape.intersect(last_shape)
        last_overlap = float(sum(s.volume for s in common.solids())) if common is not None else 0.0
        check("actual_last_exclusion", "PASS" if last_overlap <= 0.01 else "FAIL", last_overlap,
              {"maximum_last_common_volume_mm3": 0.01})
    for name in ("global_minimum_wall_thickness", "global_minimum_clearance",
                 "global_surface_self_intersections", "physical_insertion_and_fit",
                 "user_visual_acceptance", "SLS_TPU_manufacturing_qualification"):
        check(name, "NOT_VERIFIED", None, None, "Finite samples cannot establish this property")
    # Separate partial sampling from the always partial global acceptance scope.
    measured_checks = checks[:len(checks)-6]
    report["geometry_status"] = ("FAIL" if any(c["status"] == "FAIL" for c in measured_checks)
                                 else "PARTIAL" if any(c["status"] == "NOT_VERIFIED" for c in measured_checks)
                                 else "PASS")
    report["overall_status"] = "FAIL" if report["geometry_status"] == "FAIL" else "PARTIAL"
    report["manufacturing_status"] = "NOT_VERIFIED"
    return report
