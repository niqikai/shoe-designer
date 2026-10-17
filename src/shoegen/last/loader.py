"""Local-only STEP input, including a narrowly scoped malformed header repair.

Ported from codex/3DPrintedFootwear@5ef9038f8ac5134fba14dfc8db0162f4c8e9f957
(src/shoe_poc/last/loader.py).

Licence metadata (ADR 0003, changed in the M0 review): PR #2 recorded
``license_status = "REQUIRES_PURCHASE_AUTHORIZATION_REVIEW"``. The project decision is in
ADR 0003: the development last's header declares CC BY-NC-ND 4.0, it is used for
development only, and the file and everything derived from it stay local. The loader now
reports what the header *declares* (``license_declared``, verbatim record in
``license_notice``) and the project's ``usage_policy`` from ADR 0003. It makes no legal
assessment of its own.
"""

from __future__ import annotations

import hashlib
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

USAGE_POLICY = "DEVELOPMENT_ONLY_LOCAL_ONLY"
USAGE_POLICY_NOTE = (
    "Project policy (ADR 0003), not a legal assessment: the last file and all geometry "
    "derived from it stay on this machine; no commit, upload, publication, commercial use "
    "or vendor printing."
)
_CC_RE = re.compile(r"\bCC[ -]BY(?:[- ](?:NC|ND|SA))*[ -]\d\.\d\b", flags=re.IGNORECASE)


def declared_license(header_record: str | None) -> str | None:
    """Creative Commons licence named in the STEP ``FILE_NAME`` record, normalised
    (``"CC BY-NC-ND 4.0"``), or ``None`` if the record names none."""
    if not header_record:
        return None
    m = _CC_RE.search(header_record)
    if not m:
        return None
    tokens = re.split(r"[ -]", m.group(0).upper())
    return "CC " + "-".join(tokens[1:-1]) + " " + tokens[-1]


@dataclass
class LoadedLast:
    shape: object
    source: dict


class LastLoader:
    def load(self, path: str | Path) -> LoadedLast:
        from build123d import import_step

        path = Path(path).resolve()
        raw = path.read_bytes()
        text = raw.decode("utf-8", errors="replace")
        header, marker, data = text.partition("DATA;")
        if not marker:
            raise ValueError("STEP DATA section is missing")
        match = re.search(r"FILE_NAME\s*\((.*?)\);", header, flags=re.DOTALL)
        original_header = match.group(0) if match else None
        repaired = bool(match and not match.group(1).lstrip().startswith("'"))
        if repaired:
            # Only change the malformed metadata record; retain DATA verbatim.
            header = (
                header[: match.start()]
                + "FILE_NAME('local-input','',(''),(''),'', '', '');"
                + header[match.end() :]
            )
            with tempfile.TemporaryDirectory(prefix="shoe-step-") as directory:
                prepared = Path(directory) / "last.step"
                prepared.write_text(header + marker + data)
                shape = import_step(prepared)
        else:
            shape = import_step(path)
        unit_tokens = re.findall(r"SI_UNIT\s*\(\s*(\.[A-Z]+\.|\$)\s*,\s*\.METRE\.\s*\)", text)
        conversion_units = re.findall(r"CONVERSION_BASED_UNIT\s*\(\s*'([^']+)'", text)
        if not unit_tokens and not conversion_units:
            raise ValueError("STEP length units could not be established")
        if len(shape.solids()) != 1:
            raise ValueError(f"Expected one shoe-last solid, found {len(shape.solids())}")
        if not shape.is_valid:
            raise ValueError("Imported shoe last has invalid B-Rep topology")
        return LoadedLast(
            shape,
            {
                "path": str(path),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "size_bytes": len(raw),
                "header_repaired_in_temporary_copy": repaired,
                "original_file_name_record": original_header,
                "source_length_unit_prefixes": unit_tokens,
                "source_conversion_units": conversion_units,
                "working_unit": "mm",
                "units_note": (
                    "OpenCascade STEP reader converts input length units to millimetres."
                ),
                "license_declared": declared_license(original_header),
                "license_status": (
                    "DECLARED_IN_FILE_HEADER"
                    if declared_license(original_header)
                    else "NOT_DECLARED_IN_FILE_HEADER"
                ),
                "license_notice": (
                    original_header
                    if original_header and "license" in original_header.lower()
                    else None
                ),
                "usage_policy": USAGE_POLICY,
                "usage_policy_note": USAGE_POLICY_NOTE,
                "local_only": True,
            },
        )
