"""Voxel-grid soft checks: thin walls and enclosed cavities, block by block.

Both checks work on the sample lattice of the :class:`~shoegen.evaluate.BlockPlan`
(sample ``p = g * h`` is *inside* iff ``d < 0``, as for marching cubes; each sample owns a
volume ``h**3``). Pass 1 (:func:`collect_block_masks`) runs in the sampling workers and
keeps, per crossing block, only the bit-packed inside mask of its 64^3 *owned* samples
(indices 0..63; index 64 belongs to the next block): 32 KiB per block. Skipped blocks are
uniform and cost nothing. Nothing is ever allocated over the whole region; the largest
temporary is one padded block, ``(64 + 2 H)^3`` voxels.

Thin walls (local thickness)
----------------------------
Method: a maximal-inscribed-ball proxy on the sample lattice, per block with a halo.

1. ``D`` = Euclidean distance (scipy EDT, in samples) from each inside sample to the
   nearest outside sample; the radius of the ball inscribed at that sample is
   ``rho = (D - 0.5) h`` (half a voxel less, because samples sit on both sides of the
   surface).
2. *Centres* are samples with ``rho >= r`` where ``r = min_wall_mm / 2``.
3. An inside sample is *thick* if a centre lies within ``sqrt(3) r + h`` of it, else
   *thin*. (The exact morphological opening would flag the tips of every convex corner and
   edge of a thick solid, since no ball of radius ``r`` contains them; ``sqrt(3) r``
   is the distance from a cube corner to the nearest centre, so thick solids are clean.)
4. Thin volume = thin samples x ``h**3``.

Accuracy: for plates and struts, thickness is resolved to about one voxel ``h`` (walls
thinner than ``min_wall - 2h`` are flagged, walls thicker than ``min_wall + 2h`` are not;
in between depends on lattice alignment). It under-reports by up to ``(sqrt(3) - 1) r``
of length where a thin feature joins a thick body (the centres of the body still cover
the junction), and it ignores everything inside blocks that were skipped as INSIDE (they
are at least ``1.5 (55.4 h + h)`` from the surface, which exceeds ``r`` unless
``h < min_wall / 170``; :func:`thin_wall_check` refuses such a configuration).

Halo: a sample at the block face needs outside samples within ``r`` for its own ``D`` and
centres within ``sqrt(3) r + h`` whose ``D`` needs another ``r``. The halo is
``H = ceil(1.5 * min_wall / h) + 3`` samples taken from the neighbouring blocks' masks
(or their uniform status); with ``H <= 64`` only the 26 face/edge/corner neighbours matter.
Samples beyond the block grid count as outside.

Enclosed cavities
-----------------
Outside samples (6-connectivity) are labelled per block (``scipy.ndimage.label``); labels
of adjacent blocks are united where a block's last owned plane touches the next block's
first plane (union-find over ``(block, label)``). Uniform OUTSIDE blocks are one label,
INSIDE blocks have none. A component that contains a sample on the outer faces of the block
grid is exterior; the volume of all others is the unreachable void volume. Connectivity is
decided on the sample lattice, so a channel narrower than about ``h`` counts as closed.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage

from shoegen.checks.records import NOT_RUN, REPORT_ONLY, record, source
from shoegen.evaluate import (
    BLOCK_CELLS,
    CROSSING,
    DEFAULT_WORKERS,
    INSIDE,
    OUTSIDE,
    BlockIndex,
    BlockPlan,
    iter_block_results,
)
from shoegen.profile import ManufacturingProfile
from shoegen.resources import RssWatchdog

N = BLOCK_CELLS


def _owned_mask_fn(vals: np.ndarray, start: tuple[int, int, int]) -> np.ndarray:
    """Block function (runs in the worker): packed inside mask of the owned 64^3 samples."""
    return np.packbits(vals[:N, :N, :N] < 0)


@dataclass
class BlockMasks:
    """Per-block inside masks of a plan (pass 1)."""

    plan: BlockPlan
    packed: dict[BlockIndex, np.ndarray]
    eval_summary: dict
    seconds: float = 0.0
    _all_in: np.ndarray = field(default_factory=lambda: np.ones((N,) * 3, dtype=bool), repr=False)
    _all_out: np.ndarray = field(default_factory=lambda: np.zeros((N,) * 3, dtype=bool), repr=False)

    def owned(self, block: BlockIndex) -> np.ndarray:
        """Bool ``[64, 64, 64]`` inside mask of the owned samples (do not modify)."""
        st = self.plan.status[block]
        if st == INSIDE:
            return self._all_in
        if st == OUTSIDE:
            return self._all_out
        return np.unpackbits(self.packed[block], count=N**3).astype(bool).reshape((N,) * 3)

    def in_grid(self, block: tuple[int, int, int]) -> bool:
        return all(0 <= b < n for b, n in zip(block, self.plan.grid_shape, strict=True))


def collect_block_masks(
    plan: BlockPlan, workers: int = DEFAULT_WORKERS, watchdog: RssWatchdog | None = None
) -> BlockMasks:
    """Pass 1: sample every crossing block once and keep its packed inside mask."""
    t0 = time.perf_counter()
    packed: dict[BlockIndex, np.ndarray] = {}
    with iter_block_results(plan, _owned_mask_fn, (), workers, watchdog=watchdog) as stream:
        for block, bits in stream:
            packed[block] = bits
    return BlockMasks(plan, packed, stream.summary.to_dict(), time.perf_counter() - t0)


# ---------------------------------------------------------------- thin walls


def halo_cells(min_wall_mm: float, voxel_mm: float) -> int:
    return math.ceil(1.5 * min_wall_mm / voxel_mm) + 3


def _padded_mask(masks: BlockMasks, block: BlockIndex, halo: int) -> np.ndarray:
    n = N + 2 * halo
    out = np.zeros((n, n, n), dtype=bool)
    # per axis offset: (neighbour step, source slice in neighbour, destination slice)
    spans = {
        -1: (slice(N - halo, N), slice(0, halo)),
        0: (slice(0, N), slice(halo, halo + N)),
        1: (slice(0, halo), slice(halo + N, n)),
    }
    for ox in (-1, 0, 1):
        for oy in (-1, 0, 1):
            for oz in (-1, 0, 1):
                nb = (block[0] + ox, block[1] + oy, block[2] + oz)
                if not masks.in_grid(nb):
                    continue
                st = masks.plan.status[nb]
                if st == OUTSIDE:
                    continue
                sx, dx = spans[ox]
                sy, dy = spans[oy]
                sz, dz = spans[oz]
                out[dx, dy, dz] = masks.owned(nb)[sx, sy, sz]
    return out


def thin_wall_check(masks: BlockMasks, min_wall_mm: float) -> dict:
    """Thin material (local thickness < ``min_wall_mm``) per the method in the module doc.

    Returns ``thin_volume_mm3``, ``thin_samples``, ``affected_blocks``,
    ``blocks_examined`` and the method parameters.
    """
    plan = masks.plan
    h = plan.voxel_mm
    r = 0.5 * min_wall_mm
    halo = halo_cells(min_wall_mm, h)
    if halo > N:
        raise ValueError(f"min_wall_mm {min_wall_mm} needs a halo of {halo} > {N} samples")
    if plan.skip and plan.n_inside and r >= plan.threshold_mm:
        raise ValueError(
            f"voxel {h} mm is too small for min_wall_mm {min_wall_mm}: blocks skipped as "
            "INSIDE could contain thin material"
        )
    centre_depth = r / h + 0.5 - 1e-9  # D (samples) of a centre
    cover = (math.sqrt(3.0) * r + h) / h  # samples
    thin_samples = 0
    affected = 0
    examined = 0
    for block in plan.blocks(CROSSING):
        own = masks.owned(block)
        if not own.any():
            continue
        examined += 1
        pad = _padded_mask(masks, block, halo)
        if pad.all():
            continue
        dist = ndimage.distance_transform_edt(pad)
        centres = dist >= centre_depth
        if centres.any():
            near = ndimage.distance_transform_edt(~centres)
            thin = pad & (near > cover)
        else:
            thin = pad
        n_thin = int(np.count_nonzero(thin[halo : halo + N, halo : halo + N, halo : halo + N]))
        if n_thin:
            thin_samples += n_thin
            affected += 1
    return {
        "thin_volume_mm3": thin_samples * h**3,
        "thin_samples": thin_samples,
        "affected_blocks": affected,
        "blocks_examined": examined,
        "voxel_mm": h,
        "halo_samples": halo,
        "method": "max-inscribed-ball proxy on the sample lattice (EDT), per block with halo",
        "resolution_mm": h,
    }


# ---------------------------------------------------------------- cavities


class _UnionFind:
    def __init__(self) -> None:
        self.parent: list[int] = []
        self.size: list[int] = []
        self.exterior: list[bool] = []

    def add(self, size: int) -> int:
        self.parent.append(len(self.parent))
        self.size.append(size)
        self.exterior.append(False)
        return len(self.parent) - 1

    def find(self, a: int) -> int:
        p = self.parent
        while p[a] != a:
            p[a] = p[p[a]]
            a = p[a]
        return a

    def union(self, a: int, b: int) -> None:
        a, b = self.find(a), self.find(b)
        if a == b:
            return
        if a > b:
            a, b = b, a
        self.parent[b] = a
        self.size[a] += self.size[b]
        self.exterior[a] = self.exterior[a] or self.exterior[b]


_NONE_FACE = -1


def cavity_check(masks: BlockMasks, *, max_listed: int = 10) -> dict:
    """Outside space not connected to the region boundary (see module doc)."""
    plan = masks.plan
    h = plan.voxel_mm
    shape = plan.grid_shape
    uf = _UnionFind()
    # faces[block] = 6 arrays/scalars of component ids: lo0, lo1, lo2, hi0, hi1, hi2
    faces: dict[BlockIndex, list] = {}
    for block in plan.blocks():
        st = plan.status[block]
        if st == INSIDE:
            continue
        if st == OUTSIDE:
            cid = uf.add(N**3)
            faces[block] = [cid] * 6
            continue
        out = ~masks.owned(block)
        if not out.any():
            continue
        lab, n = ndimage.label(out)
        counts = np.bincount(lab.ravel(), minlength=n + 1)
        idmap = np.full(n + 1, _NONE_FACE, dtype=np.int64)
        for k in range(1, n + 1):
            idmap[k] = uf.add(int(counts[k]))
        ids = idmap[lab]
        faces[block] = [
            ids[0, :, :],
            ids[:, 0, :],
            ids[:, :, 0],
            ids[-1, :, :],
            ids[:, -1, :],
            ids[:, :, -1],
        ]

    def face(block: BlockIndex, idx: int) -> np.ndarray | None:
        f = faces.get(block)
        if f is None:
            return None
        v = f[idx]
        return v if isinstance(v, np.ndarray) else np.full((N, N), v, dtype=np.int64)

    for block in faces:
        for axis in range(3):
            # exterior: outer faces of the block grid
            for side, edge in ((0, 0), (3, shape[axis] - 1)):
                if block[axis] == edge:
                    arr = face(block, axis + side)
                    for cid in np.unique(arr[arr >= 0]):
                        uf.exterior[uf.find(int(cid))] = True
            nb = list(block)
            nb[axis] += 1
            nb = tuple(nb)
            if nb[axis] >= shape[axis]:
                continue
            a = face(block, 3 + axis)
            b = face(nb, axis)
            if a is None or b is None:
                continue
            both = (a >= 0) & (b >= 0)
            if not both.any():
                continue
            pairs = np.unique(np.stack([a[both], b[both]], axis=1), axis=0)
            for x, y in pairs:
                uf.union(int(x), int(y))

    roots: dict[int, int] = {}
    for i in range(len(uf.parent)):
        if uf.find(i) == i:
            roots[i] = uf.size[i]
    enclosed = sorted((s for r_, s in roots.items() if not uf.exterior[r_]), reverse=True)
    return {
        "unreachable_void_volume_mm3": sum(enclosed) * h**3,
        "unreachable_void_samples": sum(enclosed),
        "cavity_count": len(enclosed),
        "largest_cavity_volumes_mm3": [s * h**3 for s in enclosed[:max_listed]],
        "voxel_mm": h,
        "method": "6-connected flood fill of outside samples, union-find across block faces",
    }


# ---------------------------------------------------------------- records


def run_grid_checks(
    plan: BlockPlan,
    profile: ManufacturingProfile,
    *,
    workers: int = DEFAULT_WORKERS,
    watchdog: RssWatchdog | None = None,
) -> list[dict]:
    """Thin-wall and enclosed-cavity records (soft: ``report_only``).

    Re-samples the crossing blocks once (the mesh stage does not keep its samples).
    An error inside a check yields a ``not_run`` record with the message, never a pass.
    """
    t0 = time.perf_counter()
    masks = collect_block_masks(plan, workers, watchdog)
    t_masks = time.perf_counter() - t0
    out: list[dict] = []

    t1 = time.perf_counter()
    try:
        thin = thin_wall_check(masks, profile.min_wall_mm)
        out.append(
            record(
                "thin_walls",
                "soft",
                REPORT_ONLY,
                measured=thin,
                threshold={"min_wall_mm": profile.min_wall_mm},
                src=source(profile, "min_wall_mm"),
                finding=thin["thin_samples"] > 0,
                seconds=time.perf_counter() - t1,
            )
        )
    except ValueError as exc:
        out.append(
            record(
                "thin_walls",
                "soft",
                NOT_RUN,
                measured={},
                threshold={"min_wall_mm": profile.min_wall_mm},
                src=source(profile, "min_wall_mm"),
                detail=str(exc),
                finding=False,
            )
        )

    t2 = time.perf_counter()
    cav = cavity_check(masks)
    out.append(
        record(
            "enclosed_cavities",
            "soft",
            REPORT_ONLY,
            measured=cav,
            threshold={"max_unreachable_void_mm3": 0.0},
            src=source(profile, None),
            finding=cav["cavity_count"] > 0,
            seconds=time.perf_counter() - t2,
            mask_pass_seconds=t_masks,
            requires_drain_holes=profile.requires_drain_holes,
        )
    )
    return out
