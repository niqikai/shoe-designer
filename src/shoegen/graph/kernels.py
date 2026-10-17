"""The single numba evaluation path of the GeometryGraph (ADR 0005).

A compiled program (see :mod:`shoegen.graph.compiler`) is a register machine in
topological order. Instruction ``i`` writes register ``i``; the last register is the
result::

    code   int64[n, 4]  rows (opcode, param_offset, arg_offset, n_args)
    params float64[*]   per-instruction float parameters, at param_offset
    args   int64[*]     per-instruction ints at arg_offset: child register indices for
                        operators; data-slot indices and array dims for sampled leaves
    f32    typed List of read-only 1-D float32 arrays (sampled values, raveled C order)
    i32    typed List of read-only 1-D int32 arrays (brick tables)

:func:`eval_point` is the only routine that computes a field value. :func:`eval_points`
and :func:`eval_grid` are plain loops over it, so a point's value does not depend on the
batch it is evaluated in, nor on its position in the batch. Every function is
``njit(cache=True, nogil=True)``; ``fastmath`` and ``parallel`` are never used.

Parameter layouts (``po`` = param_offset, ``ao`` = arg_offset):

=================  ===============================================  =====================
opcode             params                                           args
=================  ===============================================  =====================
OP_BOX             cx cy cz  bx by bz                               -
OP_ROUNDED_BOX     cx cy cz  bx by bz  r   (b = outer half size)    -
OP_CAPSULE         ax ay az  bx by bz  r                            -
OP_HALFSPACE       nx ny nz  offset   (unit normal)                 -
OP_SAMPLED3D       fo(3) fh  co(3) ch  bbmin(3) bbmax(3)            fine_f32 index_i32
                                                                    coarse_f32 NBx NBy NBz
                                                                    S Nx Ny Nz
OP_EXTRUDE2D       o(2) h  bbmin(2) bbmax(2)  zc hz  offset         values_f32 Nx Ny
OP_STRUT_LATTICE   ox oy oz  cell  r  k  (k already resolved)       -
OP_UNION           -                                                children...
OP_INTERSECT       -                                                children...
OP_SUBTRACT        -                                                base tool
OP_SMOOTH_UNION    k                                                children...
OP_OFFSET          distance                                         child
OP_SHELL_OUTWARD   thickness                                        child
=================  ===============================================  =====================

The lattice (:func:`_strut_lattice`) is an analytic leaf: its params are packed by the
compiler like any other leaf and it reads no sampled data.
"""

from __future__ import annotations

import math

import numpy as np
from numba import njit, types
from numba.typed import List

OP_BOX = 1
OP_ROUNDED_BOX = 2
OP_CAPSULE = 3
OP_HALFSPACE = 4
OP_SAMPLED3D = 5
OP_EXTRUDE2D = 6
OP_STRUT_LATTICE = 7
OP_UNION = 20
OP_INTERSECT = 21
OP_SUBTRACT = 22
OP_SMOOTH_UNION = 23
OP_OFFSET = 24
OP_SHELL_OUTWARD = 25

F32_ARRAY = types.Array(types.float32, 1, "C", readonly=True)
I32_ARRAY = types.Array(types.int32, 1, "C", readonly=True)

_JIT = {"cache": True, "nogil": True}


def empty_f32_list() -> List:
    return List.empty_list(F32_ARRAY)


def empty_i32_list() -> List:
    return List.empty_list(I32_ARRAY)


# ---------------------------------------------------------------- analytic leaves


@njit(**_JIT)
def _box(params, po, x, y, z):
    qx = abs(x - params[po]) - params[po + 3]
    qy = abs(y - params[po + 1]) - params[po + 4]
    qz = abs(z - params[po + 2]) - params[po + 5]
    ox = max(qx, 0.0)
    oy = max(qy, 0.0)
    oz = max(qz, 0.0)
    return math.sqrt(ox * ox + oy * oy + oz * oz) + min(max(qx, max(qy, qz)), 0.0)


