"""Process configuration and manufacturability checks (#10 S6 follow-up: SLS/MJF TPU).

* :class:`ProcessConfig` - wall / closure / bed / shrink / powder / build-volume settings from a TOML file
  (``config/process_sls_tpu.toml``, ``config/process_fdm_tpu.toml``). Nothing is hard-coded: the numbers are
  generic reference ranges until a factory data sheet exists.
* :func:`sealed_cavities` - air that cannot reach the outside (no powder escape): count and volume.
* :func:`channel_width` - widest ball that passes from the cavity core to the outside (narrowest internal
  channel on the best route) and the cavity volume a ball of ``min_channel`` cannot reach.
* :func:`build_volume` - bounding box and the smallest rotated envelope about z against machine sizes.
"""

from __future__ import annotations

import math
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from scipy import ndimage
from scipy.spatial import ConvexHull

from .sdf import Grid

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"


class ProcessError(Exception):
    pass


@dataclass(frozen=True)
class ProcessConfig:
    name: str
    wall: float
    wall_min: float
    t_fill: float
    sole_min: float
    closure: bool
    alpha_b_deg: float
    bed_cut: bool
    shrink_mm: float
    min_channel_mm: float
    min_xy_mm: float
    part_gap_mm: float
    machines: dict = field(default_factory=dict)  # name -> (x, y, z)

    @classmethod
    def from_toml(cls, path: str | Path) -> ProcessConfig:
        p = Path(path)
        if not p.exists() and (CONFIG_DIR / p).exists():
            p = CONFIG_DIR / p
        with open(p, "rb") as f:
            raw = tomllib.load(f)
        try:
            return cls(
                name=raw["process"]["name"],
                wall=float(raw["wall"]["target_mm"]), wall_min=float(raw["wall"]["min_mm"]),
                t_fill=float(raw["wall"]["fill_mm"]), sole_min=float(raw["wall"]["sole_min_mm"]),
                closure=bool(raw["closure"]["enabled"]), alpha_b_deg=float(raw["closure"]["alpha_b_deg"]),
                bed_cut=bool(raw["bed"]["cut"]), shrink_mm=float(raw["shrink"]["compensation_mm"]),
                min_channel_mm=float(raw["powder"]["min_channel_mm"]),
                min_xy_mm=float(raw["fit"]["min_xy_mm"]), part_gap_mm=float(raw["fit"]["part_gap_mm"]),
                machines={k: (float(v["x"]), float(v["y"]), float(v["z"])) for k, v in raw.get("machine", {}).items()},
            )
        except KeyError as e:
            raise ProcessError(f"{p}: missing key {e}") from None

    def to_dict(self) -> dict:
        return {k: (list(v) if isinstance(v, tuple) else v) for k, v in self.__dict__.items()}


def sealed_cavities(phi: np.ndarray, grid: Grid) -> dict:
    """Air voxels (phi >= 0) not connected to the grid border: they would trap powder. Needs a grid margin."""
    air, n = ndimage.label(phi >= 0)
    outside = air[0, 0, 0]
    sealed = (air != outside) & (air > 0)
    count = n - 1 if n else 0
    voxel = grid.h * grid.h * grid.dz
    return {"count": int(count), "volume_mm3": round(float(sealed.sum() * voxel), 2)}


def mesh_inner_shells(mesh) -> dict:
    """Inward-facing shells (negative volume) of the exported mesh: the same thing seen on the STL."""
    parts = mesh.split(only_watertight=False)
    inner = [p for p in parts if p.volume < -1e-6]
    return {"count": len(inner), "volume_mm3": round(float(sum(-p.volume for p in inner)), 2)}


