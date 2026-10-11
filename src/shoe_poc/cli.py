"""Headless commands with explicit milestone boundaries and diagnostic exit codes."""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import time
import traceback

import numpy as np

from shoe_poc.reporting import artifact_manifest, environment, sha256, utc_now, write_json

LOG = logging.getLogger("shoe_poc")


def inspect_last(path: Path, output: Path) -> dict:
    from shoe_poc.last import LastLoader, analyze_shape
    started = time.perf_counter()
    loaded = LastLoader().load(path)
    metadata = {"source": loaded.source, "source_geometry": analyze_shape(loaded.shape),
                "normalization": "NOT_PERFORMED: inspect-last measures the source coordinate frame",
                "elapsed_seconds": time.perf_counter() - started}
    write_json(output / "last_metadata.json", metadata)
    LOG.info("Last: %s", json.dumps(metadata["source_geometry"], ensure_ascii=False))
    return metadata


def build_base(spec_path: Path, output: Path) -> dict:
    from build123d import export_step, export_stl, import_step
    from shoe_poc.geometry.assembler import ShoeAssembler
    from shoe_poc.last import LastLoader, analyze_shape, normalize_last
    from shoe_poc.preview import render_preview
    from shoe_poc.schemas import ShoeSpec
    from shoe_poc.validation import validate_mesh

    output.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    spec = ShoeSpec.load(spec_path)
    timings = {}

    def stage(name, action):
        LOG.info("%s", name)
        before = time.perf_counter()
        result = action()
        timings[name] = time.perf_counter() - before
        return result

    loaded = stage("import_last", lambda: LastLoader().load(spec.last.file))
    source_geometry = analyze_shape(loaded.shape)
    last, transform = stage("normalize_last", lambda: normalize_last(loaded.shape, spec.last.coordinate_system))
    last_metadata = {"source": loaded.source, "source_geometry": source_geometry,
                     "normalization": transform, "normalized_geometry": analyze_shape(last),
                     "declared_side": spec.last.side, "declared_size": spec.shoe.size}
    write_json(output / "last_metadata.json", last_metadata)
    stage("export_normalized_last", lambda: export_step(last, output / "last_normalized.step"))
    write_json(output / "shoe_spec.json", spec.model_dump(mode="json"))
    result = stage("build_base", lambda: ShoeAssembler().build(last, spec))
    write_json(output / "geometry_report.json", result.metadata)
    stage("export_step", lambda: export_step(result.shape, output / "base_shoe.step"))
    stage("export_stl", lambda: export_stl(result.shape, output / "base_shoe.stl",
                                         tolerance=0.15, angular_tolerance=0.1))
    reloaded = stage("reimport_step", lambda: import_step(output / "base_shoe.step"))
    original = analyze_shape(result.shape)
    roundtrip = analyze_shape(reloaded)
    mesh = stage("reimport_validate_stl", lambda: validate_mesh(output / "base_shoe.stl"))
    step_bbox_delta = float(np.max(np.abs(np.asarray(original["bbox_mm"]["size"])
                                           - np.asarray(roundtrip["bbox_mm"]["size"]))))
    volume_delta = abs(roundtrip["volume_mm3"] - original["volume_mm3"]) / original["volume_mm3"]
    stl_bbox_delta = float(np.max(np.abs(np.asarray(original["bbox_mm"]["size"])
                                          - np.asarray(mesh["extents_mm"]))))
    roundtrip_checks = {"step_topology_valid": roundtrip["topology_valid"],
                        "step_single_solid": roundtrip["solid_count"] == 1,
                        "step_bbox_max_delta_mm": step_bbox_delta,
                        "step_volume_relative_delta": volume_delta,
                        "stl_bbox_max_delta_mm": stl_bbox_delta,
                        "bbox_tolerance_mm": 0.3, "volume_relative_tolerance": 1e-5,
                        "status": "PASS" if roundtrip["topology_valid"] and roundtrip["solid_count"] == 1
                        and step_bbox_delta <= 0.3 and stl_bbox_delta <= 0.3 and volume_delta <= 1e-5 else "FAIL"}
    write_json(output / "roundtrip_report.json", roundtrip_checks)
    write_json(output / "validation_report.json", mesh)
    stage("render_preview", lambda: render_preview(output / "base_shoe.stl", output / "preview.png"))
    passed = mesh["geometry_status"] == "PASS" and roundtrip_checks["status"] == "PASS"
    execution = {"task": "Milestone 1 build-base", "status": "PASS" if passed else "FAIL",
                 "scope": "CAD and exported geometry only; full POC/manufacturing/print NOT_VERIFIED",
                 "generated_at": utc_now(), "input_file": str(Path(spec.last.file)),
                 "input_sha256": loaded.source["sha256"], "spec_source": str(spec_path.resolve()),
                 "spec_sha256": sha256(spec_path), "schema_version": spec.schema_version,
                 "timings_seconds": timings, "elapsed_seconds": time.perf_counter() - started,
                 "triangles": mesh["triangles"], "environment": environment(),
                 "outputs": artifact_manifest(output)}
    write_json(output / "execution_report.json", execution)
    write_json(output / "artifact_manifest.json", artifact_manifest(output))
    if not passed:
        raise RuntimeError("Exported geometry failed integrity/roundtrip checks; inspect saved reports")
    LOG.info("Milestone 1 geometry checks PASS; manufacturing NOT_VERIFIED. Output: %s", output.resolve())
    return execution


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="AI footwear Phase 0 — Milestone 1 headless CAD")
    commands = root.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("inspect-last", help="Measure actual input STEP without normalization")
    inspect.add_argument("--input", required=True, type=Path)
    inspect.add_argument("--output", type=Path, default=Path("output/inspection"))
    for name in ("build-base", "run"):
        cmd = commands.add_parser(name, help="Build Milestone 1 only; later milestones require acceptance")
        cmd.add_argument("--spec", required=True, type=Path)
        cmd.add_argument("--output", type=Path, default=Path("output/m1"))
    validate = commands.add_parser("validate", help="Mesh integrity only; not print qualification")
    validate.add_argument("--model", required=True, type=Path)
    validate.add_argument("--output", type=Path, default=Path("output/mesh-validation"))
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    before = time.perf_counter()
    try:
        if args.command == "inspect-last":
            inspect_last(args.input, args.output)
        elif args.command in {"build-base", "run"}:
            build_base(args.spec, args.output)
        elif args.command == "validate":
            from shoe_poc.validation import validate_mesh
            report = validate_mesh(args.model)
            write_json(args.output / "validation_report.json", report)
            LOG.info("Geometry %s; manufacturing NOT_VERIFIED", report["geometry_status"])
            return 0 if report["geometry_status"] == "PASS" else 3
    except (ValueError, FileNotFoundError, json.JSONDecodeError) as error:
        LOG.error("Input error: %s", error)
        write_json(args.output / "failure_report.json", {"status": "FAIL", "error": str(error),
                   "type": type(error).__name__, "elapsed_seconds": time.perf_counter() - before})
        return 2
    except Exception as error:
        LOG.error("Geometry/runtime error: %s", error)
        write_json(args.output / "failure_report.json", {"status": "FAIL", "error": str(error),
                   "type": type(error).__name__, "traceback": traceback.format_exc(),
                   "elapsed_seconds": time.perf_counter() - before})
        return 3
    return 0
