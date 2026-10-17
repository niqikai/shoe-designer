"""Build-time analysis of a GeometryGraph: structure, metric attribute, offset reach.

Metric attribute (ADR 0001). Each node gets, per side, ``exact`` (the value equals the
Euclidean distance to the surface, up to sampling tolerance) or ``bound`` (only a lower
bound on it; Lipschitz <= 1 still holds). Rules:

==================  =============================  =============================
node                inside (d < 0)                 outside (d > 0)
==================  =============================  =============================
analytic leaves     exact                          exact
sampled leaves      exact (in the narrow band)     exact (in the narrow band)
Extrude2D           exact                          exact
StrutLattice        bound                          bound
Union               bound                          all children outside exact
Intersect           all children inside exact      bound
Subtract(a, b)      a inside and b outside exact   bound
SmoothUnion         bound                          bound
Offset(d > 0)       bound                          child outside
Offset(d < 0)       child inside                   bound
Offset(0)           child inside                   child outside
ShellOutward(t)     bound                          child inside and outside
==================  =============================  =============================

Required (else :class:`GraphValidationError`, exit 3): ``Offset`` with ``d > 0`` and
``ShellOutward`` need the child's outside exact; ``Offset`` with ``d < 0`` needs the child's
inside exact. ``ShellOutward`` measures its thickness outward, so it only needs the outside;
its own outside is exact only if the child is exact on both sides (the cavity part of the
shell's exterior is ``-d_child``).

Offset reach (ADR 0003). The mesh is only extracted where sampled fields are fine-grid
accurate, so the total offset applied above any sampled leaf, summed along every path from
the root, must be <= ``narrow_band_mm - 1``. ``Offset`` contributes ``|d|``,
``ShellOutward`` contributes ``t`` and ``SmoothUnion`` contributes ``k / 4`` (the blended
surface lies up to ``k/4`` outside ``min(a, b) = 0``).

The same accumulated reach is recorded for every ``Extrude2D`` leaf (``extrude_reach``,
not counting the node's own ``offset``). Its 2D grid is dense, so there is no narrow-band
limit, but outside the grid the field falls back to a distance-to-bbox lower bound; the
block planner checks that the grid margin exceeds ``reach + max(offset, 0)``
(:func:`shoegen.evaluate.check_far_field_margins`, added in the M0 review).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from shoegen.graph.errors import GraphValidationError
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
    Union,
    children_of,
)

Metric = Literal["exact", "bound"]
EXACT: Metric = "exact"
BOUND: Metric = "bound"

BAND_SAFETY_MM = 1.0


@dataclass(frozen=True)
class NodeMetric:
    inside: Metric
    outside: Metric


@dataclass(frozen=True)
class GraphAnalysis:
    order: tuple[str, ...]
    """Node ids in deterministic post-order from the root (children before parents)."""
    metrics: dict[str, NodeMetric]
    sampled_reach: dict[str, float]
    """Largest accumulated offset (mm) applied above each sampled leaf, from the root."""
    extrude_reach: dict[str, float]
    """Same for each ``Extrude2D`` leaf (its own ``offset`` not included)."""


def _both(m: Metric) -> bool:
    return m == EXACT


def _topo_order(nodes: dict[str, AnyNode], root: str) -> tuple[str, ...]:
    if root not in nodes:
        raise GraphValidationError(f"root {root!r} is not a node")
    order: list[str] = []
    state: dict[str, int] = {}  # 1 = on stack, 2 = done
    # Iterative DFS so deep graphs cannot hit the recursion limit.
    stack: list[tuple[str, int]] = [(root, 0)]
    state[root] = 1
    while stack:
        nid, i = stack[-1]
        kids = children_of(nodes[nid])
        if i < len(kids):
            stack[-1] = (nid, i + 1)
            kid = kids[i]
            if kid not in nodes:
                raise GraphValidationError(f"unknown child reference {kid!r}", node_id=nid)
            s = state.get(kid, 0)
            if s == 1:
                raise GraphValidationError(f"cycle through {kid!r}", node_id=nid)
            if s == 0:
                state[kid] = 1
                stack.append((kid, 0))
        else:
            state[nid] = 2
            order.append(nid)
            stack.pop()
    unreachable = sorted(set(nodes) - set(order))
    if unreachable:
        raise GraphValidationError(f"nodes not reachable from root {root!r}: {unreachable}")
    return tuple(order)


def _metric(nid: str, node: AnyNode, m: dict[str, NodeMetric]) -> NodeMetric:
    if isinstance(node, Box | RoundedBox | Capsule | HalfSpace | SampledField | HeightSurfaceField):
        return NodeMetric(EXACT, EXACT)
    if isinstance(node, Extrude2D):
        return NodeMetric(EXACT, EXACT)
    if isinstance(node, StrutLattice):
        return NodeMetric(BOUND, BOUND)
    if isinstance(node, Union):
        out = all(_both(m[c].outside) for c in node.children)
        return NodeMetric(BOUND, EXACT if out else BOUND)
    if isinstance(node, Intersect):
        ins = all(_both(m[c].inside) for c in node.children)
        return NodeMetric(EXACT if ins else BOUND, BOUND)
    if isinstance(node, Subtract):
        ins = _both(m[node.base].inside) and _both(m[node.tool].outside)
        return NodeMetric(EXACT if ins else BOUND, BOUND)
    if isinstance(node, SmoothUnion):
        return NodeMetric(BOUND, BOUND)
    if isinstance(node, Offset):
        child = m[node.child]
        if node.distance > 0:
            if not _both(child.outside):
                raise GraphValidationError(
                    f"outward Offset({node.distance!r}) needs child {node.child!r} to be "
                    f"exact outside, but it is {child.outside!r} (ADR 0001)",
                    node_id=nid,
                )
            return NodeMetric(BOUND, EXACT)
        if node.distance < 0:
            if not _both(child.inside):
                raise GraphValidationError(
                    f"inward Offset({node.distance!r}) needs child {node.child!r} to be "
                    f"exact inside, but it is {child.inside!r} (ADR 0001)",
                    node_id=nid,
                )
            return NodeMetric(EXACT, BOUND)
        return child
    if isinstance(node, ShellOutward):
        child = m[node.child]
        if not _both(child.outside):
            raise GraphValidationError(
                f"ShellOutward({node.thickness!r}) needs child {node.child!r} to be exact "
                f"outside, but it is {child.outside!r} (ADR 0001)",
                node_id=nid,
            )
        out = _both(child.inside) and _both(child.outside)
        return NodeMetric(BOUND, EXACT if out else BOUND)
    raise AssertionError(f"unhandled node type {type(node).__name__}")  # pragma: no cover


def _own_reach(node: AnyNode) -> float:
    if isinstance(node, Offset):
        return abs(node.distance)
    if isinstance(node, ShellOutward):
        return node.thickness
    if isinstance(node, SmoothUnion):
        return node.k / 4.0
    return 0.0


def analyze(nodes: dict[str, AnyNode], root: str) -> GraphAnalysis:
    """Validate structure, metric rules and offset reach; raise GraphValidationError."""
    order = _topo_order(nodes, root)
    metrics: dict[str, NodeMetric] = {}
    reach: dict[str, dict[str, float]] = {}
    for nid in order:
        node = nodes[nid]
        metrics[nid] = _metric(nid, node, metrics)
        if isinstance(node, SampledField | HeightSurfaceField | Extrude2D):
            reach[nid] = {nid: 0.0}
            continue
        own = _own_reach(node)
        acc: dict[str, float] = {}
        for kid in children_of(node):
            for leaf, r in reach[kid].items():
                acc[leaf] = max(acc.get(leaf, 0.0), r + own)
        reach[nid] = acc
    sampled_reach = {
        k: v for k, v in sorted(reach[root].items()) if not isinstance(nodes[k], Extrude2D)
    }
    extrude_reach = {
        k: v for k, v in sorted(reach[root].items()) if isinstance(nodes[k], Extrude2D)
    }
    for leaf, r in sampled_reach.items():
        node = nodes[leaf]
        assert isinstance(node, SampledField | HeightSurfaceField)
        limit = node.grid.narrow_band_mm - BAND_SAFETY_MM
        if r > limit:
            raise GraphValidationError(
                f"total offset {r!r} mm applied to sampled field exceeds narrow band "
                f"{node.grid.narrow_band_mm!r} mm - {BAND_SAFETY_MM} mm = {limit!r} mm "
                "(ADR 0003)",
                node_id=leaf,
            )
    return GraphAnalysis(
        order=order, metrics=metrics, sampled_reach=sampled_reach, extrude_reach=extrude_reach
    )
