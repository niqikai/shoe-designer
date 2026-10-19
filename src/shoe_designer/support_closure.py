"""Self-supporting closure of the cavity field (design doc issue-10-cavity-rebuild §3, S5).

Printed sole-down, every downward-facing cavity surface is a ceiling. Instead of checking overhangs
afterwards, the cavity ``C`` is grown upwards into its 45-degree closure ``V``::

    V_0 = C_0,   V_{k+1} = C_{k+1} | erode_cross(V_k)       (layer by layer along z)

On a grid with ``dz = h / tan(alpha_b)`` the one-voxel-per-layer cross erosion gives surfaces that
are exactly ``alpha_b`` from vertical (the angle is read off the grid, not passed in), with integer
operations only. Only the added part ``A = V \\ C`` is binarised: ``phi_A`` is a signed distance of
``A`` (grown one voxel so it overlaps ``C`` without a ledge) computed inside
A's bounding box padded by ``t + 1`` voxels; ``phi_C* = min(phi_C, phi_A)``. ``phi_C`` stays exact.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy import ndimage

from .sdf import Grid, SDFError

HEEL_FRAC = 0.20  # heel zone = first 20% of the cavity's x extent (§4.1: 后跟 0–0.20)
WALL_MM = 2.0  # target wall thickness t (D4); pads the phi_A box and sizes the closure shell
_CROSS2 = ndimage.generate_binary_structure(2, 1)


def alpha_of(grid: Grid) -> float:
    """Closure angle from vertical (degrees) implied by the grid: atan(h / dz)."""
    return math.degrees(math.atan2(grid.h, grid.dz))


def close_overhangs(cavity: np.ndarray) -> np.ndarray:
    """45-degree (grid-angle) closure V >= C of a boolean cavity ``[ix, iy, iz]``, grown upwards."""
    c = np.ascontiguousarray(np.asarray(cavity, bool).transpose(2, 0, 1))  # layers first
    v = np.empty_like(c)
    prev_any = False
    # The grid must leave a margin in x/y (and headroom in z) around the cavity: _grow_overlap and the EDT
    # treat the outside of the grid as "not A", so a ridge touching the x/y border would get a cut face.
    # border_value=1: the cavity is assumed to continue outside the grid in x/y, so the erosion never starts
    # at the grid faces. This errs toward more closure (printable side); callers should still keep a margin.
    for k in range(c.shape[0]):
        if prev_any:
            v[k] = c[k] | ndimage.binary_erosion(v[k - 1], _CROSS2, border_value=1)
        else:
            v[k] = c[k]
        prev_any = bool(v[k].any())
    return v.transpose(1, 2, 0)


def _grow_overlap(a: np.ndarray) -> np.ndarray:
    """A grown by one voxel (3-D cross): it then overlaps C and meets its walls flush (no ledge, no film)."""
    out = a.copy()
    out[1:] |= a[:-1]
    out[:-1] |= a[1:]
    out[:, 1:] |= a[:, :-1]
    out[:, :-1] |= a[:, 1:]
    out[:, :, 1:] |= a[:, :, :-1]
    out[:, :, :-1] |= a[:, :, 1:]
    return out


def _signed_edt(mask: np.ndarray, spacing) -> np.ndarray:
    """Negative inside ``mask``; +-1 voxel next to the boundary, so marching cubes puts it midway."""
    out = ndimage.distance_transform_edt(~mask, sampling=spacing)
    out -= ndimage.distance_transform_edt(mask, sampling=spacing)
    return out.astype(np.float32)


def _top_index(mask: np.ndarray) -> np.ndarray:
    """Highest True z index per (x, y) column; -1 where the column is empty."""
    nz = mask.shape[2]
    top = nz - 1 - np.argmax(mask[:, :, ::-1], axis=2)
    return np.where(mask.any(axis=2), top, -1)


def _by_x(col_value: np.ndarray, x0: float, h: float, step_mm: float = 10.0) -> list[dict]:
    """Max over y of a per-column value, per ``step_mm`` bin of absolute x (bin edges do not depend on the grid origin)."""
    ix = np.flatnonzero((col_value > 0).any(axis=1))
    if not len(ix):
        return []
    xs = x0 + ix * h
    peak = col_value[ix].max(axis=1)
    bins = np.floor(xs / step_mm).astype(int)
    return [{"x_mm": float(b * step_mm), "max_mm": round(float(peak[bins == b].max()), 2)} for b in np.unique(bins)]


@dataclass
class ClosureResult:
    phi_a: np.ndarray  # float32, full grid; +far outside A's padded box
    phi_c_star: np.ndarray  # float32, min(phi_c, phi_a)
    added: np.ndarray  # bool, A = V \ C cropped to ``box``
    box: tuple[slice, slice, slice]  # where ``added`` and the exact part of phi_a live
    grid: Grid
    alpha_b_deg: float
    report: dict


def closure(
    phi_c: np.ndarray,
    grid: Grid,
    t: float = WALL_MM,
    phi_e: np.ndarray | None = None,
    region: np.ndarray | None = None,
    heel_frac: float = HEEL_FRAC,
) -> ClosureResult:
    """Self-supporting closure of the cavity field ``phi_c`` (phi < 0 inside the cavity).

    ``t`` is the wall thickness the closure shell will get (pads phi_A's box, sets the shell test).
    With the outer envelope ``phi_e`` on the same grid the report also holds how far the closure
    shell sticks out of it and the bridge width ``b_req = 2 * stick_out * tan(alpha_b)`` that
    truncating the ridge there would leave (§3, D3). Nothing here depends on how ``phi_c`` was made.

    ``region`` (bool, same grid, normally M_below) limits the report to where the closure matters:
    closure added above the collar is cut away by the collar trim, so it is not counted. The fields
    ``phi_a`` / ``phi_c_star`` are never masked. ``phi_c_star`` equals ``phi_c`` exactly outside A's padded
    box; ``phi_a`` there is the constant lower bound ``far`` (> t), not a distance.
    """
    phi_c = np.asarray(phi_c, np.float32)
    if phi_c.shape != tuple(grid.shape):
        raise SDFError(f"phi_c shape {phi_c.shape} does not match grid {grid.shape}")
    if phi_e is not None and phi_e.shape != phi_c.shape:
        raise SDFError("phi_e must be on the same grid as phi_c")
    if region is not None and region.shape != phi_c.shape:
        raise SDFError("region must be on the same grid as phi_c")
    h, dz = grid.h, grid.dz
    alpha = alpha_of(grid)
    tan_a = h / dz
    sp = np.array(grid.spacing)
    voxel = h * h * dz

    c = phi_c < 0
    if not c.any():
        raise SDFError("cavity is empty (nothing below zero)")
    v = close_overhangs(c)
    a = v & ~c
    n_a = int(a.sum())
    a_rep = a if region is None else a & region  # what the report counts
    n_rep = int(a_rep.sum())

    pad = np.ceil(t / sp).astype(int) + 1
    far = float((pad * sp).min())
    # Invariant: phi_a >= far outside the box and far > t, so phi_a - t > 0 there and min(phi_c, phi_a) is
    # phi_c wherever phi_c < far. Outside the box phi_a is a lower bound, not a true distance.
    phi_a = np.full(phi_c.shape, far, np.float32)
    report: dict = {
        "alpha_b_deg": round(alpha, 3),
        "cavity_volume_mm3": round(float(c.sum() * voxel), 0),
        "added_volume_mm3": round(n_a * voxel, 0),
        "added_volume_in_region_mm3": round(n_rep * voxel, 0),
        "added_frac_of_cavity": round(n_a / int(c.sum()), 4),
    }
    if n_a == 0:
        report.update(ceiling_raise_max_mm=0.0, ceiling_raise_p95_mm=0.0, ceiling_raise_by_x=[],
                      heel_zone_x_mm=None, heel_loss_volume_mm3=0.0, heel_loss_depth_mm=0.0,
                      shell_clipped=False, ridge_hit_grid_top=False)
        if phi_e is not None:
            report.update(shell_columns_outside_envelope=0, shell_protrusion_max_mm=0.0, shell_protrusion_at_xy_mm=None, shell_protrusion_by_x=[],
                          shell_out_of_envelope_max_mm=0.0, b_req_max_mm=0.0, b_req_by_x=[])
        return ClosureResult(phi_a, phi_c.copy(), a[:0, :0, :0], (slice(0, 0),) * 3, grid, alpha, report)

    idx = np.argwhere(a)
    lo = np.maximum(idx.min(axis=0) - 1 - pad, 0)  # -1: the grown A reaches one voxel beyond A
    hi = np.minimum(idx.max(axis=0) + 2 + pad, grid.shape)
    clipped = bool((idx.min(axis=0) - 1 - pad < 0).any() or (idx.max(axis=0) + 2 + pad > grid.shape).any())
    box = tuple(slice(int(l), int(u)) for l, u in zip(lo, hi))
    a_box = a[box]
    phi_a[box] = _signed_edt(_grow_overlap(a_box), sp)
    phi_star = phi_c.copy()  # exact phi_c everywhere outside the box; the min only happens inside it
    phi_star[box] = np.minimum(phi_c[box], phi_a[box])

    # --- ceiling raise: top of V over top of C per column
    # with a region, A outside it is ignored: a ridge that starts below the region cut gets a partial raise
    top_v, top_c = _top_index(c | a_rep), _top_index(c)
    raise_mm = np.where(a_rep.any(axis=2), (top_v - top_c) * dz, 0.0)
    cols = raise_mm[raise_mm > 0]
    report["ceiling_raise_max_mm"] = round(float(raise_mm.max()), 2)
    report["ceiling_raise_p95_mm"] = round(float(np.percentile(cols, 95)) if len(cols) else 0.0, 2)
    report["ceiling_raise_by_x"] = _by_x(raise_mm, grid.origin[0], h)

    # --- heel wrap lost (volume, and how far from C it reaches)
    cx = np.flatnonzero(c.any(axis=(1, 2)))
    x_lo, x_hi = grid.origin[0] + cx[0] * h, grid.origin[0] + cx[-1] * h
    heel_x = x_lo + heel_frac * (x_hi - x_lo)
    xs = grid.origin[0] + np.arange(grid.shape[0]) * h
    # Heel loss = A in the heel zone with cavity further toe-ward in the same (y, z) row, i.e. A sitting
    # between the heel wall and the cavity. A ceiling that rises toward the toe is geometrically the same
    # thing (a leaning wall under 47 deg from horizontal is a ceiling), so this is an upper bound on wrap
    # actually lost; a flat ceiling has no cavity beside it at ridge height and is not counted.
    ix = np.arange(grid.shape[0], dtype=np.int32)[:, None, None]
    nxt = np.minimum.accumulate(np.where(c, ix, np.int32(2**30))[::-1], axis=0)[::-1]
    nxt = np.concatenate([nxt[1:], np.full_like(nxt[:1], 2**30)])  # strictly toe-ward
    heel = a_rep & (nxt < 2**30) & (xs <= heel_x)[:, None, None]
    report["heel_zone_x_mm"] = [round(float(x_lo), 1), round(float(heel_x), 1)]
    report["heel_loss_volume_mm3"] = round(float(heel.sum() * voxel), 0)
    # depth along x: from the heel-side voxel of A to the next cavity voxel toe-ward in its row
    report["heel_loss_depth_mm"] = round(float(((nxt - ix)[heel]).max() * h) if heel.any() else 0.0, 2)

    # --- closure shell vs the outer envelope
    # clipped: A's padded box was cut by a grid face on any axis, so the closure shell may be truncated.
    # ridge_hit_grid_top: the closure itself reached the top layer (the ridge is cut by the grid, not by geometry);
    # it implies shell_clipped.
    report["shell_clipped"] = clipped
    report["ridge_hit_grid_top"] = bool((v[:, :, -1] & ~c[:, :, -1]).any())
    if phi_e is not None:
        # true top of the closure shell per column (includes the offset t normal to the sloped ridge faces);
        # top_e is deliberately not limited to ``region``: material above the region still covers the shell.
        shell = phi_a[box] < t
        if region is not None:
            shell &= region[box]
        top_s = np.full(phi_c.shape[:2], -1)
        ts = _top_index(shell)
        top_s[box[0], box[1]] = np.where(ts >= 0, ts + box[2].start, -1)
        top_e = _top_index(phi_e < 0)
        ok = (top_s >= 0) & (top_e >= 0)
        stick = np.where(ok, np.maximum(top_s - top_e, 0) * dz, 0.0)
        report["shell_columns_outside_envelope"] = int(((top_s >= 0) & (top_e < 0)).sum())
        report["shell_protrusion_max_mm"] = round(float(stick.max()), 2)
        ix, iy = np.unravel_index(int(np.argmax(stick)), stick.shape)
        report["shell_protrusion_at_xy_mm"] = [round(grid.origin[0] + ix * h, 1), round(grid.origin[1] + iy * h, 1)]
        report["shell_protrusion_by_x"] = _by_x(stick, grid.origin[0], h)
        report["shell_out_of_envelope_max_mm"] = round(float(max(phi_e[box][shell].max(), 0.0)) if shell.any() else 0.0, 2)
        b_req = 2.0 * stick * tan_a
        report["b_req_max_mm"] = round(float(b_req.max()), 1)
        report["b_req_by_x"] = _by_x(b_req, grid.origin[0], h)

    return ClosureResult(phi_a, phi_star, a_box, box, grid, alpha, report)
