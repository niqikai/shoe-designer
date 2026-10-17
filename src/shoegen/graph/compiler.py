"""Compile a validated Graph into the flat program run by :mod:`shoegen.graph.kernels`.

Instructions follow ``Graph.order`` (deterministic post-order from the root), one per
node, so a node shared by several parents is evaluated once per point. Sampled arrays are
fetched from an :class:`~shoegen.graph.arrays.ArrayStore`, re-hashed and checked against
the sha256 recorded in the graph; any mismatch raises :class:`SampledDataError` (exit 3).

A ``Program`` holds numba typed lists and is not meant to be pickled: pool workers should
receive the graph JSON and the store location and compile locally.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from numba.typed import List

from shoegen.graph import kernels as K
from shoegen.graph.arrays import ArrayStore, array_sha256
from shoegen.graph.errors import SampledDataError
from shoegen.graph.model import Graph
from shoegen.graph.nodes import (
    AnyNode,
    Box,
    Capsule,
    Extrude2D,
    HalfSpace,
    HeightSurfaceField,
    Intersect,
    Offset,
    RoundedBox,
    SampledField,
    ShellOutward,
    SmoothUnion,
    StrutLattice,
    Subtract,
    TwoLevelGrid,
    Union,
)


@dataclass
class Program:
    code: np.ndarray
    params: np.ndarray
    args: np.ndarray
    f32: List
    i32: List
    node_ids: tuple[str, ...]
    """``node_ids[i]`` is the node computed by instruction ``i``; the last is the root."""
    graph_hash: str
    array_shas: tuple[str, ...] = field(default=())

    def eval(self, points: np.ndarray) -> np.ndarray:
        """Signed distance at ``points`` (``(N, 3)``), float64."""
        pts = np.ascontiguousarray(points, dtype=np.float64)
        if pts.ndim != 2 or pts.shape[1] != 3:
            raise ValueError(f"points must have shape (N, 3), got {pts.shape}")
        if not np.all(np.isfinite(pts)):
            raise ValueError("points must be finite")
        out = np.empty(pts.shape[0], dtype=np.float64)
        K.eval_points(self.code, self.params, self.args, self.f32, self.i32, pts, out)
        return out

    def eval_grid(
        self,
        origin: tuple[float, float, float],
        h: float,
        start: tuple[int, int, int],
        shape: tuple[int, int, int],
    ) -> np.ndarray:
        """Values at ``origin + (start + (a, b, c)) * h`` for ``(a, b, c)`` in ``shape``."""
        o = np.ascontiguousarray(origin, dtype=np.float64)
        st = np.ascontiguousarray(start, dtype=np.int64)
        if o.shape != (3,) or st.shape != (3,) or len(shape) != 3:
            raise ValueError("origin, start and shape must have 3 components")
        if not (np.all(np.isfinite(o)) and math.isfinite(h) and h > 0):
            raise ValueError("origin and h must be finite, h > 0")
        out = np.empty(tuple(int(s) for s in shape), dtype=np.float64)
        K.eval_grid(self.code, self.params, self.args, self.f32, self.i32, o, float(h), st, out)
        return out


class _Packer:
    def __init__(self, store: ArrayStore | None) -> None:
        self.store = store
        self.params: list[float] = []
        self.args: list[int] = []
        self.f32 = K.empty_f32_list()
        self.i32 = K.empty_i32_list()
        self._slots: dict[tuple[str, str], int] = {}
        self.shas: list[str] = []

    def array(self, sha: str, dtype: np.dtype, ndim: int, node_id: str) -> tuple[int, np.ndarray]:
        key = (sha, np.dtype(dtype).str)
        if self.store is None:
            raise SampledDataError("graph has sampled fields but no array store", node_id=node_id)
        arr = self.store.get(sha)
        arr = np.asarray(arr).view(np.ndarray)
        if arr.dtype != np.dtype(dtype) or arr.ndim != ndim:
            raise SampledDataError(
                f"array {sha} must be {np.dtype(dtype)} with {ndim} dims, "
                f"got {arr.dtype} with shape {arr.shape}",
                node_id=node_id,
            )
        if not arr.flags.c_contiguous:
            raise SampledDataError(f"array {sha} is not C-contiguous", node_id=node_id)
        if key in self._slots:
            return self._slots[key], arr
        actual = array_sha256(arr)
        if actual != sha:
            raise SampledDataError(
                f"array content hash {actual} does not match graph reference {sha}",
                node_id=node_id,
            )
        flat = arr.reshape(-1).view()
        flat.flags.writeable = False
        if arr.dtype == np.float32:
            slot = len(self.f32)
            self.f32.append(flat)
        else:
            slot = len(self.i32)
            self.i32.append(flat)
        self._slots[key] = slot
        self.shas.append(sha)
        return slot, arr


def _pack_two_level(g: TwoLevelGrid, pk: _Packer, nid: str) -> list[int]:
    fine_slot, fine = pk.array(g.fine_bricks_sha256, np.float32, 4, nid)
    idx_slot, idx = pk.array(g.fine_index_sha256, np.int32, 3, nid)
    coarse_slot, coarse = pk.array(g.coarse_sha256, np.float32, 3, nid)
    s1 = g.brick_size + 1
    if fine.shape[1:] != (s1, s1, s1):
        raise SampledDataError(
            f"fine_bricks shape {fine.shape} does not match brick_size {g.brick_size}",
            node_id=nid,
        )
    if idx.size == 0 or idx.min() < -1 or idx.max() >= fine.shape[0]:
        raise SampledDataError("fine_index entries must be -1 or a fine_bricks row", node_id=nid)
    if min(coarse.shape) < 2:
        raise SampledDataError(f"coarse grid shape {coarse.shape} too small", node_id=nid)
    pk.params.extend(
        [*g.fine_origin, g.fine_h, *g.coarse_origin, g.coarse_h, *g.bbox_min, *g.bbox_max]
    )
    return [fine_slot, idx_slot, coarse_slot, *idx.shape, g.brick_size, *coarse.shape]


def _pack(node: AnyNode, nid: str, reg: dict[str, int], pk: _Packer) -> tuple[int, list[int]]:
    """Append the node's params; return (opcode, args)."""
    p = pk.params
    if isinstance(node, Box):
        p.extend([*node.center, *node.half_size])
        return K.OP_BOX, []
    if isinstance(node, RoundedBox):
        p.extend([*node.center, *node.half_size, node.radius])
        return K.OP_ROUNDED_BOX, []
    if isinstance(node, Capsule):
        p.extend([*node.a, *node.b, node.radius])
        return K.OP_CAPSULE, []
    if isinstance(node, HalfSpace):
        n = np.asarray(node.normal, dtype=np.float64)
        n = n / math.sqrt(float(n @ n))
        p.extend([float(n[0]), float(n[1]), float(n[2]), node.offset])
        return K.OP_HALFSPACE, []
    if isinstance(node, SampledField | HeightSurfaceField):
        return K.OP_SAMPLED3D, _pack_two_level(node.grid, pk, nid)
    if isinstance(node, Extrude2D):
        f = node.field
        slot, vals = pk.array(f.values_sha256, np.float32, 2, nid)
        if min(vals.shape) < 2:
            raise SampledDataError(f"2D grid shape {vals.shape} too small", node_id=nid)
        zc = 0.5 * (node.z_min + node.z_max)
        hz = 0.5 * (node.z_max - node.z_min)
        p.extend([*f.origin, f.h, *f.bbox_min, *f.bbox_max, zc, hz, node.offset])
        return K.OP_EXTRUDE2D, [slot, *vals.shape]
    if isinstance(node, StrutLattice):
        raise NotImplementedError("StrutLattice evaluation is implemented in M0.7")
    if isinstance(node, Union):
        return K.OP_UNION, [reg[c] for c in node.children]
    if isinstance(node, Intersect):
        return K.OP_INTERSECT, [reg[c] for c in node.children]
    if isinstance(node, Subtract):
        return K.OP_SUBTRACT, [reg[node.base], reg[node.tool]]
    if isinstance(node, SmoothUnion):
        p.append(node.k)
        return K.OP_SMOOTH_UNION, [reg[c] for c in node.children]
    if isinstance(node, Offset):
        p.append(node.distance)
        return K.OP_OFFSET, [reg[node.child]]
    if isinstance(node, ShellOutward):
        p.append(node.thickness)
        return K.OP_SHELL_OUTWARD, [reg[node.child]]
    raise AssertionError(f"unhandled node type {type(node).__name__}")  # pragma: no cover


