"""Pattern layer on phi_shoe (#13): perforation (through holes) and outward surface displacement.

Input is the composed ``phi_shoe`` (S6) plus the cavity field; nothing of S1-S6 is changed. The pattern lives in a
2D parameter plane per surface family (the plane perpendicular to the dominant axis of the local normal)::

    side  -> (x, z)   normal ~ +-y          top -> (x, y)   normal ~ +z          end -> (y, z)   normal ~ +-x

Every wall voxel is projected to its closest outer-skin point ``q = p - psi(p) n`` (psi = signed distance of the
solid with the foot cavity filled), so a hole is cut along the surface normal and goes through the whole wall.

* perforation: ``phi_new = max(phi, -s(q))`` with ``s`` the signed distance to the hole (hex / voronoi / image).
  Cells are all-or-nothing: a cell that touches a protected or invalid area stays solid, so no partial holes.
* displacement: ``phi_new = phi - h(q)`` in a band around the skin, ``h`` in ``[0, depth]``; only outward, and the
  amplitude fades to 0 towards protected areas. The cavity is never touched (guard ``max(phi_new, -phi_cavity)``).

Protected (never patterned): sole (below cavity floor + margin), collar (within margin of z_T), heel / toe areas
unless named in ``zones``, faces turned more than ``acos(min_normal_cos)`` away from their projection axis, and
perforation also where the wall is thinner / thicker than ``perf_wall_min/max``. All numbers: ``config/pattern.toml``.
"""

from __future__ import annotations

import math
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import trimesh
from scipy import ndimage
from scipy.spatial import cKDTree
from skimage import measure

from .process import CONFIG_DIR, mesh_inner_shells, sealed_cavities
from .sdf import Grid, sample

KINDS = ("none", "hex_perforation", "voronoi_perforation", "wave_displacement", "rib_displacement", "image")
ZONES = ("side", "top", "toe", "heel")
IMAGE_USES = ("perforation", "displacement")
RANGES = {"scale_mm": (4.0, 30.0), "open_ratio": (0.05, 0.6), "depth_mm": (0.0, 2.0)}
DEFAULT_ZONES = ("side", "top")
SIDE, TOP, END = 0, 1, 2
LATTICE_N = 400  # voronoi seed table covers +-LATTICE_N cells around the origin
BIG = 1e3


class PatternError(ValueError):
    pass


# ----------------------------------------------------------------------------------------------- spec / config
@dataclass(frozen=True)
class PatternSpec:
    """Structured pattern parameters (shared with the intent parser, #16). Use :meth:`from_dict` to validate."""

    kind: str = "none"
    scale_mm: float = 10.0
    open_ratio: float = 0.3
    depth_mm: float = 1.0
    zones: tuple = DEFAULT_ZONES
    seed: int = 0
    image: str | None = None
    image_use: str = "perforation"
    clamped: dict = field(default_factory=dict, compare=False)  # field -> {"from": asked, "to": used}
    notes: tuple = field(default=(), compare=False)

    @classmethod
    def from_dict(cls, raw: dict) -> PatternSpec:
        """Validate ``raw``; out-of-range numbers are clamped into the design space and listed in ``clamped``."""
        if not isinstance(raw, dict):
            raise PatternError("pattern spec must be a dict")
        known = {"kind", "scale_mm", "open_ratio", "depth_mm", "zones", "seed", "image", "image_use"}
        notes = [f"ignored unknown key {k!r}" for k in raw if k not in known]
        kind = raw.get("kind", "none")
        if kind not in KINDS:
            raise PatternError(f"kind must be one of {KINDS}, got {kind!r}")
        clamped: dict = {}
        vals = {}
        defaults = {"scale_mm": 10.0, "open_ratio": 0.3, "depth_mm": 1.0}
        for name, (lo, hi) in RANGES.items():
            v = raw.get(name, defaults[name])
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                raise PatternError(f"{name} must be a finite number, got {v!r}")
            v = float(v)
            to = min(max(v, lo), hi)
            if to != v:
                clamped[name] = {"from": v, "to": to}
            vals[name] = to
        zones = raw.get("zones", list(DEFAULT_ZONES))
        if isinstance(zones, str) or not isinstance(zones, (list, tuple, set)):
            raise PatternError("zones must be a list of zone names")
        bad = [z for z in zones if z not in ZONES]
        if bad:
            raise PatternError(f"unknown zones {bad}; allowed {ZONES}")
        zones = tuple(z for z in ZONES if z in set(zones))  # canonical order, no duplicates
        seed = raw.get("seed", 0)
        if isinstance(seed, bool) or not isinstance(seed, (int, float)) or (isinstance(seed, float) and not seed.is_integer()):
            raise PatternError(f"seed must be an integer, got {seed!r}")
        image = raw.get("image")
        use = raw.get("image_use", "perforation")
        if use not in IMAGE_USES:
            raise PatternError(f"image_use must be one of {IMAGE_USES}, got {use!r}")
        if kind == "image":
            if not image or not isinstance(image, (str, Path)):
                raise PatternError("kind 'image' needs an image path")
        elif image:
            notes.append("image given but kind is not 'image': ignored")
        return cls(kind, vals["scale_mm"], vals["open_ratio"], vals["depth_mm"], zones, int(seed),
                   str(image) if image else None, use, clamped, tuple(notes))

    def to_dict(self) -> dict:
        return {"kind": self.kind, "scale_mm": self.scale_mm, "open_ratio": self.open_ratio, "depth_mm": self.depth_mm,
                "zones": list(self.zones), "seed": self.seed, "image": self.image, "image_use": self.image_use}

    @property
    def perforates(self) -> bool:
        return self.kind in ("hex_perforation", "voronoi_perforation") or (self.kind == "image" and self.image_use == "perforation")


