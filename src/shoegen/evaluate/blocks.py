"""Global voxel lattice, 64^3 blocks and the skip rule (ADR 0005, plan M0.5).

Lattice
-------
Sample points are ``p = g * h`` for global integer indices ``g = (i, j, k)`` and voxel size
``h``. Coordinates are always computed from the global index as ``float64(g) * h`` (one
correctly rounded multiplication), never as ``origin + local * h`` or by accumulation, so a
lattice point has bitwise the same coordinates, and hence the same value, in every block,
every region and every worker. ``Program.eval_grid`` is called with ``origin = (0, 0, 0)``
and ``start = g`` for that reason (``0.0 + x == x`` exactly for any ``x != -0.0``, and
``g * h`` is never ``-0.0`` for ``h > 0``).

The region's lattice origin is its min corner floored to a multiple of ``h``:
``g0 = floor(region_min / h)`` (corrected so that ``g0 * h <= region_min``). Blocks are laid
out from ``g0``: block ``b = (bi, bj, bk)`` covers cells ``g0 + 64 b .. g0 + 64 (b + 1)`` and is
sampled at its 65^3 lattice points, so neighbouring blocks share their common face of
samples bitwise. Blocks are numbered in (i, j, k) lexicographic order (``k`` fastest),
which is also the order samples are produced and merged in.

Skip rule
---------
``d`` is evaluated at the block centre ``g0 + 64 b + 32`` (a lattice point, so it equals
sample ``[32, 32, 32]`` of the block). If ``|d| > SKIP_SAFETY_K * (half_diag + h)`` with
``half_diag = sqrt(3) * 32 h``, the block lies entirely on one side of the surface and is
not sampled; its side is recorded (``INSIDE`` if ``d < 0``, ``OUTSIDE`` otherwise).

Why this is safe for the fields of the graph (ADR 0001/0003; tested in
``tests/test_evaluate_blocks.py``):

* Analytic leaves and their min/max/offset compositions are 1-Lipschitz, so the factor
  ``k`` and the extra voxel are pure margin.
* Sampled fields (two-level grid) deviate from 1-Lipschitz in two places:

  - Fine/coarse switch and trilinear interpolation: the value differs from the true
    distance by at most the coarse sampling error ``e`` (about 1 mm). A sign change between
    centre ``c`` and a point ``p`` of the block needs ``|c - p| >= T - 2e``. The margin
    ``T - half_diag = (k - 1) half_diag + k h`` is 7.3 mm at h = 0.25 mm, so ``2e`` is
    absorbed down to ``h`` of about 0.07 mm.
  - Coarse/far switch: outside the coarse box the value is ``dist(p, bbox)``, a lower
    bound that jumps *down* by up to ``R (sqrt(3) - 1)`` at the coarse corners (about 15 mm
    for R = 20 mm, more than the margin). The margin does not cover this jump and does not
    need to: far points have ``|d| >= m`` (``m`` = distance from the source bbox to the
    coarse box boundary, 30 mm by default) with the sign of the coarse grid, so a leaf
    offset by at most ``reach < m`` cannot change sign there; and for a centre in the far
    region the lower bound only makes ``|d(c)|`` smaller (fewer skips, never a wrong one).
    The coarse box is convex, so a segment between two coarse-domain points never crosses
    the far region. :func:`check_far_field_margins` enforces ``m > reach`` for every sampled
    leaf (``GraphValidationError``, exit 3).
* A min/max composition changes sign between ``c`` and ``p`` only if some single term does
  (write it as max of mins), so the per-leaf arguments above carry over to the graph.
  ``SmoothUnion`` deviates from min by at most ``k/4``, which is part of ``reach``.

Memory: the plan holds per-block arrays only (``int8`` status and ``float64`` centre value,
9 bytes per block); nothing is ever allocated over the region's voxels.
"""

from __future__ import annotations

import math
import time
from collections.abc import Iterator
from dataclasses import dataclass, field

import numpy as np

from shoegen.graph.arrays import ArrayStore
from shoegen.graph.compiler import Program, compile_graph
from shoegen.graph.errors import GraphValidationError
from shoegen.graph.model import Graph
from shoegen.graph.nodes import HeightSurfaceField, SampledField

BLOCK_CELLS = 64
"""Cells per block edge (ADR 0005)."""
BLOCK_SAMPLES = BLOCK_CELLS + 1
"""Samples per block edge; the last face is shared with the next block."""
SKIP_SAFETY_K = 1.5
"""Safety factor ``k`` of the skip rule (ADR 0005)."""

CROSSING = np.int8(0)
"""Block status: sampled (may contain the surface)."""
INSIDE = np.int8(-1)
"""Block status: skipped, every point inside (d < 0)."""
OUTSIDE = np.int8(1)
"""Block status: skipped, every point outside (d >= 0; a sample equal to 0 is outside)."""