def channel_width(phi: np.ndarray, grid: Grid, core_mask: np.ndarray, min_channel: float = 5.0, iters: int = 14) -> dict:
    """Narrowest internal channel and powder reach.

    ``dist`` = distance of every air voxel to the solid. ``width_mm`` = 2 r* with r* the largest radius for which
    the cavity core (the air voxel of ``core_mask`` farthest from the solid) is still connected to the grid border
    through voxels with ``dist >= r*``: the biggest ball that can be rolled out. ``unreachable_mm3`` = air of the
    cavity that lies farther than ``min_channel / 2`` from every voxel connected to the border through
    ``dist >= min_channel / 2`` (thin pockets a ``min_channel`` nozzle or powder stream cannot enter).
    """
    air = phi >= 0
    dist = ndimage.distance_transform_edt(air, sampling=grid.spacing).astype(np.float32)
    core = np.where(core_mask & air, dist, -1.0)
    c = np.unravel_index(int(np.argmax(core)), core.shape)
    r_core = float(dist[c])

    def connected(r):
        lab, _ = ndimage.label(dist >= r)
        return lab[c] != 0 and lab[c] == lab[0, 0, 0]

    lo, hi = 0.0, r_core
    if connected(hi):
        lo = hi
    else:
        for _ in range(iters):
            mid = 0.5 * (lo + hi)
            lo, hi = (mid, hi) if connected(mid) else (lo, mid)
    r = 0.5 * min_channel
    lab, _ = ndimage.label(dist >= r)
    reach = lab == lab[0, 0, 0]
    far = ndimage.distance_transform_edt(~reach, sampling=grid.spacing) > r
    unreachable = far & air & core_mask
    voxel = grid.h * grid.h * grid.dz
    return {"width_mm": round(2.0 * lo, 2), "core_diameter_mm": round(2.0 * r_core, 2), "min_channel_mm": min_channel,
            "ok": bool(2.0 * lo >= min_channel), "unreachable_mm3": round(float(unreachable.sum() * voxel), 1)}


def rotated_envelope(xy: np.ndarray, step_deg: float = 0.5) -> dict:
    """Smallest rectangle about z around the xy footprint: the one with the smallest longer side, and the min area."""
    hull = xy[ConvexHull(xy).vertices]
    best_side = best_area = None
    for ang in np.arange(0.0, 90.0, step_deg):
        a = math.radians(ang)
        r = hull @ np.array([[math.cos(a), math.sin(a)], [-math.sin(a), math.cos(a)]])
        w, h = np.ptp(r[:, 0]), np.ptp(r[:, 1])
        if best_side is None or max(w, h) < best_side[0]:
            best_side = (max(w, h), ang, w, h)
        if best_area is None or w * h < best_area[0]:
            best_area = (w * h, ang, w, h)
    return {"min_longer_side": {"angle_deg": best_side[1], "x_mm": round(best_side[2], 1), "y_mm": round(best_side[3], 1)},
            "min_area": {"angle_deg": best_area[1], "x_mm": round(best_area[2], 1), "y_mm": round(best_area[3], 1)}}


def build_volume(vertices: np.ndarray, machines: dict, gap_mm: float = 5.0, min_xy_mm: float = 0.0) -> dict:
    """Bounding box and rotated footprint against machine build volumes (x, y, z) with ``gap_mm`` kept around the part.

    ``aligned``: axis-aligned box + gap fits. ``rotated``: the min-longer-side rectangle + gap fits (either way round).
    ``meets_min_xy``: the machine X and Y are both >= ``min_xy_mm`` (the vendor's stated requirement).
    """
    v = np.asarray(vertices, float)
    ext = np.ptp(v, axis=0)
    env = rotated_envelope(v[:, :2])
    rx, ry = env["min_longer_side"]["x_mm"], env["min_longer_side"]["y_mm"]
    out = {"bbox_mm": [round(float(e), 1) for e in ext], "rotated": env, "machines": {}}
    for name, (mx, my, mz) in machines.items():
        z_ok = ext[2] + gap_mm <= mz
        aligned = bool(z_ok and ext[0] + gap_mm <= mx and ext[1] + gap_mm <= my)
        rot = bool(z_ok and ((rx + gap_mm <= mx and ry + gap_mm <= my) or (ry + gap_mm <= mx and rx + gap_mm <= my)))
        out["machines"][name] = {"size_mm": [mx, my, mz], "aligned": aligned, "rotated": rot,
                                 "meets_min_xy": bool(mx >= min_xy_mm and my >= min_xy_mm)}
    return out
