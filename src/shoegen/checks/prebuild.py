"""Pre-build parameter checks (ADR 0004): aggregate of :func:`check_lattice_params`."""

from __future__ import annotations

from shoegen.checks.records import FAIL, NOT_APPLICABLE, PASS, record, source
from shoegen.graph import Graph, StrutLattice
from shoegen.lattice import MIN_VOXELS_ACROSS_STRUT, LatticeParameterError, check_lattice_params
from shoegen.profile import ManufacturingProfile


def run_prebuild_checks(graph: Graph, profile: ManufacturingProfile, voxel_mm: float) -> list[dict]:
    """Two records per StrutLattice node (strut diameter, voxel rule); never raises.

    Values are the node's own; pass/fail comes from the lattice check itself
    (:func:`shoegen.lattice.check_lattice_params`), so the report cannot disagree with it.
    A graph without lattices gets one ``not_applicable`` record.
    """
    try:
        check_lattice_params(graph, profile, voxel_mm)
        message = None
    except LatticeParameterError as exc:
        message = str(exc)
    lattices = [(nid, n) for nid, n in sorted(graph.nodes.items()) if isinstance(n, StrutLattice)]
    if not lattices:
        return [
            record(
                "lattice_params",
                "prebuild",
                FAIL if message else NOT_APPLICABLE,
                measured={"voxel_mm": voxel_mm, "lattice_nodes": 0},
                threshold={},
                src=source(profile, None),
                detail=message or "graph has no StrutLattice node",
            )
        ]
    out: list[dict] = []
    for nid, node in lattices:
        d = 2.0 * node.radius_mm
        bad = message is not None
        d_ok = not (bad and f"node {nid!r}: strut diameter" in message)
        v_ok = not (bad and f"node {nid!r}: voxel" in message)
        out.append(
            record(
                f"strut_diameter[{nid}]",
                "prebuild",
                PASS if d_ok else FAIL,
                measured={"strut_diameter_mm": d, "radius_mm": node.radius_mm},
                threshold={"min_strut_diameter_mm": profile.min_strut_diameter_mm},
                src=source(profile, "min_strut_diameter_mm"),
            )
        )
        out.append(
            record(
                f"voxel_vs_strut[{nid}]",
                "prebuild",
                PASS if v_ok else FAIL,
                measured={"voxel_mm": voxel_mm, "voxels_across_strut": d / voxel_mm},
                threshold={
                    "max_voxel_mm": d / MIN_VOXELS_ACROSS_STRUT,
                    "min_voxels_across_strut": MIN_VOXELS_ACROSS_STRUT,
                },
                src=source(profile, "min_strut_diameter_mm"),
            )
        )
    return out