@njit(**_JIT)
def _rounded_box(params, po, x, y, z):
    r = params[po + 6]
    qx = abs(x - params[po]) - (params[po + 3] - r)
    qy = abs(y - params[po + 1]) - (params[po + 4] - r)
    qz = abs(z - params[po + 2]) - (params[po + 5] - r)
    ox = max(qx, 0.0)
    oy = max(qy, 0.0)
    oz = max(qz, 0.0)
    return math.sqrt(ox * ox + oy * oy + oz * oz) + min(max(qx, max(qy, qz)), 0.0) - r


@njit(**_JIT)
def _capsule(params, po, x, y, z):
    ax = params[po]
    ay = params[po + 1]
    az = params[po + 2]
    bax = params[po + 3] - ax
    bay = params[po + 4] - ay
    baz = params[po + 5] - az
    pax = x - ax
    pay = y - ay
    paz = z - az
    bb = bax * bax + bay * bay + baz * baz
    t = 0.0
    if bb > 0.0:
        t = (pax * bax + pay * bay + paz * baz) / bb
        t = min(max(t, 0.0), 1.0)
    dx = pax - bax * t
    dy = pay - bay * t
    dz = paz - baz * t
    return math.sqrt(dx * dx + dy * dy + dz * dz) - params[po + 6]


@njit(**_JIT)
def _halfspace(params, po, x, y, z):
    return params[po] * x + params[po + 1] * y + params[po + 2] * z - params[po + 3]


LATTICE_CUTOFF = 40.0
"""Struts with ``d_i - min d > LATTICE_CUTOFF * k`` contribute ``< exp(-40) ~ 4e-18`` of the
smooth-union sum and are skipped (also whole neighbour cells that cannot come that close)."""


@njit(**_JIT)
def _lattice_cell_min(x, y, z, cx, cy, cz, half, r):
    """Min capsule distance over the 8 struts of the BCC cell centred at ``(cx, cy, cz)``."""
    qx = x - cx
    qy = y - cy
    qz = z - cz
    m = np.inf
    for sx in (-1.0, 1.0):
        for sy in (-1.0, 1.0):
            for sz in (-1.0, 1.0):
                # strut from the centre to the corner centre + half * (sx, sy, sz)
                t = (qx * sx + qy * sy + qz * sz) / (3.0 * half)
                t = min(max(t, 0.0), 1.0)
                dx = qx - t * half * sx
                dy = qy - t * half * sy
                dz = qz - t * half * sz
                m = min(m, math.sqrt(dx * dx + dy * dy + dz * dz) - r)
    return m


@njit(**_JIT)
def _lattice_cell_sum(x, y, z, cx, cy, cz, half, r, m, k):
    """``sum exp(-(d_i - m) / k)`` over the 8 struts of a cell, in the fixed strut order."""
    qx = x - cx
    qy = y - cy
    qz = z - cz
    acc = 0.0
    for sx in (-1.0, 1.0):
        for sy in (-1.0, 1.0):
            for sz in (-1.0, 1.0):
                t = (qx * sx + qy * sy + qz * sz) / (3.0 * half)
                t = min(max(t, 0.0), 1.0)
                dx = qx - t * half * sx
                dy = qy - t * half * sy
                dz = qz - t * half * sz
                e = (math.sqrt(dx * dx + dy * dy + dz * dz) - r - m) / k
                if e <= LATTICE_CUTOFF:
                    acc += math.exp(-e)
    return acc


@njit(**_JIT)
def _lattice_box_dist(x, y, z, lx, ly, lz, a):
    dx = max(max(lx - x, x - (lx + a)), 0.0)
    dy = max(max(ly - y, y - (ly + a)), 0.0)
    dz = max(max(lz - z, z - (lz + a)), 0.0)
    return math.sqrt(dx * dx + dy * dy + dz * dz)


