"""Rigid pose of the last inside the template's outer envelope, plus the gap map (#10 S3).

Design: docs/design/issue-10-cavity-rebuild.md §1.3.

Inputs are deliberately narrow so S3 does not depend on S2's implementation: the envelope is a
signed distance field ``phi_e`` (``< 0`` inside, on a :class:`~shoe_designer.sdf.Grid`) and
``m_below`` is a mask on the same grid (the "below the mouth" region, M_below). Both are
defined by the template alone and do not move with the last.

For a surface point ``p`` of the posed last the deficit is how far short the outer skin falls of
``ease + wall``::

    deficit(p) = max(0, phi_e(p) + ease(p) + wall(p))

The pose minimises ``sum(w(p) * deficit(p)**2)`` over last surface samples. By default
(``weight_mode="fixed"``) ``w`` is the trilinearly interpolated ``m_below`` at the *nominal* pose
(xy-centred, no rotation, lowest point at ``s_min``) and stays attached to the last's points.
Sampling the mask at the posed point instead (``weight_mode="pose"``) lets the optimiser drop
a deficient point out of the score by pitching or lifting it above the mouth; ``flags
["mask_escape"]`` reports the posed-mask area lost that way, in either mode.

Pose parameters (``Pose``): translation ``dx, dy`` relative to the xy-centred start, ``lift``
(extra height above the sole minimum), ``pitch`` and ``yaw`` in degrees. Rotation is about the
centre of the last's bounding box, ``R = Rz(yaw) @ Ry(pitch)``; **positive pitch tilts the toe
down** (+X forward, +Z up). The vertical translation is not free: it is solved so that the lowest
point of the rotated last sits exactly ``s_min + lift`` above the bed. That makes the sole
constraint hold for every pitch/yaw and puts the pitch-vs-sole trade-off into the search
space rather than into a penalty. ``lift`` is bounded by ``max_lift`` so the optimiser cannot buy
a lower score by floating the last up and out through the mouth, where M_below stops counting it.

Search: a Sobol sample of the pose box scored on a subsample, then Nelder-Mead from the nominal
start and the best well-separated candidates. All refinements are reported, so a disagreement
between starts (a local optimum) is visible.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import trimesh
from scipy.optimize import minimize
from scipy.stats import qmc

from .sdf import Grid, sample

Field = float | Callable[[np.ndarray, np.ndarray], np.ndarray]
PARAM_NAMES = ("dx", "dy", "lift", "pitch", "yaw")
SIDES = ("y+", "y-")  # last-frame +Y (left of the heading for +X forward, +Z up) / -Y
THIRDS = ("heel", "mid", "toe")
FACES = ("sole", "side", "top")  # by last-surface normal: nz < -0.5 / |nz| <= 0.5 / nz > 0.5
THIN_DEFICIT_MM = 0.5
MASK_ESCAPE_FRAC = 0.02  # warn when the optimum moves > 2% of the last's area out of M_below


def rotation(pitch_deg: float, yaw_deg: float) -> np.ndarray:
    p, y = math.radians(pitch_deg), math.radians(yaw_deg)
    cp, sp, cy, sy = math.cos(p), math.sin(p), math.cos(y), math.sin(y)
    ry = np.array([[cp, 0, sp], [0, 1, 0], [-sp, 0, cp]])  # Ry(t) x = (cos t, 0, -sin t): toe down
    rz = np.array([[cy, -sy, 0], [sy, cy, 0], [0, 0, 1]])
    return rz @ ry


@dataclass(frozen=True)
class Pose:
    dx: float
    dy: float
    lift: float
    pitch: float  # degrees, + = toe down
    yaw: float  # degrees

    def as_array(self) -> np.ndarray:
        return np.array([self.dx, self.dy, self.lift, self.pitch, self.yaw])


@dataclass
class GapMap:
    """Per-sample deficit of the posed last against the envelope (last-frame and world points)."""

    points_last: np.ndarray  # (N, 3) last frame
    normals_last: np.ndarray  # (N, 3)
    points: np.ndarray  # (N, 3) template frame (posed)
    deficit: np.ndarray  # (N,) mm, >= 0
    slack: np.ndarray  # (N,) mm; spare room (+) or shortfall (-): -(phi_e + ease + wall)
    weight: np.ndarray  # (N,) M_below weight in [0, 1]
    area_per_point: float  # mm^2
    x_range: tuple[float, float]  # last-frame x extent, for the heel/mid/toe split
    y_mid: float

    @property
    def counted(self) -> np.ndarray:
        return self.weight >= 0.5

    def cells(self) -> dict[str, np.ndarray]:
        """Boolean selector per region name: thirds, sides, faces, and thirds x sides."""
        x0, x1 = self.x_range
        f = np.clip((self.points_last[:, 0] - x0) / (x1 - x0), 0, 1 - 1e-9)
        third = (f * 3).astype(int)
        side = (self.points_last[:, 1] < self.y_mid).astype(int)
        nz = self.normals_last[:, 2]
        face = np.where(nz < -0.5, 0, np.where(nz > 0.5, 2, 1))
        out: dict[str, np.ndarray] = {"all": np.ones(len(f), bool)}
        for i, n in enumerate(THIRDS):
            out[n] = third == i
        for i, n in enumerate(SIDES):
            out[n] = side == i
        for i, n in enumerate(FACES):
            out[n] = face == i
        for i, tn in enumerate(THIRDS):
            for j, sn in enumerate(SIDES):
                out[f"{tn}/{sn}"] = (third == i) & (side == j)
        return out

    def summary(self) -> dict[str, dict]:
        """Deficit statistics per region, over the samples inside M_below only."""
        res = {}
        cnt = self.counted
        for name, sel in self.cells().items():
            s = sel & cnt
            if not s.any():
                res[name] = {"area_mm2": 0.0}
                continue
            d = self.deficit[s]
            k = int(np.argmax(d))
            res[name] = {
                "area_mm2": round(float(s.sum() * self.area_per_point), 1),
                "frac_over_0p5": round(float((d > THIN_DEFICIT_MM).mean()), 4),
                "mean": round(float(d.mean()), 3),
                "p95": round(float(np.percentile(d, 95)), 3),
                "max": round(float(d.max()), 3),
                "worst_at": [round(float(v), 1) for v in self.points[s][k]],
            }
        return res


@dataclass
class StartRecord:
    kind: str  # "nominal" or "sobol"
    start: Pose
    pose: Pose
    cost: float  # mm^4 (area-weighted sum of squared deficit)
    n_eval: int
    seconds: float


@dataclass
class PoseResult:
    pose: Pose
    transform: np.ndarray  # 4x4: last frame -> template frame
    cost: float
    rms: float  # sqrt(cost / counted area), mm
    baseline: GapMap  # nominal pose (xy-centred, no rotation, lowest point at s_min)
    gap: GapMap  # optimised pose
    baseline_cost: float
    starts: list[StartRecord]
    flags: dict = field(default_factory=dict)
    seconds: float = 0.0

    def to_dict(self) -> dict:
        return {
            "pose": {k: round(float(v), 4) for k, v in zip(PARAM_NAMES, self.pose.as_array())},
            "transform": np.round(self.transform, 5).tolist(),
            "cost": self.cost,
            "baseline_cost": self.baseline_cost,
            "rms_deficit_mm": self.rms,
            "flags": self.flags,
            "gap": self.gap.summary(),
            "baseline_gap": self.baseline.summary(),
            "starts": [
                {
                    "kind": s.kind,
                    "start": {k: round(float(v), 3) for k, v in zip(PARAM_NAMES, s.start.as_array())},
                    "pose": {k: round(float(v), 3) for k, v in zip(PARAM_NAMES, s.pose.as_array())},
                    "cost": s.cost,
                    "n_eval": s.n_eval,
                    "seconds": round(s.seconds, 2),
                }
                for s in self.starts
            ],
            "seconds": round(self.seconds, 2),
        }


class _Problem:
    def __init__(self, phi_e, grid: Grid, m_below, last: trimesh.Trimesh, ease: Field, wall: Field,
                 s_min: float, bed_z: float, n_points: int, seed: int, xy_center,
                 weight_mode: str = "pose"):
        if weight_mode not in ("fixed", "pose"):
            raise ValueError("weight_mode must be 'fixed' or 'pose'")
        if phi_e.shape != grid.shape or np.shape(m_below) != grid.shape:
            raise ValueError("phi_e and m_below must have the grid's shape")
        self.phi = phi_e
        self.grid = grid
        self.mask = np.asarray(m_below, np.float32)
        self.s_min, self.bed_z = s_min, bed_z
        pts, fidx = trimesh.sample.sample_surface(last, n_points, seed=seed)
        # shuffle once: prefixes are used as uniform subsamples and must not depend on the sampler's order
        perm = np.random.default_rng(seed).permutation(len(pts))
        self.pts = np.asarray(pts)[perm]
        self.normals = np.asarray(last.face_normals[fidx])[perm]
        self.area = float(last.area) / len(self.pts)
        need = _eval_field(ease, self.pts, self.normals) + _eval_field(wall, self.pts, self.normals)
        self.need = np.broadcast_to(need, (len(self.pts),)).astype(np.float64)
        b = last.bounds
        self.pivot = b.mean(axis=0)
        self.x_range = (float(b[0, 0]), float(b[1, 0]))
        self.y_mid = float(self.pivot[1])
        self.hull = np.asarray(last.convex_hull.vertices)
        if xy_center is None:
            inside = np.argwhere(phi_e < 0)
            if not len(inside):
                raise ValueError("phi_e has no interior")
            lo, hi = grid.coords(inside.min(axis=0)), grid.coords(inside.max(axis=0))
            xy_center = (lo[:2] + hi[:2]) / 2
        self.t0 = np.array([xy_center[0] - self.pivot[0], xy_center[1] - self.pivot[1]])
        self.w_fixed = None
        if weight_mode == "fixed":
            self.w_fixed = self.posed_mask(Pose(0.0, 0.0, 0.0, 0.0, 0.0))

    def posed_mask(self, pose: "Pose", idx=slice(None)) -> np.ndarray:
        return np.clip(sample(self.mask, self.grid, self.posed(pose, idx)), 0.0, 1.0)

    def transform(self, pose: Pose) -> np.ndarray:
        r = rotation(pose.pitch, pose.yaw)
        low = ((self.hull - self.pivot) @ r.T + self.pivot)[:, 2].min()
        t = np.array([self.t0[0] + pose.dx, self.t0[1] + pose.dy,
                      self.bed_z + self.s_min + pose.lift - low])
        m = np.eye(4)
        m[:3, :3] = r
        m[:3, 3] = t + self.pivot - r @ self.pivot
        return m

    def posed(self, pose: Pose, idx=slice(None)) -> np.ndarray:
        m = self.transform(pose)
        return self.pts[idx] @ m[:3, :3].T + m[:3, 3]

    def deficits(self, pose: Pose, idx=slice(None)):
        p = self.posed(pose, idx)
        d = np.maximum(0.0, sample(self.phi, self.grid, p) + self.need[idx])
        w = self.w_fixed[idx] if self.w_fixed is not None else np.clip(sample(self.mask, self.grid, p), 0.0, 1.0)
        return d, w, p

    def cost(self, pose: Pose, idx=slice(None)) -> float:
        d, w, _ = self.deficits(pose, idx)
        n = len(d)
        return float(self.area * len(self.pts) / n * np.sum(w * d * d))

    def gap(self, pose: Pose) -> GapMap:
        d, w, p = self.deficits(pose)
        slack = -(sample(self.phi, self.grid, p) + self.need)
        return GapMap(self.pts, self.normals, p, d, slack, w, self.area, self.x_range, self.y_mid)


def _eval_field(f: Field, pts, normals) -> np.ndarray:
    return np.asarray(f(pts, normals) if callable(f) else f, dtype=np.float64)


def gap_map(phi_e, grid: Grid, m_below, last: trimesh.Trimesh, transform: np.ndarray, ease: Field = 1.0,
            wall: Field = 2.0, n_points: int = 20000, seed: int = 0) -> GapMap:
    """Gap map of ``last`` placed by an arbitrary rigid ``transform`` (4x4, last -> template frame)."""
    prob = _Problem(phi_e, grid, m_below, last, ease, wall, 0.0, 0.0, n_points, seed, (0.0, 0.0), "pose")
    p = prob.pts @ transform[:3, :3].T + transform[:3, 3]
    s = sample(phi_e, grid, p)
    d = np.maximum(0.0, s + prob.need)
    w = np.clip(sample(prob.mask, grid, p), 0.0, 1.0)
    return GapMap(prob.pts, prob.normals, p, d, -(s + prob.need), w, prob.area, prob.x_range, prob.y_mid)


def fit_pose(
    phi_e: np.ndarray,
    grid: Grid,
    m_below: np.ndarray,
    last: trimesh.Trimesh,
    *,
    ease: Field = 1.0,
    wall: Field = 2.0,
    s_min: float = 6.0,
    bed_z: float = 0.0,
    weight_mode: str = "fixed",
    pitch_max_deg: float = 5.0,
    yaw_max_deg: float = 3.0,
    search_xy: tuple[float, float] = (10.0, 8.0),
    max_lift: float = 4.0,
    xy_center=None,
    n_coarse: int = 512,
    n_starts: int = 5,
    n_points: int = 20000,
    n_refine_points: int = 8000,
    seed: int = 0,
    maxiter: int = 600,
) -> PoseResult:
    """Best rigid pose of ``last`` (canonical frame) inside the envelope; see module docstring.

    ``ease`` / ``wall`` are mm, scalars or ``f(points_last, normals_last) -> array``.
    The envelope interior is centred in xy by its ``phi_e < 0`` bounding box unless ``xy_center``
    is given. ``max_lift`` caps how far above ``s_min`` the lowest point of the last may sit
    (guards against floating the last out through the mouth; 4 mm is an empirical value,
    about half of Zellerfeld's ~8 mm sole thickness; ``flags["lift_at_cap"]`` reports when it binds). ``weight_mode``: ``"fixed"`` or ``"pose"`` (see module docstring).
    """
    t_all = time.perf_counter()
    prob = _Problem(phi_e, grid, m_below, last, ease, wall, s_min, bed_z, n_points, seed, xy_center, weight_mode)
    lo = np.array([-search_xy[0], -search_xy[1], 0.0, -pitch_max_deg, -yaw_max_deg])
    hi = np.array([search_xy[0], search_xy[1], max_lift, pitch_max_deg, yaw_max_deg])
    span = np.where(hi > lo, hi - lo, 1.0)

    def pose_of(u):
        return Pose(*(lo + np.clip(u, 0, 1) * span))

    def to_u(pose: Pose):
        return (pose.as_array() - lo) / span

    n_sub = min(4000, len(prob.pts))
    nominal = Pose(0.0, 0.0, 0.0, 0.0, 0.0)
    u_nom = np.clip(to_u(nominal), 0, 1)

    # Coarse global search on a subsample of the surface points.
    m = max(1, math.ceil(math.log2(max(n_coarse, 2))))
    cand = qmc.Sobol(5, scramble=True, seed=seed).random_base2(m)
    sub = slice(0, n_sub)
    coarse = np.array([prob.cost(pose_of(u), sub) for u in cand])
    order = np.argsort(coarse)
    picked: list[np.ndarray] = []
    for i in order:
        if all(np.linalg.norm(cand[i] - q) > 0.3 for q in picked + [u_nom]):
            picked.append(cand[i])
        if len(picked) >= n_starts:
            break
    starts = [("nominal", u_nom)] + [("sobol", u) for u in picked]

    refine_idx = slice(0, min(n_refine_points, len(prob.pts)))
    records: list[StartRecord] = []
    for kind, u0 in starts:
        t0 = time.perf_counter()
        res = minimize(lambda u: prob.cost(pose_of(u), refine_idx), u0, method="Nelder-Mead",
                       bounds=[(0.0, 1.0)] * 5,
                       options={"maxiter": maxiter, "xatol": 2e-4, "fatol": 1e-6 * max(1.0, coarse.min()),
                                "initial_simplex": _simplex(u0)})
        pose = pose_of(res.x)
        records.append(StartRecord(kind, pose_of(u0), pose, prob.cost(pose), int(res.nfev),
                                   time.perf_counter() - t0))
    best = min(records, key=lambda r: r.cost)
    pose = best.pose

    base_gap = prob.gap(nominal)
    gap = prob.gap(pose)
    counted_area = max(float(gap.counted.sum() * prob.area), 1e-9)
    cost = prob.cost(pose)
    res = PoseResult(
        pose=pose, transform=prob.transform(pose), cost=cost,
        rms=math.sqrt(cost / counted_area), baseline=base_gap, gap=gap,
        baseline_cost=prob.cost(nominal), starts=records,
    )
    res.flags = _flags(res, lo, hi, s_min, max_lift)
    nominal_mask = prob.posed_mask(nominal)
    final_mask = prob.posed_mask(pose)
    drop = float((nominal_mask >= 0.5).mean() - (final_mask >= 0.5).mean())
    res.flags["posed_mask_area_drop"] = drop
    res.flags["mask_escape"] = drop > MASK_ESCAPE_FRAC
    res.seconds = time.perf_counter() - t_all
    return res


def _simplex(u0: np.ndarray, step: float = 0.08) -> np.ndarray:
    sx = np.vstack([u0] * 6)
    for i in range(5):
        sx[i + 1, i] += step if u0[i] + step <= 1.0 else -step
    return sx


def _flags(res: PoseResult, lo, hi, s_min, max_lift) -> dict:
    p = res.pose.as_array()
    tol = 0.005 * (hi - lo)
    at_hi = p >= hi - tol
    at_lo = p <= lo + tol
    best = res.cost
    close = [r for r in res.starts if r.cost <= best * 1.05 + 1e-6]
    spread = np.ptp([r.pose.as_array() for r in close], axis=0) if len(close) > 1 else np.zeros(5)
    return {
        "sole_active": bool(p[2] <= tol[2]),  # lift ~ 0: lowest point sits exactly at s_min
        "lift_at_cap": bool(p[2] >= hi[2] - tol[2] and max_lift > 0),
        "pitch_at_bound": bool(at_hi[3] or at_lo[3]),
        "yaw_at_bound": bool(at_hi[4] or at_lo[4]),
        "xy_at_bound": bool(at_hi[:2].any() or at_lo[:2].any()),
        "counted_area_frac": float(res.gap.counted.mean()),
        "baseline_counted_area_frac": float(res.baseline.counted.mean()),
        "n_starts": len(res.starts),
        "n_starts_within_5pct": len(close),
        "start_costs": sorted(round(r.cost, 1) for r in res.starts),
        "param_spread_among_close": {k: round(float(v), 3) for k, v in zip(PARAM_NAMES, spread)},
        "worst_start_over_best": float(max(r.cost for r in res.starts) / best) if best > 0 else None,
    }
