"""Check records: one JSON-safe dict per check (plan M0.9, ADR 0004).

Every record carries the measured value, the threshold, the profile it came from (id and
``status``) and a ``result``:

* ``pass`` / ``fail``: prebuild and hard checks; ``fail`` changes the exit code;
* ``report_only``: soft checks; ``finding`` says whether the check flags something, but
  the build never fails because of it;
* ``not_applicable``: the graph has nothing the check applies to;
* ``not_run``: the check could not run (for example no mesh after a prebuild failure).
  A check that did not run is never reported as ``pass``.
"""

from __future__ import annotations

import math
from typing import Any

from shoegen.profile import ManufacturingProfile

PASS = "pass"
FAIL = "fail"
REPORT_ONLY = "report_only"
NOT_APPLICABLE = "not_applicable"
NOT_RUN = "not_run"

EXIT_OK = 0
EXIT_PREBUILD = 3
EXIT_HARD = 5


def jsonable(value: Any) -> Any:
    """Convert numpy scalars/arrays, tuples and non-finite floats to JSON-safe values."""
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [jsonable(v) for v in value]
    if hasattr(value, "tolist"):
        return jsonable(value.tolist())
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, bool | int | str) or value is None:
        return value
    return str(value)


def source(profile: ManufacturingProfile, field: str | None) -> dict:
    """Where a threshold comes from: profile id, its status and the field's own source."""
    return {
        "profile_id": profile.id,
        "profile_status": profile.status,
        "field": field,
        "field_source": profile.sources.get(field) if field else None,
    }


def record(
    check_id: str,
    category: str,
    result: str,
    *,
    measured: dict,
    threshold: dict,
    src: dict,
    detail: str | None = None,
    **extra: Any,
) -> dict:
    rec = {
        "id": check_id,
        "category": category,
        "result": result,
        "measured": jsonable(measured),
        "threshold": jsonable(threshold),
        "source": src,
    }
    if detail is not None:
        rec["detail"] = detail
    rec.update(jsonable(extra))
    return rec