@dataclass(frozen=True)
class PatternConfig:
    strut_min_mm: float = 1.2
    strut_margin_mm: float = 0.3
    hole_min_mm: float = 3.0
    perf_wall_min_mm: float = 1.2
    perf_wall_max_mm: float = 6.0
    sole_margin_mm: float = 4.0
    collar_margin_mm: float = 8.0
    heel_frac: float = 0.20
    toe_frac: float = 0.85
    min_normal_cos: float = 0.7
    cell_margin_mm: float = 2.0
    seam_mm: float = 2.0
    fade_mm: float = 3.0
    normal_smooth_mm: float = 1.0
    voronoi_jitter: float = 0.25
    image_fit: str = "contain"
    image_threshold: float = 0.5
    image_raster_mm: float = 0.5
    image_blur_mm: float = 0.6
    density_g_cm3: float = 1.2

    _MAP = {"manufacturing": ("strut_min_mm", "strut_margin_mm", "hole_min_mm", "perf_wall_min_mm", "perf_wall_max_mm"),
            "protect": ("sole_margin_mm", "collar_margin_mm", "heel_frac", "toe_frac", "min_normal_cos", "cell_margin_mm", "seam_mm"),
            "displacement": ("fade_mm", "normal_smooth_mm"), "voronoi": ("jitter",),
            "image": ("fit", "threshold", "raster_mm", "blur_mm"), "material": ("density_g_cm3",)}

    @classmethod
    def from_toml(cls, path: str | Path | None = None) -> PatternConfig:
        p = Path(path) if path else CONFIG_DIR / "pattern.toml"
        if not p.exists() and (CONFIG_DIR / p).exists():
            p = CONFIG_DIR / p
        with open(p, "rb") as f:
            raw = tomllib.load(f)
        kw = {}
        for sec, keys in cls._MAP.items():
            for k in keys:
                if k in raw.get(sec, {}):
                    kw[{"jitter": "voronoi_jitter", "fit": "image_fit", "threshold": "image_threshold", "raster_mm": "image_raster_mm",
                        "blur_mm": "image_blur_mm"}.get(k, k)] = raw[sec][k]
        return cls(**kw)


@dataclass
class PatternMasks:
    """What the pattern layer must not touch (all on the grid of ``phi_shoe``)."""

    phi_cavity: np.ndarray  # cavity field the shoe was composed with (phi_C, or phi_C* when the closure is on); < 0 inside
    below: np.ndarray  # M_below (bool), cavity is "filled" there when measuring the outer skin
    collar: object  # anything with ``z_t(xy (N, 2)) -> (N,)``
    bed_z: float = 0.0
    sole_min: float = 6.0

    @classmethod
    def from_composed(cls, comp, phi_cavity: np.ndarray, collar, process=None) -> PatternMasks:
        return cls(phi_cavity, comp.below, collar, comp.report["params"]["bed_z"],
                   process.sole_min if process is not None else comp.report["params"]["sole_min"] or 6.0)


