"""Continuous sampled-field finishing of clipped material edges.

The centre domain comes from the entire generated material, never from a list
of failing rays. This is an opening-inspired construction with smoothing and
offset compensation, not an exact morphological opening or a thickness proof.
"""
import numpy as np

from engine.shoe.volume import geometry_dependencies


def round_material_edges(field, axes, top_map, collar_height_mm, radius_mm, *, lattice_cleanup=None):
    """Finish midsole ends and the collar lip; keep the plantar contact field.

    Mutates only the generated field. A zero radius is an exact no-op, allowing
    historical designs to retain their original geometry. Both added and
    removed sampled material are reported; the final mesh must be rechecked.
    For lattice material, bounded blends with the same original field retain
    the caller's unchanged disconnected-material cleanup gate. The first
    candidate is exactly the historical complete EDT finish.
    """
    if radius_mm == 0:
        return {"enabled": False, "radius_mm": 0.0}
    if not np.isfinite(radius_mm) or radius_mm < 0:
        raise ValueError("收口半径必须是非负有限数值。")
    geometry_dependencies()
    from scipy.ndimage import distance_transform_edt, gaussian_filter

    spacing = float(axes[0][1] - axes[0][0])
    before = field < 0
    original = field.copy() if lattice_cleanup is not None else None
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
    protected = weight == 0
    protected_values = field[protected].copy()
    # All points above top-1 mm and below collar-4 mm remain bitwise equal.
    # Elsewhere the blend may change either side of the material boundary;
    # the final mesh and its openings must therefore be checked again.
    field *= 1 - weight
    field += rounded * weight
    del rounded, weight
    attempts = []
    strength = 1.0
    cleanup = None
    if lattice_cleanup is not None:
        target = field.copy()
        try:
            for strength in (1.0, .5, .25, 0.0):
                if strength != 1:
                    if strength == 0:
                        field[:] = original
                    else:
                        field[:] = original * (1 - strength) + target * strength
                        field[protected] = protected_values
                try:
                    cleanup = lattice_cleanup(field, spacing)
                    if not np.array_equal(field[protected], protected_values):
                        raise ValueError("收口清理修改了足底保护区，拒绝此候选。")
                except ValueError as error:
                    attempts.append({"strength": strength, "status": "fail", "error": str(error)})
                    continue
                attempts.append({"strength": strength, "status": "pass", "cleanup": cleanup})
                break
            else:
                raise ValueError("收口候选及原材料场均未通过原连通门槛：" + repr(attempts))
        except Exception:
            # Failed candidates must not leak their edits to the caller.
            field[:] = original
            raise
        finally:
            del target, original
    else:
        attempts.append({"strength": 1.0, "status": "pass"})
    after = field < 0
    added = int(np.count_nonzero(after & ~before)) * spacing ** 3
    removed = int(np.count_nonzero(before & ~after)) * spacing ** 3
    protection_unchanged = bool(np.array_equal(field[protected], protected_values))
    if not protection_unchanged:
        raise ValueError("收口意外修改足底保护区，拒绝生成。")
    strength_message = (("原收口强度通过原晶格连通门槛。" if lattice_cleanup is not None else
                         "已生成请求的完整收口候选，仍须核验网格、厚度与排粉。") if strength == 1 else
                        (f"请求收口半径 {radius_mm:g} mm 的较强候选会切断材料连接；"
                         f"已从同一原材料场回退到 {strength:g} 强度，半径请求未修改；"
                         "仍须重新检查完整网格、厚度与排粉。" if strength else
                         f"请求收口半径 {radius_mm:g} mm 的正强度候选未通过连通门槛；"
                         "保留原材料场，未施加收口。仍须重新检查完整网格、厚度与排粉。"))
    return {"enabled": strength > 0, "radius_mm": radius_mm, "requested_radius_mm": radius_mm,
            "requested_strength": 1.0, "applied_strength": strength,
            "strength_attempts": attempts, "strength_message": strength_message,
            "cleanup": cleanup,
            "smoothing_sigma_mm": sigma_mm, "offset_compensation_mm": compensation_mm,
            "added_material_mm3": added, "removed_material_mm3": removed,
            "net_material_change_mm3": added - removed,
            "plantar_protection_depth_mm": 1.0, "midsole_transition_depth_mm": [1.0, 3.0],
            "collar_transition_below_lip_mm": [2.0, 4.0],
            "protected_field_unchanged": protection_unchanged,
            "protected_field_sample_count": int(protected.sum()),
            "method": "continuous EDT centre domain, distance offset, Gaussian smoothing, protected field blend; bounded original-field connectivity backtracking",
            "message": ("连续圆滑收口覆盖中底裁切末端和鞋口边缘；保留足底接触场，重新核验厚度、网格和排粉。"
                        if strength == 1 else strength_message),
            "limitations": "sampled geometry with both material removal and addition; radius is a design control, not a guaranteed final curvature or minimum wall"}


