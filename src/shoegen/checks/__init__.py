"""Pre-build, hard and soft checks and ``report.json`` (ADR 0004, plan M0.9).

Typical use::

    from shoegen.checks import run_checks, write_report

    report, code = run_checks(graph, profile, voxel_mm, plan, mesh_result, workers=4)
    write_report("output/report.json", report)
    sys.exit(code)                       # 0, 3 (prebuild) or 5 (hard)

* prebuild (exit 3): strut diameter and voxel rule, aggregated from the lattice check;
* hard (exit 5): watertight, manifold, one component, build volume;
* soft (report only): thin walls, enclosed cavities (block-wise voxel grids, see
  :mod:`.grid`), minimum pore size (analytic, :mod:`.pore`).

The manufacturing verdict in the report is always ``NOT_VERIFIED``.
"""

from shoegen.checks.grid import (
    BlockMasks,
    cavity_check,
    collect_block_masks,
    run_grid_checks,
    thin_wall_check,
)
from shoegen.checks.hard import run_hard_checks
from shoegen.checks.pore import bcc_throat_diameter_mm, run_pore_check
from shoegen.checks.prebuild import run_prebuild_checks
from shoegen.checks.records import EXIT_HARD, EXIT_OK, EXIT_PREBUILD
from shoegen.checks.report import (
    REPORT_SCHEMA,
    VERDICT,
    build_report,
    exit_code,
    run_checks,
    write_report,
)

__all__ = [
    "EXIT_HARD",
    "EXIT_OK",
    "EXIT_PREBUILD",
    "REPORT_SCHEMA",
    "VERDICT",
    "BlockMasks",
    "bcc_throat_diameter_mm",
    "build_report",
    "cavity_check",
    "collect_block_masks",
    "exit_code",
    "run_checks",
    "run_grid_checks",
    "run_hard_checks",
    "run_pore_check",
    "run_prebuild_checks",
    "thin_wall_check",
    "write_report",
]
