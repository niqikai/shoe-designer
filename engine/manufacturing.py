"""M3 manufacturing screens on the generated mesh and sampled material field.

These are scoped geometric checks, not material, process or wear certification.
"""
import math

import numpy as np
from mathutils import Vector

from engine.shoe.volume import geometry_dependencies


def process_profile(params):
    process = params["print_process"]
    minimum = 1.5 if process == "FDM" else 1.2
    return {"process": process, "material": "TPU", "shore_a": params["tpu_shore_a"],
            "minimum_wall_mm": minimum, "minimum_rod_mm": minimum,
            "powder_exit_diameter_mm": 4.0, "minimum_powder_exits": 2,
            "fdm_overhang_limit_deg": 45.0,
            "basis": "project screening defaults; material/vendor/device qualification still required"}


def measure_thickness(mesh, minimum_mm, *, samples=24000):
    """Deterministic area-stratified inward normal chords on the final surface.

    Missing rays fail closed. A chord is a sampled wall/rod estimate, not a
    proof of global minimum thickness or the diameter of every strut.
    """
    from engine.validate import coordinates, surface_tree, triangles
    points, faces = coordinates(mesh), triangles(mesh)
    corners = points[faces]
    cross = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    twice_area = np.linalg.norm(cross, axis=1)
    area = twice_area * .5
    if not len(area) or not np.isfinite(area).all() or area.sum() <= 0:
        return {"status": "fail", "message": "无法对空网格或异常面积测量壁厚。", "samples": 0}, []
    indices = np.searchsorted(np.cumsum(area), (np.arange(samples) + .5) * area.sum() / samples)
    tree = surface_tree(mesh)
    values, failures, thin = [], 0, []
    epsilon = 1e-4
    for index in indices:
        origin = corners[index].mean(axis=0)
        normal = cross[index] / twice_area[index]
        hit, hit_normal, _, distance = tree.ray_cast(Vector(origin - epsilon * normal), Vector(-normal), 2000)
        if hit is None or not math.isfinite(distance):
            failures += 1
            continue
        thickness = float(distance + epsilon)
        values.append(thickness)
        if thickness < minimum_mm - 1e-4:
            thin.append({"position_mm": origin.tolist(), "opposite_mm": list(hit),
                         "midpoint_mm": ((origin + np.asarray(hit)) * .5).tolist(),
                         "normal": normal.tolist(), "thickness_mm": thickness,
                         "opposing_normal_dot": float(np.dot(normal, hit_normal))})
    values = np.asarray(values)
    failed = bool(failures or thin or not len(values))
    report = {"status": "fail" if failed else "pass", "threshold_mm": minimum_mm,
              "samples": samples, "valid_samples": len(values), "missing_samples": failures,
              "valid_ray_fraction": len(values) / samples,
              "unique_sampled_triangles": len(np.unique(indices)), "total_triangles": len(faces),
              "sampled_triangle_area_fraction": float(area[np.unique(indices)].sum() / area.sum()),
              "sampled_minimum_mm": float(values.min()) if len(values) else None,
              "sampled_percentiles_mm": dict(zip(("p01", "p05", "p50"), np.percentile(values, [1, 5, 50]).tolist())) if len(values) else {},
              "thin_sample_count": len(thin), "thin_sample_fraction": len(thin) / samples,
              "estimated_thin_surface_area_mm2": len(thin) / samples * float(area.sum()),
              "thin_examples": sorted(thin, key=lambda value: value["thickness_mm"])[:12],
              "method": "24000 equal-area strata; triangle-centroid inward normal rays" if samples == 24000 else f"{samples} equal-area strata; triangle-centroid inward normal rays",
              "message": (f"检测到 {len(thin)} 个低于 {minimum_mm:g} mm 的壁／杆厚度采样，缺测 {failures} 个。" if failed
                          else f"有效采样未发现低于 {minimum_mm:g} mm 的壁／杆厚度；仍需高精度与打印试样复核。"),
              "limitations": "sampled normal chords; narrow unsampled regions and oblique walls may be missed; not a certified global minimum"}
    return report, thin