BlockIndex = tuple[int, int, int]


def half_diagonal(voxel_mm: float) -> float:
    """Half the diagonal of a 64^3-cell block."""
    return math.sqrt(3.0) * (BLOCK_CELLS / 2) * voxel_mm


def skip_threshold(voxel_mm: float) -> float:
    """``k * (half_diag + h)``: blocks with ``|d(centre)|`` above this are skipped."""
    return SKIP_SAFETY_K * (half_diagonal(voxel_mm) + voxel_mm)


def _floor_index(x: float, h: float) -> int:
    g = math.floor(x / h)
    while g * h > x:
        g -= 1
    while (g + 1) * h <= x:
        g += 1
    return g


def _ceil_index(x: float, h: float) -> int:
    g = math.ceil(x / h)
    while g * h < x:
        g += 1
    while (g - 1) * h >= x:
        g -= 1
    return g


def lattice_points(index: np.ndarray, voxel_mm: float) -> np.ndarray:
    """Coordinates ``float64(g) * h`` of global indices ``g`` (``(N, 3)`` int)."""
    return np.asarray(index, dtype=np.int64).astype(np.float64) * float(voxel_mm)


def far_field_margin(grid, store: ArrayStore) -> float:
    """Smallest distance from a sampled field's source bbox to its coarse-box boundary.

    Every point outside the coarse box (where the far rule ``sign * dist(p, bbox)`` is
    used) has ``|d| >= `` this value.
    """
    coarse = store.get(grid.coarse_sha256)
    m = math.inf
    for a in range(3):
        lo = grid.coarse_origin[a]
        hi = grid.coarse_origin[a] + (coarse.shape[a] - 1) * grid.coarse_h
        m = min(m, grid.bbox_min[a] - lo, hi - grid.bbox_max[a])
    return m


def check_far_field_margins(graph: Graph, store: ArrayStore | None) -> dict[str, float]:
    """Require ``far_field_margin > offset reach`` for every sampled leaf (see module doc).

    Returns ``{leaf_id: margin}``. Raises :class:`GraphValidationError` (exit 3) otherwise.
    """
    margins: dict[str, float] = {}
    for leaf, reach in graph.analysis.sampled_reach.items():
        node = graph.nodes[leaf]
        assert isinstance(node, SampledField | HeightSurfaceField)
        if store is None:
            raise GraphValidationError("graph has sampled fields but no array store", node_id=leaf)
        m = far_field_margin(node.grid, store)
        if not m > reach:
            raise GraphValidationError(
                f"coarse grid extends only {m!r} mm past the source bbox, not more than the "
                f"offset reach {reach!r} mm; block skipping could miss the surface (ADR 0005)",
                node_id=leaf,
            )
        margins[leaf] = m
    return margins


@dataclass(eq=False)
class BlockPlan:
    """Block layout of a region, with the skip decision for every block.

    ``status[bi, bj, bk]`` is :data:`CROSSING`, :data:`INSIDE` or :data:`OUTSIDE`;
    ``centre_values`` holds ``d`` at each block centre (float64, as evaluated).
    """

    graph_json: str
    graph_hash: str
    store: ArrayStore | None
    voxel_mm: float
    region_min: tuple[float, float, float]
    region_max: tuple[float, float, float]
    index_min: tuple[int, int, int]
    """Global index of sample (0, 0, 0) of block (0, 0, 0)."""
    grid_shape: tuple[int, int, int]
    """Number of blocks per axis."""
    status: np.ndarray
    centre_values: np.ndarray
    skip: bool
    threshold_mm: float
    plan_seconds: float
    far_field_margins: dict[str, float]
    _program: Program = field(repr=False)

    # ------------------------------------------------------------ geometry

    @property
    def index_max(self) -> tuple[int, int, int]:
        """Global index of the last sample of the last block (inclusive)."""
        return tuple(  # type: ignore[return-value]
            g + n * BLOCK_CELLS for g, n in zip(self.index_min, self.grid_shape, strict=True)
        )

    def block_start(self, block: BlockIndex) -> tuple[int, int, int]:
        """Global index of sample ``[0, 0, 0]`` of ``block``."""
        return tuple(  # type: ignore[return-value]
            int(g + BLOCK_CELLS * b) for g, b in zip(self.index_min, block, strict=True)
        )

    def block_number(self, block: BlockIndex) -> int:
        """Position of ``block`` in (i, j, k) lexicographic order."""
        _, ny, nz = self.grid_shape
        return (block[0] * ny + block[1]) * nz + block[2]

    def block_bounds_mm(self, block: BlockIndex) -> tuple[np.ndarray, np.ndarray]:
        """Coordinates of the first and last sample of ``block``."""
        s = np.asarray(self.block_start(block), dtype=np.int64)
        return lattice_points(s, self.voxel_mm), lattice_points(s + BLOCK_CELLS, self.voxel_mm)

    def on_grid_boundary(self, block: BlockIndex) -> bool:
        return any(b == 0 or b == n - 1 for b, n in zip(block, self.grid_shape, strict=True))

    # ------------------------------------------------------------ counts

    @property
    def total_blocks(self) -> int:
        return int(self.status.size)

    @property
    def n_crossing(self) -> int:
        return int(np.count_nonzero(self.status == CROSSING))

    @property
    def n_inside(self) -> int:
        return int(np.count_nonzero(self.status == INSIDE))

    @property
    def n_outside(self) -> int:
        return int(np.count_nonzero(self.status == OUTSIDE))

    def blocks(self, status: np.int8 | None = None) -> Iterator[BlockIndex]:
        """Block indices in lexicographic order, optionally only those with ``status``."""
        for b in np.ndindex(*self.grid_shape):  # C order = lexicographic
            if status is None or self.status[b] == status:
                yield (int(b[0]), int(b[1]), int(b[2]))

    def sampled_blocks(self) -> list[BlockIndex]:
        return list(self.blocks(CROSSING))

    # ------------------------------------------------------------ evaluation

    def sample_block(self, block: BlockIndex) -> np.ndarray:
        """The 65^3 samples of ``block``, in process (any block, skipped or not)."""
        return sample_block(self._program, self.voxel_mm, self.block_start(block))


