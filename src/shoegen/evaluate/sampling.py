"""Sampling the crossing blocks of a :class:`BlockPlan`, in process or in a worker pool.

Order and determinism (ADR 0005): blocks are submitted and yielded strictly in block
order (lexicographic (i, j, k)). Every value comes from the same numba kernel at the same
float64 coordinates, so ``workers=1`` (in process, no pool) and ``workers=4`` produce
bitwise identical arrays.

Memory bound: at most ``max_in_flight`` futures (default ``2 * workers``) exist at any
time. A finished block waits in its future until all earlier blocks have been yielded, so
the parent holds at most ``max_in_flight`` result arrays (1.07 MiB each as float32) plus
the one the consumer is using; each worker holds its compiled program (sampled arrays are
``mmap``-ed through :class:`~shoegen.graph.arrays.NpyDirectoryStore` and shared via the
page cache) and one float64 block buffer (2.1 MiB). Memory is proportional to the number
of blocks in flight, independent of the region size.

Workers: ``ProcessPoolExecutor`` with the ``spawn`` start method (the parent may run the
RSS watchdog thread, and ``fork`` with live threads is unsafe). Each worker runs
:func:`shoegen.resources.worker_parent_guard` and compiles the graph once from its
canonical JSON and the store (a compiled ``Program`` holds numba typed lists and is not
picklable). Pass an :class:`~shoegen.graph.arrays.NpyDirectoryStore` for real lasts: it
pickles as its root path. A ``MemoryArrayStore`` works too but is copied to every worker.
With ``spawn``, a script that starts a pool must guard its entry point with
``if __name__ == "__main__":`` (console-script entry points already are).
"""

from __future__ import annotations

import multiprocessing
import time
from collections import deque
from collections.abc import Iterator
from concurrent.futures import Future, ProcessPoolExecutor
from dataclasses import asdict, dataclass
from typing import Self

import numpy as np

from shoegen.evaluate.blocks import (
    BLOCK_SAMPLES,
    INSIDE,
    BlockIndex,
    BlockPlan,
    sample_block,
)
from shoegen.graph.arrays import ArrayStore
from shoegen.graph.compiler import Program, compile_graph
from shoegen.graph.model import loads_graph
from shoegen.resources import RssWatchdog, worker_parent_guard

DEFAULT_WORKERS = 4
"""Pool size; fixed, not adapted to available memory (implementation plan 1.1)."""
IN_FLIGHT_PER_WORKER = 2
SAMPLE_DTYPE = np.dtype(np.float32)
"""Dtype of yielded block samples. Marching cubes needs only the sign and a linear
interpolation weight; float32 halves memory and IPC, and matches the precision of the
sampled fields (stored as float32). The float64 -> float32 rounding is deterministic."""
BLOCK_BYTES = BLOCK_SAMPLES**3 * SAMPLE_DTYPE.itemsize


@dataclass
class EvalSummary:
    """Counts and timings of one block evaluation (``report.json`` material)."""

    voxel_mm: float
    grid_shape: tuple[int, int, int]
    total_blocks: int
    sampled_blocks: int
    skipped_inside: int
    skipped_outside: int
    skip_threshold_mm: float
    workers: int
    max_in_flight: int
    blocks_yielded: int = 0
    samples_evaluated: int = 0
    boundary_inside_blocks: int = 0
    """Blocks with an inside sample on the outer faces of the block grid (sampled, or
    skipped as inside). Non-zero means the solid is clipped by the region and a mesh
    extracted from it would be open there."""
    plan_seconds: float = 0.0
    sample_seconds: float = 0.0
    """Wall time from the first request to exhaustion, including the consumer's time."""
    wait_seconds: float = 0.0
    """Time spent producing or waiting for samples (in process: the evaluation itself)."""
    peak_rss_bytes: int | None = None
    """Peak RSS of parent + workers as seen by the watchdog, when one was given."""
    complete: bool = False

    def to_dict(self) -> dict:
        d = asdict(self)
        d["grid_shape"] = list(self.grid_shape)
        return d


# ---------------------------------------------------------------- worker side

_WORKER: dict[str, object] = {}


def _worker_init(graph_json: str, store: ArrayStore | None, graph_hash: str) -> None:
    worker_parent_guard()
    program = compile_graph(loads_graph(graph_json), store)
    if program.graph_hash != graph_hash:
        raise RuntimeError(f"worker compiled graph {program.graph_hash}, expected {graph_hash}")
    _WORKER["program"] = program


def _worker_sample(voxel_mm: float, start: tuple[int, int, int]) -> np.ndarray:
    program = _WORKER["program"]
    assert isinstance(program, Program)
    return sample_block(program, voxel_mm, start)


# ---------------------------------------------------------------- parent side


