"""Deterministic build-volume placement and sampled surface overhang screening.

Coordinates are millimetres. The original points are never changed. A selected
pose uses ``p_print = R @ p_design + t``; Z=0 is the build plate. Build clearance
is reserved on both sides of every dimension, with the two Z clearances kept
above the part so FDM models still touch the plate.
"""
from __future__ import annotations

import math

import numpy as np


def _points_array(points):
    points = np.asarray(points, dtype=np.float64)
    if points.ndim != 2 or points.shape[1] != 3 or not len(points):
        raise ValueError("points must be a non-empty N×3 array")
    if not np.isfinite(points).all():
        raise ValueError("points must contain only finite coordinates")
    return points


def _rotation(axis, angle):
    """Rodrigues rotation: all geometry is derived from the provided model."""
    axis = np.asarray(axis, dtype=np.float64)
    axis /= np.linalg.norm(axis)
    x, y, z = axis
    skew = np.array([[0, -z, y], [z, 0, -x], [-y, x, 0]])
    theta = math.radians(angle)
    return (np.eye(3) * math.cos(theta) +
            (1 - math.cos(theta)) * np.outer(axis, axis) +
            math.sin(theta) * skew)


def _support_points(points):
    # Convex-hull vertices give exact bounds under any linear rotation. Every
    # candidate that appears to fit is nevertheless checked on the full mesh.
    if len(points) < 100:
        return points, "all_vertices"
    try:
        from scipy.spatial import ConvexHull, QhullError
        try:
            return points[ConvexHull(points).vertices], "convex_hull_vertices"
        except QhullError:
            return points, "all_vertices_degenerate_hull"
    except ImportError:
        return points, "all_vertices_scipy_unavailable"


def plan_build_pose(points, params):
    """Find a checked pose; failure means this finite search found no fit.

    FDM only changes yaw. SLS/MJF may additionally tilt around a deterministic
    set of horizontal axes. No support feasibility is implied by a fitted pose.
    """
    points = _points_array(points)
    size = np.array([params.get(f"build_size_{axis}_mm", 250) for axis in "xyz"], dtype=float)
    margin = float(params.get("build_margin_mm", 2))
    if not np.isfinite(size).all() or np.any(size <= 0) or not math.isfinite(margin) or margin < 0:
        raise ValueError("machine dimensions must be positive and margin non-negative")
    usable = size - 2 * margin
    orientation = params.get("build_orientation", "auto")
    process = str(params.get("print_process", "SLS")).upper()
    if orientation not in ("auto", "as_designed"):
        raise ValueError("build_orientation must be auto or as_designed")
    if process not in ("SLS", "MJF", "FDM"):
        raise ValueError("print_process must be SLS, MJF or FDM")
    source_min = points.min(axis=0)
    original_dims = points.max(axis=0) - source_min
    eps = 1e-7
    fits_original = bool(np.all(original_dims <= usable + eps))
    support, support_method = _support_points(points) if not fits_original and orientation == "auto" else (points, "all_vertices")
    count = 0
    best_score = float("inf")
    best = None

    def consider(rotation, label, yaw=0, tilt=0, tilt_axis=None):
        nonlocal count, best, best_score
        count += 1
        posed = support @ rotation.T
        lower, upper = posed.min(axis=0), posed.max(axis=0)
        dims = upper - lower
        fits = bool(np.all(dims <= usable + eps))
        if fits:
            # Full-vertex verification is deliberately independent of the hull.
            posed = points @ rotation.T
            lower, upper = posed.min(axis=0), posed.max(axis=0)
            dims = upper - lower
            fits = bool(np.all(dims <= usable + eps))
        score = float(np.max(dims / np.maximum(usable, eps)))
        candidate = {
            "rotation_matrix_3x3": rotation.tolist(),
            "translation_mm": (np.array([margin, margin, 0.0]) - lower).tolist(),
            "oriented_dimensions_mm": dims.tolist(),
            "unused_dimension_mm": (usable - dims).tolist(),
            "label": label, "yaw_deg": yaw, "tilt_deg": tilt,
            "tilt_axis_azimuth_deg": tilt_axis,
            "full_vertex_fit_verified": fits,
        }
        if fits or score < best_score:
            best, best_score = candidate, score
        return fits

    found = consider(np.eye(3), "as_designed")
    if not found and orientation == "auto" and np.all(usable > 0):
        # Prefer the smallest in-plane rotation, then deterministic positive yaw.
        for yaw in range(1, 180):
            if consider(_rotation([0, 0, 1], yaw), "bed_parallel_yaw", yaw=yaw):
                found = True
                break
        if not found and process in ("SLS", "MJF"):
            for tilt in (15, 30, 45, 60, 75, 90):
                if found:
                    break
                for azimuth in range(0, 180, 30):
                    if found:
                        break
                    phi = math.radians(azimuth)
                    tilted = _rotation([math.cos(phi), math.sin(phi), 0], tilt)
                    for yaw in range(0, 180, 5):
                        rotation = _rotation([0, 0, 1], yaw) @ tilted
                        if consider(rotation, "powder_bed_tilt", yaw=yaw, tilt=tilt, tilt_axis=azimuth):
                            found = True
                            break
    if found and fits_original:
        status = "pass"
        message = "原设计姿态在预留边距后可放入制造空间。"
    elif found:
        status = "warning"
        message = "原设计轴向尺寸超出可用空间；已用完整网格确认推荐旋转姿态可放入，须按该姿态摆放并由切片软件复核。"
    elif orientation == "as_designed":
        status = "fail"
        message = "保持原设计姿态时超出预留边距后的制造空间；本次未搜索旋转姿态。"
    else:
        status = "fail"
        message = "有限候选搜索未找到满足边距的姿态；这不证明所有姿态均无法放入。可调整设备尺寸或在切片软件中继续验证。"
    if process == "FDM":
        message += " FDM 仅允许绕 Z 轴旋转；未自动倾斜模型。"
    return {
        "status": status, "message": message, "print_process": process,
        "build_orientation": orientation, "fits_as_designed": fits_original,
        "fits_selected": bool(found), "selected": best,
        "as_designed_dimensions_mm": original_dims.tolist(),
        "machine_dimensions_mm": size.tolist(), "usable_dimensions_mm": usable.tolist(),
        "margin_mm": margin, "candidate_count": count,
        "bound_method": support_method,
        "coordinate_convention": "p_print = R @ p_design + t; minimum XY = margin, minimum Z = 0; both Z margins reserved above part",
        "search_scope": "as designed, integer Z yaw; SLS/MJF additionally 15° tilt steps, 30° horizontal-axis azimuth steps and 5° yaw steps",
        "limitations": ["可放入空间不代表热变形、支撑、清粉或工艺方向已获验证。",
                        "失败时 selected 仅为已搜索候选中最大尺寸超限比例最低的参考姿态，不能用于通过制造检查。"],
    }