def check_powder_paths(field, envelope_mask, core_mask, axes, *, diameter_mm=4.0, required_exits=2, surface_displacement_mm=0.0):
    """Finite-clearance paths, not just zero-size flood-fill connectivity.

    The EDT guard accounts conservatively for a grid-cell diagonal. Opening
    centres must lie on the actual sole envelope, below the solid footbed.
    """
    if not math.isfinite(surface_displacement_mm) or surface_displacement_mm < 0:
        raise ValueError("表面位移留量必须是非负有限数值。")
    geometry_dependencies()
    from scipy.ndimage import binary_erosion, binary_propagation, distance_transform_edt, generate_binary_structure, label, find_objects
    spacing = float(axes[0][1] - axes[0][0])
    void = field > 0
    distances = distance_transform_edt(void, sampling=spacing)
    guard = math.sqrt(3) * spacing + surface_displacement_mm
    safe = void & (distances >= diameter_mm / 2 + guard)
    seed = np.zeros(field.shape, dtype=bool)
    for axis in range(3):
        for side in (0, -1):
            index = [slice(None)] * 3
            index[axis] = side
            seed[tuple(index)] = safe[tuple(index)]
    reachable = binary_propagation(seed, structure=generate_binary_structure(3, 1), mask=safe)
    # The envelope is the original SOLID sole, not the shoe cavity or a grid box.
    boundary = envelope_mask & ~binary_erosion(envelope_mask, structure=generate_binary_structure(3, 1))
    candidates = boundary & core_mask & reachable
    labels, count = label(candidates, structure=generate_binary_structure(3, 3))
    options = []
    for number, region in enumerate(find_objects(labels), start=1):
        if region is None:
            continue
        indices = np.argwhere(labels[region] == number) + np.array([part.start for part in region])
        if not len(indices):
            continue
        clearance = distances[tuple(indices.T)]
        selected = indices[int(clearance.argmax())]
        position = np.array([axis[i] for axis, i in zip(axes, selected)])
        options.append({"centre_mm": position.tolist(), "conservative_clearance_diameter_mm": float(2 * (clearance.max() - guard)),
                        "boundary_sample_count": len(indices)})
    options.sort(key=lambda value: (-value["conservative_clearance_diameter_mm"], value["centre_mm"]))
    exits = []
    for candidate in options:
        if all(np.linalg.norm(np.array(candidate["centre_mm"]) - prior["centre_mm"]) >= 2 * diameter_mm for prior in exits):
            exits.append(candidate)
    wide_core = safe & core_mask
    unreachable = int(np.count_nonzero(wide_core & ~reachable))
    wide_count = int(wide_core.sum())
    enough = len(exits) >= required_exits
    status = "fail" if not enough else ("warning" if unreachable else "pass")
    return {"status": status, "required_exit_count": required_exits, "required_diameter_mm": diameter_mm,
            "detected_separate_exit_count": len(exits), "exit_examples": exits[:12],
            "grid_spacing_mm": spacing, "clearance_guard_mm": guard,
            "surface_displacement_guard_mm": surface_displacement_mm,
            "wide_core_centres": wide_count, "unreachable_wide_core_centres": unreachable,
            "wide_core_reachable_fraction": (wide_count - unreachable) / wide_count if wide_count else None,
            "message": f"检测到 {len(exits)} 处相互分离、满足 {diameter_mm:g} mm 保守净空通路的中底出口；{unreachable} 个宽孔中心未通过此净空通路连到外界。",
            "method": "EDT clearance minus sqrt(3)*spacing and maximum surface displacement; six-neighbour eroded-void exterior flood; separate components on sole envelope",
            "limitations": "finite grid and ideal spherical clearance; not actual powder-flow certification; vendor aperture requirements may be stricter"}
