"""Signed distance to a closed triangle mesh with a reusable libigl AABB tree.

``igl.signed_distance`` rebuilds its tree (and the pseudo-normals) on every call, which for a
10k-triangle last costs about as much as 100k queries; the samplers call the distance
function hundreds of times. :class:`MeshSDF` builds the tree once and signs the distance with
angle-weighted pseudo-normals (Baerentzen & Aanaes), which is exact for the closed,
manifold, consistently oriented meshes that :func:`shoegen.last.mesh.last_to_mesh` accepts:
the closest point is classified as face / edge / vertex from its barycentric coordinates and
the sign is that of ``(p - c) . n`` with the matching normal.

``sign="winding_number"`` / ``"fast_winding_number"`` fall back to ``igl.signed_distance``
for meshes that were not checked (about 4 000 and 20 000 points/s respectively).
"""

from __future__ import annotations

import igl
import numpy as np

_BARY_EPS = 1e-9
_LIBIGL_SIGN = {
    "winding_number": igl.SIGNED_DISTANCE_TYPE_WINDING_NUMBER,
    "fast_winding_number": igl.SIGNED_DISTANCE_TYPE_FAST_WINDING_NUMBER,
}


class MeshSDF:
    def __init__(
        self,
        vertices: np.ndarray,
        faces: np.ndarray,
        *,
        sign: str = "pseudonormal",
        chunk: int = 262_144,
    ) -> None:
        if sign != "pseudonormal" and sign not in _LIBIGL_SIGN:
            raise ValueError(f"unknown sign method {sign!r}")
        self.v = np.ascontiguousarray(vertices, dtype=np.float64)
        self.f = np.ascontiguousarray(faces, dtype=np.int64)
        self.sign = sign
        self.chunk = chunk
        if sign == "pseudonormal":
            self._aabb = igl.AABB()
            self._aabb.init(self.v, self.f)
            self._precompute_normals()

    # ------------------------------------------------------------------ normals

    def _precompute_normals(self) -> None:
        v, f = self.v, self.f
        a, b, c = v[f[:, 0]], v[f[:, 1]], v[f[:, 2]]
        n = np.cross(b - a, c - a)
        length = np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-300)
        fn = n / length
        self._face_n = fn
        # angle-weighted vertex normals
        vn = np.zeros_like(v)
        corners = (v[f[:, [1, 2, 0]]] - v[f], v[f[:, [2, 0, 1]]] - v[f])
        e1, e2 = corners
        cosang = np.einsum("fkc,fkc->fk", e1, e2) / np.maximum(
            np.linalg.norm(e1, axis=2) * np.linalg.norm(e2, axis=2), 1e-300
        )
        ang = np.arccos(np.clip(cosang, -1.0, 1.0))
        for k in range(3):
            np.add.at(vn, f[:, k], fn * ang[:, k : k + 1])
        self._vert_n = vn
        # edge normals: edge k of a face runs from f[k] to f[k+1]; sum the two face normals
        m = len(f)
        ea = f.reshape(-1)
        eb = np.roll(f, -1, axis=1).reshape(-1)
        nv = len(v)
        key = np.minimum(ea, eb) * nv + np.maximum(ea, eb)
        order = np.argsort(key, kind="stable")
        owner = np.repeat(np.arange(m), 3)
        starts = np.flatnonzero(np.r_[True, key[order][1:] != key[order][:-1]])
        e_first = order[starts]
        e_second = order[starts + 1]  # closed manifold: two half edges per edge
        summed = fn[owner[e_first]] + fn[owner[e_second]]
        en = np.zeros((m * 3, 3))
        en[e_first] = summed
        en[e_second] = summed
        self._edge_n = en.reshape(m, 3, 3)

    # ------------------------------------------------------------------ queries

    def __call__(self, points: np.ndarray) -> np.ndarray:
        pts = np.asarray(points, dtype=np.float64)
        out = np.empty(len(pts))
        for s in range(0, len(pts), self.chunk):
            p = np.ascontiguousarray(pts[s : s + self.chunk])
            if self.sign == "pseudonormal":
                out[s : s + self.chunk] = self._signed(p)
            else:
                out[s : s + self.chunk] = igl.signed_distance(
                    p, self.v, self.f, _LIBIGL_SIGN[self.sign]
                )[0]
        return out

    def _signed(self, p: np.ndarray) -> np.ndarray:
        sq, face, closest = self._aabb.squared_distance(self.v, self.f, p)
        tri = self.f[face]
        a, b, c = self.v[tri[:, 0]], self.v[tri[:, 1]], self.v[tri[:, 2]]
        # barycentric coordinates of the closest point
        v0, v1, v2 = b - a, c - a, closest - a
        d00 = np.einsum("ij,ij->i", v0, v0)
        d01 = np.einsum("ij,ij->i", v0, v1)
        d11 = np.einsum("ij,ij->i", v1, v1)
        d20 = np.einsum("ij,ij->i", v2, v0)
        d21 = np.einsum("ij,ij->i", v2, v1)
        den = np.maximum(d00 * d11 - d01 * d01, 1e-300)
        w1 = (d11 * d20 - d01 * d21) / den
        w2 = (d00 * d21 - d01 * d20) / den
        w0 = 1.0 - w1 - w2
        bary = np.stack([w0, w1, w2], axis=1)
        zero = bary < _BARY_EPS  # (n, 3): which coordinates vanish
        nzero = zero.sum(axis=1)
        normal = self._face_n[face].copy()
        # vertex: two coordinates vanish -> the remaining one names the vertex
        vert = nzero >= 2
        if vert.any():
            which = np.argmax(bary[vert], axis=1)
            normal[vert] = self._vert_n[tri[vert, which]]
        # edge: exactly one coordinate vanishes -> edge opposite to that vertex
        edge = nzero == 1
        if edge.any():
            opposite = np.argmax(zero[edge], axis=1)  # vertex k vanishes -> edge (k+1) -> (k+2)
            normal[edge] = self._edge_n[face[edge], (opposite + 1) % 3]
        side = np.einsum("ij,ij->i", p - closest, normal)
        return np.where(side < 0.0, -np.sqrt(sq), np.sqrt(sq))
