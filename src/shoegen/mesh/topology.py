"""Half-edge topology of an indexed triangle mesh: twins, vertex fans, components.

All kernels are ``njit(cache=True, nogil=True)`` and linear in the mesh size (no global
sort): half-edges are bucketed by origin vertex, and the twin of ``a -> b`` is looked up in
``b``'s bucket, whose size is the vertex degree.

Conventions: half-edge ``h = 3 t + s`` runs from ``tri[t, s]`` to ``tri[t, (s + 1) % 3]``;
it is also the *corner* of triangle ``t`` at vertex ``tri[t, s]``.
"""

from __future__ import annotations

import numpy as np
from numba import njit

_JIT = {"cache": True, "nogil": True}


@njit(**_JIT)
def csr_by_origin(tri, nv):
    """``(off int64[nv + 1], he int64[3 nt])``: half-edges grouped by origin vertex, each
    group in increasing half-edge order."""
    nt = tri.shape[0]
    off = np.zeros(nv + 1, np.int64)
    for t in range(nt):
        for s in range(3):
            off[tri[t, s] + 1] += 1
    for v in range(nv):
        off[v + 1] += off[v]
    fill = off[:-1].copy()
    he = np.empty(3 * nt, np.int64)
    for t in range(nt):
        for s in range(3):
            a = tri[t, s]
            he[fill[a]] = 3 * t + s
            fill[a] += 1
    return off, he


@njit(**_JIT)
def edge_analysis(tri, nv):
    """Twins and edge classification.

    Returns ``(twin, edge_of, counts)``: ``twin[h]`` is the opposite half-edge when the
    undirected edge has exactly one half-edge each way, else ``-1``; ``edge_of[h]`` is the
    smallest half-edge of ``h``'s undirected edge (the edge's representative);
    ``counts = [edges, open, nonmanifold, misoriented, degenerate_halfedges]`` where an
    edge is *open* with one half-edge, *nonmanifold* with more than two, *misoriented* with
    two in the same direction.
    """
    nt = tri.shape[0]
    nh = 3 * nt
    off, he = csr_by_origin(tri, nv)
    twin = np.full(nh, -1, np.int64)
    edge_of = np.full(nh, -1, np.int64)
    counts = np.zeros(5, np.int64)
    for h in range(nh):
        t = h // 3
        s = h - 3 * t
        a = tri[t, s]
        b = tri[t, (s + 1) % 3]
        if a == b:
            counts[4] += 1
            continue
        nab = 0
        first = h
        for q in range(off[a], off[a + 1]):
            h2 = he[q]
            t2 = h2 // 3
            s2 = h2 - 3 * t2
            if tri[t2, (s2 + 1) % 3] == b:
                nab += 1
                first = min(first, h2)
        nba = 0
        rev = -1
        for q in range(off[b], off[b + 1]):
            h2 = he[q]
            t2 = h2 // 3
            s2 = h2 - 3 * t2
            if tri[t2, (s2 + 1) % 3] == a:
                nba += 1
                rev = h2
                first = min(first, h2)
        edge_of[h] = first
        if nab == 1 and nba == 1:
            twin[h] = rev
        if first == h:
            counts[0] += 1
            m = nab + nba
            if m == 1:
                counts[1] += 1
            elif m > 2:
                counts[2] += 1
            elif nab != 1:
                counts[3] += 1
    return twin, edge_of, counts


@njit(**_JIT)
def vertex_fans(tri, twin, nv):
    """Fans around every vertex: maximal sets of corners connected through shared edges.

    Returns ``(n_fans int32[nv], fan_of int64[3 nt], n_fan_total)``. A vertex of a
    manifold mesh has exactly one fan; more than one means the surface touches itself at
    that vertex.
    """
    nt = tri.shape[0]
    nh = 3 * nt
    fan_of = np.full(nh, -1, np.int64)
    n_fans = np.zeros(nv, np.int32)
    nf = 0
    for h0 in range(nh):
        if fan_of[h0] >= 0:
            continue
        t0 = h0 // 3
        v = tri[t0, h0 - 3 * t0]
        if v == tri[t0, (h0 - 3 * t0 + 1) % 3] or v == tri[t0, (h0 - 3 * t0 + 2) % 3]:
            fan_of[h0] = nf  # corner of a degenerate triangle: a fan of its own
            n_fans[v] += 1
            nf += 1
            continue
        h = h0
        closed = False
        while True:
            fan_of[h] = nf
            t = h // 3
            s = h - 3 * t
            tw = twin[3 * t + (s + 2) % 3]  # into v in t -> its twin leaves v
            if tw < 0:
                break
            h = tw
            if h == h0:
                closed = True
                break
            if fan_of[h] >= 0:
                break
        if not closed:
            h = h0
            while True:
                tw = twin[h]  # leaves v in t -> twin enters v; corner at v follows it
                if tw < 0:
                    break
                t = tw // 3
                h = 3 * t + (tw - 3 * t + 1) % 3
                if fan_of[h] >= 0:
                    break
                fan_of[h] = nf
        n_fans[v] += 1
        nf += 1
    return n_fans, fan_of, nf


