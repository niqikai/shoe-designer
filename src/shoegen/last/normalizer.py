"""Rigid explicit coordinate transformations, faithfully recorded as 4x4 matrices.

Ported from codex/3DPrintedFootwear@5ef9038f8ac5134fba14dfc8db0162f4c8e9f957
(src/shoe_poc/last/normalizer.py). The PR #2 pydantic spec object is replaced by the minimal
CoordinateSystem dataclass below; normalize_last stays duck-typed on
rotation / translation_mm / orientation_confirmed.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class CoordinateSystem:
    """Configured source -> normalized rigid transform (normalized = R @ source + t)."""

    rotation: tuple
    translation_mm: tuple
    orientation_confirmed: bool = False


def normalize_last(shape, coordinate_system):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.gp import gp_Trsf

    rotation = np.asarray(coordinate_system.rotation, dtype=float)
    translation = np.asarray(coordinate_system.translation_mm, dtype=float)
    if (
        rotation.shape != (3, 3)
        or not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-6, rtol=0)
        or not np.isclose(np.linalg.det(rotation), 1, atol=1e-6, rtol=0)
    ):
        raise ValueError("Normalization rotation must be a proper orthonormal 3x3 matrix")
    if translation.shape != (3,) or not np.isfinite(translation).all():
        raise ValueError("Normalization translation must be a finite 3-vector in mm")
    matrix = np.eye(4)
    matrix[:3, :3] = rotation
    matrix[:3, 3] = translation
    rigid = gp_Trsf()
    rigid.SetValues(*matrix[:3, :].ravel().tolist())
    transformed = type(shape).cast(BRepBuilderAPI_Transform(shape.wrapped, rigid, True).Shape())
    return transformed, {
        "convention": (
            "X heel-to-toe, Y width, Z up; column vectors; "
            "normalized = rotation @ source + translation_mm"
        ),
        "matrix_4x4": matrix.tolist(),
        "orientation_confirmed": coordinate_system.orientation_confirmed,
        "orientation_status": (
            "CONFIGURATION_CONFIRMED"
            if coordinate_system.orientation_confirmed
            else "PROVISIONAL_REQUIRES_USER_CONFIRMATION"
        ),
        "method": "Explicit rigid transformation; no automatic toe/side inference",
    }
