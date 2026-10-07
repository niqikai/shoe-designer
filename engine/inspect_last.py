"""M0.5 headless inspection, normalization, closed cuts and evidence previews."""
import argparse
import json
from pathlib import Path
import sys
import time

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.build import build_shoe
from engine.params import load_params, normalize_params, read_schema
from engine.render import contact_sheet, material, render_views, VIEWS
from engine.shoe.features import extract_features
from engine.shoe.last import (ASSET_DIR, REFERENCE_NAMES, compare_surfaces, inspect_template, load_last,
                              load_reference, normalization_frame, repair_copy, source_manifest,
                              stl_encoding_report, unit_evidence)
from engine.validate import coordinates, mesh_health, require_healthy


def write_json(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def mesh_object(mesh, name):
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def export_normalized(high, low):
    # Save just the two validated, normalized lasts, without camera annotations.
    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.length_unit = "MILLIMETERS"
    scene.unit_settings.scale_length = 0.001
    high.data.name = "last_highpoly_mesh"
    low.data.name = "last_lowpoly_mesh"
    high["unit"] = low["unit"] = "mm"
    high["toe_axis"] = low["toe_axis"] = "+Y"
    low.hide_render = True
    low.hide_set(True)
    for obj in scene.objects:
        obj.select_set(False)
    high.select_set(True)
    bpy.context.view_layer.objects.active = high
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=str(ASSET_DIR / "last_normalized.blend"))
    bpy.ops.wm.obj_export(filepath=str(ASSET_DIR / "last_normalized.obj"), export_selected_objects=True,
                          forward_axis="Y", up_axis="Z", global_scale=1.0,
                          export_materials=False, export_uv=False, export_normals=True, apply_modifiers=False)


