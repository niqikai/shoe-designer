"""Headless commands with explicit milestone boundaries and diagnostic exit codes."""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import time
import traceback
from uuid import uuid4

import numpy as np
from pydantic import ValidationError

from shoe_poc.reporting import artifact_manifest, environment, sha256, utc_now, write_json

LOG = logging.getLogger("shoe_poc")

# Clear only this pipeline's files before a new generation. Preserve logs,
# diagnostics, variants and unrelated user files.
GENERATED_NAMES = (
    "shoe_spec.json", "last_metadata.json", "last_normalized.step", "last_normalized.stl",
    "base_shoe.step", "base_shoe.stl", "upper.stl", "cavity.stl", "preview.png",
    "upper_cavity_overlay.png", "comparison.png", "geometry_report.json",
    "validation_report.json", "roundtrip_report.json", "surface_report.json",
    "execution_report.json", "failure_report.json", "mesh_export_report.json",
    "artifact_manifest.json", "test_report.json", "parameter_change_report.json",
    "acceptance_report.json",
)


def _prepare_build_output(output: Path, spec_path: Path, last_path: Path,
                          compare_with: Path | None) -> list[str]:
    generated = {(output / name).resolve() for name in GENERATED_NAMES}
    if last_path.resolve() in generated:
        raise InputError("Source last must differ from generated output files")
    if compare_with is not None and compare_with.resolve() in generated:
        raise InputError("Comparison input must differ from generated output files; preserve the previous STL")
    if spec_path.resolve() in generated and spec_path.resolve() != (output / "shoe_spec.json").resolve():
        raise InputError("Input spec conflicts with a generated output file")
    output.mkdir(parents=True, exist_ok=True)
    cleared = []
    for name in GENERATED_NAMES:
        path = output / name
        if path.is_file() and path.resolve() != spec_path.resolve():
            path.unlink()
            cleared.append(name)
    return cleared


def _record_build_failure(output: Path, run_id: str, error: Exception, elapsed: float) -> None:
    path = output / "execution_report.json"
    try:
        previous = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        previous = {}
    if previous.get("run_id") != run_id:
        previous = {}
        # Invalid input can fail before output preparation. Old reports cannot
        # be reused as evidence for that new failed attempt.
        for name in GENERATED_NAMES:
            if name.endswith("_report.json") or name == "artifact_manifest.json":
                (output / name).unlink(missing_ok=True)
    write_json(path, {**previous, "task": "Milestone 1 build-base", "run_id": run_id,
                     "status": "FAIL", "milestone_status": "PARTIAL",
                     "generated_at": utc_now(), "error": str(error),
                     "elapsed_seconds": previous.get("elapsed_seconds", elapsed)})


class InputError(ValueError):
    """Input import/coordinate failure, as distinct from CAD runtime failure."""


def load_last(path):
    from shoe_poc.last import LastLoader
    try:
        return LastLoader().load(path)
    except ValueError as error:
        raise InputError(str(error)) from error


def inspect_last(path: Path, output: Path) -> dict:
    from shoe_poc.last import LastLoader, analyze_shape
    started = time.perf_counter()
    loaded = load_last(path)
    metadata = {"source": loaded.source, "source_geometry": analyze_shape(loaded.shape),
                "normalization": "NOT_PERFORMED: inspect-last measures the source coordinate frame",
                "elapsed_seconds": time.perf_counter() - started}
    write_json(output / "last_metadata.json", metadata)
    LOG.info("Last: %s", json.dumps(metadata["source_geometry"], ensure_ascii=False))
    return metadata


