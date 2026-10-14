"""Bounded appearance descriptors and independent CAD safety probes.

These measurements describe the supplied geometry, never generator metadata or
rendering normals. A numerical change cannot approve an annotated appearance.
Finite sections and rays do not certify global wall thickness or absence of folds.
"""
from __future__ import annotations

import numpy as np

from .surface import CAD_TOLERANCE_MM, THICKNESS_ERROR_MM, _ray_hits
from .volume import measure_cad_volume

SECTION_FRACTIONS = tuple(sorted(set([float(v) for v in np.linspace(.02, .98, 25)] +
                                     [.2, .4, .48, .55, .6, .63, .64, .65, .68, .7, .75, .85, .9, .95, .98])))


def _volume(shape):
    if isinstance(shape, (list, tuple)):
        return sum(_volume(member) for member in shape)
    if shape is None or shape.wrapped is None or shape.wrapped.IsNull() or not shape.solids():
        return 0.
    return measure_cad_volume(shape)["volume_mm3"]


def _sections(shape, last_box, fractions=SECTION_FRACTIONS):
    from build123d import Compound, Plane
    rows = []
    for fraction in fractions:
        x = last_box.min.X + fraction * last_box.size.X
        row = {"source_length_fraction": fraction, "x_mm": x}
        try:
            section = shape.intersect(Plane(origin=(x, 0, 0), z_dir=(1, 0, 0)))
            if isinstance(section, (list, tuple)):
                section = Compound(children=list(section))
            if section is None or not section.faces():
                raise ValueError("No material face in the actual CAD section")
            box = section.bounding_box()
            # A section can retain several adjacent planar faces at Boolean
            # seams. Count geometric components rather than rejecting a seam
            # merely because the kernel did not unify coplanar faces.
            faces = list(section.faces())
            groups = [{i} for i in range(len(faces))]
            for i in range(len(faces)):
                for j in range(i):
                    if faces[i].distance_to(faces[j]) <= CAD_TOLERANCE_MM:
                        left = next(g for g in groups if i in g)
                        right = next(g for g in groups if j in g)
                        if left is not right:
                            left.update(right)
                            groups.remove(right)
            row.update(width_mm=box.size.Y, y_min_mm=box.min.Y, y_max_mm=box.max.Y,
                       floor_z_mm=box.min.Z, roof_z_mm=box.max.Z,
                       material_face_count=len(faces), material_component_count=len(groups), wire_count=len(section.wires()))
        except (ValueError, RuntimeError) as exc:
            row["unmeasurable_reason"] = str(exc)
        rows.append(row)
    return rows


def _profile(rows):
    measured = [row for row in rows if "width_mm" in row]
    if len(measured) < 3:
        return {"status": "NOT_VERIFIED"}
    x, widths, roof = (np.asarray([r[key] for r in measured]) for key in ("x_mm", "width_mm", "roof_z_mm"))
    area = float(np.trapezoid(widths, x))
    rear = [r["width_mm"] for r in measured if .1 <= r["source_length_fraction"] <= .3]
    front = [r["width_mm"] for r in measured if .65 <= r["source_length_fraction"] <= .85]
    prominence = []
    for i in range(1, len(measured)-1):
        chord = roof[i-1] + (roof[i+1]-roof[i-1])*(x[i]-x[i-1])/(x[i+1]-x[i-1])
        prominence.append(float(roof[i]-chord))
    return {"status": "OBSERVED", "sampled_projected_area_proxy_mm2": area,
            "sampled_width_weighted_center_x_mm": float(np.trapezoid(x*widths, x)/area),
            "mean_forefoot_width_mm": float(np.mean(front)) if front else None,
            "mean_rearfoot_width_mm": float(np.mean(rear)) if rear else None,
            "forefoot_rearfoot_width_ratio": float(np.mean(front)/np.mean(rear)) if rear and front else None,
            "maximum_adjacent_section_roof_prominence_mm": max(prominence),
            "maximum_sampled_roof_slope": float(np.max(np.abs(np.diff(roof)/np.diff(x)))),
            "scope": "Fixed exact CAD sections; area/center and roof prominence are sampled silhouette descriptors, not exact projected area or a curvature certificate"}


