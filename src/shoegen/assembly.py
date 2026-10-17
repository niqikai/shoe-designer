"""Shoe assembly: last fields + assembly parameters -> the whole-shoe GeometryGraph (plan M0.8).

Typical use::

    from shoegen.assembly import load_assembly_params, build_shoe_graph, build_shoe_mesh

    params = load_assembly_params("examples/m0.json")
    graph = build_shoe_graph(last_fields, params)          # validated (exit 3 on failure)
    build = build_shoe_mesh(last_fields, params, voxel_mm=0.5, profile="pbf-tpu-generic@0")
    build.mesh_result.mesh, build.mesh_result.summary

Geometry (all lengths in mm; ``c`` = clearance, ``z_min`` = lowest point of the last)::

    cavity    = Offset(last, +c)                                       outside exact
    outline   = Extrude2D(footprint, offset +outline_margin, z in [z_ground, z_top])
    cap       = HeightSurfaceField(h + height_surface_offset)          (below = inside)
    envelope  = Subtract(Intersect(outline, cap), cavity)              inside exact
    core      = Offset(envelope, -skin)                                inside exact
    pores     = Subtract(core, lattice)                                (bound)
    sole.body = Subtract(envelope, pores)       = skin + (core ∩ lattice)
    footbed   = Intersect(ShellOutward(cavity, footbed), envelope)
    sole      = Union(sole.body, footbed)
    upper     = Intersect(ShellOutward(cavity, upper_thickness), z <= z_collar)
    shoe      = Subtract(Union(sole, upper), cavity)

with ``z_ground = z_min - forefoot_sole``, ``z_top = max h + top_margin`` (the highest bottom
point of the last, so a heel rise and a toe spring stay inside the envelope) and
``z_collar = z_min + collar_height``.

Skin and core (ADR 0001). The plan writes the sole as ``(shrunk envelope ∩ lattice) ∪ skin ∪
footbed`` with ``skin = envelope - shrunk envelope``. ``sole.body = envelope - (shrunk -
lattice)`` is the same set: ``E ∩ ¬(S ∩ ¬L) = (E ∩ ¬S) ∪ (E ∩ L)`` and ``E ∩ L ∩ ¬S`` lies in
the skin. It is used because

* the only metric requirement is the inward ``Offset(envelope, -skin)``, whose child is
  ``exact`` inside (intersection and difference of exact leaves); the lattice (``bound``) is
  never offset or shelled;
* the union form has ``min(max(E + s, L), max(E, -E - s)) = 0`` on the whole skin/core
  interface inside every strut (both terms vanish there), so a grid point that happens to
  lie on it is sampled as 0 = outside and opens a spurious void; the difference form is
  ``max(E, min(-E - s, L)) = max(-s, L) < 0`` there.

The pores of the lattice are enclosed by the skin and the footbed: the M0 sole is a closed
solid with internal void shells (the mesh has one positive-volume shell plus negative-volume
void shells). Draining the pores is not part of M0.

Memory: nothing here allocates over the shoe volume; :func:`build_shoe_mesh` goes through
the block-sparse evaluator and mesher (ADR 0005).
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from shoegen.evaluate import DEFAULT_WORKERS, BlockPlan, plan_blocks
from shoegen.graph import (
    Extrude2D,
    Graph,
    GraphBuilder,
    GraphValidationError,
    HalfSpace,
    Intersect,
    Offset,
    ShellOutward,
    StrutLattice,
    Subtract,
    Union,
)
from shoegen.graph.arrays import ArrayStore
from shoegen.lattice import check_lattice_params
from shoegen.mesh import MeshResult, extract_mesh
from shoegen.profile import ManufacturingProfile, load_profile
from shoegen.resources import RssWatchdog

ASSEMBLY_VERSION = 1
PositiveMm = Annotated[float, Field(gt=0)]
NonNegativeMm = Annotated[float, Field(ge=0)]

REGION_MARGIN_MM = 2.0
REGION_MARGIN_VOXELS = 3


class AssemblyParamError(GraphValidationError):
    """Assembly parameters are malformed or do not fit the last (exit code 3)."""


# ---------------------------------------------------------------------- parameters


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class LatticeParams(_Model):
    """Sole lattice (ADR 0002). ``fillet_k = None`` means ``0.15 * radius_mm``."""

    topology: Literal["bcc"] = "bcc"
    cell_mm: PositiveMm
    radius_mm: PositiveMm
    fillet_k: PositiveMm | None = None


class AssemblyParams(_Model):
    """Parameters of the M0 shoe (``examples/m0.json``); not the graph itself."""

    assembly_version: Literal[1] = ASSEMBLY_VERSION
    clearance_mm: PositiveMm = 1.0
    """Gap between the last and the inside of the shoe (``cavity = Offset(last, +c)``)."""
    forefoot_sole_mm: PositiveMm = 12.0
    """``H_fore``: the flat ground plane lies this far below the lowest point of the last."""
    skin_mm: PositiveMm = 1.2
    """Solid skin of the sole envelope; the lattice fills the envelope shrunk by this."""
    footbed_mm: PositiveMm = 1.5
    """Solid plate grown outward from the cavity inside the envelope."""
    upper_thickness_mm: PositiveMm = 2.0
    """Solid upper shell grown outward from the cavity."""
    collar_height_mm: PositiveMm
    """Height of the horizontal collar cut above the lowest point of the last."""
    outline_margin_mm: NonNegativeMm = 3.0
    """The envelope's outline is the footprint offset outward by this."""
    height_surface_offset_mm: NonNegativeMm = 3.0
    """The envelope's top follows ``z = h(x, y) + offset`` (``h``: bottom of the last)."""
    top_margin_mm: NonNegativeMm = 3.0
    """``z_top`` of the outline prism = highest bottom point of the last + this."""
    lattice: LatticeParams