@njit(**_JIT)
def _strut_lattice(params, po, x, y, z):
    """BCC strut lattice: ``m - k ln sum exp(-(d_i - m) / k)`` over the 27 cells around the
    query point (cells in lexicographic order, struts in the fixed order of
    :func:`_lattice_cell_sum`), ``m = min d_i``.

    Params: ``ox oy oz  a  r  k``. A neighbour cell whose box is farther than
    ``m_own + LATTICE_CUTOFF * k + r`` cannot contribute more than ``exp(-40)``; it is
    skipped, using a rule that depends on the query point only.
    """
    ox = params[po]
    oy = params[po + 1]
    oz = params[po + 2]
    a = params[po + 3]
    r = params[po + 4]
    k = params[po + 5]
    half = 0.5 * a
    ci = math.floor((x - ox) / a)
    cj = math.floor((y - oy) / a)
    ck = math.floor((z - oz) / a)
    m0 = _lattice_cell_min(
        x, y, z, ox + (ci + 0.5) * a, oy + (cj + 0.5) * a, oz + (ck + 0.5) * a, half, r
    )
    limit = m0 + LATTICE_CUTOFF * k + r
    m = m0
    for di in range(-1, 2):
        lx = ox + (ci + di) * a
        for dj in range(-1, 2):
            ly = oy + (cj + dj) * a
            for dk in range(-1, 2):
                lz = oz + (ck + dk) * a
                if _lattice_box_dist(x, y, z, lx, ly, lz, a) > limit:
                    continue
                m = min(m, _lattice_cell_min(x, y, z, lx + half, ly + half, lz + half, half, r))
    acc = 0.0
    for di in range(-1, 2):
        lx = ox + (ci + di) * a
        for dj in range(-1, 2):
            ly = oy + (cj + dj) * a
            for dk in range(-1, 2):
                lz = oz + (ck + dk) * a
                if _lattice_box_dist(x, y, z, lx, ly, lz, a) > limit:
                    continue
                acc += _lattice_cell_sum(x, y, z, lx + half, ly + half, lz + half, half, r, m, k)
    return m - k * math.log(acc)


# ---------------------------------------------------------------- sampled leaves


@njit(**_JIT)
def _lerp(a, b, t):
    return a * (1.0 - t) + b * t


@njit(**_JIT)
def _trilinear(vals, base, sx, sy, tx, ty, tz):
    """Trilinear interpolation of the cell whose (0,0,0) corner is ``vals[base]``.

    ``sx``/``sy`` are the C-order strides of the first two axes (the third is 1).
    """
    v000 = np.float64(vals[base])
    v001 = np.float64(vals[base + 1])
    v010 = np.float64(vals[base + sy])
    v011 = np.float64(vals[base + sy + 1])
    v100 = np.float64(vals[base + sx])
    v101 = np.float64(vals[base + sx + 1])
    v110 = np.float64(vals[base + sx + sy])
    v111 = np.float64(vals[base + sx + sy + 1])
    c00 = _lerp(v000, v100, tx)
    c01 = _lerp(v001, v101, tx)
    c10 = _lerp(v010, v110, tx)
    c11 = _lerp(v011, v111, tx)
    c0 = _lerp(c00, c10, ty)
    c1 = _lerp(c01, c11, ty)
    return _lerp(c0, c1, tz)


@njit(**_JIT)
def _cell(u, n_cells):
    """Cell index and local weight for grid coordinate ``0 <= u <= n_cells``."""
    c = math.floor(u)
    c = min(c, n_cells - 1)
    return c, u - c


@njit(**_JIT)
def _coarse3d(vals, nx, ny, nz, ux, uy, uz):
    cx, tx = _cell(ux, nx - 1)
    cy, ty = _cell(uy, ny - 1)
    cz, tz = _cell(uz, nz - 1)
    sy = nz
    sx = ny * nz
    return _trilinear(vals, cx * sx + cy * sy + cz, sx, sy, tx, ty, tz)


