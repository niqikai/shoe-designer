"""The GeometryGraph document: nodes by id, a root, canonical JSON and ``graph_hash``.

A ``Graph`` is validated when it is constructed (structure, metric attribute and offset
reach, see :mod:`shoegen.graph.analysis`); an invalid graph raises
:class:`GraphValidationError` (exit code 3) and never exists as an object.

JSON document (``graph.json``)::

    {"graph_version": 1, "nodes": {"<id>": {"type": "...", ...}, ...}, "root": "<id>"}

``graph_hash`` is the sha256 of the compact canonical JSON (sorted keys, so nodes are
ordered by id; floats via ``repr``). Node ids are part of the hash.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, PrivateAttr, ValidationError, model_validator

from shoegen.graph.analysis import GraphAnalysis, NodeMetric, analyze
from shoegen.graph.canonical import canonical_dumps
from shoegen.graph.errors import GraphValidationError
from shoegen.graph.nodes import AnyNode, Node, NodeId, children_of

GRAPH_VERSION = 1


class Graph(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    graph_version: Literal[1] = GRAPH_VERSION
    root: NodeId
    nodes: dict[NodeId, Node]

    _analysis: GraphAnalysis = PrivateAttr()

    @model_validator(mode="after")
    def _validate_graph(self) -> Graph:
        self._analysis = analyze(self.nodes, self.root)
        return self

    # ------------------------------------------------------------ queries

    @property
    def analysis(self) -> GraphAnalysis:
        return self._analysis

    @property
    def order(self) -> tuple[str, ...]:
        """Node ids, children before parents, root last."""
        return self._analysis.order

    def metric(self, node_id: str) -> NodeMetric:
        return self._analysis.metrics[node_id]

    # ------------------------------------------------------------ serialization

    def to_canonical_obj(self) -> dict[str, Any]:
        return self.model_dump(mode="python")

    def canonical_json(self, *, indent: int | None = None) -> str:
        """Compact canonical JSON (the hashed form) or, with ``indent``, a readable one."""
        return canonical_dumps(self.to_canonical_obj(), indent=indent)

    def graph_hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("ascii")).hexdigest()

    def subgraph(self, root: str) -> Graph:
        """The graph of everything reachable from ``root`` (a valid graph on its own)."""
        if root not in self.nodes:
            raise GraphValidationError(f"unknown node {root!r}")
        keep: set[str] = set()
        todo = [root]
        while todo:
            nid = todo.pop()
            if nid in keep:
                continue
            keep.add(nid)
            todo.extend(children_of(self.nodes[nid]))
        return Graph(root=root, nodes={k: v for k, v in self.nodes.items() if k in keep})


def graph_hash(graph: Graph) -> str:
    return graph.graph_hash()


def _wrap(exc: ValidationError) -> GraphValidationError:
    return GraphValidationError(f"invalid graph document: {exc}")


def graph_from_obj(obj: Any) -> Graph:
    """Build a graph from parsed JSON; any problem raises GraphValidationError (exit 3)."""
    try:
        return Graph.model_validate(obj)
    except ValidationError as exc:
        raise _wrap(exc) from exc


def loads_graph(text: str) -> Graph:
    # json.loads uses Python's correctly rounded float parser, so repr floats round-trip.
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        raise GraphValidationError(f"graph is not valid JSON: {exc}") from exc
    return graph_from_obj(obj)


def load_graph(path: str | Path) -> Graph:
    return loads_graph(Path(path).read_text(encoding="utf-8"))


def dump_graph(graph: Graph, path: str | Path, *, indent: int | None = 2) -> None:
    """Write ``graph.json`` (canonical, sorted keys, no timestamps; ADR 0005)."""
    Path(path).write_text(graph.canonical_json(indent=indent) + "\n", encoding="ascii")


class GraphBuilder:
    """Convenience for building graphs in Python.

    >>> b = GraphBuilder()
    >>> box = b.add(Box(center=(0, 0, 0), half_size=(1, 1, 1)))
    >>> g = b.build(box)

    Ids default to ``n0``, ``n1``, ... in insertion order, so the same build code always
    yields the same ids and hence the same hash.
    """

    def __init__(self) -> None:
        self._nodes: dict[str, AnyNode] = {}
        self._counter = 0

    def add(self, node: AnyNode, node_id: str | None = None) -> str:
        if node_id is None:
            while f"n{self._counter}" in self._nodes:
                self._counter += 1
            node_id = f"n{self._counter}"
            self._counter += 1
        if node_id in self._nodes:
            raise GraphValidationError(f"duplicate node id {node_id!r}")
        self._nodes[node_id] = node
        return node_id

    def build(self, root: str, *, prune: bool = False) -> Graph:
        """Build and validate. ``prune=True`` drops nodes not reachable from ``root``."""
        nodes = dict(self._nodes)
        if prune:
            keep: set[str] = set()
            todo = [root]
            while todo:
                nid = todo.pop()
                if nid in keep or nid not in nodes:
                    continue
                keep.add(nid)
                todo.extend(children_of(nodes[nid]))
            nodes = {k: v for k, v in nodes.items() if k in keep}
        try:
            return Graph(root=root, nodes=nodes)
        except ValidationError as exc:
            raise _wrap(exc) from exc