def load_assembly_params(path: str | Path) -> AssemblyParams:
    """Read and validate an assembly parameter file (``AssemblyParamError`` on failure)."""
    try:
        obj = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AssemblyParamError(f"cannot read assembly parameters {path}: {exc}") from exc
    return assembly_params_from_obj(obj)


def assembly_params_from_obj(obj: object) -> AssemblyParams:
    try:
        return AssemblyParams.model_validate(obj)
    except ValidationError as exc:
        raise AssemblyParamError(f"invalid assembly parameters: {exc}") from exc


# ---------------------------------------------------------------------- layout


@dataclass(frozen=True)
class ShoeLayout:
    """Heights and extents derived from the last and the parameters (mm)."""

    last_min: tuple[float, float, float]
    last_max: tuple[float, float, float]
    footprint_min: tuple[float, float]
    footprint_max: tuple[float, float]
    z_min: float
    """Lowest point of the last."""
    z_ground: float
    """Flat bottom of the sole: ``z_min - forefoot_sole_mm``."""
    h_max: float
    """Highest bottom point of the last (maximum of the height map)."""
    z_top: float
    """Top of the outline prism: ``h_max + top_margin_mm``."""
    z_collar: float
    """Collar plane: ``z_min + collar_height_mm``."""
    lattice_origin: tuple[float, float, float]

    def to_dict(self) -> dict:
        return {k: (list(v) if isinstance(v, tuple) else v) for k, v in self.__dict__.items()}


def shoe_layout(last_fields, params: AssemblyParams) -> ShoeLayout:
    """Derive the layout; refuses parameters that would close the collar (exit 3)."""
    grid = last_fields.sampled_field.grid
    lo = tuple(float(v) for v in grid.bbox_min)
    hi = tuple(float(v) for v in grid.bbox_max)
    fp = last_fields.footprint
    fmin = (float(fp.bbox_min[0]), float(fp.bbox_min[1]))
    fmax = (float(fp.bbox_max[0]), float(fp.bbox_max[1]))
    values = np.asarray(last_fields.height_map.values)
    if values.size == 0 or not np.all(np.isfinite(values)):
        raise AssemblyParamError("height map of the last is empty or not finite")
    # Outside the footprint h holds the nearest outline value, so the maximum over the
    # grid is the maximum over the footprint (outline included).
    h_max = float(values.max())
    z_min = lo[2]
    z_ground = z_min - params.forefoot_sole_mm
    z_top = h_max + params.top_margin_mm
    z_collar = z_min + params.collar_height_mm
    if not z_collar < hi[2]:
        raise AssemblyParamError(
            f"collar plane z = {z_collar!r} mm (z_min + collar_height_mm) is not below the top "
            f"of the last z = {hi[2]!r} mm: the upper would close over the last and enclose "
            "the cavity"
        )
    if not z_top > z_ground:
        raise AssemblyParamError(f"z_top {z_top!r} must be above z_ground {z_ground!r}")
    # Lattice anchored at the heel end of the footprint, its centreline and the ground, so
    # the cell layout follows the last instead of the world origin.
    origin = (fmin[0], 0.5 * (fmin[1] + fmax[1]), z_ground)
    return ShoeLayout(
        last_min=lo,  # type: ignore[arg-type]
        last_max=hi,  # type: ignore[arg-type]
        footprint_min=fmin,
        footprint_max=fmax,
        z_min=z_min,
        z_ground=z_ground,
        h_max=h_max,
        z_top=z_top,
        z_collar=z_collar,
        lattice_origin=origin,
    )


