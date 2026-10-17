"""Deterministic binary STL (ADR 0005: ``shoe.stl`` is bitwise reproducible).

Layout: an 80-byte fixed header (no timestamp, does not start with ``solid``), the
triangle count as little-endian uint32, then per triangle the unit normal and the three
vertices as little-endian float32 and a zero uint16 attribute. The normal is
``(b - a) x (c - a)`` computed in float64 from the mesh vertices, normalised, then rounded
to float32 (zero for a zero-area triangle). Triangles are written in mesh order, which is
the canonical order for meshes from :func:`shoegen.mesh.extract_mesh`.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import numpy as np
from numba import njit

from shoegen.mesh.model import Mesh

STL_HEADER = b"shoegen binary STL: float32 little-endian, canonical triangle order".ljust(80, b" ")
assert len(STL_HEADER) == 80 and not STL_HEADER.startswith(b"solid")

STL_RECORD = np.dtype([("normal", "<f4", (3,)), ("vertices", "<f4", (3, 3)), ("attr", "<u2")])
assert STL_RECORD.itemsize == 50

_CHUNK = 1 << 20  # triangles per write


@njit(cache=True, nogil=True)
def _normals(verts, faces, lo, hi, out):
    for t in range(lo, hi):
        a = faces[t, 0]
        b = faces[t, 1]
        c = faces[t, 2]
        ux = verts[b, 0] - verts[a, 0]
        uy = verts[b, 1] - verts[a, 1]
        uz = verts[b, 2] - verts[a, 2]
        wx = verts[c, 0] - verts[a, 0]
        wy = verts[c, 1] - verts[a, 1]
        wz = verts[c, 2] - verts[a, 2]
        nx = uy * wz - uz * wy
        ny = uz * wx - ux * wz
        nz = ux * wy - uy * wx
        n = np.sqrt(nx * nx + ny * ny + nz * nz)
        if n > 0.0:
            nx /= n
            ny /= n
            nz /= n
        out[t - lo, 0] = np.float32(nx)
        out[t - lo, 1] = np.float32(ny)
        out[t - lo, 2] = np.float32(nz)


def _records(mesh: Mesh, lo: int, hi: int) -> np.ndarray:
    rec = np.zeros(hi - lo, dtype=STL_RECORD)
    nrm = np.empty((hi - lo, 3), dtype=np.float32)
    _normals(mesh.vertices, mesh.faces, lo, hi, nrm)
    rec["normal"] = nrm
    rec["vertices"] = mesh.vertices[mesh.faces[lo:hi]].astype(np.float32)
    return rec


def write_stl(mesh: Mesh, path: str | os.PathLike[str]) -> str:
    """Write ``mesh`` as binary STL; returns the file's sha256 (hex)."""
    n = mesh.n_faces
    if n >= 2**32:
        raise ValueError(f"binary STL holds at most 2**32 - 1 triangles, got {n}")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("wb") as fh:
        head = STL_HEADER + np.uint32(n).astype("<u4").tobytes()
        fh.write(head)
        digest.update(head)
        for lo in range(0, n, _CHUNK):
            data = _records(mesh, lo, min(n, lo + _CHUNK)).tobytes()
            fh.write(data)
            digest.update(data)
    os.replace(tmp, path)
    return digest.hexdigest()


def stl_bytes(mesh: Mesh) -> bytes:
    """The bytes :func:`write_stl` would write (small meshes / tests)."""
    head = STL_HEADER + np.uint32(mesh.n_faces).astype("<u4").tobytes()
    return head + _records(mesh, 0, mesh.n_faces).tobytes()


def read_stl(path: str | os.PathLike[str]) -> tuple[bytes, np.ndarray, np.ndarray]:
    """``(header, normals float32 [n, 3], triangles float32 [n, 3, 3])`` of a binary STL."""
    data = Path(path).read_bytes()
    header = data[:80]
    n = int(np.frombuffer(data, dtype="<u4", count=1, offset=80)[0])
    if len(data) != 84 + 50 * n:
        raise ValueError(f"binary STL size {len(data)} does not match {n} triangles")
    rec = np.frombuffer(data, dtype=STL_RECORD, count=n, offset=84)
    return header, np.array(rec["normal"]), np.array(rec["vertices"])