def compile_graph(
    graph: Graph, store: ArrayStore | None = None, *, root: str | None = None
) -> Program:
    """Compile ``graph`` (or the subgraph under ``root``) into a :class:`Program`."""
    g = graph if root is None or root == graph.root else graph.subgraph(root)
    pk = _Packer(store)
    reg: dict[str, int] = {}
    rows: list[tuple[int, int, int, int]] = []
    for i, nid in enumerate(g.order):
        po = len(pk.params)
        op, args = _pack(g.nodes[nid], nid, reg, pk)
        ao = len(pk.args)
        pk.args.extend(int(a) for a in args)
        rows.append((op, po, ao, len(args)))
        reg[nid] = i
    code = np.array(rows, dtype=np.int64).reshape(-1, 4)
    params = np.array(pk.params, dtype=np.float64)
    if params.size == 0:
        params = np.zeros(1, dtype=np.float64)
    args_arr = np.array(pk.args, dtype=np.int64)
    if args_arr.size == 0:
        args_arr = np.zeros(1, dtype=np.int64)
    return Program(
        code=code,
        params=params,
        args=args_arr,
        f32=pk.f32,
        i32=pk.i32,
        node_ids=g.order,
        graph_hash=g.graph_hash(),
        array_shas=tuple(pk.shas),
    )


def evaluate(graph: Graph, points: np.ndarray, store: ArrayStore | None = None) -> np.ndarray:
    """One-shot convenience: compile and evaluate."""
    return compile_graph(graph, store).eval(points)
