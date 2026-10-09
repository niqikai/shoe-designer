"""Continuous sampled-field finishing of clipped material edges.

The centre domain comes from the entire generated material, never from a list
of failing rays. This is an opening-inspired construction with smoothing and
offset compensation, not an exact morphological opening or a thickness proof.
"""
import numpy as np

from engine.shoe.volume import geometry_dependencies


def round_material_edges(field, axes, top_map, collar_height_mm, radius_mm):
    """Finish midsole ends and the collar lip; keep the plantar contact field.

    Mutates only the generated field. A zero radius is an exact no-op, allowing
    historical designs to retain their original geometry. Both added and
    removed sampled material are reported; the final mesh must be rechecked.
    """
    if radius_mm == 0:
        return {"enabled": False, "radius_mm": 0.0}
    if not np.isfinite(radius_mm) or radius_mm < 0:
        raise ValueError("收口半径必须是非负有限数值。")
    geometry_dependencies()
    from scipy.ndimage import distance_transform_edt, gaussian_filter

    spacing = float(axes[0][1] - axes[0][0])
    before = field < 0
    inside_distance = distance_transform_edt(before, sampling=spacing)
    centres = inside_distance >= radius_mm
    del inside_distance
    if not centres.any():
        raise ValueError("材料中没有能容纳收口半径的中心域，拒绝生成空模型。")
    rounded = (distance_transform_edt(~centres, sampling=spacing) - radius_mm).astype(np.float32)
    del centres
    # Smooth the grid-scale scallops of the sampled centre domain. Offset
    # compensation avoids shaving already adequate sheets into thinner ones.
    # These are geometric design settings, not manufacturing tolerances.
    sigma_mm = radius_mm * .375
    compensation_mm = radius_mm * .25
    gaussian_filter(rounded, sigma_mm / spacing, output=rounded)
    rounded -= compensation_mm

    height = np.asarray(axes[2], dtype=np.float32)[None, None, :]
    depth = np.asarray(top_map, dtype=np.float32)[:, :, None] - height
    midsole_weight = np.clip((depth - 1.0) / 2.0, 0, 1)
    collar_weight = np.clip((height - collar_height_mm + 4.0) / 2.0, 0, 1)
    weight = np.maximum(midsole_weight, collar_weight)
    # All points above top-1 mm and below collar-4 mm remain bitwise equal.
    # Elsewhere the blend may change either side of the material boundary;
    # the final mesh and its openings must therefore be checked again.
    field *= 1 - weight
    field += rounded * weight
    after = field < 0
    added = int(np.count_nonzero(after & ~before)) * spacing ** 3
    removed = int(np.count_nonzero(before & ~after)) * spacing ** 3
    return {"enabled": True, "radius_mm": radius_mm,
            "smoothing_sigma_mm": sigma_mm, "offset_compensation_mm": compensation_mm,
            "added_material_mm3": added, "removed_material_mm3": removed,
            "net_material_change_mm3": added - removed,
            "plantar_protection_depth_mm": 1.0, "midsole_transition_depth_mm": [1.0, 3.0],
            "collar_transition_below_lip_mm": [2.0, 4.0],
            "method": "continuous EDT centre domain, distance offset, Gaussian smoothing, protected field blend",
            "message": "连续圆滑收口覆盖中底裁切末端和鞋口边缘；保留足底接触场，重新核验厚度、网格和排粉。",
            "limitations": "sampled geometry with both material removal and addition; radius is a design control, not a guaranteed final curvature or minimum wall"}


def smooth_finished_surface(mesh, axes, top_map, collar_height_mm):
    """Remove extraction-scale corners with bounded, protected mesh smoothing.

    Connectivity is unchanged. The maximum vertex displacement bounds the
    displacement of every triangle point; powder screening reserves that
    additional clearance against the original sampled material field.
    """
    import bpy
    from engine.shoe.volume import sample_map
    from engine.validate import coordinates

    spacing = float(axes[0][1] - axes[0][0])
    before = coordinates(mesh)
    roof = sample_map(top_map, before[:, :2], (axes[0][0], axes[1][0]), spacing)
    weights = np.maximum(np.clip((roof - before[:, 2] - 1) / 2, 0, 1),
                         np.clip((before[:, 2] - collar_height_mm + 4) / 2, 0, 1))
    bins = np.floor(weights * 32).astype(np.int32)
    obj = bpy.data.objects.new("edge_finish_temporary", mesh)
    bpy.context.scene.collection.objects.link(obj)
    previous_active = bpy.context.view_layer.objects.active
    result = mesh
    try:
        group = obj.vertex_groups.new(name="protected_edge_finish")
        for level in range(1, 33):
            indices = np.flatnonzero(bins == level)
            if len(indices):
                group.add(indices.tolist(), level / 32, "REPLACE")
        modifier = obj.modifiers.new("continuous_edge_smoothing", "SMOOTH")
        modifier.factor = .5
        modifier.iterations = 6
        modifier.vertex_group = group.name
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.modifier_apply(modifier=modifier.name)
        result = obj.data
        after = coordinates(result)
        displacement = np.linalg.norm(after - before, axis=1)
        protected_max = float(displacement[bins == 0].max()) if np.any(bins == 0) else 0.0
        if protected_max > 1e-6:
            raise ValueError("边缘平滑意外移动足底保护区，拒绝导出。")
        info = {"method": "Blender weighted Smooth modifier; unchanged triangle connectivity",
                "iterations": 6, "factor": .5,
                "max_surface_displacement_mm": float(displacement.max()),
                "protected_vertex_count": int(np.count_nonzero(bins == 0)),
                "protected_max_displacement_mm": protected_max}
    finally:
        bpy.data.objects.remove(obj, do_unlink=True)
        bpy.context.view_layer.objects.active = previous_active
    if result is not mesh:
        bpy.data.meshes.remove(mesh)
    return result, info
