"""Block-sparse evaluation of a GeometryGraph (ADR 0005, plan M0.5).

Typical use::

    from shoegen.evaluate import plan_blocks, iter_block_samples

    plan = plan_blocks(graph, store, region_min, region_max, voxel_mm=0.25)
    with iter_block_samples(plan, workers=4) as stream:
        for block, vals in stream:      # block order; vals: float32[65, 65, 65]
            start = plan.block_start(block)   # global index of vals[0, 0, 0]
            ...                               # vals[a, b, c] = d((start + (a, b, c)) * h)
    stream.summary.to_dict()            # counts, timings, peak RSS

Skipped blocks are not yielded; ``plan.status`` records whether each is inside or
outside. Modules: ``blocks`` (lattice, layout, skip rule), ``sampling`` (in-process and
pooled sampling, summary).
"""

from shoegen.evaluate.blocks import (
    BLOCK_CELLS,
    BLOCK_SAMPLES,
    CROSSING,
    INSIDE,
    OUTSIDE,
    SKIP_SAFETY_K,
    BlockIndex,
    BlockPlan,
    check_far_field_margins,
    far_field_margin,
    half_diagonal,
    lattice_points,
    plan_blocks,
    sample_block,
    skip_threshold,
)
from shoegen.evaluate.sampling import (
    DEFAULT_WORKERS,
    SAMPLE_DTYPE,
    BlockSampleStream,
    EvalSummary,
    iter_block_samples,
)

__all__ = [
    "BLOCK_CELLS",
    "BLOCK_SAMPLES",
    "CROSSING",
    "DEFAULT_WORKERS",
    "INSIDE",
    "OUTSIDE",
    "SAMPLE_DTYPE",
    "SKIP_SAFETY_K",
    "BlockIndex",
    "BlockPlan",
    "BlockSampleStream",
    "EvalSummary",
    "check_far_field_margins",
    "far_field_margin",
    "half_diagonal",
    "iter_block_samples",
    "lattice_points",
    "plan_blocks",
    "sample_block",
    "skip_threshold",
]
