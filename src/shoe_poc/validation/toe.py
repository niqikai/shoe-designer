"""Finite longitudinal toe descriptors read independently from the actual CAD.

The angle used to label a flat/vertical run is a descriptor convention, not a
footwear standard. No generator profile, declared radius, or rendering normal
is evidence here. Complete measurements do not approve the appearance.
"""
from __future__ import annotations

import json
import math

import numpy as np

from .design import _intersector
from .surface import _ray_hits

DESCRIPTOR_ANGLE_DEGREES = 3.


def _profile_descriptors(points, *, independent_axis, run_window_start=None):
    """Measure contiguous runs without bridging missing/duplicate coordinates."""
    if len(points) < 3:
        raise ValueError("Fewer than three actual profile intersections")
    values = np.asarray(points, dtype=float)
    if not np.isfinite(values).all():
        raise ValueError("Nonfinite actual profile intersection")
    differences = np.diff(values, axis=0)
    advance = differences[:, independent_axis]
    if np.any(advance <= 0):
        raise ValueError("Profile sampling coordinate is not strictly increasing")
    other = differences[:, 1-independent_axis]
    angles = np.degrees(np.arctan2(other, advance))
    low = np.abs(angles) <= DESCRIPTOR_ANGLE_DEGREES
    if run_window_start is not None:
        low &= values[:-1, independent_axis] >= run_window_start
    longest = run = 0.
    for eligible, distance in zip(low, advance):
        run = run+float(distance) if eligible else 0.
        longest = max(longest, run)
    return {"maximum_low_angle_run_mm": longest,
            "maximum_adjacent_secant_turn_degrees": float(np.max(np.abs(np.diff(angles)))),
            "minimum_secant_angle_degrees": float(np.min(angles)),
            "maximum_secant_angle_degrees": float(np.max(angles)),
            "measured_span_mm": float(np.sum(advance)),
            "sample_count": len(points)}


def _measure(shape, source_box, *, toe_band_top_z_mm, window_mm, sample_step_mm):
    box = shape.bounding_box()
    start_x = float(source_box.max.X-window_mm)
    lower_z = float(box.min.Z-1.)
    upper_z = float(toe_band_top_z_mm)
    if upper_z <= lower_z:
        raise ValueError("Toe band top must be above the actual solid minimum")
    probe = _intersector(shape)
    y_center = float((source_box.min.Y+source_box.max.Y)/2)
    y_offset = min(5., float(source_box.size.Y)*.1)
    lanes, errors = [], []
    x_origin = float(box.min.X-1.)
    ray_length_x = float(box.size.X+2.)
    ray_length_z = float(box.size.Z+2.)
    for lane_index, y in enumerate((y_center-y_offset, y_center, y_center+y_offset)):
        lane = {"lane_index": lane_index, "y_mm": y, "front_points_xz_mm": [],
                "floor_points_xz_mm": [], "measurement_errors": [],
                "outside_toe_band_scan_count": 0}
        lanes.append(lane)
        # Empty low scan lines are expected below an upturned toe. A missing
        # line inside the discovered material span is retained as a failure.
        count = max(4, int(math.ceil((upper_z-lower_z)/sample_step_mm))+1)
        front, started, ended = [], False, False
        for z in np.linspace(lower_z, upper_z, count):
            try:
                hits = _ray_hits(probe, (x_origin, y, float(z)), (1, 0, 0), ray_length_x)
                if hits and not all(math.isfinite(value) for value in hits):
                    raise ValueError("Nonfinite CAD ray intersection")
                x = x_origin+hits[-1] if hits else None
                if x is None or x < start_x:
                    lane["outside_toe_band_scan_count"] += 1
                    ended |= started
                    if started:
                        raise ValueError("Missing front material inside the planned band after the measured toe floor")
                    continue
                if ended:
                    raise ValueError("Toe material reappeared after a gap in front scan")
                started = True
                front.append([float(x), float(z)])
            except Exception as exc:
                lane["measurement_errors"].append({"probe": "front", "z_mm": float(z), "reason": f"{type(exc).__name__}: {exc}"})
        lane["front_points_xz_mm"] = front
        try:
            if len(front) < 3:
                raise ValueError("No measurable toe-front span in the explicit sole band")
            tip_x = max(point[0] for point in front)
            lane["measured_tip_x_mm"] = tip_x
            # The furthest X is local to this Y lane, not a global bounding-box
            # extreme that an off-center section may never reach.
            stop_x = tip_x-min(.05, sample_step_mm*.2)
            if stop_x-start_x < 2*sample_step_mm:
                raise ValueError("Insufficient longitudinal toe profile span")
            count = max(4, int(math.ceil((stop_x-start_x)/sample_step_mm))+1)
            for x in np.linspace(start_x, stop_x, count):
                try:
                    hits = _ray_hits(probe, (float(x), y, lower_z), (0, 0, 1), ray_length_z)
                    if not hits:
                        raise ValueError("No actual CAD material at planned floor probe")
                    if not all(math.isfinite(value) for value in hits):
                        raise ValueError("Nonfinite CAD ray intersection")
                    floor_z = lower_z+hits[0]
                    if floor_z > upper_z:
                        raise ValueError("Planned toe floor lies above the explicit sole band")
                    lane["floor_points_xz_mm"].append([float(x), float(floor_z)])
                except Exception as exc:
                    lane["measurement_errors"].append({"probe": "floor", "x_mm": float(x), "reason": f"{type(exc).__name__}: {exc}"})
            if not lane["measurement_errors"]:
                lane["floor"] = _profile_descriptors(lane["floor_points_xz_mm"], independent_axis=0,
                    run_window_start=tip_x-min(15., window_mm))
                lane["front"] = _profile_descriptors(front, independent_axis=1)
                lane["maximum_terminal_floor_low_slope_run_mm"] = lane["floor"]["maximum_low_angle_run_mm"]
                lane["maximum_front_near_vertical_run_mm"] = lane["front"]["maximum_low_angle_run_mm"]
        except Exception as exc:
            lane["measurement_errors"].append({"probe": "profile", "reason": f"{type(exc).__name__}: {exc}"})
        lane["geometry_status"] = "NOT_VERIFIED" if lane["measurement_errors"] else "PASS"
        errors.extend({"lane_index": lane_index, **error} for error in lane["measurement_errors"])
    return {"geometry_status": "NOT_VERIFIED" if errors else "PASS", "lanes": lanes,
            "measurement_errors": errors, "measurement_error_count": len(errors)}