# ---------------------------------------------------------------------- graph


NODE_IDS = (
    "last",
    "cavity",
    "envelope.outline",
    "envelope.cap",
    "envelope.outer",
    "envelope",
    "sole.core",
    "sole.lattice",
    "sole.pores",
    "sole.body",
    "footbed.shell",
    "footbed",
    "sole",
    "upper.shell",
    "upper.collar_cut",
    "upper",
    "shoe.solid",
    "shoe",
)
"""Node ids of the assembled graph (stable: they are part of ``graph_hash``)."""


SHEET_COVER_MARGIN_MM = 1.0


def _check_sheet_covers_outline(cap, lay: ShoeLayout, p: AssemblyParams) -> None:
    """The height surface is a finite sheet, exact only over its XY rectangle
    (:mod:`shoegen.last.fields`); it must cover the envelope outline."""
    g = cap.grid
    need_lo = [v - p.outline_margin_mm - SHEET_COVER_MARGIN_MM for v in lay.footprint_min]
    need_hi = [v + p.outline_margin_mm + SHEET_COVER_MARGIN_MM for v in lay.footprint_max]
    if any(g.bbox_min[a] > need_lo[a] or g.bbox_max[a] < need_hi[a] for a in range(2)):
        raise AssemblyParamError(
            f"height surface sheet XY {g.bbox_min[:2]}..{g.bbox_max[:2]} does not cover the "
            f"envelope outline {need_lo}..{need_hi}; increase FieldParams.sheet_margin_mm"
        )


def build_shoe_graph(last_fields, params: AssemblyParams) -> Graph:
    """The whole-shoe graph of plan M0.8, validated (metric rules, band reach; exit 3).

    ``last_fields`` is a :class:`shoegen.last.LastFields`. The height surface
    ``h + height_surface_offset_mm`` is sampled (and cached) on demand.
    """
    lay = shoe_layout(last_fields, params)
    p = params
    b = GraphBuilder()
    last = b.add(last_fields.sampled_field, "last")
    cavity = b.add(Offset(child=last, distance=p.clearance_mm), "cavity")

    outline = b.add(
        Extrude2D(
            field=last_fields.footprint,
            z_min=lay.z_ground,
            z_max=lay.z_top,
            offset=p.outline_margin_mm,
        ),
        "envelope.outline",
    )
    cap_node = last_fields.height_surface(p.height_surface_offset_mm)
    _check_sheet_covers_outline(cap_node, lay, p)
    cap = b.add(cap_node, "envelope.cap")
    outer = b.add(Intersect(children=[outline, cap]), "envelope.outer")
    envelope = b.add(Subtract(base=outer, tool=cavity), "envelope")

    core = b.add(Offset(child=envelope, distance=-p.skin_mm), "sole.core")
    lattice = b.add(
        StrutLattice(
            cell_mm=p.lattice.cell_mm,
            radius_mm=p.lattice.radius_mm,
            topology=p.lattice.topology,
            origin=lay.lattice_origin,
            fillet_k=p.lattice.fillet_k,
        ),
        "sole.lattice",
    )
    pores = b.add(Subtract(base=core, tool=lattice), "sole.pores")
    body = b.add(Subtract(base=envelope, tool=pores), "sole.body")

    fb_shell = b.add(ShellOutward(child=cavity, thickness=p.footbed_mm), "footbed.shell")
    footbed = b.add(Intersect(children=[fb_shell, envelope]), "footbed")
    sole = b.add(Union(children=[body, footbed]), "sole")

    up_shell = b.add(ShellOutward(child=cavity, thickness=p.upper_thickness_mm), "upper.shell")
    collar = b.add(HalfSpace(normal=(0.0, 0.0, 1.0), offset=lay.z_collar), "upper.collar_cut")
    upper = b.add(Intersect(children=[up_shell, collar]), "upper")

    solid = b.add(Union(children=[sole, upper]), "shoe.solid")
    root = b.add(Subtract(base=solid, tool=cavity), "shoe")
    return b.build(root)


# ---------------------------------------------------------------------- region and meshing