@njit(**_JIT)
def _box_exterior_dist(x, y, z, lx, ly, lz, hx, hy, hz):
    dx = max(max(lx - x, x - hx), 0.0)
    dy = max(max(ly - y, y - hy), 0.0)
    dz = max(max(lz - z, z - hz), 0.0)
    return math.sqrt(dx * dx + dy * dy + dz * dz)


@njit(**_JIT)
def _sampled3d(params, po, args, ao, f32, i32, x, y, z):
    fine = f32[args[ao]]
    index = i32[args[ao + 1]]
    coarse = f32[args[ao + 2]]
    nbx = args[ao + 3]
    nby = args[ao + 4]
    nbz = args[ao + 5]
    s = args[ao + 6]
    nx = args[ao + 7]
    ny = args[ao + 8]
    nz = args[ao + 9]

    # 1. fine narrow band
    fh = params[po + 3]
    ux = (x - params[po]) / fh
    uy = (y - params[po + 1]) / fh
    uz = (z - params[po + 2]) / fh
    if ux >= 0.0 and uy >= 0.0 and uz >= 0.0 and ux < nbx * s and uy < nby * s and uz < nbz * s:
        cx = math.floor(ux)
        cy = math.floor(uy)
        cz = math.floor(uz)
        bx = cx // s
        by = cy // s
        bz = cz // s
        row = index[(bx * nby + by) * nbz + bz]
        if row >= 0:
            s1 = s + 1
            sy = s1
            sx = s1 * s1
            base = row * s1 * s1 * s1 + (cx - bx * s) * sx + (cy - by * s) * sy + (cz - bz * s)
            return _trilinear(fine, base, sx, sy, ux - cx, uy - cy, uz - cz)

    # 2. coarse grid
    ch = params[po + 7]
    vx = (x - params[po + 4]) / ch
    vy = (y - params[po + 5]) / ch
    vz = (z - params[po + 6]) / ch
    if vx >= 0.0 and vy >= 0.0 and vz >= 0.0 and vx <= nx - 1 and vy <= ny - 1 and vz <= nz - 1:
        return _coarse3d(coarse, nx, ny, nz, vx, vy, vz)

    # 3. far: signed distance to the source bbox (a lower bound of the true distance)
    qx = min(max(vx, 0.0), nx - 1.0)
    qy = min(max(vy, 0.0), ny - 1.0)
    qz = min(max(vz, 0.0), nz - 1.0)
    sign = 1.0
    if _coarse3d(coarse, nx, ny, nz, qx, qy, qz) < 0.0:
        sign = -1.0
    dist = _box_exterior_dist(
        x,
        y,
        z,
        params[po + 8],
        params[po + 9],
        params[po + 10],
        params[po + 11],
        params[po + 12],
        params[po + 13],
    )
    return sign * dist


@njit(**_JIT)
def _bilinear(vals, nx, ny, ux, uy):
    cx, tx = _cell(ux, nx - 1)
    cy, ty = _cell(uy, ny - 1)
    base = cx * ny + cy
    v00 = np.float64(vals[base])
    v01 = np.float64(vals[base + 1])
    v10 = np.float64(vals[base + ny])
    v11 = np.float64(vals[base + ny + 1])
    return _lerp(_lerp(v00, v10, tx), _lerp(v01, v11, tx), ty)


@njit(**_JIT)
def _field2d(params, po, vals, nx, ny, x, y):
    h = params[po + 2]
    ux = (x - params[po]) / h
    uy = (y - params[po + 1]) / h
    if ux >= 0.0 and uy >= 0.0 and ux <= nx - 1 and uy <= ny - 1:
        return _bilinear(vals, nx, ny, ux, uy)
    qx = min(max(ux, 0.0), nx - 1.0)
    qy = min(max(uy, 0.0), ny - 1.0)
    sign = 1.0
    if _bilinear(vals, nx, ny, qx, qy) < 0.0:
        sign = -1.0
    dx = max(max(params[po + 3] - x, x - params[po + 5]), 0.0)
    dy = max(max(params[po + 4] - y, y - params[po + 6]), 0.0)
    return sign * math.sqrt(dx * dx + dy * dy)