def validate_toe_profile(shape, source_box, *, toe_band_top_z_mm, baseline=None,
                         window_mm=35., sample_step_mm=.25):
    """Describe actual final-shoe floor/front toe profiles on three Y planes.

    ``baseline`` is an optional actual CAD shape. The unchanged source box and
    explicit band are used for both geometries. PASS covers finite measurement
    completion only; baseline reductions are observations, never aesthetic or
    global continuity acceptance. Upper-fit/clearance need independent checks.
    """
    values = (toe_band_top_z_mm, window_mm, sample_step_mm)
    if not all(math.isfinite(value) for value in values) or window_mm <= 0 or sample_step_mm <= 0:
        raise ValueError("Toe probe dimensions must be finite and positive")
    report = {"method": "Actual B-Rep vertical floor rays and horizontal front rays in three longitudinal planes",
              "geometry_status": "NOT_VERIFIED", "appearance_acceptance": "PENDING",
              "scope": "PASS means completion of planned finite descriptors only; no global regularity, fit, or appearance approval",
              "toe_band_top_z_mm": float(toe_band_top_z_mm), "window_mm": float(window_mm),
              "maximum_probe_step_mm": float(sample_step_mm),
              "terminal_floor_window_mm": min(15., float(window_mm)),
              "descriptor_angle_degrees": DESCRIPTOR_ANGLE_DEGREES,
              "descriptor_scope": "3 degrees labels sampled low-slope/near-vertical runs; an experimental measurement convention, not a footwear standard or acceptance threshold",
              "limitations": ["Finite rays can miss a narrow shelf, corner, or fold between samples.",
                              "Lower and front secant turns depend on the reported probe spacing and are not exact curvature.",
                              "The explicit Z band excludes higher upper geometry; fit and source preservation require separate CAD checks.",
                              "A shorter flat or vertical run alone cannot establish a conventional or preferred appearance."]}
    shapes = [("candidate", shape)]+([("baseline", baseline)] if baseline is not None else [])
    for name, member in shapes:
        if member is None or not member.is_valid or len(member.solids()) != 1 or member.volume <= 0:
            report[name] = {"geometry_status": "FAIL", "measurement_errors": [{"reason": "Input is not a valid positive single solid"}],
                            "measurement_error_count": 1, "lanes": []}
        else:
            try:
                report[name] = _measure(member, source_box, toe_band_top_z_mm=toe_band_top_z_mm,
                                       window_mm=window_mm, sample_step_mm=sample_step_mm)
            except Exception as exc:
                report[name] = {"geometry_status": "NOT_VERIFIED", "measurement_errors": [{"reason": f"{type(exc).__name__}: {exc}"}],
                                "measurement_error_count": 1, "lanes": []}
    states = [report[name]["geometry_status"] for name, _ in shapes]
    report["geometry_status"] = "FAIL" if "FAIL" in states else "NOT_VERIFIED" if "NOT_VERIFIED" in states else "PASS"
    if baseline is not None:
        changes = []
        for old, new in zip(report["baseline"]["lanes"], report["candidate"]["lanes"]):
            row = {"lane_index": new["lane_index"], "y_mm": new["y_mm"]}
            for key in ("maximum_terminal_floor_low_slope_run_mm", "maximum_front_near_vertical_run_mm"):
                if key in old and key in new:
                    row[key] = {"baseline_mm": old[key], "candidate_mm": new[key], "change_mm": new[key]-old[key]}
            changes.append(row)
        report["baseline_comparison"] = {"status": "OBSERVED" if report["geometry_status"] == "PASS" else "NOT_VERIFIED",
                                          "lane_changes": changes, "appearance_acceptance": "PENDING"}
    json.dumps(report, allow_nan=False)
    return report
