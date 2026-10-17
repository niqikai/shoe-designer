"""Node whitelist of the GeometryGraph (ADR 0001, plan M0.4).

Every node is a frozen pydantic model with a literal ``type`` discriminator; unknown
fields are rejected. Children are referenced by node id, so a node can be shared by
several parents (the graph is a DAG). All lengths are millimetres.

Sign convention: d < 0 inside, d > 0 outside.

Adding a node type, or changing what an existing field means, requires bumping
``GRAPH_VERSION`` (ADR 0001).
"""

from __future__ import annotations

import math
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Vec2 = tuple[float, float]
Vec3 = tuple[float, float, float]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
NodeId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.:\-]{1,128}$")]
Scalar = bool | int | float | str
PositiveFloat = Annotated[float, Field(gt=0)]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


def _all_positive(v: tuple[float, ...], name: str) -> None:
    if any(c <= 0 for c in v):
        raise ValueError(f"{name} components must be > 0, got {v}")


# ---------------------------------------------------------------- sampled-data references


class TwoLevelGrid(_Model):
    """Grid parameters and array hashes of a two-level 3D sampled field.

    The array layout is specified in :mod:`shoegen.graph.sampled`.
    """

    fine_origin: Vec3
    fine_h: PositiveFloat
    brick_size: Annotated[int, Field(ge=1, le=64)]
    narrow_band_mm: PositiveFloat
    coarse_origin: Vec3
    coarse_h: PositiveFloat
    bbox_min: Vec3
    bbox_max: Vec3
    fine_bricks_sha256: Sha256
    fine_index_sha256: Sha256
    coarse_sha256: Sha256

    @model_validator(mode="after")
    def _check(self) -> TwoLevelGrid:
        if any(lo > hi for lo, hi in zip(self.bbox_min, self.bbox_max, strict=True)):
            raise ValueError("bbox_min must be <= bbox_max")
        return self

    def array_shas(self) -> tuple[str, ...]:
        return (self.fine_bricks_sha256, self.fine_index_sha256, self.coarse_sha256)


class Grid2D(_Model):
    """A dense 2D signed-distance grid (layout in :mod:`shoegen.graph.sampled`)."""

    source_sha256: Sha256
    sampling: dict[str, Scalar] = Field(default_factory=dict)
    origin: Vec2
    h: PositiveFloat
    bbox_min: Vec2
    bbox_max: Vec2
    values_sha256: Sha256

    @model_validator(mode="after")
    def _check(self) -> Grid2D:
        if any(lo > hi for lo, hi in zip(self.bbox_min, self.bbox_max, strict=True)):
            raise ValueError("bbox_min must be <= bbox_max")
        return self


# ---------------------------------------------------------------- analytic leaves


class Box(_Model):
    """Axis-aligned box, exact SDF."""

    type: Literal["Box"] = "Box"
    center: Vec3
    half_size: Vec3

    @model_validator(mode="after")
    def _check(self) -> Box:
        _all_positive(self.half_size, "half_size")
        return self


class RoundedBox(_Model):
    """Axis-aligned box of outer half size ``half_size`` with edges rounded by ``radius``.

    Exact SDF: it is the exact SDF of the inner box (``half_size - radius``) minus
    ``radius``.
    """

    type: Literal["RoundedBox"] = "RoundedBox"
    center: Vec3
    half_size: Vec3
    radius: Annotated[float, Field(ge=0)]

    @model_validator(mode="after")
    def _check(self) -> RoundedBox:
        _all_positive(self.half_size, "half_size")
        if self.radius > min(self.half_size):
            raise ValueError("radius must be <= min(half_size)")
        return self


class Capsule(_Model):
    """Segment ``a``–``b`` swept by a ball of ``radius``; exact SDF."""

    type: Literal["Capsule"] = "Capsule"
    a: Vec3
    b: Vec3
    radius: PositiveFloat


class HalfSpace(_Model):
    """``{p : n·p <= offset}`` with ``n = normal / |normal|``; exact SDF ``n·p - offset``."""

    type: Literal["HalfSpace"] = "HalfSpace"
    normal: Vec3
    offset: float

    @model_validator(mode="after")
    def _check(self) -> HalfSpace:
        norm = math.sqrt(sum(c * c for c in self.normal))
        if not norm > 1e-12:
            raise ValueError("normal must be non-zero")
        return self


# ---------------------------------------------------------------- sampled leaves


class SampledField(_Model):
    """Signed distance of a last sampled on two grids (ADR 0003); exact in the narrow band.

    ``source_sha256`` is the sha256 of the source file (STEP/mesh); ``sampling`` holds the
    sampling and normalisation/morph parameters that produced the arrays. Both are part of
    ``graph_hash`` along with the array hashes in ``grid``.
    """

    type: Literal["SampledField"] = "SampledField"
    source_sha256: Sha256
    sampling: dict[str, Scalar] = Field(default_factory=dict)
    grid: TwoLevelGrid


