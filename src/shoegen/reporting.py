"""Small, JSON-safe execution records; reports never infer unrun checks.

Ported from codex/3DPrintedFootwear@5ef9038f8ac5134fba14dfc8db0162f4c8e9f957
(src/shoe_poc/reporting.py). Changes: package list adapted to shoegen's dependencies
(scikit-image dropped), JSON keys are written sorted for deterministic output.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
import resource
import sys
from datetime import UTC, datetime
from pathlib import Path

_PACKAGES = (
    "shoegen",
    "build123d",
    "cadquery-ocp",
    "numpy",
    "scipy",
    "trimesh",
    "pydantic",
    "pytest",
)


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False, sort_keys=True) + "\n"
    )


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def environment() -> dict:
    packages = {}
    for name in _PACKAGES:
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = "NOT_INSTALLED"
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Darwin reports bytes; Linux reports KiB. This is process lifetime high-water RSS.
    peak_bytes = peak if sys.platform == "darwin" else peak * 1024
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "packages": packages,
        "process_peak_rss_bytes": peak_bytes,
        "rss_scope": "process lifetime high-water mark, not per-stage allocation",
    }


def artifact_manifest(directory: Path) -> dict:
    return {
        p.name: {"bytes": p.stat().st_size, "sha256": sha256(p)}
        for p in sorted(directory.iterdir())
        if p.is_file() and p.name not in {"artifact_manifest.json"}
    }