def smooth_finished_surface(mesh, axes, top_map, collar_height_mm):
    """Smooth protected generated geometry with bounded health backtracking.

    A close pair of clipped sheets can cross under unconstrained Laplacian
    smoothing. Every candidate starts from the same Marching Cubes mesh;
    a failed candidate is discarded, with no edits to the source field.
    Both foot reflections retain the existing complete mesh-health gate.
    """
    import bmesh
    import bpy
    from mathutils import Matrix
    import time
    from engine.shoe.volume import sample_map
    from engine.validate import coordinates, mesh_health

    started = time.monotonic()
    raw_health = None

    def reflected_health(candidate):
        reflected = candidate.copy()
        try:
            reflected.transform(Matrix.Diagonal((-1, 1, 1, 1)))
            bm = bmesh.new()
            try:
                bm.from_mesh(reflected)
                bmesh.ops.reverse_faces(bm, faces=list(bm.faces))
                bm.to_mesh(reflected)
            finally:
                bm.free()
            reflected.update()
            return mesh_health(reflected)
        finally:
            bpy.data.meshes.remove(reflected)

    def brief(health):
        keys = ("status", "self_intersection_pairs", "degenerate_faces", "near_duplicate_vertices",
                "non_manifold_edges", "connected_components", "signed_volume_mm3")
        return {key: health[key] for key in keys}

    spacing = float(axes[0][1] - axes[0][0])
    before = coordinates(mesh)
    roof = sample_map(top_map, before[:, :2], (axes[0][0], axes[1][0]), spacing)
    weights = np.maximum(np.clip((roof - before[:, 2] - 1) / 2, 0, 1),
                         np.clip((before[:, 2] - collar_height_mm + 4) / 2, 0, 1))
    bins = np.floor(weights * 32).astype(np.int32)
    obj = bpy.data.objects.new("edge_finish_temporary", mesh)
    bpy.context.scene.collection.objects.link(obj)
    previous_active = bpy.context.view_layer.objects.active
    result = None
    attempts = []
    accepted_factor = None
    displacement = None
    try:
        group = obj.vertex_groups.new(name="protected_edge_finish")
        for level in range(1, 33):
            indices = np.flatnonzero(bins == level)
            if len(indices):
                group.add(indices.tolist(), level / 32, "REPLACE")
        for factor in (.5, .25, .125, 0.0):
            candidate = mesh.copy() if factor else mesh
            obj.data = candidate
            if factor:
                modifier = obj.modifiers.new("continuous_edge_smoothing", "SMOOTH")
                modifier.factor = factor
                modifier.iterations = 6
                modifier.vertex_group = group.name
                bpy.context.view_layer.objects.active = obj
                bpy.ops.object.modifier_apply(modifier=modifier.name)
                if obj.data is not candidate:
                    bpy.data.meshes.remove(candidate)
                    candidate = obj.data
            health = mesh_health(candidate) if factor else raw_health
            mirror = reflected_health(candidate) if health["status"] == "pass" else None
            attempt = {"factor": factor, "iterations": 6 if factor else 0,
                       "health": brief(health), "mirrored_health": brief(mirror) if mirror else None}
            attempts.append(attempt)
            after = coordinates(candidate)
            candidate_displacement = np.linalg.norm(after - before, axis=1)
            protected_max = float(candidate_displacement[bins == 0].max()) if np.any(bins == 0) else 0.0
            if protected_max > 1e-6:
                raise ValueError("边缘平滑意外移动足底保护区，拒绝导出。")
            if health["status"] == "pass" and mirror["status"] == "pass":
                result = candidate
                displacement = candidate_displacement
                accepted_factor = factor
                break
            obj.data = mesh
            if candidate is not mesh:
                bpy.data.meshes.remove(candidate)
            if raw_health is None:
                # The original surface is relevant only on the fallback path;
                # the normal candidate retains the same six-iteration operator.
                raw_health = mesh_health(mesh)
        if result is None:
            raise ValueError("原收口网格或平滑候选未通过双脚完整健康检查：" + repr(attempts))
        info = {"method": "Blender weighted Smooth modifier; unchanged triangle connectivity; bounded complete-health backtracking",
                "iterations": 6 if accepted_factor else 0, "factor": accepted_factor,
                "requested_iterations": 6, "requested_factor": .5, "attempts": attempts,
                "original_health_when_backtracking": brief(raw_health) if raw_health else None,
                "reason": ("首选平滑通过双脚完整健康检查。" if len(attempts) == 1 else
                           ("较强平滑未通过双脚完整健康检查，保留原健康的隐式收口提取面，附加平滑位移为零。" if accepted_factor == 0 else
                            "较强平滑未通过双脚完整健康检查，已从原提取网格重新生成较弱候选；隐式收口场保持不变。")),
                "max_surface_displacement_mm": float(displacement.max()),
                "protected_vertex_count": int(np.count_nonzero(bins == 0)),
                "protected_max_displacement_mm": protected_max,
                "smoothing_and_health_elapsed_seconds": time.monotonic() - started}
    finally:
        temporary_mesh = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        bpy.context.view_layer.objects.active = previous_active
        if temporary_mesh is not mesh and temporary_mesh is not result:
            bpy.data.meshes.remove(temporary_mesh)
    if result is not mesh:
        bpy.data.meshes.remove(mesh)
    return result, info
