"""Declared adaptive CAD volume measurements, separate from exactness claims.

build123d's default volume uses OCCT's nonadaptive integration. Tight numerical
comparisons use the fixed Gauss-Kronrod protocol below, including BSPLINE spans.
Returned error estimates cover quadrature only, not tolerated CAD boundaries or
all integration errors. No generator geometry or triangulation is changed.
"""
from __future__ import annotations

import math

VOLUME_EPS = 1e-12
REFINEMENT_EPS = 1e-10


def _integrate(shape, reference, eps):
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from OCP.gp import gp_Dir, gp_Pln, gp_Pnt

    properties = GProp_GProps()
    # OnlyClosed=True, IsUseSpan=True, CGFlag=False, IFlag=False,
    # SkipShared=False: exact surfaces, no mesh-based volume approximation.
    arguments = (eps, True, True, False, False, False)
    if reference == "point":
        error = BRepGProp.VolumePropertiesGK_s(shape.wrapped, properties, *arguments)
    else:
        plane = gp_Pln(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1))
        error = BRepGProp.VolumePropertiesGK_s(shape.wrapped, properties, plane, *arguments)
    value = float(properties.Mass())
    error = float(error)
    if not math.isfinite(value) or value <= 0 or not math.isfinite(error) or error < 0:
        raise ValueError("CAD volume integration failed or produced a non-positive/non-finite result")
    return {"volume_mm3": value, "requested_relative_error": eps,
            "estimated_relative_quadrature_error": error,
            "estimated_absolute_quadrature_error_mm3": abs(value*error)}


def measure_cad_volume(shape, *, reference="point", refine=False) -> dict:
    """Measure valid closed material with one fixed numerical protocol.

    ``reference`` is either ``point`` (OCCT's native point-reference integral)
    or ``xy_plane`` (the native plane-reference integral at world Z=0).
    ``refine`` also records Eps=1e-10 for comparison with the fixed 1e-12 result;
    it does not choose the most convenient result or change any tolerance.
    No returned estimate is a rigorous bound on true absolute CAD volume.
    """
    if reference not in {"point", "xy_plane"}:
        raise ValueError("Volume reference must be point or xy_plane")
    if shape is None or shape.wrapped is None or shape.wrapped.IsNull():
        raise ValueError("CAD volume requires a nonempty valid closed solid shape")
    if not shape.is_valid or not shape.solids():
        raise ValueError("CAD volume requires a valid closed solid shape")
    result = {
        "method": "OCCT adaptive Gauss-Kronrod exact-surface integration",
        "bspline_span_splitting": True, "only_closed_shells": True,
        "reference": reference, "absolute_accuracy": "NOT_VERIFIED",
        "error_scope": "OCCT quadrature estimate only; not a rigorous bound on tolerated CAD geometry or all numerical errors",
        **_integrate(shape, reference, VOLUME_EPS),
    }
    if reference == "xy_plane":
        result["reference_plane"] = {"origin_mm": [0., 0., 0.], "normal": [0., 0., 1.]}
    if refine:
        comparison = _integrate(shape, reference, REFINEMENT_EPS)
        delta = abs(result["volume_mm3"]-comparison["volume_mm3"])
        result["refinement"] = {**comparison, "absolute_change_mm3": delta,
                                "relative_change": delta/abs(result["volume_mm3"]),
                                "scope": "Observed protocol refinement, not an absolute accuracy bound"}
    return result
