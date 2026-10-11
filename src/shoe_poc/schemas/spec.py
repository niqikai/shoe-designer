"""Validated, millimetre-based parameters for the Milestone 1 CAD pipeline."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


RotationRow = Annotated[list[float], Field(min_length=3, max_length=3)]


class CoordinateSystem(StrictModel):
    """Input transform: p_design_mm = rotation @ p_source_mm + translation_mm."""

    rotation: Annotated[list[RotationRow], Field(min_length=3, max_length=3)]
    translation_mm: Annotated[list[float], Field(min_length=3, max_length=3)]
    orientation_confirmed: bool
    convention: Literal["X_heel_to_toe_Y_width_Z_up"] = "X_heel_to_toe_Y_width_Z_up"

    @field_validator("rotation")
    @classmethod
    def proper_rotation(cls, matrix: list[list[float]]) -> list[list[float]]:
        for i in range(3):
            for j in range(3):
                dot = sum(matrix[i][k] * matrix[j][k] for k in range(3))
                expected = 1.0 if i == j else 0.0
                if not math.isclose(dot, expected, rel_tol=0, abs_tol=1e-6):
                    raise ValueError("rotation must be orthonormal within 1e-6")
        a, b, c = matrix
        determinant = (
            a[0] * (b[1] * c[2] - b[2] * c[1])
            - a[1] * (b[0] * c[2] - b[2] * c[0])
            + a[2] * (b[0] * c[1] - b[1] * c[0])
        )
        if not math.isclose(determinant, 1.0, rel_tol=0, abs_tol=1e-6):
            raise ValueError("rotation must have determinant +1; reflections are not rotations")
        return matrix

    @field_validator("translation_mm")
    @classmethod
    def bounded_translation(cls, value: list[float]) -> list[float]:
        if any(abs(component) > 10000 for component in value):
            raise ValueError("each translation_mm component must lie within ±10000 mm")
        return value


class LastSpec(StrictModel):
    file: Annotated[str, Field(min_length=1)]
    coordinate_system: CoordinateSystem
    side: Literal["left", "right", "unknown"]
    source: Annotated[str, Field(min_length=1)] = "User-supplied test asset"
    license_status: Literal["pending_confirmation", "confirmed"] = "pending_confirmation"

    @field_validator("file")
    @classmethod
    def step_file(cls, value: str) -> str:
        if not value.strip() or Path(value).suffix.lower() not in {".stp", ".step"}:
            raise ValueError("last.file must name a .stp or .step file")
        return value


class ShoeParameters(StrictModel):
    template: Literal["slip_on_01"]
    # Size is a declaration, never inferred from bounding-box dimensions.
    size: Annotated[str, Field(min_length=1, max_length=80)] | None


class OpeningParameters(StrictModel):
    collar_height_mm: Annotated[float, Field(ge=10, le=100)]
    opening_start_x_fraction: Annotated[float, Field(ge=0, le=0.8)]
    opening_end_x_fraction: Annotated[float, Field(gt=0, le=1)]

    @model_validator(mode="after")
    def ordered_opening(self) -> Self:
        if self.opening_end_x_fraction - self.opening_start_x_fraction < 0.1:
            raise ValueError("opening end must exceed start by at least 0.1 of last length")
        return self


class PatternParameters(StrictModel):
    # Milestone 1 implements a continuous upper only.
    type: Literal["none"]


class UpperParameters(StrictModel):
    thickness_mm: Annotated[float, Field(ge=0.8, le=8)]
    clearance_mm: Annotated[float, Field(ge=0, le=5)]
    opening_parameters: OpeningParameters
    pattern_parameters: PatternParameters


class SoleProfileParameters(StrictModel):
    margin_mm: Annotated[float, Field(ge=1, le=20)]


class SoleParameters(StrictModel):
    height_mm: Annotated[float, Field(ge=5, le=60)]
    profile_parameters: SoleProfileParameters


class LatticeParameters(StrictModel):
    """Reserved protocol values. Milestone 1 does not generate lattice geometry."""

    type: Literal["gyroid"]
    period_mm: Annotated[float, Field(ge=5, le=50)]
    threshold: Annotated[float, Field(ge=0.05, le=1.5)]
    resolution_mm: Annotated[float, Field(ge=0.2, le=5)]

    @model_validator(mode="after")
    def useful_resolution(self) -> Self:
        if self.resolution_mm > self.period_mm / 4:
            raise ValueError("resolution_mm must be no greater than period_mm / 4")
        return self


class MaterialProfile(StrictModel):
    id: Annotated[str, Field(min_length=1, max_length=80)]
    material: Literal["TPU"]
    status: Literal["EXPERIMENTAL"]


class RulesProfile(StrictModel):
    id: Annotated[str, Field(min_length=1, max_length=80)]
    file: Annotated[str, Field(min_length=1)]
    status: Literal["EXPERIMENTAL"]

    @field_validator("file")
    @classmethod
    def json_file(cls, value: str) -> str:
        if not value.strip() or Path(value).suffix.lower() != ".json":
            raise ValueError("rules_profile.file must name a .json file")
        return value


class ManufacturingParameters(StrictModel):
    process: Literal["SLS"]
    material_profile: MaterialProfile
    rules_profile: RulesProfile


class ShoeSpec(StrictModel):
    schema_version: Literal["0.1"]
    design_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")]
    unit: Literal["mm"]
    last: LastSpec
    shoe: ShoeParameters
    upper: UpperParameters
    sole: SoleParameters
    lattice: LatticeParameters
    manufacturing: ManufacturingParameters

    @model_validator(mode="after")
    def connected_sole_margin(self) -> Self:
        envelope = self.upper.thickness_mm + self.upper.clearance_mm
        if self.sole.profile_parameters.margin_mm < envelope:
            raise ValueError("sole margin_mm must cover upper thickness_mm + clearance_mm")
        return self

    @classmethod
    def load(cls, path: str | Path) -> Self:
        """Read JSON and resolve asset/profile paths relative to its parent directory.

        Existence and CAD validity of the STEP asset are the loader's responsibility.
        JSON validation precedes path resolution; a malformed spec raises ValidationError.
        """
        source = Path(path).resolve()
        spec = cls.model_validate_json(source.read_text(encoding="utf-8"))
        spec.last.file = str((source.parent / spec.last.file).resolve())
        profile = spec.manufacturing.rules_profile
        profile.file = str((source.parent / profile.file).resolve())
        return spec