@njit(**_JIT)
def _find(parent, x):
    while parent[x] != x:
        parent[x] = parent[parent[x]]
        x = parent[x]
    return x


@njit(**_JIT)
def _union_min(parent, a, b):
    ra = _find(parent, a)
    rb = _find(parent, b)
    if ra == rb:
        return
    if ra < rb:
        parent[rb] = ra
    else:
        parent[ra] = rb


@njit(**_JIT)
def components(tri, nv):
    """Connected components through shared vertices.

    Returns ``(vlabel int64[nv], tlabel int64[nt], n_comp)``; components are numbered in
    order of their smallest vertex; unused vertices get ``-1``.
    """
    nt = tri.shape[0]
    parent = np.arange(nv)
    for t in range(nt):
        _union_min(parent, tri[t, 0], tri[t, 1])
        _union_min(parent, tri[t, 1], tri[t, 2])
    used = np.zeros(nv, np.bool_)
    for t in range(nt):
        for s in range(3):
            used[tri[t, s]] = True
    vlabel = np.full(nv, -1, np.int64)
    root_label = np.full(nv, -1, np.int64)
    nc = 0
    for v in range(nv):
        if not used[v]:
            continue
        r = _find(parent, v)
        if root_label[r] < 0:
            root_label[r] = nc
            nc += 1
        vlabel[v] = root_label[r]
    tlabel = np.empty(nt, np.int64)
    for t in range(nt):
        tlabel[t] = vlabel[tri[t, 0]]
    return vlabel, tlabel, nc


@njit(**_JIT)
def component_measures(verts, tri, vlabel, tlabel, edge_of, n_fans, nc):
    """Per component: ``float64[nc, 2]`` (signed volume, area) and ``int64[nc, 6]``
    (vertices, edges, triangles, nonmanifold vertices, zero-area triangles, degenerate
    triangles). Sums run in triangle order."""
    nt = tri.shape[0]
    fl = np.zeros((nc, 2), np.float64)
    it = np.zeros((nc, 6), np.int64)
    for v in range(vlabel.shape[0]):
        c = vlabel[v]
        if c >= 0:
            it[c, 0] += 1
            if n_fans[v] > 1:
                it[c, 3] += 1
    for t in range(nt):
        c = tlabel[t]
        i0 = tri[t, 0]
        i1 = tri[t, 1]
        i2 = tri[t, 2]
        ax = verts[i0, 0]
        ay = verts[i0, 1]
        az = verts[i0, 2]
        bx = verts[i1, 0]
        by = verts[i1, 1]
        bz = verts[i1, 2]
        cx = verts[i2, 0]
        cy = verts[i2, 1]
        cz = verts[i2, 2]
        # signed volume of the tetrahedron (0, a, b, c): a . (b x c) / 6
        fl[c, 0] += (
            ax * (by * cz - bz * cy) + ay * (bz * cx - bx * cz) + az * (bx * cy - by * cx)
        ) / 6.0
        ux = bx - ax
        uy = by - ay
        uz = bz - az
        wx = cx - ax
        wy = cy - ay
        wz = cz - az
        nx = uy * wz - uz * wy
        ny = uz * wx - ux * wz
        nz = ux * wy - uy * wx
        n2 = nx * nx + ny * ny + nz * nz
        fl[c, 1] += 0.5 * np.sqrt(n2)
        it[c, 2] += 1
        if i0 == i1 or i1 == i2 or i0 == i2:
            it[c, 5] += 1
        elif n2 == 0.0:
            it[c, 4] += 1
        for s in range(3):
            h = 3 * t + s
            if edge_of[h] == h:
                it[c, 1] += 1
    return fl, it