# ----------------------------------------------------------------------------------------------- textures
def _smoothstep(t):
    t = np.clip(t, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _plane_uv(fam: np.ndarray, side: np.ndarray, pts: np.ndarray, mirror_x: tuple | None = None):
    x, y, z = pts[:, 0], pts[:, 1], pts[:, 2]
    u = np.where(fam == END, y, x)
    if mirror_x is not None:  # viewed from the +y side the image is mirrored so it reads the same from both sides
        u = np.where((fam == SIDE) & (side == 1), mirror_x[0] + mirror_x[1] - x, u)
    v = np.where(fam == TOP, y, z)
    return u, v


class _Hex:
    cells = True

    def __init__(self, p: float, ratio: float):
        self.p, self.ratio = p, ratio
        self.apothem = math.sqrt(ratio) * p / 2.0
        self.h = p * math.sqrt(3.0)
        a = np.radians([0.0, 60.0, 120.0])
        self.normals = np.stack([np.cos(a), np.sin(a)], axis=1)

    def hole(self, fam, side, q):
        u, v = _plane_uv(fam, side, q)
        p, h = self.p, self.h
        ia, ja = np.rint(u / p), np.rint(v / h)
        ib, jb = np.rint(u / p - 0.5), np.rint(v / h - 0.5)
        ca = np.stack([ia * p, ja * h], axis=1)
        cb = np.stack([(ib + 0.5) * p, (jb + 0.5) * h], axis=1)
        uv = np.stack([u, v], axis=1)
        da, db = uv - ca, uv - cb
        use_b = (db**2).sum(axis=1) < (da**2).sum(axis=1)
        d = np.where(use_b[:, None], db, da)
        s = np.abs(d @ self.normals.T).max(axis=1) - self.apothem
        i = np.where(use_b, ib, ia).astype(np.int64)
        j = np.where(use_b, jb, ja).astype(np.int64)
        return s, _key(fam, side, use_b.astype(np.int64), i, j)


def _key(fam, side, sub, i, j):
    return ((((fam.astype(np.int64) * 2 + side) * 2 + sub) * 2001 + (i + 1000)) * 2001 + (j + 1000))


class _Voronoi:
    cells = True

    def __init__(self, p: float, strut: float, jitter: float, seed: int, hole_min: float):
        self.p, self.strut, self.hole_min = p, strut, hole_min
        self.n = LATTICE_N
        self.tables = {}
        self.jitter, self.seed = jitter, seed

    def _table(self, fam: int):
        if fam not in self.tables:
            rng = np.random.default_rng([self.seed & 0x7FFFFFFF, 17 + fam])
            n, p = self.n, self.p
            idx = np.arange(-n, n)
            off = rng.uniform(-self.jitter, self.jitter, (2 * n, 2 * n, 2))
            sx = ((idx[:, None] + 0.5 + off[..., 0]) * p).astype(np.float64)
            sy = ((idx[None, :] + 0.5 + off[..., 1]) * p).astype(np.float64)
            dnn = np.full(sx.shape, np.inf)
            for di in (-1, 0, 1):
                for dj in (-1, 0, 1):
                    if di == 0 and dj == 0:
                        continue
                    dnn = np.minimum(dnn, np.hypot(sx - np.roll(sx, (di, dj), (0, 1)), sy - np.roll(sy, (di, dj), (0, 1))))
            self.tables[fam] = (sx, sy, dnn)
        return self.tables[fam]

    def query(self, fam_id: int, u, v, chunk: int = 60000):
        """(b, dnn of the nearest seed, i, j): distance to the nearest bisector, nearest seed's neighbour gap and index."""
        sx_t, sy_t, dnn_t = self._table(fam_id)
        n, p = self.n, self.p
        offs = np.arange(-2, 3)
        out = [np.empty(len(u)), np.empty(len(u)), np.empty(len(u), np.int64), np.empty(len(u), np.int64)]
        for a in range(0, len(u), chunk):
            uu, vv = u[a : a + chunk], v[a : a + chunk]
            i0, j0 = np.floor(uu / p).astype(np.int64), np.floor(vv / p).astype(np.int64)
            ii = np.clip(i0[:, None, None] + offs[None, :, None], -n, n - 1) + n
            jj = np.clip(j0[:, None, None] + offs[None, None, :], -n, n - 1) + n
            ii, jj = np.broadcast_to(ii, (len(uu), 5, 5)).reshape(len(uu), 25), np.broadcast_to(jj, (len(uu), 5, 5)).reshape(len(uu), 25)
            sx, sy = sx_t[ii, jj], sy_t[ii, jj]
            d2 = (uu[:, None] - sx) ** 2 + (vv[:, None] - sy) ** 2
            r = np.arange(len(uu))
            k = np.argmin(d2, axis=1)
            gap = np.hypot(sx - sx[r, k][:, None], sy - sy[r, k][:, None])
            gap[r, k] = np.inf
            bis = (d2 - d2[r, k][:, None]) / (2.0 * gap)
            bis[r, k] = np.inf
            b = bis.min(axis=1)
            out[0][a : a + chunk], out[1][a : a + chunk] = b, dnn_t[ii[r, k], jj[r, k]]
            out[2][a : a + chunk], out[3][a : a + chunk] = ii[r, k] - n, jj[r, k] - n
        return out

    def hole(self, fam, side, q):
        u, v = _plane_uv(fam, side, q)
        s = np.full(len(u), BIG)
        key = np.zeros(len(u), np.int64)
        for f in np.unique(fam[fam >= 0]):
            m = fam == f
            b, dnn, i, j = self.query(int(f), u[m], v[m])
            has = dnn >= self.strut + self.hole_min
            s[m] = np.where(has, self.strut / 2.0 - b, BIG)
            key[m] = _key(fam[m], side[m], np.zeros(m.sum(), np.int64), i, j)
        return s, key

    @classmethod
    def calibrate(cls, p, ratio, strut_floor, jitter, seed, hole_min):
        """Strut width such that the measured open fraction (cells below the hole minimum dropped) hits ``ratio``."""
        tex = cls(p, strut_floor, jitter, seed, hole_min)
        k = 14
        g = (np.arange(k * 24) + 0.5) * p / 24.0 + 4 * p
        uu, vv = [a.ravel() for a in np.meshgrid(g, g, indexing="ij")]
        b, dnn, _, _ = tex.query(0, uu, vv)

        def frac(st):
            return float(((b > st / 2.0) & (dnn >= st + hole_min)).mean())

        top = frac(strut_floor)
        if top <= ratio:
            return strut_floor, top
        lo, hi = strut_floor, p
        for _ in range(30):
            mid = 0.5 * (lo + hi)
            lo, hi = (mid, hi) if frac(mid) > ratio else (lo, mid)
        st = 0.5 * (lo + hi)
        return st, frac(st)


class _Wave:
    cells = False

    def __init__(self, p: float, seed: int, rib: bool):
        self.p, self.rib = p, rib
        r = np.random.default_rng(seed)
        self.ph = r.uniform(0, 2 * np.pi, 2)

    def height(self, fam, side, q):
        u, v = _plane_uv(fam, side, q)
        k = 2 * np.pi / self.p
        if self.rib:  # vertical ribs, flat top: stripes along u
            t = 0.5 + 0.5 * np.cos(k * u + self.ph[0])
            return _smoothstep((t - 0.15) / 0.5)
        return 0.5 + 0.25 * np.sin(k * u + self.ph[0]) + 0.25 * np.sin(k * v + self.ph[1])


class _Image:
    """Image mask / height map in the side face plane (viewer coordinates; +y side is mirrored)."""

    cells = False

    def __init__(self, path, bbox, cfg: PatternConfig, use: str, strut: float, depth: float, notes: list):
        from skimage import io

        img = np.asarray(io.imread(str(path)), float)
        if img.ndim == 3:
            if img.shape[2] == 4:  # composite alpha on white
                a = img[..., 3:4] / 255.0 if img.max() > 1.0 else img[..., 3:4]
                img = img[..., :3] * a + (255.0 if img.max() > 1.0 else 1.0) * (1 - a)
            img = img[..., :3].mean(axis=2)
        gray = img / (255.0 if img.max() > 1.0 else 1.0)
        self.bbox = bbox  # (u0, u1, v0, v1)
        u0, u1, v0, v1 = bbox
        r = cfg.image_raster_mm
        nu, nv = int(math.ceil((u1 - u0) / r)) + 1, int(math.ceil((v1 - v0) / r)) + 1
        hpx, wpx = gray.shape
        if cfg.image_fit == "stretch":
            sx, sy = (u1 - u0) / wpx, (v1 - v0) / hpx
        else:
            sx = sy = min((u1 - u0) / wpx, (v1 - v0) / hpx)
        ug = u0 + np.arange(nu) * r
        vg = v0 + np.arange(nv) * r
        col = (ug - 0.5 * (u0 + u1)) / sx + wpx / 2.0 - 0.5
        row = -(vg - 0.5 * (v0 + v1)) / sy + hpx / 2.0 - 0.5  # row 0 is the top (largest v)
        cc, rr = np.meshgrid(col, row, indexing="ij")
        ras = ndimage.map_coordinates(gray, [rr, cc], order=1, mode="constant", cval=1.0)
        self.r, self.use = r, use
        if use == "perforation":
            hole = ras < cfg.image_threshold
            ball = lambda rad: _disk(max(1, int(round(rad / r))))  # noqa: E731
            hole = ndimage.binary_opening(hole, ball(cfg.hole_min_mm / 2.0))
            solid = ndimage.binary_opening(~hole, ball(strut / 2.0), border_value=1)
            hole = ~solid
            lab, n = ndimage.label(np.pad(solid, 1, constant_values=True))
            lab = lab[1:-1, 1:-1]
            islands = (lab != lab[0, 0]) & solid if n > 1 else np.zeros_like(solid)
            if islands.any():
                notes.append(f"image: {int(islands.sum() * r * r)} mm^2 of solid islands surrounded by holes became holes")
                hole |= islands
                solid &= ~islands
            d_s = ndimage.distance_transform_edt(solid) * r - 0.5 * r  # > 0 in the solid: distance to the nearest hole
            d_h = ndimage.distance_transform_edt(hole) * r - 0.5 * r if hole.any() else np.zeros_like(d_s)
            self.field = np.where(hole, -np.maximum(d_h, 0.0), np.maximum(d_s, 0.0)).astype(np.float32)
            self.hole_frac = float(hole.mean())
        else:
            h = 1.0 - np.clip(ras, 0.0, 1.0)  # dark is raised, white stays flat
            self.field = ndimage.gaussian_filter(h, cfg.image_blur_mm / r).astype(np.float32)

    def _lookup(self, fam, side, q, cval):
        u0, u1, v0, v1 = self.bbox
        u, v = _plane_uv(fam, side, q, mirror_x=(u0, u1))
        out = ndimage.map_coordinates(self.field, [(u - u0) / self.r, (v - v0) / self.r], order=1, mode="constant", cval=cval)
        inside = (u >= u0) & (u <= u1) & (v >= v0) & (v <= v1) & (fam == SIDE)
        return np.where(inside, out, cval)

    def hole(self, fam, side, q):
        return self._lookup(fam, side, q, BIG), np.zeros(len(fam), np.int64)

    def height(self, fam, side, q):
        return self._lookup(fam, side, q, 0.0)


def _disk(rad: int):
    y, x = np.ogrid[-rad : rad + 1, -rad : rad + 1]
    return x * x + y * y <= rad * rad


# ----------------------------------------------------------------------------------------------- geometry
class _Surface:
    """psi (signed distance of the cavity-filled solid), its smoothed gradient and the skin vertices, on a cropped grid."""

    def __init__(self, phi, grid, masks: PatternMasks, cfg: PatternConfig):
        filled_full = (phi < 0) | ((masks.phi_cavity < 0) & masks.below)
        idx = np.argwhere(filled_full)
        pad = 8
        lo = np.maximum(idx.min(axis=0) - pad, 0)
        hi = np.minimum(idx.max(axis=0) + pad + 1, grid.shape)
        self.sl = tuple(slice(int(a), int(b)) for a, b in zip(lo, hi))
        self.grid = Grid(tuple(float(o + l * s) for o, l, s in zip(grid.origin, lo, grid.spacing)), grid.h, grid.dz,
                         tuple(int(b - a) for a, b in zip(lo, hi)))
        g = self.grid
        self.phi = phi[self.sl]
        self.cav = masks.phi_cavity[self.sl]
        self.below = masks.below[self.sl]
        filled = filled_full[self.sl]
        hbar = (g.h * g.h * g.dz) ** (1 / 3)
        d_in = ndimage.distance_transform_edt(filled, sampling=g.spacing)
        d_out = ndimage.distance_transform_edt(~filled, sampling=g.spacing)
        psi = np.where(filled, -np.maximum(d_in - 0.5 * hbar, 0.05 * hbar), d_out - 0.5 * hbar).astype(np.float32)
        outer_in = filled & (self.phi < 0) & (-self.phi < self.cav - 0.05)  # phi is the outer-skin distance there
        psi[outer_in] = self.phi[outer_in]
        psi[~filled] = np.maximum(self.phi[~filled], 0.0)
        del d_in, d_out
        self.psi = psi
        sig = tuple(cfg.normal_smooth_mm / s for s in g.spacing)
        self.grad = np.gradient(ndimage.gaussian_filter(psi, sig), g.h, g.h, g.dz)
        # skin vertices
        padded = np.pad(psi, 1, mode="constant", constant_values=float(np.abs(psi).max()))
        verts, faces, _, _ = measure.marching_cubes(padded, level=0.0, spacing=g.spacing)
        self.verts = verts + np.asarray(g.origin) - np.asarray(g.spacing)
        m = trimesh.Trimesh(self.verts, faces, process=False)
        self.area = np.bincount(m.faces.ravel(), weights=np.repeat(m.area_faces / 3.0, 3), minlength=len(self.verts))
        self.normals = self.unit_normal(self.verts)

    def unit_normal(self, pts):
        n = np.stack([sample(c, self.grid, pts) for c in self.grad], axis=1)
        return n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-9)


