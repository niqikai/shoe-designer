"""Build-time parameter check for strut lattices (ADR 0002, plan M0.7).

The thin-wall check of a strut lattice is a parameter check: the strut diameter is
``2 * radius_mm``, so a lattice that violates the process limit is refused before any
geometry is evaluated. The voxel rule keeps struts connected on the mesh grid.
"""

from __future__ import annotations

import math

from shoegen.graph import Graph, GraphValidationError, StrutLattice
from shoegen.profile import ManufacturingProfile, load_profile

MIN_VOXELS_ACROSS_STRUT = 4.0
"""``voxel_mm <= strut diameter / 4``: at least four voxels across every strut."""

_REL_TOL = 1e-9


class LatticeParameterError(GraphValidationError):
    """A StrutLattice violates the ManufacturingProfile or the voxel rule (exit code 3)."""


def check_lattice_params(
    graph: Graph, profile: ManufacturingProfile | str, voxel_mm: float
) -> None:
    """Refuse a graph whose lattices cannot be manufactured or meshed.

    For every :class:`StrutLattice` node: ``2 * radius_mm >= profile.min_strut_diameter_mm``
    and ``voxel_mm <= 2 * radius_mm / 4``. All violations are reported together. Raises
    :class:`LatticeParameterError` (``exit_code == 3``); returns ``None`` when the graph
    is fine. ``profile`` may be a loaded profile or a profile id such as
    ``"pbf-tpu-generic@0"``.
    """
    if isinstance(profile, str):
        profile = load_profile(profile)
    if not (math.isfinite(voxel_mm) and voxel_mm > 0):
        raise LatticeParameterError(f"voxel_mm must be finite and > 0, got {voxel_mm!r}")
    problems: list[str] = []
    for nid in sorted(graph.nodes):
        node = graph.nodes[nid]
        if not isinstance(node, StrutLattice):
            continue
        diameter = 2.0 * node.radius_mm
        if diameter < profile.min_strut_diameter_mm * (1.0 - _REL_TOL):
            problems.append(
                f"node {nid!r}: strut diameter {diameter:.6g} mm < "
                f"{profile.id} min_strut_diameter_mm {profile.min_strut_diameter_mm:.6g} mm"
            )
        max_voxel = diameter / MIN_VOXELS_ACROSS_STRUT
        if voxel_mm > max_voxel * (1.0 + _REL_TOL):
            problems.append(
                f"node {nid!r}: voxel {voxel_mm:.6g} mm > strut diameter / "
                f"{MIN_VOXELS_ACROSS_STRUT:g} = {max_voxel:.6g} mm"
            )
    if problems:
        raise LatticeParameterError("lattice parameter check failed: " + "; ".join(problems))
