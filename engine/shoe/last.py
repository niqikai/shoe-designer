"""Read-only licensed-source boundary and parameterized last normalization/cutting.

No vertex lists are authored here: imported geometry is processed using Blender
matrix transforms and BMesh operators. Derived geometry stays under assets/.
"""
import hashlib
import math
from collections import Counter
from pathlib import Path
import zipfile

import bmesh
import bpy
import numpy as np
from mathutils import Matrix, Vector

from engine.validate import boundary_loops, coordinates, surface_tree, triangles

ROOT = Path(__file__).resolve().parents[2]
ASSET_DIR = ROOT / "assets/last"
RAW_DIR = ASSET_DIR / "raw"
ARCHIVE = ASSET_DIR / "Sneaker - Zellerfeld - Template Files.zip"
TEMPLATE = RAW_DIR / "Zellerfeld_sneaker_template_files.blend"
REFERENCE_NAMES = (
    "sneaker_sole_thickness_region", "sneaker_ankle_height", "sneaker_reference_thickness",
    "sneaker_reference_collar_instep", "sneaker_reference_overhangs",
    "sneaker_reference_shoe_height", "sneaker_reference_texturemap",
)


def _safe_value(item):
    if item is None or isinstance(item, (str, int, float, bool)):
        return item
    if isinstance(item, bpy.types.ID):
        return {"name": item.name, "type": type(item).__name__}
    if hasattr(item, "items"):
        return {str(key): _safe_value(value) for key, value in item.items()}
    try:
        return [_safe_value(value) for value in item]
    except TypeError:
        return str(item)


def _properties(item):
    return {key: _safe_value(item[key]) for key in item.keys()}


def inspect_template():
    """Read the source scene without running its text blocks, then clear it."""
    bpy.ops.wm.open_mainfile(filepath=str(TEMPLATE), load_ui=False, use_scripts=False)
    inventory = {
        "file_version_metadata": list(bpy.data.version),
        "autoexec_enabled": bpy.context.preferences.filepaths.use_scripts_auto_execute,
        "collections": [{"name": c.name, "objects": [obj.name for obj in c.objects], "custom_properties": _properties(c)} for c in bpy.data.collections],
        "objects": [{"name": obj.name, "type": obj.type, "location": list(obj.location),
                     "rotation_euler": list(obj.rotation_euler), "scale": list(obj.scale),
                     "modifiers": [{"name": m.name, "type": m.type} for m in obj.modifiers],
                     "constraints": [{"name": c.name, "type": c.type} for c in obj.constraints],
                     "custom_properties": _properties(obj),
                     "mesh_custom_properties": _properties(obj.data) if obj.type == "MESH" else None,
                     "materials": [slot.material.name if slot.material else None for slot in obj.material_slots],
                     "vertices": len(obj.data.vertices) if obj.type == "MESH" else None,
                     "polygons": len(obj.data.polygons) if obj.type == "MESH" else None} for obj in bpy.data.objects],
        "materials": [{"name": m.name, "custom_properties": _properties(m),
                       "node_types": dict(Counter(node.bl_idname for node in m.node_tree.nodes)) if m.node_tree else {}} for m in bpy.data.materials],
        "node_groups": [{"name": group.name, "type": group.bl_idname, "custom_properties": _properties(group),
                         "node_types": dict(Counter(node.bl_idname for node in group.nodes))} for group in bpy.data.node_groups],
        "scenes": [{"name": scene.name, "unit_system": scene.unit_settings.system,
                    "length_unit": scene.unit_settings.length_unit, "scale_length": scene.unit_settings.scale_length,
                    "custom_properties": _properties(scene)} for scene in bpy.data.scenes],
        "texts": [{"name": text.name, "use_module": text.use_module} for text in bpy.data.texts],
    }
    bpy.ops.wm.read_factory_settings(use_empty=True)
    return inventory


def source_manifest():
    """Compare every raw file to the original ZIP, and require read-only sources."""
    if ARCHIVE.stat().st_mode & 0o222:
        raise ValueError("原始 ZIP 尚未设置只读。")
    entries = []
    with zipfile.ZipFile(ARCHIVE) as archive:
        for info in archive.infolist():
            relative = Path(info.filename)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("素材包中存在不安全路径。")
            if info.is_dir():
                continue
            path = RAW_DIR / relative
            if path.stat().st_mode & 0o222:
                raise ValueError("原始文件尚未设置只读：" + path.name)
            original_hash = hashlib.sha256(archive.read(info)).hexdigest()
            actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            if original_hash != actual_hash:
                raise ValueError("解压文件与 ZIP 不一致：" + path.name)
            entries.append({"file": info.filename, "bytes": info.file_size, "sha256": actual_hash})
    return {"zip_sha256": hashlib.sha256(ARCHIVE.read_bytes()).hexdigest(), "files": entries}