def _classify(n: np.ndarray, cos_min: float):
    ax = np.argmax(np.abs(n), axis=1)
    val = np.abs(n[np.arange(len(n)), ax])
    pos = n[np.arange(len(n)), ax] > 0
    fam = np.full(len(n), -1)
    ok = val >= cos_min
    fam[ok & (ax == 1)] = SIDE
    fam[ok & (ax == 2) & pos] = TOP
    fam[ok & (ax == 0)] = END
    return fam, pos.astype(np.int64)


def _floor_map(surf: _Surface, masks: PatternMasks) -> np.ndarray:
    """Lowest cavity z per (x, y) column; columns outside the cavity footprint take the nearest column's floor."""
    g = surf.grid
    cav = (surf.cav < 0) & surf.below
    has = cav.any(axis=2)
    z = g.origin[2] + cav.argmax(axis=2) * g.dz
    if not has.any():
        return np.full(has.shape, masks.bed_z + masks.sole_min)
    _, (ii, jj) = ndimage.distance_transform_edt(~has, return_indices=True)
    return z[ii, jj]


# ----------------------------------------------------------------------------------------------- the layer
def _resolve(spec: PatternSpec, cfg: PatternConfig, process) -> tuple[dict, list]:
    """Manufacturing limits applied on top of the clamped spec: effective pitch / ratio / strut. Returns (values, adjustments)."""
    strut = max(cfg.strut_min_mm, process.wall_min if process is not None else 0.0) + cfg.strut_margin_mm
    adj = []
    out = {"strut_mm": strut}
    p = spec.scale_mm
    need = cfg.hole_min_mm + strut
    if spec.kind in ("hex_perforation", "voronoi_perforation") and p < need:
        adj.append({"field": "scale_mm", "from": p, "to": round(need, 3), "why": f"pitch >= hole_min + strut = {need:.2f}"})
        p = need
    out["pitch_mm"] = p
    if spec.kind == "hex_perforation":
        lo, hi = (cfg.hole_min_mm / p) ** 2, (1.0 - strut / p) ** 2
        r = min(max(spec.open_ratio, lo), hi)
        out["open_ratio"] = r
        if abs(r - spec.open_ratio) > 1e-9:
            adj.append({"field": "open_ratio", "from": spec.open_ratio, "to": round(r, 4),
                        "why": f"hole >= {cfg.hole_min_mm} mm and strut >= {strut:.2f} mm at pitch {p:g}"})
    elif spec.kind == "voronoi_perforation":
        st, r = _Voronoi.calibrate(p, spec.open_ratio, strut, cfg.voronoi_jitter, spec.seed, cfg.hole_min_mm)
        out["strut_mm"], out["open_ratio"] = st, r
        if abs(r - spec.open_ratio) > 0.01:
            adj.append({"field": "open_ratio", "from": spec.open_ratio, "to": round(r, 4),
                        "why": f"limited by strut >= {strut:.2f} mm / hole >= {cfg.hole_min_mm} mm at pitch {p:g}"})
    return out, adj