def shoe_region(
    last_fields, params: AssemblyParams, voxel_mm: float | None = None
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """Axis-aligned region containing the whole shoe with a margin on every side.

    The margin (``max(2 mm, 3 voxels)``) keeps the solid off the region's outer faces, so
    the extracted mesh is closed (M0.6: a solid clipped by the region gives an open mesh).
    """
    lay = shoe_layout(last_fields, params)
    p = params
    grow_xy = max(
        p.outline_margin_mm,
        p.clearance_mm + p.upper_thickness_mm,
        p.clearance_mm + p.footbed_mm,
    )
    z_upper = min(lay.z_collar, lay.last_max[2] + p.clearance_mm + p.upper_thickness_mm)
    lo = (lay.footprint_min[0] - grow_xy, lay.footprint_min[1] - grow_xy, lay.z_ground)
    hi = (lay.footprint_max[0] + grow_xy, lay.footprint_max[1] + grow_xy, max(lay.z_top, z_upper))
    m = (
        REGION_MARGIN_MM
        if voxel_mm is None
        else max(REGION_MARGIN_MM, REGION_MARGIN_VOXELS * voxel_mm)
    )
    return (
        tuple(v - m for v in lo),  # type: ignore[return-value]
        tuple(v + m for v in hi),  # type: ignore[return-value]
    )


def mesh_graph(
    graph: Graph,
    store: ArrayStore | None,
    region_min,
    region_max,
    voxel_mm: float,
    *,
    workers: int = DEFAULT_WORKERS,
    watchdog: RssWatchdog | None = None,
    min_fragment_volume_mm3: float = 0.0,
    skip: bool = True,
    check_manifold3d: bool = False,
) -> tuple[BlockPlan, MeshResult]:
    """``plan_blocks`` -> ``extract_mesh`` for one graph, region and voxel size.

    Raises ``RuntimeError`` if the solid touches the region boundary (the mesh would be
    open there): that is a wrong region, not a property of the shape.
    """
    plan = plan_blocks(graph, store, region_min, region_max, voxel_mm, skip=skip)
    res = extract_mesh(
        plan,
        workers=workers,
        watchdog=watchdog,
        min_fragment_volume_mm3=min_fragment_volume_mm3,
        check_manifold3d=check_manifold3d,
    )
    if res.summary["open_at_region_boundary"]:
        raise RuntimeError(
            f"solid touches the boundary of region {plan.region_min}..{plan.region_max}; "
            "the mesh is open there"
        )
    return plan, res


@dataclass
class ShoeBuild:
    graph: Graph
    layout: ShoeLayout
    region_min: tuple[float, float, float]
    region_max: tuple[float, float, float]
    voxel_mm: float
    plan: BlockPlan
    mesh_result: MeshResult
    seconds: dict = field(default_factory=dict)
    """Wall times (not deterministic): ``graph``, ``mesh``, ``total``."""


def build_shoe_mesh(
    last_fields,
    params: AssemblyParams,
    voxel_mm: float,
    profile: ManufacturingProfile | str,
    *,
    workers: int = DEFAULT_WORKERS,
    watchdog: RssWatchdog | None = None,
    skip: bool = True,
    check_manifold3d: bool = False,
) -> ShoeBuild:
    """Graph -> pre-build lattice check (exit 3, before any sampling) -> blocks -> mesh.

    Fragments below ``profile.min_fragment_volume_mm3`` are removed by the mesher.
    """
    if isinstance(profile, str):
        profile = load_profile(profile)
    if not (math.isfinite(voxel_mm) and voxel_mm > 0):
        raise AssemblyParamError(f"voxel_mm must be finite and > 0, got {voxel_mm!r}")
    t0 = time.perf_counter()
    graph = build_shoe_graph(last_fields, params)
    check_lattice_params(graph, profile, voxel_mm)
    layout = shoe_layout(last_fields, params)
    rmin, rmax = shoe_region(last_fields, params, voxel_mm)
    t1 = time.perf_counter()
    plan, res = mesh_graph(
        graph,
        last_fields.store,
        rmin,
        rmax,
        voxel_mm,
        workers=workers,
        watchdog=watchdog,
        min_fragment_volume_mm3=profile.min_fragment_volume_mm3,
        skip=skip,
        check_manifold3d=check_manifold3d,
    )
    t2 = time.perf_counter()
    return ShoeBuild(
        graph=graph,
        layout=layout,
        region_min=rmin,
        region_max=rmax,
        voxel_mm=float(voxel_mm),
        plan=plan,
        mesh_result=res,
        seconds={"graph": t1 - t0, "mesh": t2 - t1, "total": t2 - t0},
    )
