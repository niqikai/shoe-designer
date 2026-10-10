"""Read-only byte provenance for the actual normalized M5 shoe-last input.

This does not load or inspect geometry; mesh access remains in shoe/last.py.
"""
import hashlib
from pathlib import Path
import re

NORMALIZED_LAST = "assets/last/last_normalized.blend"


def valid_last_signature(value):
    return (isinstance(value, dict) and set(value) == {"file", "bytes", "sha256"}
            and value.get("file") == NORMALIZED_LAST
            and type(value.get("bytes")) is int and value["bytes"] > 0
            and isinstance(value.get("sha256"), str)
            and re.fullmatch(r"[0-9a-f]{64}", value["sha256"]) is not None)


def normalized_last_signature(root):
    """Stream the actual input file without touching design or source assets."""
    digest = hashlib.sha256()
    size = 0
    with (Path(root) / NORMALIZED_LAST).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
    signature = {"file": NORMALIZED_LAST, "bytes": size, "sha256": digest.hexdigest()}
    if not valid_last_signature(signature):
        raise ValueError("规范化鞋楦文件为空或摘要无效，请先完成 M0.5。")
    return signature
