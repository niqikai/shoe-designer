"""Independent experimental M3 controls; the source-bound ShoeSpec stays frozen."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AssemblyModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False,
                              validate_default=True)


Positive = Annotated[float, Field(gt=0)]
Nonnegative = Annotated[float, Field(ge=0)]
Pair = Annotated[list[float], Field(min_length=2, max_length=2)]


class PatternRegion(AssemblyModel):
    last_x_fraction_range: Pair = Field(default_factory=lambda: [0.67, 0.82])
    y_range_mm: Pair = Field(default_factory=lambda: [-20., 20.])
    minimum_outward_normal_z: Annotated[float, Field(ge=0, le=1)] = 0.8

    @model_validator(mode="after")
    def ordered_region(self) -> Self:
        x0, x1 = self.last_x_fraction_range
        y0, y1 = self.y_range_mm
        if not 0 <= x0 < x1 <= 1:
            raise ValueError("last_x_fraction_range must increase within [0, 1]")
        if not y0 < y1:
            raise ValueError("y_range_mm must be strictly increasing")
        return self


class AssemblyPattern(AssemblyModel):
    """Template controls, not qualified manufacturing minimum-feature rules."""
    type: Literal["none", "hex"] = "hex"
    hole_flat_to_flat_mm: Positive = 4.0
    pitch_mm: Positive = 8.0
    region: PatternRegion = Field(default_factory=PatternRegion)
    collar_keepout_mm: Nonnegative = 4.0
    sole_connection_keepout_mm: Nonnegative = 3.0
    minimum_web_mm: Positive = 2.0
    cavity_overshoot_mm: Positive = 0.3
    maximum_cut_depth_mm: Positive = 8.0
    max_holes: Annotated[int, Field(ge=1, le=1000)] = 64
    placement_algorithm_version: Literal["retained-upper-hex-v1"] = "retained-upper-hex-v1"

    @model_validator(mode="after")
    def useful_pitch_and_depth(self) -> Self:
        # Circumdiameter bounds every in-plane hex orientation. The complete
        # curved cutters still need independent geometric web checks.
        import math
        if self.pitch_mm < 2 * self.hole_flat_to_flat_mm / math.sqrt(3) + self.minimum_web_mm:
            raise ValueError("pitch_mm must cover hex circumdiameter plus minimum_web_mm")
        if self.maximum_cut_depth_mm <= self.cavity_overshoot_mm:
            raise ValueError("maximum_cut_depth_mm must exceed cavity_overshoot_mm")
        return self


class VoidAccessParameters(AssemblyModel):
    enabled: bool = True
    port_radius_mm: Positive = 2.0
    cavity_overshoot_mm: Positive = 0.3
    minimum_exterior_skin_keepout_mm: Positive = 2.0
    minimum_port_web_mm: Positive = 2.0
    minimum_overlap_volume_mm3: Positive = 1e-5
    minimum_connection_witness_radius_mm: Positive = 0.1
    max_port_count: Annotated[int, Field(ge=1, le=1000)] = 64
    candidate_budget_per_component: Annotated[int, Field(ge=1, le=100000)] = 128
    placement_version: Literal["footbed-access-v1"] = "footbed-access-v1"

    @model_validator(mode="after")
    def witness_fits_port(self) -> Self:
        if self.minimum_connection_witness_radius_mm >= self.port_radius_mm:
            raise ValueError("minimum_connection_witness_radius_mm must be below port_radius_mm")
        return self


class AssemblySpec(AssemblyModel):
    """Separate M3 configuration referencing immutable M1/M2 evidence.

    Loading does not authorize execution: the pipeline must independently
    verify the final M2 acceptance report's M3_gate == READY and all hashes.
    """
    schema_version: Literal["m3-0.1"] = "m3-0.1"
    unit: Literal["mm"] = "mm"
    m1_directory: Annotated[str, Field(min_length=1)] = "output/m1_toe_refine"
    m2_directory: Annotated[str, Field(min_length=1)] = "output/m2_toe_complete"
    m2_acceptance_report: Annotated[str, Field(min_length=1)] = "docs/milestone2_lattice_acceptance.json"
    overlap_field_mm: Positive = 0.8
    overlap_definition: Literal["offset of saved conservative domain field"] = "offset of saved conservative domain field"
    numerical_volume_floor_mm3: Positive = 1e-7
    numerical_area_floor_mm2: Nonnegative = 0.0
    numerical_distance_tolerance_mm: Positive = 1e-7
    self_intersection_pair_budget: Annotated[int, Field(ge=1)] = 2_000_000
    maximum_core_grid_nodes: Annotated[int, Field(ge=8)] = 50_000_000
    pattern: AssemblyPattern = Field(default_factory=AssemblyPattern)
    void_access: VoidAccessParameters = Field(default_factory=VoidAccessParameters)
    manufacturing_rules_file: Annotated[str, Field(min_length=1)] = "config/manufacturing_rules.json"

    def configuration_sha256(self) -> str:
        payload = json.dumps(self.model_dump(mode="json"), sort_keys=True,
                             separators=(",", ":"), allow_nan=False).encode()
        return hashlib.sha256(payload).hexdigest()

    @classmethod
    def load(cls, path: str | Path) -> Self:
        """Read strict JSON; paths remain relative to the caller's project root."""
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))
