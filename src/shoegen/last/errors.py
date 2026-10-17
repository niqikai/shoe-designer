"""Errors raised while turning a last into sampled fields."""

from __future__ import annotations

EXIT_LAST_INVALID = 4
"""CLI exit code for a last that cannot be used (plan M0.10)."""


class LastMeshError(Exception):
    """The last's tessellation is not a usable closed solid (CLI exit code 4)."""

    exit_code: int = EXIT_LAST_INVALID
