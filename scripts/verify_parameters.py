"""Rebuild a full real-last variant and measure the sole-height geometry change.

Run with the project's installed Python environment. This compares complete CAD
and exported meshes, not just two JSON fields. Full identical-spec build
repeatability is explicitly separate from the normalization repeatability check.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import time

import numpy as np
import trimesh

from shoe_poc.cli import build_base
from shoe_poc.last import analyze_shape
from shoe_poc.reporting import sha256, utc_now, write_json
from shoe_poc.schemas import ShoeSpec


GENERATED_NAMES = (
    "shoe_spec.json", "last_metadata.json", "last_normalized.step",
    "base_shoe.step", "base_shoe.stl", "preview.png", "geometry_report.json",
    "validation_report.json", "roundtrip_report.json", "execution_report.json",
    "artifact_manifest.json", "failure_report.json", "parameter_change_report.json",
)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def measure(directory: Path) -> dict:
    from build123d import import_step

    cad = analyze_shape(import_step(directory / "base_shoe.step"))
    mesh = trimesh.load_mesh(directory / "base_shoe.stl", process=True)
    geometry = read_json(directory / "geometry_report.json")
    return {
        "directory": str(directory.resolve()),
        "sole_height_mm": ShoeSpec.load(directory / "shoe_spec.json").sole.height_mm,
        "cad": cad,
        "mesh": {
            "bbox_mm": mesh.bounds.tolist(), "extents_mm": mesh.extents.tolist(),
            "volume_mm3": float(mesh.volume), "watertight": bool(mesh.is_watertight),
            "winding_consistent": bool(mesh.is_winding_consistent),
            "connected_components": len(mesh.split(only_watertight=False)),
            "euler_number": int(mesh.euler_number), "triangles": len(mesh.faces),
        },
        "containment_mm3": {key: geometry["upper"][key] for key in (
            "last_outside_cavity_volume_mm3", "cavity_outside_outer_volume_mm3",
            "last_outside_outer_volume_mm3")},
        "exclusion_mm3": {key: geometry[key] for key in (
            "last_intersection_volume_mm3", "cavity_intersection_volume_mm3",
            "opening_intersection_volume_mm3")},
        "files": {name: {"sha256": sha256(directory / name),
                          "bytes": (directory / name).stat().st_size}
                  for name in ("base_shoe.step", "base_shoe.stl", "last_normalized.step", "shoe_spec.json")},
        "execution_elapsed_seconds": read_json(directory / "execution_report.json")["elapsed_seconds"],
        "execution_status": read_json(directory / "execution_report.json")["status"],
        "input_sha256": read_json(directory / "last_metadata.json")["source"]["sha256"],
    }


def verify(spec_path: Path, baseline: Path, output: Path, height_mm: float) -> dict:
    started = time.perf_counter()
    baseline, output = baseline.resolve(), output.resolve()
    if baseline == output:
        raise ValueError("Variant --output must differ from --baseline")
    spec = ShoeSpec.load(spec_path)
    baseline_spec = ShoeSpec.load(baseline / "shoe_spec.json")
    if spec.model_dump() != baseline_spec.model_dump():
        raise ValueError("--spec does not match the baseline's resolved ShoeSpec")
    if height_mm <= spec.sole.height_mm:
        raise ValueError("Variant height must exceed the baseline height")
    data = spec.model_dump(mode="json")
    data["sole"]["height_mm"] = height_mm
    variant_spec = ShoeSpec.model_validate(data)
    original = measure(baseline)
    output.mkdir(parents=True, exist_ok=True)
    # Only remove known generated files. Baseline artifacts and unrelated files
    # are never touched; old pre-fix variant models/reports cannot survive.
    cleared = []
    for name in GENERATED_NAMES:
        path = output / name
        if path.is_file():
            path.unlink()
            cleared.append(name)
    variant_path = output / "shoe_spec.json"
    write_json(variant_path, variant_spec.model_dump(mode="json"))
    build_base(variant_path, output)
    variant = measure(output)
    delta = height_mm - original["sole_height_mm"]
    bounds0 = np.asarray([original["cad"]["bbox_mm"]["min"], original["cad"]["bbox_mm"]["max"]])
    bounds1 = np.asarray([variant["cad"]["bbox_mm"]["min"], variant["cad"]["bbox_mm"]["max"]])
    expected = np.zeros((2, 3))
    expected[0, 2] = -delta
    bound_change = bounds1 - bounds0
    extent_change = np.asarray(variant["cad"]["bbox_mm"]["size"]) - original["cad"]["bbox_mm"]["size"]
    checks = {
        "same_actual_last_source": original["input_sha256"] == variant["input_sha256"],
        "both_execution_checks_pass": original["execution_status"] == variant["execution_status"] == "PASS",
        "both_valid_single_cad_solid": all(v["cad"]["topology_valid"] and v["cad"]["solid_count"] == 1 for v in (original, variant)),
        "both_watertight_connected_meshes_without_unintended_handles": all(
            v["mesh"]["watertight"] and v["mesh"]["winding_consistent"]
            and v["mesh"]["connected_components"] == 1 and v["mesh"]["euler_number"] == 2
            for v in (original, variant)),
        "both_envelopes_contain_last_and_cavity": all(max(v["containment_mm3"].values()) <= 0.01 for v in (original, variant)),
        "both_preserve_cavity_and_opening": all(max(v["exclusion_mm3"].values()) <= 0.01 for v in (original, variant)),
        "only_bottom_moves_by_requested_height": bool(np.allclose(bound_change, expected, atol=1e-6, rtol=0)),
        "overall_z_extent_increases_by_requested_height": bool(np.allclose(extent_change, [0, 0, delta], atol=1e-6, rtol=0)),
        "variant_full_cad_volume_increases": variant["cad"]["volume_mm3"] > original["cad"]["volume_mm3"],
        "variant_full_mesh_volume_increases": variant["mesh"]["volume_mm3"] > original["mesh"]["volume_mm3"],
        "variant_model_files_differ": all(original["files"][name]["sha256"] != variant["files"][name]["sha256"] for name in ("base_shoe.step", "base_shoe.stl")),
    }
    # Two actual runs export the same normalized last independently. Numerical
    # comparison avoids mistaking STEP header timestamps for geometry changes.
    from build123d import import_step
    normalized = [import_step(p / "last_normalized.step") for p in (baseline, output)]
    samples = [shape.tessellate(0.4, 0.15) for shape in normalized]
    vertices = [np.asarray([tuple(v) for v in sample[0]]) for sample in samples]
    faces = [np.asarray(sample[1]) for sample in samples]
    same_vertices = vertices[0].shape == vertices[1].shape and bool(np.allclose(vertices[0], vertices[1], atol=1e-6, rtol=0))
    same_faces = bool(np.array_equal(faces[0], faces[1]))
    checks["normalized_last_repeats_numerically"] = same_vertices and same_faces
    return {
        "status": "PASS" if all(checks.values()) else "FAIL", "generated_at": utc_now(),
        "scope": "Full real-last Milestone 1 CAD/STL sole-height parameter change; manufacturing and physical fit NOT_VERIFIED",
        "changed_parameter": "sole.height_mm", "baseline_value_mm": original["sole_height_mm"],
        "variant_value_mm": height_mm, "parameter_delta_mm": delta,
        "cad_bbox_min_max_delta_mm": bound_change.tolist(), "cad_extents_delta_mm": extent_change.tolist(),
        "cad_volume_delta_mm3": variant["cad"]["volume_mm3"] - original["cad"]["volume_mm3"],
        "mesh_volume_delta_mm3": variant["mesh"]["volume_mm3"] - original["mesh"]["volume_mm3"],
        "tolerances": {"cad_coordinate_mm": 1e-6, "containment_and_exclusion_mm3": 0.01},
        "checks": checks, "baseline": original, "variant": variant,
        "normalized_last_repeatability": {"status": "PASS" if same_vertices and same_faces else "FAIL",
            "method": "Independent baseline and variant normalized STEP reimports tessellated with identical tolerances; compare coordinates and connectivity",
            "vertex_count": len(vertices[0]), "triangle_count": len(faces[0]),
            "vertices_equal_within_1e_6_mm": same_vertices, "triangle_connectivity_equal": same_faces},
        "identical_spec_full_shoe_rebuild_repeatability": {"status": "NOT_TESTED",
            "reason": "This run changes sole height; it does not repeat the entire full-shoe build at an identical ShoeSpec."},
        "stale_variant_files_removed_before_build": cleared,
        "elapsed_seconds": time.perf_counter() - started,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, default=Path("input/specs/shoe_spec.json"))
    parser.add_argument("--baseline", type=Path, default=Path("output/m1"))
    parser.add_argument("--output", type=Path, default=Path("output/m1/variant"))
    parser.add_argument("--height-mm", type=float, default=25.0)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    report_path = args.output.resolve().parent / "parameter_change_report.json"
    try:
        report = verify(args.spec, args.baseline, args.output, args.height_mm)
    except Exception as error:
        write_json(report_path, {"status": "FAIL", "generated_at": utc_now(), "error": str(error),
                                "error_type": type(error).__name__, "scope": "Full-shoe parameter comparison"})
        logging.exception("Parameter comparison failed")
        return 3
    write_json(report_path, report)
    logging.info("Parameter comparison %s: %s", report["status"], report_path)
    return 0 if report["status"] == "PASS" else 3


if __name__ == "__main__":
    raise SystemExit(main())