def _import_mesh(path):
    previous = set(bpy.data.objects)
    if path.suffix == ".stl":
        bpy.ops.wm.stl_import(filepath=str(path), global_scale=1.0, use_scene_unit=False,
                              use_mesh_validate=False, forward_axis="Y", up_axis="Z")
    elif path.suffix == ".obj":
        # Identity import first. The source OBJ is Y-up; Rx(+90) is explicit later.
        bpy.ops.wm.obj_import(filepath=str(path), forward_axis="Y", up_axis="Z",
                              validate_meshes=False, use_split_objects=False, use_split_groups=False)
    else:
        raise ValueError("不支持的鞋楦格式。")
    imported = [obj for obj in bpy.data.objects if obj not in previous and obj.type == "MESH"]
    if len(imported) != 1:
        raise ValueError("预期读取单个鞋楦网格。")
    obj = imported[0]
    mesh = obj.data
    mesh.transform(obj.matrix_world)
    mesh.update()
    bpy.data.objects.remove(obj, do_unlink=True)
    return mesh


def load_last(quality="high", *, normalized=True):
    """Return a Blender Mesh; source geometry access is isolated to this interface."""
    if quality not in ("high", "low"):
        raise ValueError("鞋楦只支持 high / low 两种精度。")
    if normalized:
        normalized_file = ASSET_DIR / "last_normalized.blend"
        mesh_name = "last_highpoly_mesh" if quality == "high" else "last_lowpoly_mesh"
        if not normalized_file.is_file():
            raise FileNotFoundError("请先运行 tools/inspect_last.sh 生成规范化鞋楦。")
        with bpy.data.libraries.load(str(normalized_file), link=False) as (source, target):
            if mesh_name not in source.meshes:
                raise ValueError("规范化文件中缺少预期鞋楦，请重新执行 M0.5。")
            target.meshes = [mesh_name]
        return target.meshes[0]
    filename = "sneaker_last_highpoly.stl" if quality == "high" else "sneaker_last_lowpoly.obj"
    return _import_mesh(RAW_DIR / filename)


def load_reference(name):
    if name not in REFERENCE_NAMES:
        raise ValueError("不是允许读取的参考对象。")
    return _import_mesh(RAW_DIR / (name + ".stl"))


def unit_evidence():
    # Read STEP unit metadata only; do not import or use its geometry.
    step_text = (RAW_DIR / "sneaker_last.stp").read_text(errors="replace")
    return {"stl": "unitless", "obj": "unitless", "step_declares_millimeters": "SI_UNIT(.MILLI.,.METRE.)" in step_text,
            "coordinate_convention": "1 coordinate unit = 1 mm",
            "evidence": "STEP unit declaration and matching source mesh dimensions; template scene unit metadata is inconsistent"}


def stl_encoding_report():
    data = (RAW_DIR / "sneaker_last_highpoly.stl").read_bytes()
    count = int.from_bytes(data[80:84], "little")
    if len(data) != 84 + count * 50:
        raise ValueError("高模 STL 不是预期的二进制格式。")
    dtype = np.dtype([("normal", "<f4", 3), ("vertices", "<f4", (3, 3)), ("attribute", "<u2")])
    facets = np.frombuffer(data, dtype=dtype, offset=84, count=count)
    points = facets["vertices"]
    computed = np.cross(points[:, 1] - points[:, 0], points[:, 2] - points[:, 0])
    dots = (computed * facets["normal"]).sum(axis=1)
    return {"facets": count, "corner_records": count * 3,
            "unique_positions": len(np.unique(points.reshape((-1, 3)), axis=0)),
            "stored_facet_normals_opposed_to_winding": int((dots < -1e-8).sum()),
            "note": "STL repeats three corner coordinates per triangle by format; this is not a duplicate-vertex defect in the welded mesh"}


def normalization_frame(high_mesh):
    points = coordinates(high_mesh)
    minimum, maximum = points.min(axis=0), points.max(axis=0)
    heel_band = points[points[:, 0] <= minimum[0] + 0.05 * (maximum[0] - minimum[0])]
    heel_center_y = float((heel_band[:, 1].min() + heel_band[:, 1].max()) * 0.5)
    frame = Matrix.Translation((heel_center_y, -float(minimum[0]), -float(minimum[2]))) @ Matrix.Rotation(math.pi / 2, 4, "Z")
    obj_to_stl = Matrix.Rotation(math.pi / 2, 4, "X")
    return frame, frame @ obj_to_stl, {
        "origin_definition": "heel rear projection onto the horizontal plane at the lowest plantar point; heel pitch is preserved",
        "source_origin_xyz_mm": [float(minimum[0]), heel_center_y, float(minimum[2])],
        "ground_plane_source_z_mm": float(minimum[2]),
        "heel_center_method": "midpoint of lateral extrema in posterior 5% band",
        "high_source_to_normalized": [list(row) for row in frame],
        "low_obj_source_to_normalized": [list(row) for row in (frame @ obj_to_stl)],
        "axes": {"toe": "+Y", "up": "+Z", "plantar_outward": "-Z"},
        "scale": 1.0, "unit": "mm",
    }