def _intersector(shape):
    from OCP.IntCurvesFace import IntCurvesFace_ShapeIntersector
    result = IntCurvesFace_ShapeIntersector()
    result.Load(shape.wrapped, CAD_TOLERANCE_MM)
    return result


def _sidewall(build, last_box, radius):
    """Measure final exterior, above the bottom rounding window only."""
    sole_rows = _sections(build.sole, last_box, (.2, .4, .68, .85, .95))
    shoe_rows = _sections(build.shape, last_box, (.2, .4, .68, .85, .95))
    probe, result = _intersector(build.shape), []
    box = build.shape.bounding_box()
    for sole, shoe in zip(sole_rows, shoe_rows):
        row = {"source_length_fraction": sole["source_length_fraction"]}
        try:
            lo, hi = shoe["floor_z_mm"]+radius+.25, sole["roof_z_mm"]-.25
            if hi-lo < 1.:
                raise ValueError("No one-mm sidewall window remains after excluding bottom rounding")
            points = []
            for z in np.linspace(lo, hi, 7):
                hits = _ray_hits(probe, (shoe["x_mm"], box.min.Y-5, float(z)), (0, 1, 0), box.size.Y+10)
                if len(hits) < 2:
                    raise ValueError("Exterior horizontal line has fewer than two CAD intersections")
                points.append((float(z), box.min.Y-5+hits[0], box.min.Y-5+hits[-1]))
            sides = []
            for side in (1, 2):
                z, y = np.asarray([p[0] for p in points]), np.asarray([p[side] for p in points])
                chord = y[0]+(y[-1]-y[0])*(z-z[0])/(z[-1]-z[0])
                angles = np.degrees(np.arctan(np.abs(np.diff(y)/np.diff(z))))
                sides.append({"maximum_endpoint_chord_bow_mm": float(np.max(np.abs(y-chord))),
                              "sampled_near_vertical_span_fraction": float(np.mean(angles <= 10.))})
            row.update(height_window_mm=[lo, hi], samples_z_left_right_mm=[list(p) for p in points], sides=sides)
        except (ValueError, RuntimeError, KeyError) as exc:
            row["unmeasurable_reason"] = str(exc)
        result.append(row)
    return {"stations": result, "bottom_rounding_excluded_mm": radius+.25,
            "near_vertical_descriptor_angle_degrees": 10.,
            "scope": "Seven actual horizontal CAD silhouette lines per local sole window; bow and near-vertical ratio are descriptors, not aesthetic limits"}