def sample_block(program: Program, voxel_mm: float, start: tuple[int, int, int]) -> np.ndarray:
    """``float32[65, 65, 65]`` samples at global indices ``start + (a, b, c)``.

    Evaluated in float64 by the single kernel, then rounded to float32 (round to nearest
    even; sign preserved for every ``|v| >= 2**-149``, smaller values become ``±0.0``, which
    the "0 is outside" rule of ADR 0005 classifies as outside).
    """
    vals = program.eval_grid((0.0, 0.0, 0.0), voxel_mm, start, (BLOCK_SAMPLES,) * 3)
    return vals.astype(np.float32)


def plan_blocks(
    graph: Graph,
    store: ArrayStore | None,
    region_min,
    region_max,
    voxel_mm: float,
    *,
    skip: bool = True,
) -> BlockPlan:
    """Lay out blocks over ``[region_min, region_max]`` and decide which to sample.

    The region should contain the zero set with some margin; blocks extend past
    ``region_max`` to a whole number of blocks. With ``skip=False`` every block is
    sampled (reference runs for tests). Raises :class:`GraphValidationError` if a sampled
    leaf's far-field margin is too small for safe skipping.
    """
    t0 = time.perf_counter()
    h = float(voxel_mm)
    if not (math.isfinite(h) and h > 0):
        raise ValueError(f"voxel_mm must be finite and > 0, got {voxel_mm!r}")
    rmin = tuple(float(v) for v in region_min)
    rmax = tuple(float(v) for v in region_max)
    if len(rmin) != 3 or len(rmax) != 3:
        raise ValueError("region_min and region_max must have 3 components")
    if not all(math.isfinite(a) and math.isfinite(b) and a < b for a, b in zip(rmin, rmax)):
        raise ValueError(f"region must be finite with min < max, got {rmin}..{rmax}")

    margins = check_far_field_margins(graph, store)
    program = compile_graph(graph, store)

    g0 = tuple(_floor_index(v, h) for v in rmin)
    g1 = tuple(_ceil_index(v, h) for v in rmax)
    shape = tuple(max(1, -(-(b - a) // BLOCK_CELLS)) for a, b in zip(g0, g1, strict=True))

    blocks = np.indices(shape, dtype=np.int64).reshape(3, -1).T  # lexicographic
    centres = np.asarray(g0, dtype=np.int64)[None, :] + blocks * BLOCK_CELLS + BLOCK_CELLS // 2
    d = program.eval(lattice_points(centres, h)).reshape(shape)

    thr = skip_threshold(h)
    status = np.full(shape, CROSSING, dtype=np.int8)
    if skip:
        far = np.abs(d) > thr
        status[far & (d < 0)] = INSIDE
        status[far & ~(d < 0)] = OUTSIDE
    status.flags.writeable = False
    d.flags.writeable = False

    return BlockPlan(
        graph_json=graph.canonical_json(),
        graph_hash=program.graph_hash,
        store=store,
        voxel_mm=h,
        region_min=rmin,  # type: ignore[arg-type]
        region_max=rmax,  # type: ignore[arg-type]
        index_min=g0,  # type: ignore[arg-type]
        grid_shape=shape,  # type: ignore[arg-type]
        status=status,
        centre_values=d,
        skip=skip,
        threshold_mm=thr,
        plan_seconds=time.perf_counter() - t0,
        far_field_margins=margins,
        _program=program,
    )