def check_fdm_overhang(points, faces, pose, limit_deg=45):
    """Area screening of down-facing triangles in the selected build pose.

    Angle is measured from a vertical wall (0°) to a horizontal ceiling (90°).
    Triangles entirely within 0.25 mm of the lowest Z are build-plate contacts.
    Face winding must already have passed outward-normal mesh validation.
    """
    points = _points_array(points)
    faces = np.asarray(faces)
    if faces.ndim != 2 or faces.shape[1] != 3 or not np.issubdtype(faces.dtype, np.integer):
        raise ValueError("faces must be an M×3 integer triangle array")
    if faces.size and (faces.min() < 0 or faces.max() >= len(points)):
        raise ValueError("face vertex index out of bounds")
    if not math.isfinite(limit_deg) or not 0 <= limit_deg <= 90:
        raise ValueError("overhang angle limit must be in [0, 90]")
    selected = pose.get("selected", pose)
    rotation = np.asarray(selected["rotation_matrix_3x3"], dtype=float)
    if rotation.shape != (3, 3) or not np.isfinite(rotation).all():
        raise ValueError("pose rotation must be a finite 3×3 matrix")
    posed = points @ rotation.T
    # Translation cancels when comparing to the lowest surface.
    triangles = posed[faces]
    cross = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    doubled_area = np.linalg.norm(cross, axis=1)
    areas = doubled_area * .5
    valid = doubled_area > 1e-12
    nz = np.divide(cross[:, 2], doubled_area, out=np.zeros(len(faces)), where=valid)
    down_angle = np.degrees(np.arcsin(np.clip(-nz, 0, 1)))
    on_plate = np.all(triangles[:, :, 2] <= posed[:, 2].min() + .25 + 1e-8, axis=1)
    considered = valid & ~on_plate
    overhang = considered & (down_angle > float(limit_deg) + 1e-6)
    total_area = float(areas[valid].sum())
    considered_area = float(areas[considered].sum())
    risk_area = float(areas[overhang].sum())
    max_angle = float(down_angle[considered].max()) if np.any(considered) else 0.0
    status = "warning" if np.any(overhang) else "pass"
    return {
        "status": status, "limit_deg": float(limit_deg), "max_downward_angle_deg": max_angle,
        "overhang_triangle_count": int(overhang.sum()), "overhang_area_mm2": risk_area,
        "considered_surface_area_mm2": considered_area, "total_surface_area_mm2": total_area,
        "overhang_area_fraction": risk_area / considered_area if considered_area else 0.0,
        "overhang_fraction_denominator": "surface area excluding build-plate contact triangles",
        "build_plate_contact_triangle_count": int((on_plate & valid).sum()),
        "build_plate_contact_area_mm2": float(areas[on_plate & valid].sum()),
        "build_plate_tolerance_mm": .25,
        "degenerate_triangle_count": int((~valid).sum()),
        "requires_slicer_support_review": bool(np.any(overhang)),
        "message": ("存在超过悬垂角阈值的下表面，需切片检查支撑及其可移除性；晶格内部支撑可能难以清除。"
                    if np.any(overhang) else "按当前姿态未检测到超过阈值的非底板下表面；仍需切片复核桥接、层间结合和实际工艺参数。"),
        "limitations": ["按三角面法向和面积筛查，未模拟桥接长度、逐层支撑或支撑移除路径。",
                        "假定网格绕序已通过朝外法向检查；不作为可打印性证明。"],
    }
