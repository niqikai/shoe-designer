"""ManufacturingProfile: process-specific thresholds loaded from versioned JSON files.

See ADR 0004. Thresholds are never hard-coded in the geometry code; they are read from
``profiles/<id>.json`` at the repository root (or ``$SHOEGEN_PROFILES_DIR``).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PROFILES_DIR_ENV = "SHOEGEN_PROFILES_DIR"
_PROFILE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*@[0-9]+$")

NUMERIC_FIELDS = (
    "min_strut_diameter_mm",
    "min_wall_mm",
    "min_pore_mm",
    "min_fragment_volume_mm3",
    "build_volume_mm",
)


class ManufacturingProfile(BaseModel):
    """Process-specific manufacturing constraints (ADR 0004)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=_PROFILE_ID_RE.pattern)
    process: str
    status: Literal["placeholder", "vendor_guideline", "measured"]
    min_strut_diameter_mm: float = Field(gt=0)
    min_wall_mm: float = Field(gt=0)
    min_pore_mm: float = Field(gt=0)
    min_fragment_volume_mm3: float = Field(ge=0)
    build_volume_mm: tuple[float, float, float]
    requires_drain_holes: bool
    requires_supports: bool
    output_formats: list[str]
    # Source of each numeric field (vendor guideline reference, measurement id).
    # null means the value has not been verified yet.
    sources: dict[str, str | None]
    notes: str


def profiles_dir() -> Path:
    """Return the directory that holds profile JSON files."""
    override = os.environ.get(PROFILES_DIR_ENV)
    if override:
        return Path(override)
    # src/shoegen/profile.py -> repository root is three levels up from this file's dir.
    return Path(__file__).resolve().parents[2] / "profiles"


def load_profile(profile_id: str) -> ManufacturingProfile:
    """Load and validate ``<profiles_dir>/<profile_id>.json``."""
    if not _PROFILE_ID_RE.match(profile_id):
        raise ValueError(f"invalid profile id: {profile_id!r}")
    path = profiles_dir() / f"{profile_id}.json"
    if not path.is_file():
        raise FileNotFoundError(f"profile not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    profile = ManufacturingProfile.model_validate(data)
    if profile.id != profile_id:
        raise ValueError(f"profile file {path.name} declares id {profile.id!r}")
    return profile