def build_base(spec_path: Path, output: Path, compare_with: Path | None = None,
               *, run_id: str | None = None) -> dict:
    if compare_with is not None:
        if not compare_with.is_file():
            raise FileNotFoundError(f"Comparison STL does not exist: {compare_with}")
        if compare_with.resolve() == (output / "base_shoe.stl").resolve():
            raise InputError("Comparison STL must differ from the generated base_shoe.stl; preserve the previous input")
    from build123d import export_step, import_step
    from shoe_poc.exporting import export_mesh
    from shoe_poc.geometry.assembler import ShoeAssembler
    from shoe_poc.last import LastLoader, analyze_shape, normalize_last
    from shoe_poc.preview import render_comparison, render_preview, render_upper_gate
    from shoe_poc.schemas import ShoeSpec
    from shoe_poc.validation import validate_mesh

    started = time.perf_counter()
    spec = ShoeSpec.load(spec_path)
    cleared = _prepare_build_output(output, spec_path, Path(spec.last.file), compare_with)
    run_id = run_id or str(uuid4())
    write_json(output / "execution_report.json", {"task": "Milestone 1 build-base",
               "status": "RUNNING", "run_id": run_id, "generated_at": utc_now(),
               "stale_generated_files_removed": cleared})
    timings = {}

    def stage(name, action):
        LOG.info("%s", name)
        before = time.perf_counter()
        result = action()
        timings[name] = time.perf_counter() - before
        return result

    loaded = stage("import_last", lambda: load_last(spec.last.file))
    source_geometry = analyze_shape(loaded.shape)
    last, transform = stage("normalize_last", lambda: normalize_last(loaded.shape, spec.last.coordinate_system))
    last_metadata = {"source": loaded.source, "source_geometry": source_geometry,
                     "normalization": transform, "normalized_geometry": analyze_shape(last),
                     "declared_side": spec.last.side, "declared_size": spec.shoe.size}
    write_json(output / "last_metadata.json", last_metadata)
    stage("export_normalized_last", lambda: export_step(last, output / "last_normalized.step"))
    normalized_reloaded = stage("reimport_normalized_last", lambda: import_step(output / "last_normalized.step"))
    normalized_roundtrip = analyze_shape(normalized_reloaded)
    normalized_original = last_metadata["normalized_geometry"]
    normalized_bounds_delta = float(np.max(np.abs(
        np.asarray([normalized_roundtrip["bbox_mm"]["min"], normalized_roundtrip["bbox_mm"]["max"]])
        - np.asarray([normalized_original["bbox_mm"]["min"], normalized_original["bbox_mm"]["max"]]))))
    if not normalized_roundtrip["topology_valid"] or normalized_roundtrip["solid_count"] != 1 or normalized_bounds_delta > 1e-4:
        raise RuntimeError("Normalized last STEP roundtrip changed coordinates/topology")
    write_json(output / "shoe_spec.json", spec.model_dump(mode="json"))
    result = stage("build_base", lambda: ShoeAssembler().build(last, spec))
    write_json(output / "geometry_report.json", result.metadata)
    exports = {}
    for name, shape in [("upper", result.upper), ("cavity", result.cavity),
                         ("last_normalized", last)]:
        exports[name] = stage(f"export_{name}_preview_stl", lambda name=name, shape=shape:
                              export_mesh(shape, output / f"{name}.stl"))
    stage("render_upper_gate", lambda: render_upper_gate(output / "upper.stl", output / "cavity.stl",
                                                         output / "last_normalized.stl",
                                                         output / "upper_cavity_overlay.png"))
    stage("export_step", lambda: export_step(result.shape, output / "base_shoe.step"))
    exports["base_shoe"] = stage("export_stl", lambda: export_mesh(result.shape, output / "base_shoe.stl"))
    write_json(output / "mesh_export_report.json", exports)
    reloaded = stage("reimport_step", lambda: import_step(output / "base_shoe.step"))
    original = analyze_shape(result.shape)
    roundtrip = analyze_shape(reloaded)
    mesh = stage("reimport_validate_stl", lambda: validate_mesh(output / "base_shoe.stl"))
    cad_bounds = np.asarray([original["bbox_mm"]["min"], original["bbox_mm"]["max"]])
    step_bbox_delta = float(np.max(np.abs(cad_bounds - np.asarray(
        [roundtrip["bbox_mm"]["min"], roundtrip["bbox_mm"]["max"]]))))
    volume_delta = abs(roundtrip["volume_mm3"] - original["volume_mm3"]) / original["volume_mm3"]
    stl_bbox_delta = float(np.max(np.abs(cad_bounds - np.asarray(mesh["bounds_mm"]))))
    roundtrip_checks = {"step_topology_valid": roundtrip["topology_valid"],
                        "step_single_solid": roundtrip["solid_count"] == 1,
                        "step_bbox_max_delta_mm": step_bbox_delta,
                        "step_volume_relative_delta": volume_delta,
                        "normalized_last_bounds_max_delta_mm": normalized_bounds_delta,
                        "stl_bbox_max_delta_mm": stl_bbox_delta,
                        "bbox_tolerance_mm": 0.3, "volume_relative_tolerance": 1e-5,
                        "status": "PASS" if roundtrip["topology_valid"] and roundtrip["solid_count"] == 1
                        and step_bbox_delta <= 0.3 and stl_bbox_delta <= 0.3 and volume_delta <= 1e-5 else "FAIL"}
    write_json(output / "roundtrip_report.json", roundtrip_checks)
    write_json(output / "validation_report.json", mesh)
    from shoe_poc.validation.surface import validate_surface_samples
    surface = stage("measure_surface_samples", lambda: validate_surface_samples(last, result, spec))
    write_json(output / "surface_report.json", surface)
    stage("render_preview", lambda: render_preview(output / "base_shoe.stl", output / "preview.png"))
    if compare_with is not None:
        stage("render_comparison", lambda: render_comparison(compare_with, output / "base_shoe.stl",
                                                              output / "last_normalized.stl",
                                                              output / "comparison.png"))
    passed = (mesh["geometry_status"] == "PASS" and roundtrip_checks["status"] == "PASS"
              and surface["geometry_status"] != "FAIL")
    execution = {"task": "Milestone 1 build-base", "status": "PASS" if passed else "FAIL",
                 "run_id": run_id, "stale_generated_files_removed": cleared,
                 "milestone_status": "PARTIAL",
                 "appearance_acceptance": "PENDING",
                 "scope": "CAD and exported geometry only; full POC/manufacturing/print NOT_VERIFIED",
                 "generated_at": utc_now(), "input_file": str(Path(spec.last.file)),
                 "input_sha256": loaded.source["sha256"], "spec_source": str(spec_path.resolve()),
                 "spec_sha256": sha256(spec_path), "schema_version": spec.schema_version,
                 "timings_seconds": timings, "elapsed_seconds": time.perf_counter() - started,
                 "triangles": mesh["triangles"], "environment": environment(),
                 "outputs": {name: record for name, record in artifact_manifest(output).items()
                             if name in {"base_shoe.step", "base_shoe.stl", "last_normalized.step",
                                         "last_metadata.json", "shoe_spec.json", "geometry_report.json",
                                         "roundtrip_report.json", "validation_report.json", "surface_report.json",
                                         "preview.png", "comparison.png", "last_normalized.stl",
                                         "upper.stl", "cavity.stl", "upper_cavity_overlay.png",
                                         "mesh_export_report.json"}}}
    write_json(output / "execution_report.json", execution)
    if passed:
        (output / "failure_report.json").unlink(missing_ok=True)
    write_json(output / "artifact_manifest.json", artifact_manifest(output))
    if not passed:
        raise RuntimeError("Exported geometry failed integrity/roundtrip checks; inspect saved reports")
    LOG.info("Geometry pipeline PASS; M1 PARTIAL, appearance PENDING, manufacturing NOT_VERIFIED. Output: %s", output.resolve())
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
        cmd.add_argument("--compare-with", type=Path, help="Previous STL for identical-camera comparison; input is preserved")
    validate = commands.add_parser("validate", help="Mesh integrity only; not print qualification")
    validate.add_argument("--model", required=True, type=Path)
    validate.add_argument("--output", type=Path, default=Path("output/mesh-validation"))
    for subcommand in commands.choices.values():
        subcommand.add_argument("--max-rss-mb", type=float, default=8192,
                                help="Sampled process RSS budget in MiB (default 8192); exit 3 when observed exceeded")
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if not np.isfinite(args.max_rss_mb) or args.max_rss_mb <= 0:
        parser().error("--max-rss-mb must be a positive finite number")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    # Native OCCT allocations are not visible to Python tracemalloc.
    from shoe_poc.resource_limits import MemoryBudget
    budget = MemoryBudget(args.max_rss_mb, args.output, args.command)
    budget.__enter__()
    before = time.perf_counter()
    run_id = str(uuid4())
    try:
        if args.command == "inspect-last":
            inspect_last(args.input, args.output)
        elif args.command in {"build-base", "run"}:
            build_base(args.spec, args.output, args.compare_with, run_id=run_id)
        elif args.command == "validate":
            from shoe_poc.validation import validate_mesh
            report = validate_mesh(args.model)
            write_json(args.output / "validation_report.json", report)
            LOG.info("Geometry %s; manufacturing NOT_VERIFIED", report["geometry_status"])
            return 0 if report["geometry_status"] == "PASS" else 3
    except (InputError, ValidationError, FileNotFoundError, json.JSONDecodeError) as error:
        LOG.error("Input error: %s", error)
        if args.command in {"build-base", "run"}:
            _record_build_failure(args.output, run_id, error, time.perf_counter() - before)
        write_json(args.output / "failure_report.json", {"status": "FAIL", "error": str(error),
                   "type": type(error).__name__, "elapsed_seconds": time.perf_counter() - before})
        return 2
    except Exception as error:
        LOG.error("Geometry/runtime error: %s", error)
        if args.command in {"build-base", "run"}:
            _record_build_failure(args.output, run_id, error, time.perf_counter() - before)
        write_json(args.output / "failure_report.json", {"status": "FAIL", "error": str(error),
                   "type": type(error).__name__, "traceback": traceback.format_exc(),
                   "elapsed_seconds": time.perf_counter() - before})
        return 3
    finally:
        budget.__exit__()
    return 0
