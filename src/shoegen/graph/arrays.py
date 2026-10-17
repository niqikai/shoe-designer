"""Content-addressed storage for the arrays behind sampled-field nodes.

Graph nodes never embed sampled data. They name each array by :func:`array_sha256`, and
the arrays are supplied at compile time by an :class:`ArrayStore`. The compiler re-hashes
every array it receives and refuses a mismatch, so a ``graph_hash`` always pins the exact
numbers that were evaluated (ADR 0001).

``array_sha256`` hashes dtype, shape and the C-order bytes, not the ``.npy`` file: the file
header depends on the numpy version, the array content does not.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Protocol

import numpy as np

from shoegen.graph.errors import SampledDataError

_HASH_DOMAIN = b"shoegen-array-v1\0"
_CHUNK = 1 << 24


def array_sha256(arr: np.ndarray) -> str:
    """Return the content sha256 of ``arr`` (dtype, shape and C-order bytes).

    Only little-endian or byte-order-free dtypes are accepted so the hash does not depend
    on the host byte order.
    """
    a = np.asarray(arr)
    if a.dtype.byteorder == ">":
        raise ValueError("big-endian arrays are not supported")
    a = np.ascontiguousarray(a)
    h = hashlib.sha256()
    h.update(_HASH_DOMAIN)
    h.update(a.dtype.str.encode("ascii") + b"\0")
    h.update(",".join(str(int(s)) for s in a.shape).encode("ascii") + b"\0")
    flat = memoryview(a.reshape(-1).view(np.uint8))
    for start in range(0, len(flat), _CHUNK):
        h.update(flat[start : start + _CHUNK])
    return h.hexdigest()


def _readonly(arr: np.ndarray) -> np.ndarray:
    view = np.asarray(arr).view()
    view.flags.writeable = False
    return view


class ArrayStore(Protocol):
    """Anything that returns an array for a content sha256."""

    def get(self, sha256: str) -> np.ndarray:  # pragma: no cover - protocol
        ...


class MemoryArrayStore:
    """In-memory store; used by tests and for small derived arrays."""

    def __init__(self) -> None:
        self._arrays: dict[str, np.ndarray] = {}

    def put(self, arr: np.ndarray) -> str:
        """Store a read-only copy of ``arr`` and return its sha256."""
        copy = np.array(arr, copy=True, order="C")
        sha = array_sha256(copy)
        self._arrays[sha] = _readonly(copy)
        return sha

    def put_unchecked(self, sha256: str, arr: np.ndarray) -> None:
        """Store ``arr`` under a caller-supplied key (tests use this to simulate corruption)."""
        self._arrays[sha256] = _readonly(np.ascontiguousarray(arr))

    def get(self, sha256: str) -> np.ndarray:
        try:
            return self._arrays[sha256]
        except KeyError:
            raise SampledDataError(f"array {sha256} not found in memory store") from None


class NpyDirectoryStore:
    """Arrays stored as ``<root>/<sha256>.npy`` and opened with ``mmap_mode='r'``.

    Workers that open the same files share the OS page cache instead of each holding a
    private copy (ADR 0003).
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def path_for(self, sha256: str) -> Path:
        return self.root / f"{sha256}.npy"

    def put(self, arr: np.ndarray) -> str:
        """Write ``arr`` (C order) as ``<sha256>.npy`` and return the sha256."""
        a = np.ascontiguousarray(arr)
        sha = array_sha256(a)
        path = self.path_for(sha)
        if not path.exists():
            self.root.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            with tmp.open("wb") as fh:
                np.save(fh, a, allow_pickle=False)
            tmp.replace(path)
        return sha

    def get(self, sha256: str) -> np.ndarray:
        path = self.path_for(sha256)
        if not path.is_file():
            raise SampledDataError(f"array {sha256} not found: {path}")
        return np.load(path, mmap_mode="r", allow_pickle=False)
