"""Minimum pore size: analytic M0 estimate for the BCC strut lattice."""

from __future__ import annotations

import math

from shoegen.checks.records import NOT_APPLICABLE, REPORT_ONLY, record, source
from shoegen.graph import Graph, StrutLattice
from shoegen.profile import ManufacturingProfile


def bcc_throat_diameter_mm(cell_mm: float, radius_mm: float) -> float:
    """Diameter of the largest sphere that passes through a cell face of a BCC lattice.

    The struts run along the cube diagonals; the face centre is the point farthest from
    the four corner struts, at distance ``a / sqrt(6)`` from each strut axis. The sphere
    that fits has radius ``a / sqrt(6) - r``. Node fillets (``fillet_k``) make the real
    throat slightly smaller, so this is an upper bound; clamped at 0.
    """
    return max(0.0, 2.0 * (cell_mm / math.sqrt(6.0) - radius_mm))


def run_pore_check(graph: Graph, profile: ManufacturingProfile) -> dict:
    lattices = [(nid, n) for nid, n in sorted(graph.nodes.items()) if isinstance(n, StrutLattice)]
    src = source(profile, "min_pore_mm")
    thr = {"min_pore_mm": profile.min_pore_mm}
    if not lattices:
        return record(
            "min_pore_size",
            "soft",
            NOT_APPLICABLE,
            measured={},
            threshold=thr,
            src=src,
            detail="graph has no StrutLattice node",
            finding=False,
        )
    per = {
        nid: {
            "cell_mm": n.cell_mm,
            "radius_mm": n.radius_mm,
            "throat_diameter_mm": bcc_throat_diameter_mm(n.cell_mm, n.radius_mm),
        }
        for nid, n in lattices
    }
    smallest = min(v["throat_diameter_mm"] for v in per.values())
    return record(
        "min_pore_size",
        "soft",
        REPORT_ONLY,
        measured={"min_throat_diameter_mm": smallest, "lattices": per},
        threshold=thr,
        src=src,
        detail="analytic estimate 2*(a/sqrt(6)-r) for BCC; upper bound (ignores node fillets)",
        finding=smallest < profile.min_pore_mm,
    )