@njit(**_JIT)
def _extrude2d(params, po, args, ao, f32, x, y, z):
    vals = f32[args[ao]]
    d2 = _field2d(params, po, vals, args[ao + 1], args[ao + 2], x, y) - params[po + 9]
    dz = abs(z - params[po + 7]) - params[po + 8]
    wx = max(d2, 0.0)
    wz = max(dz, 0.0)
    return min(max(d2, dz), 0.0) + math.sqrt(wx * wx + wz * wz)


# ---------------------------------------------------------------- interpreter


@njit(**_JIT)
def eval_point(code, params, args, f32, i32, regs, x, y, z):
    """Evaluate the program at one point; ``regs`` is scratch of length ``code.shape[0]``."""
    n = code.shape[0]
    for i in range(n):
        op = code[i, 0]
        po = code[i, 1]
        ao = code[i, 2]
        na = code[i, 3]
        if op == OP_BOX:
            v = _box(params, po, x, y, z)
        elif op == OP_ROUNDED_BOX:
            v = _rounded_box(params, po, x, y, z)
        elif op == OP_CAPSULE:
            v = _capsule(params, po, x, y, z)
        elif op == OP_HALFSPACE:
            v = _halfspace(params, po, x, y, z)
        elif op == OP_SAMPLED3D:
            v = _sampled3d(params, po, args, ao, f32, i32, x, y, z)
        elif op == OP_EXTRUDE2D:
            v = _extrude2d(params, po, args, ao, f32, x, y, z)
        elif op == OP_STRUT_LATTICE:
            v = _strut_lattice(params, po, x, y, z)
        elif op == OP_UNION:
            v = regs[args[ao]]
            for j in range(1, na):
                v = min(v, regs[args[ao + j]])
        elif op == OP_INTERSECT:
            v = regs[args[ao]]
            for j in range(1, na):
                v = max(v, regs[args[ao + j]])
        elif op == OP_SUBTRACT:
            v = max(regs[args[ao]], -regs[args[ao + 1]])
        elif op == OP_SMOOTH_UNION:
            k = params[po]
            v = regs[args[ao]]
            for j in range(1, na):
                b = regs[args[ao + j]]
                h = max(k - abs(v - b), 0.0) / k
                v = min(v, b) - h * h * k * 0.25
        elif op == OP_OFFSET:
            v = regs[args[ao]] - params[po]
        elif op == OP_SHELL_OUTWARD:
            d = regs[args[ao]]
            v = max(d - params[po], -d)
        else:
            v = np.nan
        regs[i] = v
    return regs[n - 1]


@njit(**_JIT)
def eval_points(code, params, args, f32, i32, points, out):
    """``out[i] = field(points[i])`` for an ``(N, 3)`` float64 array."""
    regs = np.empty(code.shape[0], dtype=np.float64)
    for i in range(points.shape[0]):
        out[i] = eval_point(
            code, params, args, f32, i32, regs, points[i, 0], points[i, 1], points[i, 2]
        )


@njit(**_JIT)
def eval_grid(code, params, args, f32, i32, origin, h, start, out):
    """``out[a, b, c] = field(origin + (start + (a, b, c)) * h)``, coordinates from the
    global integer index (ADR 0005)."""
    regs = np.empty(code.shape[0], dtype=np.float64)
    for a in range(out.shape[0]):
        x = origin[0] + np.float64(start[0] + a) * h
        for b in range(out.shape[1]):
            y = origin[1] + np.float64(start[1] + b) * h
            for c in range(out.shape[2]):
                z = origin[2] + np.float64(start[2] + c) * h
                out[a, b, c] = eval_point(code, params, args, f32, i32, regs, x, y, z)
