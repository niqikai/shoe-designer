"""Canonical JSON: the byte string that ``graph_hash`` is computed from.

Rules: object keys sorted; no insignificant whitespace in the hashed form; ASCII only;
floats written with ``repr`` (shortest string that round-trips exactly, Python >= 3.1);
ints written as ints; tuples written as arrays; NaN/inf rejected.
"""

from __future__ import annotations

import json
import math
from typing import Any


def _encode(value: Any, out: list[str], indent: int | None, level: int) -> None:
    if value is None:
        out.append("null")
    elif value is True:
        out.append("true")
    elif value is False:
        out.append("false")
    elif isinstance(value, int):
        out.append(str(int(value)))
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"non-finite float in canonical JSON: {value!r}")
        out.append(repr(float(value)))
    elif isinstance(value, str):
        out.append(json.dumps(value, ensure_ascii=True))
    elif isinstance(value, dict):
        keys = sorted(value)
        if any(not isinstance(k, str) for k in keys):
            raise TypeError("canonical JSON object keys must be strings")
        if not keys:
            out.append("{}")
            return
        out.append("{")
        for i, k in enumerate(keys):
            if i:
                out.append(",")
            _newline(out, indent, level + 1)
            out.append(json.dumps(k, ensure_ascii=True))
            out.append(": " if indent is not None else ":")
            _encode(value[k], out, indent, level + 1)
        _newline(out, indent, level)
        out.append("}")
    elif isinstance(value, list | tuple):
        if not value:
            out.append("[]")
            return
        # Short scalar arrays (vectors) stay on one line in the indented form.
        flat = indent is not None and all(
            isinstance(v, int | float | bool | str) or v is None for v in value
        )
        out.append("[")
        for i, v in enumerate(value):
            if i:
                out.append(", " if flat else ",")
            if not flat:
                _newline(out, indent, level + 1)
            _encode(v, out, indent, level + 1)
        if not flat:
            _newline(out, indent, level)
        out.append("]")
    else:
        raise TypeError(f"cannot encode {type(value).__name__} in canonical JSON")


def _newline(out: list[str], indent: int | None, level: int) -> None:
    if indent is not None:
        out.append("\n" + " " * (indent * level))


def canonical_dumps(value: Any, *, indent: int | None = None) -> str:
    """Serialize ``value`` canonically. ``indent=None`` gives the hashed compact form."""
    out: list[str] = []
    _encode(value, out, indent, 0)
    return "".join(out)
