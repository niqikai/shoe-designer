"""``shoegen build``: last (STEP) + assembly parameters -> ``shoe.stl`` and reports (plan M0.10).

Usage::

    shoegen build --graph examples/m0.json --last input/lasts/<file>.step \\
        --profile pbf-tpu-generic@0 -o output/m0 [--voxel-mm 0.5] [--max-rss-gb 6] \\
        [--workers 4] [--normalization normalization.json] [--cache-dir output/cache/fields]

``--graph`` is the assembly-parameter file (``examples/m0.json``, see
:mod:`shoegen.assembly`); lattice radius / cell and the collar height are overridden by
editing that file.

Pipeline: load the STEP -> explicit rigid normalization -> tessellate -> sampled fields
(cached under ``--cache-dir``) -> shoe graph -> pre-build checks -> block-sparse evaluation
and marching cubes -> hard / soft checks -> outputs. The whole run is under
:class:`shoegen.resources.RssWatchdog`.

Normalization: ``--normalization`` is a JSON file ``{"rotation": [[..],[..],[..]],
"translation_mm": [x, y, z], "orientation_confirmed": false}`` with
``normalized = rotation @ source + translation``. Without the flag the transform is the
identity and is recorded in ``report.json`` as ``PROVISIONAL_REQUIRES_USER_CONFIRMATION``;
the last must already be in the X heel-to-toe, Y width, Z up convention.

Outputs in ``-o``: ``shoe.stl``, ``graph.json`` (both bitwise deterministic on one machine
and one ``uv.lock``), ``report.json``, ``manifest.json`` (file sha256, dependency versions,
run_id, timings, peak RSS; not deterministic). Stale outputs of an earlier run in ``-o``
are removed first.

Exit codes::

    0  success
    1  internal error (report.json says stage and message)
    2  usage error (argparse)
    3  input, graph or pre-build check failure (no mesh produced)
    4  last import failure (missing / unreadable STEP, bad solid, bad mesh)
    5  hard check failure
    6  resource limit (RssWatchdog writes its failure report to ``-o/report.json``)

Hard check failure (exit 5): ``shoe.stl`` is NOT written. PR #2 principle: a failed
candidate must not masquerade as success, and a file named ``shoe.stl`` next to a failing
report would. ``report.json`` and ``manifest.json`` say ``status: "hard_check_failed"``
and ``shoe_stl: null``; ``graph.json`` is still written (it describes the input, not a
result).

Sheet margin: the height-surface sheet is sampled over footprint + ``sheet_margin_mm``
(default 30 mm costs ~30 s). The CLI uses ``max(8, outline_margin_mm + 5)`` mm, which
covers the envelope outline (``outline_margin_mm`` + 1 mm check margin) with room to spare.

Known limitation (M0.6a spike): the watchdog is an in-process thread. While the parent
process holds the GIL in a long native call or numba kernel, the thread is starved and
cannot sample RSS, so the limit can be overshot (it is reliable for the worker pool, whose
memory is sampled while the parent waits). An external supervisor is not built in M0.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path

from shoegen.reporting import artifact_manifest, environment, utc_now, write_json

EXIT_OK = 0
EXIT_INTERNAL = 1
EXIT_USAGE = 2
EXIT_INPUT = 3
EXIT_LAST = 4
EXIT_HARD = 5
EXIT_RESOURCE = 6

OUTPUT_NAMES = ("shoe.stl", "graph.json", "report.json", "manifest.json")
TESSELLATION_TOL_MM = 0.01
TESSELLATION_ANGULAR_RAD = 0.05
DEFAULT_CACHE_DIR = "output/cache/fields"
MIN_SHEET_MARGIN_MM = 8.0


class CliError(Exception):
    """A failure with a defined exit code and the stage it happened in."""

    def __init__(self, code: int, stage: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.stage = stage


# ---------------------------------------------------------------------- arguments


def _positive_float(text: str) -> float:
    try:
        v = float(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not a number: {text!r}") from exc
    if not (v > 0 and v < float("inf")):
        raise argparse.ArgumentTypeError(f"must be finite and > 0, got {text!r}")
    return v


def _positive_int(text: str) -> int:
    try:
        v = int(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not an integer: {text!r}") from exc
    if v < 1:
        raise argparse.ArgumentTypeError(f"must be >= 1, got {text!r}")
    return v


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="shoegen", description="Generative lattice-shoe CLI.")
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser(
        "build",
        help="build shoe.stl from a last",
        description=__doc__.split("Usage::")[0].strip(),
    )
    b.add_argument("--graph", required=True, type=Path, help="assembly parameter JSON")
    b.add_argument("--last", required=True, type=Path, help="shoe-last STEP file")
    b.add_argument(
        "--profile", required=True, help="manufacturing profile id, e.g. pbf-tpu-generic@0"
    )
    b.add_argument("-o", "--output", required=True, type=Path, help="output directory")
    b.add_argument("--voxel-mm", type=_positive_float, default=0.5, help="voxel size (default 0.5)")
    b.add_argument("--max-rss-gb", type=_positive_float, default=6.0, help="RSS limit (default 6)")
    b.add_argument("--workers", type=_positive_int, default=4, help="process pool size (default 4)")
    b.add_argument(
        "--normalization",
        type=Path,
        default=None,
        help="JSON with rotation (3x3), translation_mm (3), orientation_confirmed; "
        "default: identity, recorded as provisional",
    )
    b.add_argument(
        "--cache-dir", type=Path, default=Path(DEFAULT_CACHE_DIR), help="sampled-field cache"
    )
    return parser


# ---------------------------------------------------------------------- stages


def _load_normalization(path: Path | None):
    from shoegen.last import CoordinateSystem

    if path is None:
        eye = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
        return CoordinateSystem(rotation=eye, translation_mm=(0.0, 0.0, 0.0))
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        return CoordinateSystem(
            rotation=tuple(tuple(float(x) for x in row) for row in obj["rotation"]),
            translation_mm=tuple(float(x) for x in obj["translation_mm"]),
            orientation_confirmed=bool(obj.get("orientation_confirmed", False)),
        )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CliError(EXIT_INPUT, "normalization", f"bad --normalization {path}: {exc}") from exc


def _clean_outputs(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for name in OUTPUT_NAMES:
        (out / name).unlink(missing_ok=True)


def _failure_report(code: int, status: str, stage: str, message: str, extra: dict) -> dict:
    return {
        "schema": "shoegen.report/0",
        "manufacturing_verdict": "NOT_VERIFIED",
        "status": status,
        "exit_code": code,
        "failed_stage": stage,
        "error": message,
        "run": extra,
        "environment": environment(),
    }


def _run_build(args, out: Path, wd, run: dict, timings: dict) -> tuple[int, dict, dict]:
    """The pipeline. Returns (exit code, report, manifest extras); raises CliError."""
    from shoegen.assembly import (
        AssemblyParamError,
        build_shoe_graph,
        load_assembly_params,
        mesh_graph,
        shoe_layout,
        shoe_region,
    )
    from shoegen.checks import run_checks, write_report
    from shoegen.graph import GraphValidationError, dump_graph
    from shoegen.last import (
        FieldParams,
        LastLoader,
        build_last_fields,
        last_to_mesh,
        normalize_last,
    )
    from shoegen.mesh import write_stl
    from shoegen.profile import load_profile

    def stage(name: str, t0: float) -> None:
        timings[name] = time.perf_counter() - t0

    # -- inputs (exit 3)
    t = time.perf_counter()
    try:
        profile = load_profile(args.profile)
    except Exception as exc:
        raise CliError(
            EXIT_INPUT, "profile", f"cannot load profile {args.profile!r}: {exc}"
        ) from exc
    try:
        params = load_assembly_params(args.graph)
    except AssemblyParamError as exc:
        raise CliError(EXIT_INPUT, "params", str(exc)) from exc
    coord = _load_normalization(args.normalization)
    run["assembly_params"] = params.model_dump(mode="json")
    stage("inputs", t)

    # -- last (exit 4)
    t = time.perf_counter()
    try:
        loaded = LastLoader().load(args.last)
        shape, norm_info = normalize_last(loaded.shape, coord)
        mesh = last_to_mesh(
            shape,
            TESSELLATION_TOL_MM,
            angular_tolerance=TESSELLATION_ANGULAR_RAD,
            source_sha256=loaded.source["sha256"],
        )
    except Exception as exc:  # every import failure is exit 4 (LastMeshError included)
        raise CliError(EXIT_LAST, "last_import", f"{type(exc).__name__}: {exc}") from exc
    run["last"] = {
        "file": args.last.name,
        "sha256": loaded.source["sha256"],
        "size_bytes": loaded.source["size_bytes"],
        "header_repaired_in_temporary_copy": loaded.source["header_repaired_in_temporary_copy"],
        "mesh_sha256": mesh.mesh_sha256,
        "n_vertices": len(mesh.vertices),
        "n_faces": len(mesh.faces),
    }
    run["normalization"] = norm_info
    stage("last_import", t)

    # -- fields, graph, pre-build checks (exit 3)
    t = time.perf_counter()
    sheet = max(MIN_SHEET_MARGIN_MM, params.outline_margin_mm + 5.0)
    fparams = FieldParams(sheet_margin_mm=sheet)
    fields = build_last_fields(mesh, args.cache_dir, fparams)
    run["fields"] = {"cache_hit": bool(fields.cache_hit), "sheet_margin_mm": sheet}
    stage("fields", t)

    t = time.perf_counter()
    try:
        graph = build_shoe_graph(fields, params)
        layout = shoe_layout(fields, params)
        region_min, region_max = shoe_region(fields, params, args.voxel_mm)
    except GraphValidationError as exc:
        raise CliError(EXIT_INPUT, "graph", f"{type(exc).__name__}: {exc}") from exc
    dump_graph(graph, out / "graph.json")
    run["layout"] = layout.to_dict()
    run["graph_hash"] = graph.graph_hash()
    stage("graph", t)

    pre_report, pre_code = run_checks(
        graph, profile, args.voxel_mm, workers=args.workers, timings_s=dict(timings), extra=run
    )
    if pre_code != EXIT_OK:  # pre-build failure: no mesh
        return pre_code, pre_report, {"shoe_stl": None, "status": "prebuild_failed"}

    # -- mesh
    t = time.perf_counter()
    plan, res = mesh_graph(
        graph,
        fields.store,
        region_min,
        region_max,
        args.voxel_mm,
        workers=args.workers,
        watchdog=wd,
        min_fragment_volume_mm3=profile.min_fragment_volume_mm3,
    )
    stage("mesh", t)

    # -- checks
    t = time.perf_counter()
    report, code = run_checks(
        graph,
        profile,
        args.voxel_mm,
        plan,
        res,
        workers=args.workers,
        watchdog=wd,
        timings_s=dict(timings),
        extra=run,
    )
    stage("checks", t)
    manifest_extra: dict = {"status": "ok" if code == EXIT_OK else "hard_check_failed"}
    if code == EXIT_OK:
        write_stl(res.mesh, out / "shoe.stl")
        manifest_extra["shoe_stl"] = "shoe.stl"
    else:
        manifest_extra["shoe_stl"] = None
        manifest_extra["note"] = "hard check failed: shoe.stl deliberately not written"
    manifest_extra["triangles"] = int(res.mesh.n_faces)
    write_report(out / "report.json", report)  # keep before manifest hashing
    return code, report, manifest_extra


def run_build(args) -> int:
    from shoegen.resources import RssWatchdog

    out: Path = args.output
    run_id = f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{uuid.uuid4().hex[:8]}"
    run = {
        "run_id": run_id,
        "started_utc": utc_now(),
        "voxel_mm": args.voxel_mm,
        "profile": args.profile,
        "workers": args.workers,
        "max_rss_gb": args.max_rss_gb,
    }
    timings: dict = {}
    t_start = time.perf_counter()
    _clean_outputs(out)
    wd = RssWatchdog(int(args.max_rss_gb * 1024**3), report_path=out / "report.json")
    code = EXIT_INTERNAL
    report: dict
    extra: dict = {}
    with wd:
        try:
            code, report, extra = _run_build(args, out, wd, run, timings)
        except CliError as exc:
            code = exc.code
            status = {EXIT_INPUT: "input_failed", EXIT_LAST: "last_import_failed"}.get(
                code, "failed"
            )
            report = _failure_report(code, status, exc.stage, str(exc), run)
            extra = {"status": status, "shoe_stl": None}
        except Exception as exc:  # noqa: BLE001 - exit 1 by contract
            code = EXIT_INTERNAL
            report = _failure_report(
                code, "internal_error", "unknown", f"{type(exc).__name__}: {exc}", run
            )
            extra = {"status": "internal_error", "shoe_stl": None}
    timings["total"] = time.perf_counter() - t_start
    report["timings_s"] = {**report.get("timings_s", {}), **timings}
    report["peak_rss_bytes"] = wd.peak_bytes
    write_json(out / "report.json", report)
    manifest = {
        "schema": "shoegen.manifest/0",
        "run_id": run_id,
        "exit_code": code,
        "files": artifact_manifest(out),
        "environment": environment(),
        "timings_s": report["timings_s"],
        "peak_rss_bytes": wd.peak_bytes,
        "deterministic_files": ["graph.json", "shoe.stl"],
        **extra,
    }
    write_json(out / "manifest.json", manifest)
    print(
        f"shoegen build: exit {code} ({extra.get('status')}) in {timings['total']:.1f} s, "
        f"peak RSS {wd.peak_bytes / 1024**3:.2f} GB -> {out}",
        file=sys.stderr,
    )
    return code


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)  # argparse exits with 2 on usage errors
    if args.command == "build":
        return run_build(args)
    return EXIT_USAGE  # pragma: no cover


def entry() -> None:
    sys.exit(main())


if __name__ == "__main__":
    entry()