class HeightSurfaceField(_Model):
    """Signed distance to the open surface ``z = h(x, y) + offset`` (plan M0.3).

    Same data layout as :class:`SampledField`; negative below the surface. Exact in the
    narrow band, so it can be offset (ADR 0001). There is deliberately no node that
    divides a height map by ``sqrt(1 + G^2)``.
    """

    type: Literal["HeightSurfaceField"] = "HeightSurfaceField"
    source_sha256: Sha256
    sampling: dict[str, Scalar] = Field(default_factory=dict)
    grid: TwoLevelGrid


class Extrude2D(_Model):
    """A 2D signed-distance field extruded along Z and cut to ``z_min <= z <= z_max``.

    ``offset`` is applied to the 2D field before extrusion (positive grows the outline),
    so ``d2 = field(x, y) - offset``. The 3D value uses the exact extrusion formula
    ``min(max(d2, dz), 0) + |max((d2, dz), 0)|`` with ``dz = |z - zc| - hz``; it is the
    exact SDF of the prism on both sides whenever ``d2`` is exact.
    """

    type: Literal["Extrude2D"] = "Extrude2D"
    field: Grid2D
    z_min: float
    z_max: float
    offset: float = 0.0

    @model_validator(mode="after")
    def _check(self) -> Extrude2D:
        if not self.z_min < self.z_max:
            raise ValueError("z_min must be < z_max")
        return self


class StrutLattice(_Model):
    """Periodic strut lattice (ADR 0002). Evaluation is implemented in M0.7.

    ``fillet_k`` is the node-fillet smoothing length; ``None`` means ``0.15 * radius_mm``.
    """

    type: Literal["StrutLattice"] = "StrutLattice"
    cell_mm: PositiveFloat
    radius_mm: PositiveFloat
    topology: Literal["bcc"] = "bcc"
    origin: Vec3 = (0.0, 0.0, 0.0)
    fillet_k: PositiveFloat | None = None


# ---------------------------------------------------------------- operators


class Union(_Model):
    """``min`` of the children."""

    type: Literal["Union"] = "Union"
    children: Annotated[list[NodeId], Field(min_length=1)]


class Intersect(_Model):
    """``max`` of the children."""

    type: Literal["Intersect"] = "Intersect"
    children: Annotated[list[NodeId], Field(min_length=1)]


class Subtract(_Model):
    """``max(d_base, -d_tool)``."""

    type: Literal["Subtract"] = "Subtract"
    base: NodeId
    tool: NodeId


class SmoothUnion(_Model):
    """Quadratic smooth minimum with blend width ``k``, folded left over the children.

    ``smin(a, b) = min(a, b) - k/4 * max(k - |a - b|, 0)^2 / k^2``. Lipschitz <= 1, but the
    result is only a distance bound on both sides.
    """

    type: Literal["SmoothUnion"] = "SmoothUnion"
    children: Annotated[list[NodeId], Field(min_length=2)]
    k: PositiveFloat


class Offset(_Model):
    """``d_child - distance``: positive grows the shape, negative shrinks it."""

    type: Literal["Offset"] = "Offset"
    child: NodeId
    distance: float


class ShellOutward(_Model):
    """``max(d - t, -d)``: a shell of thickness ``t`` grown outward from the child surface."""

    type: Literal["ShellOutward"] = "ShellOutward"
    child: NodeId
    thickness: PositiveFloat


AnyNode = (
    Box
    | RoundedBox
    | Capsule
    | HalfSpace
    | SampledField
    | HeightSurfaceField
    | Extrude2D
    | StrutLattice
    | Union
    | Intersect
    | Subtract
    | SmoothUnion
    | Offset
    | ShellOutward
)
Node = Annotated[AnyNode, Field(discriminator="type")]

NODE_TYPES: tuple[type[_Model], ...] = (
    Box,
    RoundedBox,
    Capsule,
    HalfSpace,
    SampledField,
    HeightSurfaceField,
    Extrude2D,
    StrutLattice,
    Union,
    Intersect,
    Subtract,
    SmoothUnion,
    Offset,
    ShellOutward,
)


def children_of(node: AnyNode) -> tuple[str, ...]:
    """Child node ids in evaluation order."""
    if isinstance(node, Union | Intersect | SmoothUnion):
        return tuple(node.children)
    if isinstance(node, Subtract):
        return (node.base, node.tool)
    if isinstance(node, Offset | ShellOutward):
        return (node.child,)
    return ()


def array_refs(node: AnyNode) -> tuple[str, ...]:
    """sha256 of every data array a node needs at evaluation time."""
    if isinstance(node, SampledField | HeightSurfaceField):
        return node.grid.array_shas()
    if isinstance(node, Extrude2D):
        return (node.field.values_sha256,)
    return ()