def inspect(output, params_path, *, render=True):
    started = time.monotonic()
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "health_report.json", {"stage": "M0.5", "status": "running"})
    before = source_manifest()
    effective, warnings = load_params(params_path)
    write_json(output / "effective_params.json", effective)
    inventory = inspect_template()
    write_json(output / "template_inventory.json", inventory)
    print("M0.5: template inventory complete", flush=True)
    evidence = unit_evidence()
    if not evidence["step_declares_millimeters"]:
        raise ValueError("未能确认源模型的毫米单位依据。")
    high_source, low_source = load_last(normalized=False), load_last("low", normalized=False)
    source_high_report = mesh_health(high_source)
    source_low_report = mesh_health(low_source)
    write_json(output / "source_high_health.json", source_high_report)
    write_json(output / "source_low_health.json", source_low_report)
    high, high_actions = repair_copy(high_source)
    low, low_actions = repair_copy(low_source)
    frame, low_frame, normalization = normalization_frame(high)
    high.transform(frame)
    low.transform(low_frame)
    high.update()
    low.update()
    high_report = require_healthy(high, "规范化高模")
    low_report = require_healthy(low, "规范化低模")
    alignment = compare_surfaces(high, low)
    print("M0.5: alignment sampled maximum = %.4f mm" % alignment["bidirectional_sampled_max_mm"], flush=True)
    features = extract_features(high, normalization)
    features["source_manifest"] = before
    features["unit_evidence"] = evidence
    features["alignment"] = alignment
    write_json(ASSET_DIR / "last_features.json", features)
    write_json(output / "last_features.json", features)
    print("M0.5: features extracted", flush=True)

    body = material("last_surface", (0.54, 0.66, 0.67))
    cap = material("cut_cap", (0.10, 0.50, 0.40))
    high.materials.clear()
    high.materials.append(body)
    high.materials.append(cap)
    for poly in high.polygons:
        poly.use_smooth = True
    low.materials.clear()
    low.materials.append(body)
    high_obj = mesh_object(high, "last_highpoly")
    low_obj = mesh_object(low, "last_lowpoly")
    bpy.context.view_layer.update()
    export_normalized(high_obj, low_obj)
    cut_reports = {}
    candidates = {}
    case_params = {"current": effective}
    for style, default in read_schema()["x-style-defaults"].items():
        case_params[style] = normalize_params({**effective, "collar_style": style, "collar_height_mm": default})[0]
    for case, values in case_params.items():
        candidate = build_shoe(values, last_mesh=high)
        candidate.name = case + "_last_candidate"
        for poly in candidate.data.polygons:
            poly.use_smooth = poly.material_index != 1
        candidates[case] = candidate
        cut_reports[case] = {"params": values, "health": require_healthy(candidate.data, case),
                             "capped_loops": candidate.data["cut_loops_capped"],
                             "cleanup_merged_vertices": candidate.data["cut_cleanup_merged_vertices"],
                             "cleanup_tolerance_mm": candidate.data["cut_cleanup_tolerance_mm"]}
    print("M0.5: all collar candidates are closed", flush=True)
    reference_reports = {}
    for name in REFERENCE_NAMES:
        reference = load_reference(name)
        reference_reports[name] = mesh_health(reference, intersections=False)
        reference_reports[name]["usage"] = "measurements only; never boolean operand"
        bpy.data.meshes.remove(reference)

    previews = {}
    if render:
        preview_dir = output / "previews"
        side = features["laterality"]["inferred"]
        previews["original"] = render_views([high_obj], high_obj, preview_dir, "original", "ORIGINAL LAST | 136 mm", foot=side)
        for style in ("low", "mid"):
            height = case_params[style]["collar_height_mm"]
            previews[style] = render_views([candidates[style]], high_obj, preview_dir, style, f"{style.upper()} CANDIDATE | {height:g} mm", foot=side)
        if effective == case_params[effective["collar_style"]]:
            previews["current"] = previews[effective["collar_style"]]
        else:
            height = effective["collar_height_mm"]
            previews["current"] = render_views([candidates["current"]], high_obj, preview_dir, "current", f"CURRENT {effective['collar_style'].upper()} | {height:g} mm", foot=side)
        for case, files in previews.items():
            contact_sheet([files[view] for view in VIEWS], output / (case + "_four_views.png"))
        contact_sheet([previews["low"]["side"], previews["mid"]["side"], previews["low"]["iso"], previews["mid"]["iso"]], output / "collar_comparison.png")
        # A separate wire overlay makes proxy approximation visible without changing either last.
        wire = mesh_object(low.copy(), "proxy_wire_overlay")
        wire.data.materials.clear()
        wire.data.materials.append(material("proxy_red", (0.65, 0.12, 0.08)))
        modifier = wire.modifiers.new("proxy_edges", "WIREFRAME")
        modifier.thickness = 0.25
        previews["proxy_overlay"] = render_views([high_obj, wire], high_obj, preview_dir, "proxy_overlay", "HIGH + LOW PROXY (RED)", views=("top", "iso"), foot=side)
        contact_sheet(list(previews["proxy_overlay"].values()), output / "proxy_alignment.png")
        bpy.data.objects.remove(wire, do_unlink=True)
    after = source_manifest()
    if after != before:
        raise ValueError("原始素材校验发生变化，停止交付。")
    write_json(output / "source_manifest.json", after)
    report = {
        "stage": "M0.5", "status": "pass_pending_user_visual_confirmation",
        "blender": bpy.app.version_string, "units": evidence, "params_file": str(Path(params_path).resolve()),
        "effective_params": effective, "clamp_messages": warnings, "template_inventory": inventory,
        "warnings": warnings + ["低模与高模并不精确重合，最大双向采样偏差 %.3f mm；正式造型以高模为准。" % alignment["bidirectional_sampled_max_mm"]],
        "source_stl_encoding": stl_encoding_report(), "source_high_health": source_high_report,
        "source_low_health": source_low_report, "normalized_high_health": high_report,
        "normalized_low_health": low_report, "repairs": {"high": high_actions, "low": low_actions},
        "normalization": normalization, "alignment": alignment, "laterality": features["laterality"],
        "size_estimate": features["size_estimate"], "collar_candidates": cut_reports,
        "reference_meshes": reference_reports, "original_sources_unchanged": True,
        "normalized_blend": str(ASSET_DIR / "last_normalized.blend"),
        "normalized_obj": str(ASSET_DIR / "last_normalized.obj"),
        "features": str(ASSET_DIR / "last_features.json"), "previews": previews,
        "manufacturing_status": "not_checked; last-only study; M1 shoe and M3 printability are not implemented",
        "elapsed_seconds": time.monotonic() - started,
        "license_reminder": "本鞋楦仅限非商业使用，不得分发。",
    }
    write_json(output / "health_report.json", report)
    print("M0.5_REPORT", json.dumps({"output": str(output), "length_mm": features["dimensions_mm"]["length"],
                                       "side": features["laterality"]["inferred"], "source_unchanged": True,
                                       "elapsed_seconds": report["elapsed_seconds"]}, ensure_ascii=False), flush=True)
    return report


if __name__ == "__main__":
    args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--params", type=Path, default=ROOT / "designs/current.json")
    parser.add_argument("--out", type=Path, default=ROOT / "out/m0_5")
    parser.add_argument("--no-render", action="store_true")
    options = parser.parse_args(args)
    try:
        inspect(options.out, options.params, render=not options.no_render)
    except Exception as exc:
        write_json(options.out / "health_report.json", {"stage": "M0.5", "status": "fail", "error": str(exc),
                                                     "manufacturing_status": "not_checked"})
        raise