def repair_copy(mesh, *, merge_tolerance_mm=1e-5, maximum_hole_perimeter_mm=1.0):
    result = mesh.copy()
    bm = bmesh.new()
    actions = []
    try:
        bm.from_mesh(result)
        bm.normal_update()
        before = len(bm.verts)
        bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=merge_tolerance_mm)
        removed = before - len(bm.verts)
        if removed:
            actions.append({"action": "merge_near_duplicates", "removed_vertices": removed, "tolerance_mm": merge_tolerance_mm})
        boundaries = [edge for edge in bm.edges if edge.is_boundary]
        if boundaries:
            loops = boundary_loops(boundaries)
            for loop in loops:
                perimeter = sum((loop[(i + 1) % len(loop)].co - vert.co).length for i, vert in enumerate(loop))
                if perimeter > maximum_hole_perimeter_mm:
                    raise ValueError("鞋楦有超过自动修复范围的开口，拒绝改变脚型。")
            filled = bmesh.ops.holes_fill(bm, edges=boundaries, sides=0)["faces"]
            bmesh.ops.triangulate(bm, faces=filled)
            actions.append({"action": "fill_small_holes", "loops": len(loops), "maximum_perimeter_mm": maximum_hole_perimeter_mm})
        bm.normal_update()
        old_normals = {face: face.normal.copy() for face in bm.faces}
        bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
        if bm.calc_volume(signed=True) < 0:
            bmesh.ops.reverse_faces(bm, faces=list(bm.faces))
        bm.normal_update()
        changed = sum(old_normals[face].dot(face.normal) < 0 for face in old_normals if face.is_valid)
        if changed:
            actions.append({"action": "orient_outward", "flipped_faces": changed})
        bm.to_mesh(result)
        result.update()
        return result, actions
    finally:
        bm.free()


def cut_last(mesh, height_mm):
    """Cut an imported last using a plane and algorithmically cap every cut loop."""
    points = coordinates(mesh)
    if not math.isfinite(height_mm) or not (points[:, 2].min() + 1 < height_mm < points[:, 2].max() - 1):
        raise ValueError("鞋口裁切高度必须位于鞋楦内部。")
    result = mesh.copy()
    bm = bmesh.new()
    try:
        bm.from_mesh(result)
        bmesh.ops.bisect_plane(bm, geom=list(bm.verts) + list(bm.edges) + list(bm.faces),
                               plane_co=(0, 0, height_mm), plane_no=(0, 0, 1), dist=1e-4,
                               use_snap_center=True, clear_outer=True, clear_inner=False)
        before_vertices = len(bm.verts)
        bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=1e-4)
        merged = before_vertices - len(bm.verts)
        bmesh.ops.dissolve_degenerate(bm, edges=list(bm.edges), dist=1e-4)
        edges = [edge for edge in bm.edges if edge.is_boundary]
        if not edges:
            raise ValueError("裁切没有产生预期鞋口边界。")
        if any(abs(vert.co.z - height_mm) > 1e-3 for edge in edges for vert in edge.verts):
            raise ValueError("裁切发现其他位置的开口，拒绝自动掩盖。")
        loops = boundary_loops(edges)
        caps = bmesh.ops.holes_fill(bm, edges=edges, sides=0)["faces"]
        for face in caps:
            face.material_index = 1
            face.smooth = False
        bmesh.ops.triangulate(bm, faces=list(bm.faces), ngon_method="BEAUTY")
        bmesh.ops.dissolve_degenerate(bm, edges=list(bm.edges), dist=1e-5)
        bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
        if bm.calc_volume(signed=True) < 0:
            bmesh.ops.reverse_faces(bm, faces=list(bm.faces))
        bm.to_mesh(result)
        result.update()
        result["collar_height_mm"] = height_mm
        result["cut_loops_capped"] = len(loops)
        result["cut_cleanup_merged_vertices"] = merged
        result["cut_cleanup_tolerance_mm"] = 1e-4
        return result
    finally:
        bm.free()


def compare_surfaces(high_mesh, low_mesh):
    def directed(source, target):
        points = coordinates(source)
        faces = triangles(source)
        centroids = points[faces].mean(axis=1)
        samples = np.concatenate((points, centroids))
        target_tree = surface_tree(target)
        distances = np.array([target_tree.find_nearest(Vector(point))[3] for point in samples])
        largest = int(distances.argmax())
        return {"samples": len(samples), "vertex_samples": len(points), "triangle_centroid_samples": len(centroids),
                "max_mm": float(distances[largest]), "mean_mm": float(distances.mean()),
                "rms_mm": float(np.sqrt(np.mean(distances ** 2))),
                "p50_mm": float(np.percentile(distances, 50)), "p95_mm": float(np.percentile(distances, 95)),
                "source_point_at_max_mm": samples[largest].tolist()}
    high_to_low = directed(high_mesh, low_mesh)
    low_to_high = directed(low_mesh, high_mesh)
    return {"high_to_low": high_to_low, "low_to_high": low_to_high,
            "bidirectional_sampled_max_mm": max(high_to_low["max_mm"], low_to_high["max_mm"]),
            "method": "all vertices and all triangle centroids to the other continuous triangle surface; same rigid anatomical frame, no fitting or scaling",
            "limitation": "sampled maximum, not an exact continuous Hausdorff maximum; percentiles are not area-weighted"}