def _outer_face_inside(plan: BlockPlan, block: BlockIndex, vals: np.ndarray) -> bool:
    for axis, (b, n) in enumerate(zip(block, plan.grid_shape, strict=True)):
        if b == 0 and np.any(vals.take(0, axis=axis) < 0):
            return True
        if b == n - 1 and np.any(vals.take(-1, axis=axis) < 0):
            return True
    return False


class BlockSampleStream(Iterator[tuple[BlockIndex, np.ndarray]]):
    """Iterator of ``(block_index, float32[65, 65, 65])`` over the plan's crossing blocks.

    ``summary`` is updated as blocks are yielded and is final once the stream is
    exhausted (``summary.complete``). Use as a context manager, or call :meth:`close`, to
    shut the pool down early.
    """

    def __init__(
        self,
        plan: BlockPlan,
        workers: int,
        max_in_flight: int | None,
        watchdog: RssWatchdog | None,
    ) -> None:
        if workers < 1:
            raise ValueError(f"workers must be >= 1, got {workers}")
        inflight = max_in_flight if max_in_flight is not None else IN_FLIGHT_PER_WORKER * workers
        if inflight < 1:
            raise ValueError(f"max_in_flight must be >= 1, got {max_in_flight}")
        self.plan = plan
        self.workers = int(workers)
        self.max_in_flight = int(inflight)
        self.watchdog = watchdog
        self._blocks = plan.sampled_blocks()
        boundary_inside = sum(1 for b in plan.blocks(INSIDE) if plan.on_grid_boundary(b))
        self.summary = EvalSummary(
            voxel_mm=plan.voxel_mm,
            grid_shape=plan.grid_shape,
            total_blocks=plan.total_blocks,
            sampled_blocks=len(self._blocks),
            skipped_inside=plan.n_inside,
            skipped_outside=plan.n_outside,
            skip_threshold_mm=plan.threshold_mm,
            workers=self.workers,
            max_in_flight=self.max_in_flight,
            boundary_inside_blocks=boundary_inside,
            plan_seconds=plan.plan_seconds,
        )
        self._gen = self._run()

    def __iter__(self) -> BlockSampleStream:
        return self

    def __next__(self) -> tuple[BlockIndex, np.ndarray]:
        return next(self._gen)

    def close(self) -> None:
        self._gen.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # ------------------------------------------------------------ internals

    def _emit(self, block: BlockIndex, vals: np.ndarray) -> tuple[BlockIndex, np.ndarray]:
        s = self.summary
        s.blocks_yielded += 1
        s.samples_evaluated += vals.size
        if self.plan.on_grid_boundary(block) and _outer_face_inside(self.plan, block, vals):
            s.boundary_inside_blocks += 1
        return block, vals

    def _finish(self, t0: float) -> None:
        s = self.summary
        s.sample_seconds = time.perf_counter() - t0
        if self.watchdog is not None:
            s.peak_rss_bytes = self.watchdog.peak_bytes
        s.complete = True

    def _run(self) -> Iterator[tuple[BlockIndex, np.ndarray]]:
        plan = self.plan
        t0 = time.perf_counter()
        if self.workers == 1:
            for block in self._blocks:
                tw = time.perf_counter()
                vals = plan.sample_block(block)
                self.summary.wait_seconds += time.perf_counter() - tw
                yield self._emit(block, vals)
            self._finish(t0)
            return
        if not self._blocks:
            self._finish(t0)
            return

        executor = ProcessPoolExecutor(
            max_workers=self.workers,
            mp_context=multiprocessing.get_context("spawn"),
            initializer=_worker_init,
            initargs=(plan.graph_json, plan.store, plan.graph_hash),
        )
        try:
            pending: deque[tuple[BlockIndex, Future[np.ndarray]]] = deque()
            todo = iter(self._blocks)

            def submit() -> None:
                block = next(todo, None)
                if block is not None:
                    fut = executor.submit(_worker_sample, plan.voxel_mm, plan.block_start(block))
                    pending.append((block, fut))

            for _ in range(self.max_in_flight):
                submit()
            while pending:
                block, fut = pending.popleft()
                tw = time.perf_counter()
                vals = fut.result()
                self.summary.wait_seconds += time.perf_counter() - tw
                del fut
                submit()
                yield self._emit(block, vals)
                del vals
            self._finish(t0)
        finally:
            executor.shutdown(wait=True, cancel_futures=True)


def iter_block_samples(
    plan: BlockPlan,
    workers: int = DEFAULT_WORKERS,
    *,
    max_in_flight: int | None = None,
    watchdog: RssWatchdog | None = None,
) -> BlockSampleStream:
    """Sample every crossing block of ``plan``; yields ``(block_index, float32[65,65,65])``.

    Blocks come strictly in lexicographic block order. ``workers=1`` evaluates in process
    without a pool; any ``workers`` gives bitwise identical arrays. ``watchdog`` (an active
    :class:`~shoegen.resources.RssWatchdog`) is only read for the peak RSS in the summary.
    """
    return BlockSampleStream(plan, workers, max_in_flight, watchdog)
