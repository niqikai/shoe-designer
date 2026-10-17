"""Errors raised while building, loading or compiling a GeometryGraph."""

from __future__ import annotations

EXIT_GRAPH_INVALID = 3
"""CLI exit code for an invalid input, Graph or pre-build parameter check (plan M0.10)."""


class GraphValidationError(Exception):
    """The graph cannot be built as specified (CLI exit code 3).

    Deliberately not a ``ValueError`` subclass: pydantic would otherwise wrap it into a
    ``ValidationError`` when it is raised from a model validator.
    """

    exit_code: int = EXIT_GRAPH_INVALID

    def __init__(self, message: str, *, node_id: str | None = None) -> None:
        self.node_id = node_id
        prefix = f"node {node_id!r}: " if node_id is not None else ""
        super().__init__(prefix + message)


class SampledDataError(GraphValidationError):
    """A sampled-field array is missing, malformed or does not match its sha256."""