def apply_pattern(phi_shoe: np.ndarray, grid: Grid, spec: PatternSpec | dict, masks: PatternMasks, process=None,
                  cfg: PatternConfig | None = None) -> tuple[np.ndarray, dict]:
    """Apply ``spec`` to ``phi_shoe``; returns ``(phi_new, report)``. ``process``: a ProcessConfig (wall_min raises the strut floor)."""
    if isinstance(spec, dict):
        spec = PatternSpec.from_dict(spec)
    cfg = cfg or PatternConfig.from_toml()
    if phi_shoe.shape != tuple(grid.shape) or masks.phi_cavity.shape != tuple(grid.shape):
        raise PatternError("phi_shoe / masks do not match the grid")
    voxel = grid.h * grid.h * grid.dz
    rep: dict = {"spec": spec.to_dict(), "clamped": dict(spec.clamped), "notes": list(spec.notes), "adjustments": [],
                 "applied": False, "process": process.name if process is not None else None}
    if spec.kind == "none" or not spec.zones:
        rep["notes"].append("nothing to do (kind none or no zones)")
        return phi_shoe.copy(), rep
    if spec.kind == "image" and "side" not in spec.zones:
        rep["notes"].append("image patterns use the side zone only and 'side' is not in zones: nothing applied")
        return phi_shoe.copy(), rep

    res, rep["adjustments"] = _resolve(spec, cfg, process)
    strut = res["strut_mm"]
    perf = spec.perforates
    surf = _Surface(phi_shoe, grid, masks, cfg)
    v, nrm = surf.verts, surf.normals
    fam, side = _classify(nrm, cfg.min_normal_cos)

    # ---- which skin vertices may carry a pattern
    solid_x = np.argwhere(phi_shoe < 0)[:, 0]
    x_lo, x_hi = grid.origin[0] + solid_x.min() * grid.h, grid.origin[0] + solid_x.max() * grid.h
    length = x_hi - x_lo
    toe, heel = v[:, 0] >= x_lo + cfg.toe_frac * length, v[:, 0] <= x_lo + cfg.heel_frac * length
    z_toe, z_heel = "toe" in spec.zones, "heel" in spec.zones
    region_ok = (~toe & ~heel) | (toe & z_toe) | (heel & z_heel)
    if spec.kind == "image":
        fam_ok = (fam == SIDE)
    else:
        fam_ok = ((fam == SIDE) & ("side" in spec.zones)) | ((fam == TOP) & ("top" in spec.zones)) \
            | ((fam == END) & ((toe & z_toe) | (heel & z_heel)))
    floor = _floor_map(surf, masks)
    g = surf.grid
    ix = np.clip(np.rint((v[:, 0] - g.origin[0]) / g.h).astype(int), 0, g.shape[0] - 1)
    iy = np.clip(np.rint((v[:, 1] - g.origin[1]) / g.h).astype(int), 0, g.shape[1] - 1)
    prot_sole = (v[:, 2] < floor[ix, iy] + cfg.sole_margin_mm) | (v[:, 2] < masks.bed_z + masks.sole_min + cfg.sole_margin_mm)
    prot_collar = v[:, 2] > masks.collar.z_t(v[:, :2]) - cfg.collar_margin_mm
    t_s = sample(surf.cav, g, v)  # wall thickness at the skin = distance to the cavity
    thick_ok = (t_s >= cfg.perf_wall_min_mm) & (t_s <= cfg.perf_wall_max_mm) if perf else np.ones(len(v), bool)
    good = region_ok & fam_ok & ~prot_sole & ~prot_collar & thick_ok & (surf.area > 0)
    rep["skin"] = {"vertices": int(len(v)), "area_mm2": round(float(surf.area.sum())), "eligible_area_mm2": round(float(surf.area[good].sum())),
                   "protected_by": {"sole": round(float(surf.area[prot_sole].sum())), "collar": round(float(surf.area[prot_collar & ~prot_sole].sum())),
                                    "thickness": round(float(surf.area[~thick_ok & ~prot_sole & ~prot_collar].sum())) if perf else 0}}
    if not good.any():
        rep["notes"].append("no eligible surface: nothing applied")
        return phi_shoe.copy(), rep
    # a vertex next to another family / side is on a seam: a cell there would be cut in half
    vt = cKDTree(v)
    pr = vt.query_pairs(cfg.seam_mm, output_type="ndarray")
    seam = np.zeros(len(v), bool)
    diff = (fam[pr[:, 0]] != fam[pr[:, 1]]) | (side[pr[:, 0]] != side[pr[:, 1]])
    seam[pr[diff].ravel()] = True
    good &= ~seam
    bad_tree = cKDTree(v[~good]) if (~good).any() else None
    d_bad = bad_tree.query(v)[0] if bad_tree is not None else np.full(len(v), np.inf)

    # ---- the texture
    notes: list = rep["notes"]
    if spec.kind == "hex_perforation":
        tex = _Hex(res["pitch_mm"], res["open_ratio"])
    elif spec.kind == "voronoi_perforation":
        tex = _Voronoi(res["pitch_mm"], res["strut_mm"], cfg.voronoi_jitter, spec.seed, cfg.hole_min_mm)
    elif spec.kind in ("wave_displacement", "rib_displacement"):
        tex = _Wave(spec.scale_mm, spec.seed, spec.kind == "rib_displacement")
    else:
        sv = good & (fam == SIDE)
        bbox = (float(v[sv, 0].min()), float(v[sv, 0].max()), float(v[sv, 2].min()), float(v[sv, 2].max()))
        tex = _Image(spec.image, bbox, cfg, spec.image_use, strut, spec.depth_mm, notes)
        rep["image_bbox_mm"] = [round(b, 1) for b in bbox]
    cells = getattr(tex, "cells", False)

    phi_new = phi_shoe.copy()
    work = phi_new[surf.sl]  # view into phi_new
    if perf:
        margin = cfg.cell_margin_mm if cells else strut
        interior = good & (d_bad >= margin)
        if cells:
            s_v, key_v = tex.hole(np.where(good, fam, -1), side, v)
            kk = key_v[good]
            uk, inv = np.unique(kk, return_inverse=True)
            n_bad = np.bincount(inv, weights=(~interior[good]).astype(float), minlength=len(uk))
            good_keys = uk[n_bad == 0]
            in_hole = interior & (s_v < 0) & np.isin(key_v, good_keys)
            rep["cells"] = {"seen": int(len(uk)), "kept": int(len(good_keys)), "dropped_at_boundary": int(len(uk) - len(good_keys))}
            n_holes = len(np.unique(key_v[in_hole]))
            rep["cells"]["holes"] = int(n_holes)
            region_area = float(surf.area[interior & np.isin(key_v, good_keys)].sum())
        else:
            s_v, _ = tex.hole(np.where(good, fam, -1), side, v)
            good_keys = None
            in_hole = interior & (s_v < 0)
            region_area = float(surf.area[interior & (fam == SIDE) & _in_bbox(v, bbox)].sum())
        patt_area = float(surf.area[in_hole].sum())
        rep["patterned_area_mm2"] = round(region_area)
        rep["hole_area_mm2"] = round(patt_area)
        rep["open_ratio_measured"] = round(patt_area / region_area, 4) if region_area > 0 else 0.0
        d_cut = cfg.perf_wall_max_mm + 1.5
        sel = np.flatnonzero(((surf.phi < 0) & (surf.psi > -d_cut)).ravel())
        _cut(surf, sel, tex, cells, good_keys, interior, fam, side, work, cfg, tree=vt)
    else:
        fade = max(cfg.fade_mm, 0.5 * spec.scale_mm if spec.kind != "image" else cfg.fade_mm)
        w = np.where(good, _smoothstep(d_bad / fade), 0.0)
        hv = spec.depth_mm * tex.height(np.where(good, fam, -1), side, v) * w
        rep["amplitude_max_mm"] = round(float(hv.max()), 3)
        rep["patterned_area_mm2"] = round(float(surf.area[w > 0.99].sum()))
        sel = np.flatnonzero(((surf.psi > -0.75) & (surf.phi < spec.depth_mm + 1.5) & (surf.phi > -1.5)).ravel())
        _raise(surf, sel, tex, w, fam, side, spec.depth_mm, work, vt)

    # ---- guards: never into the cavity, no loose bits, no sealed pockets
    cav_air = masks.phi_cavity < 0
    np.maximum(work, -surf.cav, out=work, where=((surf.cav < 0) & surf.below))
    phi_new[phi_new == 0] = np.float32(1e-3)
    invaded = int(((phi_new < 0) & cav_air & masks.below & (phi_shoe >= 0)).sum())
    rep["cavity_invaded_voxels"] = invaded
    lab, n = ndimage.label(phi_new < 0)
    if n > 1:  # pieces the pattern cut loose from the main body (pre-existing separate pieces are left alone)
        lab0, n0 = ndimage.label(phi_shoe < 0)
        main0 = lab0 == (np.argmax(np.bincount(lab0.ravel())[1:]) + 1) if n0 else np.zeros(phi_new.shape, bool)
        sizes = np.bincount(lab.ravel())[1:]
        loose_ids = [i for i in np.unique(lab[main0 & (phi_new < 0)]) if i != np.argmax(sizes) + 1]
        if loose_ids:
            loose = np.isin(lab, loose_ids)
            phi_new[loose] = np.float32(0.5)
            rep["loose_bits_removed_mm3"] = round(float(loose.sum() * voxel), 1)
            notes.append(f"{len(loose_ids)} solid piece(s) cut loose by the pattern were removed")
    air, _ = ndimage.label(phi_new >= 0)
    pockets = (air != air[0, 0, 0]) & (phi_new >= 0)
    rep["sealed_pockets_filled_mm3"] = round(float(pockets.sum() * voxel), 1)
    phi_new[pockets] = np.float32(-1e-3)
    rep["sealed_after"] = sealed_cavities(phi_new, grid)
    rep["volume_mm3"] = {"before": round(float((phi_shoe < 0).sum() * voxel)), "after": round(float((phi_new < 0).sum() * voxel))}
    rep["volume_mm3"]["delta"] = rep["volume_mm3"]["after"] - rep["volume_mm3"]["before"]
    rep["mass_g_est"] = {"before": round(rep["volume_mm3"]["before"] / 1000 * cfg.density_g_cm3, 1),
                         "after": round(rep["volume_mm3"]["after"] / 1000 * cfg.density_g_cm3, 1), "density_g_cm3": cfg.density_g_cm3}
    rep["effective"] = {k: (round(val, 4) if isinstance(val, float) else val) for k, val in res.items()}
    rep["applied"] = True
    return phi_new, rep


