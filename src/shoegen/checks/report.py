"""``report.json`` builder and the check runner (plan M0.9, ADR 0004)."""

from __future__ import annotations

import time
from pathlib import Path

from shoegen.checks.grid import run_grid_checks
from shoegen.checks.hard import run_hard_checks
from shoegen.checks.pore import run_pore_check
from shoegen.checks.prebuild import run_prebuild_checks
from shoegen.checks.records import (
    EXIT_HARD,
    EXIT_OK,
    EXIT_PREBUILD,
    FAIL,
    NOT_APPLICABLE,
    NOT_RUN,
    PASS,
    jsonable,
    record,
    source,
)
from shoegen.evaluate import DEFAULT_WORKERS, BlockPlan
from shoegen.graph import Graph
from shoegen.mesh import MeshResult
from shoegen.profile import ManufacturingProfile
from shoegen.reporting import environment, write_json
from shoegen.resources import RssWatchdog

REPORT_SCHEMA = "shoegen.report/0"
VERDICT = "NOT_VERIFIED"
VERDICT_NOTE = (
    "Manufacturing suitability is not verified: no physical print has been measured. "
    "Checks are computed on the model against the thresholds of the named profile."
)


def exit_code(prebuild: list[dict], hard: list[dict] | None) -> int:
    """0 ok, 3 pre-build parameter check failed, 5 hard check failed (3 wins)."""
    if any(r["result"] == FAIL for r in prebuild):
        return EXIT_PREBUILD
    if hard and any(r["result"] == FAIL for r in hard):
        return EXIT_HARD
    return EXIT_OK


def _group_summary(records: list[dict]) -> str:
    """``fail`` if any record failed, ``pass`` if at least one passed (a measured value),
    else ``not_applicable``: a group with nothing measured is never summarised as pass."""
    if any(r["result"] == FAIL for r in records):
        return FAIL
    if any(r["result"] == PASS for r in records):
        return PASS
    return NOT_APPLICABLE


def build_report(
    profile: ManufacturingProfile,
    prebuild: list[dict],
    hard: list[dict] | None = None,
    soft: list[dict] | None = None,
    *,
    mesh_summary: dict | None = None,
    eval_summary: dict | None = None,
    timings_s: dict | None = None,
    extra: dict | None = None,
    include_environment: bool = True,
) -> tuple[dict, int]:
    """Assemble ``report.json`` and return ``(report, exit_code)``.

    ``hard=None`` means the mesh stage did not run (for example after a pre-build
    failure); the report then says ``not_run`` for the hard group. Timings, peak RSS and
    the environment are not deterministic; the check values are for a fixed input.
    ``manufacturing_verdict`` is always ``NOT_VERIFIED``.
    """
    code = exit_code(prebuild, hard)
    soft = soft or []
    report = {
        "schema": REPORT_SCHEMA,
        "manufacturing_verdict": VERDICT,
        "manufacturing_verdict_note": VERDICT_NOTE,
        "exit_code": code,
        "profile": {
            "id": profile.id,
            "status": profile.status,
            "process": profile.process,
            "thresholds": {
                "min_strut_diameter_mm": profile.min_strut_diameter_mm,
                "min_wall_mm": profile.min_wall_mm,
                "min_pore_mm": profile.min_pore_mm,
                "min_fragment_volume_mm3": profile.min_fragment_volume_mm3,
                "build_volume_mm": list(profile.build_volume_mm),
            },
            "sources": dict(profile.sources),
            "note": (
                "thresholds are placeholders, not verified process limits"
                if profile.status == "placeholder"
                else None
            ),
        },
        "summary": {
            "prebuild": _group_summary(prebuild),
            "hard": "not_run" if hard is None else _group_summary(hard),
            "soft_findings": [r["id"] for r in soft if r.get("finding")],
            "soft_not_run": [r["id"] for r in soft if r["result"] == NOT_RUN],
        },
        "checks": {
            "prebuild": prebuild,
            "hard": hard if hard is not None else [],
            "soft": soft,
        },
        "mesh": mesh_summary,
        "eval": eval_summary,
        "timings_s": timings_s or {},
    }
    if include_environment:
        report["environment"] = environment()
    if extra:
        report["run"] = extra
    return jsonable(report), code


def write_report(path: Path | str, report: dict) -> None:
    """Write ``report.json`` with sorted keys (:func:`shoegen.reporting.write_json`)."""
    write_json(Path(path), report)


def _not_implemented_soft(profile: ManufacturingProfile) -> list[dict]:
    """ADR 0004 lists drainage and supports among the soft checks; M0 implements neither
    (plan M0.9 scope). They are listed as ``not_run`` so their absence is explicit in the
    report instead of silent (M0 review)."""
    return [
        record(
            "drainage",
            "soft",
            NOT_RUN,
            measured={},
            threshold={"requires_drain_holes": profile.requires_drain_holes},
            src=source(profile, None),
            detail="not implemented in M0; sealed voids are reported by enclosed_cavities",
            finding=False,
        ),
        record(
            "supports",
            "soft",
            NOT_RUN,
            measured={},
            threshold={"requires_supports": profile.requires_supports},
            src=source(profile, None),
            detail="not implemented in M0",
            finding=False,
        ),
    ]


def run_checks(
    graph: Graph,
    profile: ManufacturingProfile,
    voxel_mm: float,
    plan: BlockPlan | None = None,
    mesh_result: MeshResult | None = None,
    *,
    workers: int = DEFAULT_WORKERS,
    watchdog: RssWatchdog | None = None,
    timings_s: dict | None = None,
    extra: dict | None = None,
    include_environment: bool = True,
) -> tuple[dict, int]:
    """Run all three groups and build the report; returns ``(report, exit_code)``.

    Pre-build checks always run. Hard checks need ``mesh_result``; soft grid checks need
    ``plan`` (the plan the mesh was extracted from) and re-sample its crossing blocks.
    Pass neither after a pre-build failure: the report then lists no hard or soft results
    rather than invented ones. Soft checks run even when a hard check failed.
    """
    t0 = time.perf_counter()
    pre = run_prebuild_checks(graph, profile, voxel_mm)
    t1 = time.perf_counter()
    hard = run_hard_checks(mesh_result, profile) if mesh_result is not None else None
    t2 = time.perf_counter()
    soft: list[dict] = []
    if plan is not None:
        soft.extend(run_grid_checks(plan, profile, workers=workers, watchdog=watchdog))
        soft.append(run_pore_check(graph, profile))
        soft.extend(_not_implemented_soft(profile))
    t3 = time.perf_counter()
    timings = dict(timings_s or {})
    timings["checks_prebuild"] = t1 - t0
    timings["checks_hard"] = t2 - t1
    timings["checks_soft"] = t3 - t2
    return build_report(
        profile,
        pre,
        hard,
        soft,
        mesh_summary=mesh_result.summary if mesh_result is not None else None,
        eval_summary=(mesh_result.summary.get("eval") if mesh_result is not None else None),
        timings_s=timings,
        extra=extra,
        include_environment=include_environment,
    )
