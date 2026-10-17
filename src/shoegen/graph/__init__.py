"""GeometryGraph: the SDF scene graph that is also the BuildPlan (ADR 0001, plan M0.4).

Typical use::

    from shoegen.graph import GraphBuilder, Box, HalfSpace, Intersect, Offset, compile_graph

    b = GraphBuilder()
    box = b.add(Box(center=(0, 0, 0), half_size=(10, 10, 10)), "box")
    cut = b.add(HalfSpace(normal=(0, 0, 1), offset=5.0), "cut")
    body = b.add(Intersect(children=[box, cut]), "body")
    root = b.add(Offset(child=body, distance=-1.0), "shrunk")
    graph = b.build(root)            # validates; GraphValidationError (exit 3) on failure
    graph.graph_hash()               # sha256 of canonical JSON
    graph.metric("shrunk")           # NodeMetric(inside='exact', outside='bound')
    d = compile_graph(graph).eval(points)   # (N, 3) -> (N,), single numba kernel

Modules: ``nodes`` (whitelist models), ``model`` (Graph, JSON, hash, builder),
``analysis`` (metric attribute and build-time checks), ``sampled`` (sampled-field data
layout and producers), ``arrays`` (content-addressed array stores), ``kernels`` (numba
interpreter), ``compiler`` (Graph -> Program).
"""

from shoegen.graph.analysis import BOUND, EXACT, GraphAnalysis, NodeMetric
from shoegen.graph.arrays import ArrayStore, MemoryArrayStore, NpyDirectoryStore, array_sha256
from shoegen.graph.compiler import Program, compile_graph, evaluate
from shoegen.graph.errors import EXIT_GRAPH_INVALID, GraphValidationError, SampledDataError
from shoegen.graph.model import (
    GRAPH_VERSION,
    Graph,
    GraphBuilder,
    dump_graph,
    graph_from_obj,
    graph_hash,
    load_graph,
    loads_graph,
)
from shoegen.graph.nodes import (
    Box,
    Capsule,
    Extrude2D,
    Grid2D,
    HalfSpace,
    HeightSurfaceField,
    Intersect,
    Node,
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
from shoegen.graph.sampled import sample_grid2d, sample_two_level

__all__ = [
    "BOUND",
    "EXACT",
    "EXIT_GRAPH_INVALID",
    "GRAPH_VERSION",
    "ArrayStore",
    "Box",
    "Capsule",
    "Extrude2D",
    "Graph",
    "GraphAnalysis",
    "GraphBuilder",
    "GraphValidationError",
    "Grid2D",
    "HalfSpace",
    "HeightSurfaceField",
    "Intersect",
    "MemoryArrayStore",
    "Node",
    "NodeMetric",
    "NpyDirectoryStore",
    "Offset",
    "Program",
    "RoundedBox",
    "SampledDataError",
    "SampledField",
    "ShellOutward",
    "SmoothUnion",
    "StrutLattice",
    "Subtract",
    "TwoLevelGrid",
    "Union",
    "array_sha256",
    "compile_graph",
    "dump_graph",
    "evaluate",
    "graph_from_obj",
    "graph_hash",
    "load_graph",
    "loads_graph",
    "sample_grid2d",
    "sample_two_level",
]
