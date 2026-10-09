"""JSON -> deformed last -> solid/lattice midsole and upper -> checked exports."""
import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bmesh
import bpy
import numpy as np
from mathutils import Matrix

from engine.params import load_params, normalize_params
from engine.manufacturing import check_powder_paths, measure_thickness, process_profile
from engine.render import contact_sheet, material, render_cutaway, render_thin_locations, render_views, VIEWS
from engine.shoe.edge_finish import round_material_edges, smooth_finished_surface
from engine.shoe.features import extract_features
from engine.shoe.last import cut_last, deform_last, load_last, source_manifest
from engine.shoe.lattice import clean_lattice_field, inspect_voids, lattice_midsole
from engine.shoe.sole import sole_maps, solid_sole_field
from engine.shoe.upper import upper_field
from engine.shoe.volume import mesh_from_field, sample_map
from engine.validate import coordinates, require_healthy, surface_tree, triangles, validate_manufacturing


def build_last_candidate(params, *, last_mesh=None):
    """Retain the standalone M0.5 inspection workflow."""
    effective, warnings = normalize_params(params)
    source = last_mesh if last_mesh is not None else load_last()
    mesh = cut_last(source, effective["collar_height_mm"])
    label = effective["collar_style"] + "_last_candidate"
    require_healthy(mesh, label)
    obj = bpy.data.objects.new(label, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj["stage"] = "M0.5_last_only"
    obj["collar_height_mm"] = effective["collar_height_mm"]
    obj["clamp_messages"] = "\n".join(warnings)
    return obj


@dataclass
class ShoeBuild:
    model: object
    last: object
    features: dict
    report: dict


def sampling_spacing(params, override=None):
    """Resolution is an output setting; lattice features limit coarse sampling."""
    lattice = params["midsole_structure"] == "lattice"
    nominal = ({"preview": .8, "export": .5} if lattice else {"preview": 1.0, "export": .5})[params["resolution"]]
    limit = min(nominal, params["lattice_rod_mm"] / 2.5) if lattice else nominal
    if override is None:
        return limit, []
    if not (.5 <= override <= 1):
        raise ValueError("体素间距支持 0.5–1.0 mm。")
    if lattice and override > params["lattice_rod_mm"] / 2.5:
        fine = params["lattice_rod_mm"] / 2.5
        return fine, [f"晶格特征需要更细采样，体素间距已从 {override:g} mm 限制为 {fine:g} mm。"]
    return override, []


def mirror_mesh(mesh):
    result = mesh.copy()
    result.transform(Matrix.Diagonal((-1, 1, 1, 1)))
    bm = bmesh.new()
    bm.from_mesh(result)
    bmesh.ops.reverse_faces(bm, faces=list(bm.faces))
    bm.to_mesh(result)
    bm.free()
    result.update()
    return result


def build_shoe(params, *, last_mesh=None, voxel_mm=None, manufacturing=False):
    started = time.monotonic()
    effective, warnings = normalize_params(params)
    voxel_mm, resolution_warnings = sampling_spacing(effective, voxel_mm)
    warnings.extend(resolution_warnings)
    is_lattice = effective["midsole_structure"] == "lattice"
    stage = "M3" if manufacturing else ("M2" if is_lattice else "M1")
    source = last_mesh if last_mesh is not None else load_last()
    last, deformation = deform_last(source, effective)
    last_health = require_healthy(last, "形变鞋楦")
    features = extract_features(last, {"unit": "mm", "axes": {"toe": "+Y", "up": "+Z"}, "deformation": deformation})
    tree = surface_tree(last)
    points = coordinates(last)
    margin = effective["outsole_flare_mm"] + 4 * voxel_mm
    lower = points.min(axis=0) - margin
    upper = points.max(axis=0) + margin
    x, y = [np.arange(lower[i], upper[i] + voxel_mm, voxel_mm, dtype=np.float64) for i in (0, 1)]
    maps = sole_maps(tree, features, effective, x, y)
    z = np.arange(float(maps["bottom"].min()) - 4 * voxel_mm,
                  effective["collar_height_mm"] + 4 * voxel_mm, voxel_mm)
    print(f"{stage}: sampling {len(x)} x {len(y)} x {len(z)} at {voxel_mm:g} mm", flush=True)
    sole = solid_sole_field(maps, z, effective)
    envelope_mask = sole < 0 if manufacturing and is_lattice else None
    shell, shell_info = upper_field(tree, x, y, z, effective["upper_thickness_mm"], effective["collar_height_mm"])
    solid_sample_volume = int(np.count_nonzero(np.minimum(sole, shell) < 0)) * voxel_mm ** 3
    lattice_info = {"enabled": False}
    if is_lattice:
        sole, core_mask, lattice_info = lattice_midsole(sole, maps, x, y, z, features["dimensions_mm"]["length"], effective)
        lattice_info["enabled"] = True
        print(f"M2: {effective['lattice_type']} period {lattice_info['period_mm']:.2f} mm", flush=True)
    field = np.minimum(sole, shell)
    del sole, shell
    if is_lattice:
        lattice_info["field_cleanup"] = clean_lattice_field(field, voxel_mm)
    edge_finish = round_material_edges(field, (x, y, z), maps["top"],
                                       effective["collar_height_mm"], effective["boundary_rounding_mm"])
    if is_lattice:
        if edge_finish["enabled"]:
            edge_finish["cleanup"] = clean_lattice_field(field, voxel_mm)
        length = features["dimensions_mm"]["length"]
        for zone, lo, hi in (("heel", -np.inf, .30), ("arch", .30, .60), ("forefoot", .60, np.inf)):
            mask = core_mask & ((y >= lo * length) & (y < hi * length))[None, :, None]
            stats = lattice_info["zone_core_sampling"][zone]
            stats["periodic_field_fraction_before_union"] = stats["cropped_core_material_fraction"]
            stats["cropped_core_material_fraction"] = float(np.count_nonzero((field < 0) & mask) / mask.sum()) if mask.any() else None
        lattice_info["void_connectivity"] = inspect_voids(field, core_mask, voxel_mm)
        lattice_info["solid_reference_sample_volume_mm3"] = solid_sample_volume
        lattice_sample_volume = int(np.count_nonzero(field < 0)) * voxel_mm ** 3
        lattice_info["lattice_sample_volume_mm3"] = lattice_sample_volume
        lattice_info["sampled_material_reduction_fraction"] = 1 - lattice_sample_volume / solid_sample_volume
        if lattice_info["void_connectivity"]["trapped_core_void_voxels"]:
            warnings.append("采样发现部分内部空隙尚未连通外界；排粉／排料通道及制造适配留待 M3，当前不作可打印承诺。")
    mesh = mesh_from_field(field, (x[0], y[0], z[0]), voxel_mm, "shoe_right_mesh")
    surface_displacement = 0.0
    if edge_finish["enabled"]:
        mesh, edge_finish["surface_smoothing"] = smooth_finished_surface(mesh, (x, y, z), maps["top"], effective["collar_height_mm"])
        surface_displacement = edge_finish["surface_smoothing"]["max_surface_displacement_mm"]
    manufacturing_info = None
    if manufacturing:
        profile = process_profile(effective)
        minimum = profile["minimum_wall_mm"]
        thickness, _ = measure_thickness(mesh, minimum, samples=192000 if edge_finish["enabled"] else 24000)
        powder = {"status": "not_applicable", "message": "实心中底或非粉末工艺，不启用晶格排粉检查。"}
        if is_lattice and effective["print_process"] in ("SLS", "MJF"):
            powder = check_powder_paths(field, envelope_mask, core_mask, (x, y, z), surface_displacement_mm=surface_displacement)
        manufacturing_info = {"profile": profile, "thickness": thickness,
                              "powder_removal": powder,
                              "measurement_frame": "right design basis before optional X mirror"}
    del field, envelope_mask
    if is_lattice:
        del core_mask
    # Three regions share one welded watertight body. Material boundaries do not
    # create intersecting internal surfaces in the printing mesh.
    for name, color in (("outsole", (.16, .24, .25)), ("lattice_midsole" if is_lattice else "solid_midsole", (.79, .80, .72)), ("upper", (.18, .43, .40))):
        mesh.materials.append(material(name, color))
    centres = coordinates(mesh)[triangles(mesh)].mean(axis=1)
    bottom = sample_map(maps["bottom"], centres[:, :2], (x[0], y[0]), voxel_mm)
    top = sample_map(maps["top"], centres[:, :2], (x[0], y[0]), voxel_mm)
    region = np.where(centres[:, 2] < bottom + 3, 0, np.where(centres[:, 2] <= top + .3, 1, 2))
    mesh.polygons.foreach_set("material_index", region.astype(np.int32))
    for polygon in mesh.polygons:
        polygon.use_smooth = True
    if effective["foot_side"] == "left":
        original = mesh
        mesh = mirror_mesh(original)
        bpy.data.meshes.remove(original)
        right_last = last
        last = mirror_mesh(right_last)
        bpy.data.meshes.remove(right_last)
        features = extract_features(last, {"unit": "mm", "axes": {"toe": "+Y", "up": "+Z"}, "deformation": deformation, "mirrored": True})
    features["size_estimate"].update({
        "label": f"nominal EU {effective['size_eu']}; unconfirmed fit",
        "basis": deformation["grading_note"],
        "nominal_size_eu": effective["size_eu"],
    })
    features["selected_foot_side"] = effective["foot_side"]
    obj = bpy.data.objects.new("shoe_" + effective["foot_side"], mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj["unit"] = "mm"
    obj["stage"] = "M2_lattice_midsole" if is_lattice else "M1_solid_midsole"
    obj["foot_side"] = effective["foot_side"]
    health = require_healthy(mesh, stage + " 整鞋")
    if health["connected_components"] != 1:
        raise ValueError("整鞋未连接成单一实体。")
    if manufacturing:
        if effective["foot_side"] == "left":
            # Position diagnostics follow the actual shoe's design coordinate frame.
            for item in manufacturing_info["powder_removal"].get("exit_examples", []):
                item["centre_mm"][0] *= -1
            for diagnostic in (manufacturing_info["thickness"],):
                for item in diagnostic.get("thin_examples", []):
                    for key in ("position_mm", "opposite_mm", "midpoint_mm", "normal"):
                        item[key][0] *= -1
        manufacturing_info["measurement_frame"] = "selected foot in design coordinates"
        manufacturing_info = validate_manufacturing(mesh, effective, health, manufacturing_info)
        health["manufacturing_status"] = manufacturing_info["status"]
    radius = np.minimum(effective["edge_radius_mm"], (maps["top"] - maps["bottom"]) * .45)
    radius = radius[maps["outline_distance"] <= effective["outsole_flare_mm"]]
    radius_range = [float(radius.min()), float(radius.max())]
    if radius_range[0] < effective["edge_radius_mm"] - 1e-4:
        warnings.append(f"部分薄底区域不足以容纳目标圆角，局部半径已限制为 {radius_range[0]:.2f}–{radius_range[1]:.2f} mm。")
    report = {"effective_params": effective, "clamp_messages": warnings + deformation["warnings"],
              "deformation": deformation, "deformed_last_health": last_health,
              "deformed_last_health_frame": "right basis before optional X reflection", "mesh_health": health,
              "voxel_mm": voxel_mm, "resolution": effective["resolution"], "lattice": lattice_info,
              "grid_shape": [len(x), len(y), len(z)], "upper": shell_info, "edge_finish": edge_finish,
              "sole": {"outsole_base_mm": 3, "flare_mm": effective["outsole_flare_mm"],
                       "edge_radius_mm": effective["edge_radius_mm"], "effective_radius_range_mm": radius_range, "anchors": maps["anchors"],
                       "bottom_anchor_z_mm": maps["bottom_anchor_z_mm"],
                       "midsole": "lattice core with curved solid top skin" if is_lattice else "solid; top sampled from deformed high-poly plantar surface"},
              "geometry_elapsed_seconds": time.monotonic() - started,
              "manufacturing": manufacturing_info}
    return ShoeBuild(obj, last, features, report)


def write_json(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def export_shoe(obj, output):
    for other in bpy.context.scene.objects:
        other.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.length_unit = "MILLIMETERS"
    scene.unit_settings.scale_length = .001
    stl = output / (obj.name + ".stl")
    glb = output / (obj.name + ".glb")
    bpy.ops.wm.stl_export(filepath=str(stl), export_selected_objects=True, apply_modifiers=True,
                          global_scale=1, use_scene_unit=False, forward_axis="Y", up_axis="Z")
    # glTF uses metres independently of Blender's display-unit setting.
    # Scale an export-only copy explicitly so round-trips can verify dimensions.
    proxy = obj.copy()
    proxy.data = obj.data.copy()
    proxy.data.transform(Matrix.Scale(.001, 4))
    scene.collection.objects.link(proxy)
    obj.select_set(False)
    proxy.select_set(True)
    bpy.context.view_layer.objects.active = proxy
    try:
        bpy.ops.export_scene.gltf(filepath=str(glb), export_format="GLB", use_selection=True,
                                  export_apply=True, export_yup=True)
    finally:
        copy_mesh = proxy.data
        bpy.data.objects.remove(proxy, do_unlink=True)
        bpy.data.meshes.remove(copy_mesh)
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
    return {"stl": str(stl), "glb": str(glb), "stl_coordinate_unit": "mm", "glb_coordinate_unit": "m"}


def print_pose_copy(obj, pose):
    selected = pose["selected"]
    matrix = Matrix(selected["rotation_matrix_3x3"]).to_4x4()
    matrix.translation = selected["translation_mm"]
    proxy = obj.copy()
    proxy.data = obj.data.copy()
    proxy.name = obj.name + "_print"
    proxy.data.transform(matrix)
    bpy.context.scene.collection.objects.link(proxy)
    return proxy


def export_print_pose(obj, pose, output):
    if not pose["fits_selected"]:
        raise ValueError("选定制造姿态未通过构建空间检查。")
    proxy = print_pose_copy(obj, pose)
    try:
        result = export_shoe(proxy, output)
        return {"print_" + key: value for key, value in result.items()}
    finally:
        mesh = proxy.data
        bpy.data.objects.remove(proxy, do_unlink=True)
        bpy.data.meshes.remove(mesh)


def render_build_pose(obj, pose, output, process):
    proxy = print_pose_copy(obj, pose)
    dims = pose["machine_dimensions_mm"]
    bpy.ops.mesh.primitive_cube_add(size=1, location=tuple(value / 2 for value in dims))
    chamber = bpy.context.object
    chamber.name = "build_volume_guide"
    chamber.data.transform(Matrix.Diagonal((*dims, 1)))
    chamber.data.materials.append(material("build_volume_guide", (.50, .58, .60)))
    wire = chamber.modifiers.new("volume_outline", "WIREFRAME")
    wire.thickness = .5
    bpy.context.view_layer.update()
    try:
        selected = pose["selected"]
        files = render_views([proxy, chamber], chamber, output, "build", f"{process} | " + " x ".join(f"{v:g}" for v in dims) + " mm",
                             views=("iso",), footer=f"M3 | yaw {selected['yaw_deg']:g} deg | tilt {selected['tilt_deg']:g} deg | {'SPACE FITS' if pose['fits_selected'] else 'DOES NOT FIT'}")
        return files["iso"]
    finally:
        for temporary in (proxy, chamber):
            mesh = temporary.data
            bpy.data.objects.remove(temporary, do_unlink=True)
            bpy.data.meshes.remove(mesh)


def run(params_path, output, *, voxel_mm=None, render=True):
    started = time.monotonic()
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    # A failed new run must not leave an earlier print file appearing current.
    for foot in ("right", "left"):
        for suffix in ("", "_print"):
            for extension in ("stl", "glb"):
                (output / f"shoe_{foot}{suffix}.{extension}").unlink(missing_ok=True)
    # A passing rerun must not leave the previous failed candidate's markers.
    for diagnostic in ("thin_locations.png", "previews/thin_side.png", "previews/thin_iso.png"):
        (output / diagnostic).unlink(missing_ok=True)
    effective, warnings = load_params(params_path)
    stage = "M3"
    write_json(output / "report.json", {"stage": stage, "status": "running"})
    before = source_manifest()
    built = build_shoe(effective, voxel_mm=voxel_mm, manufacturing=True)
    built.report["clamp_messages"] = warnings + built.report["clamp_messages"]
    bpy.context.view_layer.update()
    if source_manifest() != before:
        raise ValueError("原始素材校验发生变化，已阻止导出。")
    exports = {}
    if built.report["manufacturing"]["export_allowed"]:
        exports = export_shoe(built.model, output)
        exports.update(export_print_pose(built.model, built.report["manufacturing"]["build_volume"], output))
    previews = {}
    if render:
        previews = render_views([built.model], built.model, output / "previews", "shoe", f"LOW 75 mm | EU {effective['size_eu']}" if effective["collar_height_mm"] == 75 else f"{effective['collar_style'].upper()} {effective['collar_height_mm']:g} mm | EU {effective['size_eu']}",
                                foot=effective["foot_side"], footer=f"{effective['foot_side'].upper()} | {effective['lattice_type'].upper() if effective['midsole_structure'] == 'lattice' else 'SOLID MIDSOLE'} | {stage}")
        contact_sheet([previews[key] for key in VIEWS], output / "four_views.png")
        if effective["midsole_structure"] == "lattice":
            cutaway = render_cutaway(built.model, output / "previews", effective["lattice_type"])
            contact_sheet(list(cutaway.values()), output / "cutaway.png")
            previews["cutaway"] = cutaway
        if built.report["manufacturing"]["thickness"]["thin_sample_count"]:
            issues = render_thin_locations(built.model, built.report["manufacturing"]["thickness"], output / "previews")
            contact_sheet(list(issues.values()), output / "thin_locations.png")
            previews["thin_locations"] = issues
        previews["build_pose"] = render_build_pose(built.model, built.report["manufacturing"]["build_volume"], output / "previews", effective["print_process"])
    if source_manifest() != before:
        raise ValueError("原始素材校验发生变化。")
    report = {"stage": stage, "status": built.report["manufacturing"]["status"], **built.report,
              "params_file": str(Path(params_path).resolve()), "exports": exports, "previews": previews,
              "original_sources_unchanged": True, "elapsed_seconds": time.monotonic() - started,
              "manufacturing_status": built.report["manufacturing"]["status"], "license_reminder": "本鞋楦仅限非商业使用，不得分发。"}
    write_json(output / "features.json", built.features)
    write_json(output / "effective_params.json", effective)
    write_json(output / "report.json", report)
    manufacturing = report["manufacturing"]
    lines = ["# M3 制造筛查", "", manufacturing["summary"], "",
             f"工艺：{effective['print_process']}；TPU {effective['tpu_shore_a']}A；采样间距 {report['voxel_mm']:g} mm。", "",
             manufacturing["thickness"]["message"], "", manufacturing["powder_removal"]["message"], "",
             manufacturing["build_volume"]["message"], "", manufacturing["overhang"]["message"], "",
             "## 警告与后续", "", *["- " + item for item in manufacturing["warnings"]], "",
             "厚度为最终网格的射线采样，不能证明所有未采样位置的最小值；详见 report.json。", "",
             "导出状态：" + ("候选文件已导出；打印时使用 *_print.stl 中已核验的姿态。" if manufacturing["export_allowed"] else "已阻止所有 STL／GLB 导出。"), "",
             "本鞋楦仅限非商业使用，不得分发。"]
    (output / "manufacturing_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(stage + "_REPORT " + json.dumps({"out": str(output), "elapsed_seconds": report["elapsed_seconds"], "mesh": report["mesh_health"]["status"]}), flush=True)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--params", type=Path, default=ROOT / "designs/current.json")
    parser.add_argument("--out", type=Path, default=ROOT / "out/m3")
    parser.add_argument("--voxel-mm", type=float)
    parser.add_argument("--no-render", action="store_true")
    options = parser.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else [])
    try:
        result = run(options.params, options.out, voxel_mm=options.voxel_mm, render=not options.no_render)
    except Exception as exc:
        for foot in ("right", "left"):
            for suffix in ("", "_print"):
                for extension in ("stl", "glb"):
                    (options.out / f"shoe_{foot}{suffix}.{extension}").unlink(missing_ok=True)
        write_json(options.out / "report.json", {"stage": "M3", "status": "fail", "error": str(exc), "manufacturing_status": "error"})
        raise
    if result["status"] == "fail":
        raise SystemExit(2)