def _in_bbox(v, bbox):
    return (v[:, 0] >= bbox[0]) & (v[:, 0] <= bbox[1]) & (v[:, 2] >= bbox[2]) & (v[:, 2] <= bbox[3])


def _voxels(surf: _Surface, sel, a, b):
    ijk = np.stack(np.unravel_index(sel[a:b], surf.psi.shape), axis=1)
    pts = surf.grid.coords(ijk)
    psi = surf.psi[tuple(ijk.T)]
    n = np.stack([c[tuple(ijk.T)] for c in surf.grad], axis=1)
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-9)
    return ijk, pts, psi, pts - psi[:, None] * n  # q: closest skin point


def _cut(surf, sel, tex, cells, good_keys, interior, fam_v, side_v, work, cfg, tree, chunk=200000):
    for a in range(0, len(sel), chunk):
        ijk, pts, psi, q = _voxels(surf, sel, a, a + chunk)
        dist, nv = tree.query(q)
        fam, side = fam_v[nv], side_v[nv]
        ok = (dist < 2.5) & interior[nv] & (fam >= 0)
        # inside a wall no thicker than the perforation limit (+1 mm slack); thicker solid is not drilled
        ok &= (-psi + surf.cav[tuple(ijk.T)]) <= cfg.perf_wall_max_mm + 1.0
        if not ok.any():
            continue
        s, key = tex.hole(np.where(ok, fam, -1), side, q)
        if cells:
            ok &= np.isin(key, good_keys)
        m = ok & (s < 1.5)
        if m.any():
            t = tuple(ijk[m].T)
            work[t] = np.maximum(work[t], -s[m].astype(np.float32))


def _raise(surf, sel, tex, w_v, fam_v, side_v, depth, work, tree, chunk=200000):
    for a in range(0, len(sel), chunk):
        ijk, pts, psi, q = _voxels(surf, sel, a, a + chunk)
        dist, nv = tree.query(q)
        fam, side = fam_v[nv], side_v[nv]
        ok = (dist < 2.5) & (fam >= 0) & (w_v[nv] > 0)
        if not ok.any():
            continue
        h = depth * tex.height(np.where(ok, fam, -1), side, q) * w_v[nv]
        m = ok & (h > 0)
        t = tuple(ijk[m].T)
        work[t] = work[t] - h[m].astype(np.float32)


def export_checks(mesh) -> dict:
    """Printability facts of an exported pattern mesh: watertight, bodies, inner shells (sealed cavities in the STL)."""
    return {"watertight": bool(mesh.is_watertight), "bodies": int(mesh.body_count), "inner_shells": mesh_inner_shells(mesh)}