def _surface_conditioning(shape):
    """Never discard a singular/nonfinite interior derivative as a smooth sample."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepClass import BRepClass_FaceClassifier
    from OCP.BRepLProp import BRepLProp_SLProps
    from OCP.BRepTools import BRepTools
    from OCP.TopAbs import TopAbs_IN
    from OCP.gp import gp_Pnt, gp_Pnt2d, gp_Vec
    faces = shape.faces()
    covered, samples, errors = set(), [], []
    uv = [(.5, .5)]+[(u, v) for u in (.2, .5, .8) for v in (.2, .5, .8) if (u, v) != (.5, .5)]
    for u_fraction, v_fraction in uv:
        for index, face in enumerate(faces):
            if len(samples)+len(errors) >= 512:
                break
            a, b, c, d = BRepTools.UVBounds_s(face.wrapped)
            u, v = a+u_fraction*(b-a), c+v_fraction*(d-c)
            if BRepClass_FaceClassifier(face.wrapped, gp_Pnt2d(u, v), CAD_TOLERANCE_MM).State() != TopAbs_IN:
                continue
            record = {"face_index": index, "uv": [u, v]}
            covered.add(index)
            known_numerical_failure = False
            try:
                adaptor = BRepAdaptor_Surface(face.wrapped)
                point, du, dv = gp_Pnt(), gp_Vec(), gp_Vec()
                adaptor.D1(u, v, point, du, dv)
                jacobian = float(du.Crossed(dv).Magnitude())
                if not np.isfinite(jacobian) or jacobian <= 1e-12:
                    known_numerical_failure = True
                    raise ValueError("Interior CAD surface Jacobian is nonfinite or <=1e-12")
                props = BRepLProp_SLProps(adaptor, u, v, 2, 1e-12)
                if not props.IsCurvatureDefined():
                    raise ValueError("Interior CAD surface curvature is undefined")
                curvature = max(abs(float(props.MaxCurvature())), abs(float(props.MinCurvature())))
                if not np.isfinite(curvature):
                    known_numerical_failure = True
                    raise ValueError("Nonfinite interior CAD surface curvature")
                samples.append({**record, "point_mm": [point.X(), point.Y(), point.Z()],
                                "jacobian_mm2_per_uv": jacobian, "maximum_absolute_principal_curvature_per_mm": curvature})
            except (ValueError, RuntimeError) as exc:
                errors.append({**record, "reason": str(exc), "known_numerical_failure": known_numerical_failure})
    return {"method": "Up to 512 trim-IN actual CAD first/second derivative evaluations, centers before subdivisions",
            "sample_count": len(samples), "samples": samples, "measurement_errors": errors,
            "derivative_numerical_floor": 1e-12, "curvature_evaluation_precision": 1e-12,
            "faces_without_measured_interior": sorted(set(range(len(faces)))-covered),
            "maximum_observed_principal_curvature_per_mm": max((s["maximum_absolute_principal_curvature_per_mm"] for s in samples), default=None),
            "minimum_observed_jacobian_mm2_per_uv": min((s["jacobian_mm2_per_uv"] for s in samples), default=None),
            "global_self_intersection": "NOT_VERIFIED", "global_curvature": "NOT_VERIFIED",
            "scope": "Local derivative descriptors; finite interior samples cannot rule out narrow spikes, folded patches or self-contact elsewhere. No aesthetic curvature threshold is imposed."}


def _rim_curve_descriptors(outer, opening):
    from build123d import Compound
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Section
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.gp import gp_Pnt, gp_Vec
    section = BRepAlgoAPI_Section(outer.wrapped, opening.wrapped, False)
    section.Approximation(True)
    section.Build()
    if not section.IsDone():
        raise ValueError("Actual rim curve section failed")
    samples, errors, projection_degenerate = [], [], []
    for index, edge in enumerate(Compound(section.Shape()).edges()):
        curve = BRepAdaptor_Curve(edge.wrapped)
        for fraction in (.125, .375, .625, .875):
            if len(samples)+len(errors) >= 512:
                break
            record = {"edge_index": index, "parameter_fraction": fraction}
            try:
                t = curve.FirstParameter()+fraction*(curve.LastParameter()-curve.FirstParameter())
                point, d1, d2 = gp_Pnt(), gp_Vec(), gp_Vec()
                curve.D2(t, point, d1, d2)
                velocity = np.asarray([d1.X(), d1.Y(), d1.Z()])
                acceleration = np.asarray([d2.X(), d2.Y(), d2.Z()])
                speed = float(np.linalg.norm(velocity))
                if not np.isfinite(speed) or speed <= 1e-12:
                    raise ValueError("Zero/nonfinite actual 3D rim derivative")
                curvature = float(np.linalg.norm(np.cross(velocity, acceleration))/speed**3)
                projected_speed = float(np.linalg.norm(velocity[[0, 2]]))
                projected_curvature = (abs(velocity[0]*acceleration[2]-velocity[2]*acceleration[0])/projected_speed**3
                                       if projected_speed > 1e-12 else None)
                if not np.isfinite(curvature) or (projected_curvature is not None and not np.isfinite(projected_curvature)):
                    raise ValueError("Nonfinite actual rim curvature")
                record.update(point_mm=[point.X(), point.Y(), point.Z()], curvature_3d_per_mm=curvature,
                              curvature_projected_xz_per_mm=projected_curvature,
                              projected_xz_speed_per_parameter=projected_speed)
                if projected_curvature is None:
                    projection_degenerate.append(record)
                samples.append(record)
            except (ValueError, RuntimeError) as exc:
                errors.append({**record, "reason": str(exc)})
    return {"samples": samples, "measurement_errors": errors, "projected_xz_degenerate_sites": projection_degenerate,
            "maximum_observed_curvature_3d_per_mm": max((s["curvature_3d_per_mm"] for s in samples), default=None),
            "scope": "Actual rim curve derivatives at four interior parameters per edge. 3D G1 alone does not prove a smooth side silhouette; XZ projection may degenerate at ordinary view extrema. No global curvature or aesthetic PASS."}


def validate_design_shape(last_shape, build, spec, *, baseline=None, outer_shape=None) -> dict:
    """Optional design assessment; no ordinary M1 acceptance rule is changed.

    ``baseline`` may contain shape, last_shape, cavity (actual CAD shapes).
    ``outer_shape`` is the actual offset envelope, needed to measure its rim.
    No data from build.metadata is used as evidence.
    """
    checks, missing = [], []
    report = {"method": "Independent exact B-Rep sections, intersections and fixed source-relative rays",
              "geometry_status": "PARTIAL", "overall_status": "PARTIAL", "metrics_status": "NOT_VERIFIED",
              "geometry_status_scope": "Executed CAD safety checks and finite probes only; no global surface regularity, appearance or manufacturing acceptance",
              "appearance_acceptance": "PENDING", "user_acceptance": "PENDING", "checks": checks,
              "unmeasurable_regions": missing,
              "annotation_8_constraint": "Forefoot width and proportions are constrained by the unchanged source last and clearance cavity. A smaller source/cavity is not an appearance repair.",
              "global_wall_minimum": "NOT_VERIFIED", "global_self_intersection": "NOT_VERIFIED",
              "global_curvature_continuity": "NOT_VERIFIED", "manufacturing_qualification": "NOT_VERIFIED"}
    def check(name, passed, value, threshold):
        checks.append({"name": name, "status": "PASS" if passed else "FAIL", "value": value, "threshold": threshold})
    quality = {name: {"valid": bool(shape.is_valid), "solid_count": len(shape.solids())}
               for name, shape in (("last", last_shape), ("shoe", build.shape), ("cavity", build.cavity), ("opening", build.opening))}
    valid = all(q["valid"] and q["solid_count"] == 1 for q in quality.values())
    check("valid_single_solid_breps", valid, quality, "Valid, one solid each")
    if not valid:
        report["geometry_status"] = "FAIL"
        return report
    box = last_shape.bounding_box()
    try:
        overlaps = {name: _volume(build.shape.intersect(shape)) for name, shape in
                    (("last", last_shape), ("cavity", build.cavity), ("opening", build.opening))}
        check("source_cavity_opening_exclusion", max(overlaps.values()) <= .01, overlaps, "Each overlap <=0.01 mm3")
        outside = _volume(last_shape.cut(build.cavity))
        check("source_contained_in_cavity", outside <= .01, outside, "<=0.01 mm3")
        entry = build.cavity.intersect(build.opening)
        entry_volume = _volume(entry)
        entry_count = len(entry.solids()) if entry is not None else 0
        check("one_connected_cavity_entry", entry_count == 1 and entry_volume > 1.,
              {"solid_count": entry_count, "volume_mm3": entry_volume}, "One connected entry, volume >1 mm3")
        if baseline and baseline.get("last_shape") is not None:
            old_last = baseline["last_shape"]
            difference = _volume(last_shape.cut(old_last))+_volume(old_last.cut(last_shape))
            check("unchanged_source_last", difference <= .01, difference, "Symmetric material difference <=0.01 mm3")
        if baseline and baseline.get("cavity") is not None:
            old_cavity = baseline["cavity"]
            difference = _volume(build.cavity.cut(old_cavity))+_volume(old_cavity.cut(build.cavity))
            check("unchanged_clearance_cavity", difference <= .01, difference,
                  "Symmetric material difference <=0.01 mm3; fixed-fit appearance refinement")
    except (ValueError, RuntimeError) as exc:
        missing.append({"region": "Boolean safety checks", "reason": str(exc)})
    source_rows = _sections(last_shape, box)
    shoe_rows, cavity_rows = _sections(build.shape, box), _sections(build.cavity, box)
    report["sections"] = {"source": source_rows, "cavity": cavity_rows, "shoe": shoe_rows}
    section_missing = [r for r in shoe_rows if "unmeasurable_reason" in r]
    missing.extend({"region": "final shoe section", **r} for r in section_missing)
    missing.extend({"region": region, **r} for region, rows in (("source section", source_rows), ("cavity section", cavity_rows))
                   for r in rows if "unmeasurable_reason" in r)
    disconnected = [r for r in shoe_rows if r.get("material_component_count", 1) != 1]
    check("sampled_section_material_connectivity", not disconnected, disconnected,
          "One geometrically connected material component at each measured station; adjacent CAD faces joined within 1e-6 mm")
    report["silhouette"] = _profile(shoe_rows)
    report["source_proportions"] = _profile(source_rows)
    at_forefoot = [next(r for r in rows if r["source_length_fraction"] == .68)
                   for rows in (source_rows, cavity_rows, shoe_rows)]
    if all("width_mm" in row for row in at_forefoot):
        source, cavity, shoe = (row["width_mm"] for row in at_forefoot)
        report["forefoot_fit_constraint"] = {
            "source_length_fraction": .68, "source_width_mm": source,
            "cavity_width_mm": cavity, "final_shoe_width_mm": shoe,
            "nominal_cavity_plus_two_walls_reference_mm": cavity+2*spec.upper.thickness_mm,
            "scope": "Actual CAD section widths. Cavity plus two nominal walls is an explanatory reference, not a rigorous silhouette lower bound on angled surfaces or an aesthetic PASS."}
    report["upper_peak_z_mm"] = build.upper.bounding_box().max.Z
    report["shoe_peak_z_mm"] = build.shape.bounding_box().max.Z
    from .footbed import validate_underfoot_material
    report["underfoot"] = underfoot = validate_underfoot_material(last_shape, build, spec)
    minimum = spec.upper.thickness_mm-THICKNESS_ERROR_MM
    measured = underfoot["minimum_measured_directional_material_mm"]
    check("sampled_underfoot_material_thickness", measured is not None and measured >= minimum,
          measured, {"minimum_mm": minimum, "scope": "Experimental sampled outward-normal minimum only"})
    checks.append({"name": "underfoot_clearance_and_region_coverage", "status":
                   "PASS" if underfoot["geometry_status"] == "PASS" else "FAIL" if underfoot["geometry_status"] == "FAIL" else "NOT_VERIFIED",
                   "value": underfoot["regions"], "threshold": "All selected actual probes pass; heel/middle/toe covered; zero measurement errors"})
    if underfoot["geometry_status"] == "NOT_VERIFIED":
        missing.append({"region": "underfoot coverage", "reason": "Selected heel/middle/toe sites do not all have verified measurements"})
    missing.extend({"region": "underfoot", **r} for r in underfoot["measurement_errors"])
    report["sidewall"] = _sidewall(build, box, spec.sole.profile_parameters.edge_radius_mm)
    report["surface_conditioning"] = conditioning = _surface_conditioning(build.shape)
    if any(error["known_numerical_failure"] for error in conditioning["measurement_errors"]):
        check("sampled_surface_derivatives_finite_nonsingular", False,
              conditioning["measurement_errors"], "No nonfinite derivatives or Jacobian <=1e-12 at selected trim-IN sites")
    elif conditioning["measurement_errors"]:
        checks.append({"name": "sampled_surface_derivatives_finite_nonsingular", "status": "NOT_VERIFIED",
                       "value": conditioning["measurement_errors"], "threshold": "All selected actual derivative measurements complete"})
        missing.extend({"region": "surface derivatives", **error} for error in conditioning["measurement_errors"])
    elif conditioning["samples"]:
        check("sampled_surface_derivatives_finite_nonsingular", True, [],
              "All selected derivatives finite and Jacobian >1e-12; no global or aesthetic threshold")
    else:
        checks.append({"name": "sampled_surface_derivatives_finite_nonsingular", "status": "NOT_VERIFIED",
                       "value": None, "threshold": "At least one actual trim-IN derivative measurement"})
        missing.append({"region": "surface derivatives", "reason": "No actual trim-IN derivative site was measurable"})
    report["surface_conditioning"]["coverage_status"] = "PARTIAL" if conditioning["faces_without_measured_interior"] else "OBSERVED"
    report["actual_rocker"] = {"floor_samples": [{k: r[k] for k in ("source_length_fraction", "floor_z_mm")}
                                               for r in shoe_rows if "floor_z_mm" in r],
                               "scope": "Actual final CAD section floors; declared toe rise is not used as measured height"}
    if outer_shape is not None:
        from shoe_poc.geometry.upper import _collar_rim_metrics
        try:
            rim = _collar_rim_metrics(outer_shape, build.opening)
            rim["curve_descriptors"] = _rim_curve_descriptors(outer_shape, build.opening)
            report["rim"] = rim
            check("sampled_rim_curve_derivatives_finite", not rim["curve_descriptors"]["measurement_errors"],
                  rim["curve_descriptors"]["measurement_errors"], "No zero/nonfinite 3D rim derivative at selected sites")
            check("actual_closed_entry_rim", rim["one_closed_entry_loop"], rim, "One closed exterior entry loop")
            tangent = rim["maximum_tangent_discontinuity_degrees"]
            check("sampled_rim_endpoint_continuity", not rim["unmeasurable_tangent_joints"] and tangent is not None and tangent <= .5
                  and rim["maximum_curve_endpoint_vertex_error_mm"] <= .25,
                  {"maximum_corner_degrees": tangent, "endpoint_error_mm": rim["maximum_curve_endpoint_vertex_error_mm"]},
                  "Corner <=0.5 degrees, curve/vertex error <=0.25 mm, all joints measurable")
        except (ValueError, RuntimeError) as exc:
            missing.append({"region": "actual exterior rim", "reason": str(exc)})
    else:
        report["rim"] = {"status": "NOT_VERIFIED", "reason": "Actual outer envelope not supplied; final cutter contact does not reliably reconstruct the exterior rim"}
        missing.append({"region": "actual exterior rim", "reason": "Actual outer envelope not supplied"})
    if baseline and baseline.get("shape") is not None:
        old_rows = _sections(baseline["shape"], box)
        old_profile = _profile(old_rows)
        changes = {}
        for key, value in report["silhouette"].items():
            old = old_profile.get(key)
            if isinstance(value, (float, int)) and isinstance(old, (float, int)):
                changes[key] = {"baseline": old, "candidate": value, "change": value-old,
                                "direction": "LOWER" if value < old else "HIGHER" if value > old else "UNCHANGED"}
        report["baseline_comparison"] = {"sections": old_rows, "silhouette_changes": changes,
                                         "shoe_peak_z_change_mm": report["shoe_peak_z_mm"]-baseline["shape"].bounding_box().max.Z,
                                         "appearance_acceptance": "PENDING"}
    report["metrics_status"] = "OBSERVED"
    report["geometry_status"] = "FAIL" if any(c["status"] == "FAIL" for c in checks) else "PARTIAL" if missing else "PASS"
    # All values must survive strict JSON; silently serializing NaN hides failed probes.
    import json
    json.dumps(report, allow_nan=False)
    return report
