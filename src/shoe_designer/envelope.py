"""Outer envelope phi_E and the below-collar mask M_below of a template shoe (design doc
issue-10-cavity-rebuild §1.4, step S2).

Only the template itself is used (no last, no pose). Pipeline:

1. Split every face into inner (old cavity) and outer skin: a few rays in a small cone around the
   outward normal; faces whose rays mostly hit the mesh again are "inner". Only the largest
   face-connected inner patch is kept.
2. The collar curve is the upper edge of that patch (coarse), snapped *along its horizontal normal*
   to the crest of the wall (max of the height field H(x, y)), smoothed and resampled. The inner
   skin is then redefined as the surface region on the cavity side of the collar (flood fill that
   treats the faces next to the collar as a barrier): the ray vote cannot classify the cavity floor
   or the band under the collar, because the rays escape through the opening.
3. The opening is sealed with a cap surface z_T(x, y) (harmonic interpolation of the collar).
4. phi_E = signed distance of (outer skin + cap), sign by the fast winding number. The old inner
   skin is dropped, otherwise the cavity would cancel out of the winding number.

Convention as in :mod:`shoe_designer.sdf`: phi < 0 inside, arrays indexed ``[ix, iy, iz]``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import trimesh
from scipy import ndimage, sparse
from scipy.interpolate import CubicSpline, LinearNDInterpolator
from scipy.sparse.csgraph import connected_components
from scipy.sparse.linalg import spsolve
from scipy.spatial import Delaunay, cKDTree
from skimage.measure import points_in_poly

from .sdf import ExportReport, Grid, sample, sdf_from_mesh, sdf_to_mesh

RAY_OFFSET_MM = 0.05
RAY_CHUNK = 500_000


class EnvelopeError(Exception):
    pass


@dataclass
class CollarSurface:
    """Collar curve and the sealing height field z_T(x, y)."""

    rim: np.ndarray  # (N, 3) closed curve, counter-clockwise seen from +z, last point != first
    cap: trimesh.Trimesh  # sealing surface, normals up
    _tree: cKDTree = field(repr=False)
    _interp: LinearNDInterpolator = field(repr=False)

    def z_t(self, xy: np.ndarray) -> np.ndarray:
        """z_T at world (x, y) (N, 2): the cap inside the collar, the nearest collar point's z outside."""
        xy = np.asarray(xy, float)
        z = np.full(len(xy), np.nan)
        inside = self.inside(xy)  # the interpolator spans the convex hull, so it must not see outside points
        if inside.any():
            z[inside] = self._interp(xy[inside])
        out = np.isnan(z)
        if out.any():
            z[out] = self.rim[self._tree.query(xy[out])[1], 2]
        return z

    def inside(self, xy: np.ndarray) -> np.ndarray:
        return points_in_poly(np.asarray(xy, float), self.rim[:, :2])

    def mask_below(self, grid: Grid) -> np.ndarray:
        """M_below = {z < z_T(x, y)} on ``grid`` (bool)."""
        nx, ny, nz = grid.shape
        gx, gy = np.meshgrid(grid.origin[0] + np.arange(nx) * grid.h,
                             grid.origin[1] + np.arange(ny) * grid.h, indexing="ij")
        zt = self.z_t(np.stack([gx.ravel(), gy.ravel()], axis=1)).reshape(nx, ny)
        z = grid.origin[2] + np.arange(nz) * grid.dz
        return z[None, None, :] < zt[:, :, None]


@dataclass
class Envelope:
    grid: Grid
    phi: np.ndarray  # float32, exact within ``band`` of the surface, +-band beyond
    collar: CollarSurface
    inner_faces: np.ndarray  # bool per face of ``source`` (the mesh the pipeline ran on)
    source: trimesh.Trimesh  # input mesh, or its closed version when closing_radius > 0
    surface: trimesh.Trimesh  # outer skin + cap that phi was computed from
    report: dict

    def mask_below(self) -> np.ndarray:
        return self.collar.mask_below(self.grid)

    def to_mesh(self) -> tuple[trimesh.Trimesh, ExportReport]:
        return sdf_to_mesh(self.phi, self.grid)


# --------------------------------------------------------------------------- face classification


def _cone_directions(normals: np.ndarray, n_rays: int, cone_deg: float) -> list[np.ndarray]:
    helper = np.where(np.abs(normals[:, 2:3]) < 0.9, [[0.0, 0.0, 1.0]], [[1.0, 0.0, 0.0]])
    u = np.cross(normals, helper)
    u /= np.maximum(np.linalg.norm(u, axis=1, keepdims=True), 1e-12)
    w = np.cross(normals, u)
    c = np.radians(cone_deg)
    dirs = [normals]
    for k in range(n_rays - 1):
        phi = 2 * np.pi * k / (n_rays - 1)
        dirs.append(np.cos(c) * normals + np.sin(c) * (np.cos(phi) * u + np.sin(phi) * w))
    return dirs


def vote_inner_faces(mesh: trimesh.Trimesh, n_rays: int = 5, cone_deg: float = 10.0) -> np.ndarray:
    """Per face: True when most of ``n_rays`` rays leaving along the outward normal hit the mesh again."""
    normals = np.asarray(mesh.face_normals, float)
    centers = np.asarray(mesh.triangles_center, float)
    bad = np.linalg.norm(normals, axis=1) < 0.5  # degenerate faces: no usable normal
    normals = np.where(bad[:, None], [[0.0, 0.0, 1.0]], normals)
    origins = centers + RAY_OFFSET_MM * normals
    hits = np.zeros(len(normals), np.int32)
    for d in _cone_directions(normals, n_rays, cone_deg):
        for s in range(0, len(d), RAY_CHUNK):
            hits[s : s + RAY_CHUNK] += mesh.ray.intersects_any(origins[s : s + RAY_CHUNK], d[s : s + RAY_CHUNK])
    inner = hits * 2 > n_rays
    inner[bad] = False
    return inner


def majority_filter(mesh: trimesh.Trimesh, mask: np.ndarray, iterations: int = 2) -> np.ndarray:
    """Face-wise majority vote over a face and its neighbours (removes single-face flicker of the ray vote)."""
    adj = mesh.face_adjacency
    n = len(mesh.faces)
    out = mask.copy()
    for _ in range(iterations):
        votes = out.astype(np.int32)
        count = votes.copy()
        np.add.at(count, adj[:, 0], votes[adj[:, 1]])
        np.add.at(count, adj[:, 1], votes[adj[:, 0]])
        total = np.ones(n, np.int32)
        np.add.at(total, adj[:, 0], 1)
        np.add.at(total, adj[:, 1], 1)
        out = count * 2 > total
    return out


def largest_face_component(mesh: trimesh.Trimesh, mask: np.ndarray) -> np.ndarray:
    """Keep only the face-adjacent connected component of ``mask`` with the largest area."""
    if not mask.any():
        return mask
    adj = mesh.face_adjacency
    keep = mask[adj[:, 0]] & mask[adj[:, 1]]
    n = len(mesh.faces)
    g = sparse.coo_matrix((np.ones(keep.sum()), (adj[keep, 0], adj[keep, 1])), shape=(n, n))
    _, label = connected_components(g, directed=False)
    area = np.bincount(label[mask], weights=mesh.area_faces[mask])
    best = int(np.argmax(area))
    return mask & (label == best)


# --------------------------------------------------------------------------- collar curve


def _arclength(p: np.ndarray, dims: int = 2) -> np.ndarray:
    """Cumulative length of the closed polyline, in plan by default (the coarse collar's z is ragged)."""
    seg = np.linalg.norm(np.diff(np.vstack([p, p[:1]])[:, :dims], axis=0), axis=1)
    return np.concatenate([[0.0], np.cumsum(seg)])


def _resample_closed(p: np.ndarray, step: float) -> np.ndarray:
    s = _arclength(p)
    n = max(int(round(s[-1] / step)), 8)
    t = np.linspace(0.0, s[-1], n, endpoint=False)
    closed = np.vstack([p, p[:1]])
    return np.stack([np.interp(t, s, closed[:, i]) for i in range(closed.shape[1])], axis=1)


def _smooth_closed(p: np.ndarray, sigma_samples: float) -> np.ndarray:
    return ndimage.gaussian_filter1d(p, sigma_samples, axis=0, mode="wrap") if sigma_samples > 0 else p


def _ccw(p: np.ndarray) -> np.ndarray:
    x, y = p[:, 0], p[:, 1]
    area = 0.5 * np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y)
    return p if area > 0 else p[::-1].copy()


def _outward_normals(p: np.ndarray) -> np.ndarray:
    """Horizontal unit normals of a counter-clockwise closed curve, pointing away from its interior."""
    t = np.roll(p[:, :2], -1, axis=0) - np.roll(p[:, :2], 1, axis=0)
    n = np.stack([t[:, 1], -t[:, 0]], axis=1)
    return n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)


def fill_face_holes(mesh: trimesh.Trimesh, inner: np.ndarray) -> np.ndarray:
    """Relabel every outer face region except the largest one as inner.

    The cavity floor (and any vote flicker inside the cavity) is enclosed by the inner patch; only the
    outside skin, the largest region of non-inner faces, stays outer.
    """
    out = ~inner
    adj = mesh.face_adjacency
    keep = out[adj[:, 0]] & out[adj[:, 1]]
    n = len(mesh.faces)
    g = sparse.coo_matrix((np.ones(keep.sum()), (adj[keep, 0], adj[keep, 1])), shape=(n, n))
    _, label = connected_components(g, directed=False)
    area = np.bincount(label[out], weights=mesh.area_faces[out], minlength=label.max() + 1)
    return inner | (out & (label != int(np.argmax(area))))


def _boundary_loops(mesh: trimesh.Trimesh, face_mask: np.ndarray) -> tuple[list[tuple[np.ndarray, bool]], int]:
    """Boundary polylines of a face set, by walking boundary edges: ``[(vertex indices, closed)]`` and
    the number of pinch vertices (more than two boundary edges), where the walk picks an arbitrary branch."""
    f = mesh.faces[face_mask]
    e = np.sort(np.vstack([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]]), axis=1)
    uniq, cnt = np.unique(e, axis=0, return_counts=True)
    b = uniq[cnt == 1]
    nbrs: dict[int, list[int]] = {}
    for a_, c_ in b:
        nbrs.setdefault(int(a_), []).append(int(c_))
        nbrs.setdefault(int(c_), []).append(int(a_))
    seen: set[tuple[int, int]] = set()
    loops = []
    for a_, c_ in b:
        start = (int(a_), int(c_))
        if tuple(sorted(start)) in seen:
            continue
        loop = [start[0]]
        prev, cur = start
        seen.add(tuple(sorted(start)))
        while cur != start[0]:
            loop.append(cur)
            nxt = [v for v in nbrs[cur] if v != prev and tuple(sorted((cur, v))) not in seen]
            if not nxt:  # dead end or all edges used: close the loop here
                break
            prev, cur = cur, nxt[0]
            seen.add(tuple(sorted((prev, cur))))
        if len(loop) >= 8:
            loops.append((np.array(loop), cur == start[0]))
    return loops, sum(1 for v in nbrs.values() if len(v) > 2)


def _coarse_rim(mesh: trimesh.Trimesh, inner: np.ndarray) -> tuple[np.ndarray, dict]:
    """Coarse collar: the outer boundary loop of the inner patch (holes filled), the one enclosing the most plan area.

    Where the ray vote is unreliable the loop lies up to 10-20 mm below the wall crest but still
    within the wall in plan, which is all the crest snap needs.
    """
    filled = fill_face_holes(mesh, inner)
    all_loops, pinch = _boundary_loops(mesh, filled)
    loops = [v for v, closed in all_loops if closed]  # an open polyline is a failed walk, not a collar
    if not loops:
        raise EnvelopeError("the inner skin has no closed boundary loop (closed surface, no cavity, or a pinched "
                            "boundary); pass manual_rim=<8-12 points on the collar crest>")

    def plan_area(v):
        xy = mesh.vertices[v, :2]
        return abs(0.5 * np.sum(xy[:, 0] * np.roll(xy[:, 1], -1) - np.roll(xy[:, 0], -1) * xy[:, 1]))

    areas = [plan_area(v) for v in loops]
    best = int(np.argmax(areas))
    info = {"boundary_loops": len(loops), "unclosed_loops": len(all_loops) - len(loops), "pinch_vertices": int(pinch),
            "coarse_loop_points": int(len(loops[best]))}
    return mesh.vertices[loops[best]], info


def _segments_cross(p: np.ndarray) -> bool:
    """True when the closed polygon ``p`` (K, 2) is not simple (some pair of non-adjacent edges crosses)."""
    a, b = p, np.roll(p, -1, axis=0)
    k = len(p)

    def ccw(x, y, z):
        return (z[..., 1] - x[..., 1]) * (y[..., 0] - x[..., 0]) > (y[..., 1] - x[..., 1]) * (z[..., 0] - x[..., 0])

    for i in range(k):
        for j in range(i + 2, k):
            if i == 0 and j == k - 1:
                continue
            if ccw(a[i], a[j], b[j]) != ccw(b[i], a[j], b[j]) and ccw(a[i], b[i], a[j]) != ccw(a[i], b[i], b[j]):
                return True
    return False


def _manual_rim(points: np.ndarray) -> np.ndarray:
    """Periodic spline through user points. The given order is kept when it already is a simple polygon,
    otherwise the points are ordered by polar angle around their centroid."""
    pts = np.asarray(points, float)
    if pts.ndim != 2 or pts.shape[1] != 3 or len(pts) < 4:
        raise EnvelopeError("manual_rim must be an array of at least 4 points, shape (K, 3)")
    if _segments_cross(pts[:, :2]):
        c = pts[:, :2].mean(axis=0)
        pts = pts[np.argsort(np.arctan2(pts[:, 1] - c[1], pts[:, 0] - c[0]))]
    keep = np.linalg.norm(np.diff(np.vstack([pts, pts[:1]])[:, :2], axis=0), axis=1) > 1e-6  # drop repeated points
    pts = pts[keep]
    if len(pts) < 4:
        raise EnvelopeError("manual_rim needs at least 4 distinct points in plan")
    closed = np.vstack([pts, pts[:1]])
    s = _arclength(pts)
    return CubicSpline(s, closed, bc_type="periodic")(np.linspace(0.0, s[-1], 400, endpoint=False))


def _height_field(mesh: trimesh.Trimesh, xy: np.ndarray, z_top: float) -> np.ndarray:
    """H(x, y): z of the first material hit by a vertical ray from above; nan where there is none."""
    out = np.full(len(xy), np.nan)
    origins = np.column_stack([xy, np.full(len(xy), z_top)])
    d = np.tile([0.0, 0.0, -1.0], (len(xy), 1))
    for s in range(0, len(xy), RAY_CHUNK):
        loc, ray, _ = mesh.ray.intersects_location(origins[s : s + RAY_CHUNK], d[s : s + RAY_CHUNK], multiple_hits=False)
        out[s + ray] = loc[:, 2]
    return out


def _snap_to_crest(mesh: trimesh.Trimesh, p: np.ndarray, snap_mm: float) -> np.ndarray:
    """Move curve points along their horizontal normal to the crest of H(x, y) within +-snap_mm.

    Returns the signed offsets (positive = outward). A flat crest is centred (middle of the run that is
    within 0.15 mm of the maximum, around the argmax).
    """
    n = _outward_normals(p)
    offs = np.arange(-snap_mm, snap_mm + 1e-9, 0.25)
    z_top = float(mesh.bounds[1, 2]) + 1.0
    xy = (p[:, None, :2] + offs[None, :, None] * n[:, None, :]).reshape(-1, 2)
    h = _height_field(mesh, xy, z_top).reshape(len(p), len(offs))
    h = np.where(np.isnan(h), -np.inf, h)
    arg = h.argmax(axis=1)
    hmax = h[np.arange(len(p)), arg]
    near = h >= (hmax - 0.15)[:, None]
    lo, hi = arg.copy(), arg.copy()
    rows = np.arange(len(p))
    for _ in range(len(offs)):
        step_lo = (lo > 0) & near[rows, np.maximum(lo - 1, 0)]
        step_hi = (hi < len(offs) - 1) & near[rows, np.minimum(hi + 1, len(offs) - 1)]
        if not (step_lo.any() or step_hi.any()):
            break
        lo = lo - step_lo
        hi = hi + step_hi
    s = 0.5 * (offs[lo] + offs[hi])
    s[~np.isfinite(hmax)] = 0.0
    return s


def extract_collar(
    mesh: trimesh.Trimesh,
    inner: np.ndarray,
    snap_mm: float = 10.0,
    rim_step: float = 1.0,
    manual_rim: np.ndarray | None = None,
) -> tuple[np.ndarray, dict]:
    """Collar curve (N, 3) snapped to the crest of the wall, plus diagnostics."""
    if manual_rim is not None:
        coarse, loop_info = _manual_rim(manual_rim), {}
    else:
        coarse, loop_info = _coarse_rim(mesh, inner)
    p = _ccw(_smooth_closed(_resample_closed(coarse, rim_step), 4.0 / rim_step))
    s = _snap_to_crest(mesh, p, snap_mm)
    # outliers in the offsets (a ridge elsewhere within reach) are removed before smoothing
    s_med = ndimage.median_filter(s, size=9, mode="wrap")
    xy = p[:, :2] + s_med[:, None] * _outward_normals(p)
    q = _ccw(_smooth_closed(_resample_closed(np.column_stack([xy, p[:, 2]]), rim_step), 1.5 / rim_step))
    # height: crest of H within 1 mm of the curve (absorbs xy drift), then a short median
    n = _outward_normals(q)
    offs = np.arange(-1.0, 1.0 + 1e-9, 0.5)
    z_top = float(mesh.bounds[1, 2]) + 1.0
    h = _height_field(mesh, (q[:, None, :2] + offs[None, :, None] * n[:, None, :]).reshape(-1, 2), z_top)
    z = np.nanmax(h.reshape(len(q), len(offs)), axis=1)
    if np.isnan(z).any():
        raise EnvelopeError("collar curve leaves the part (no material under some collar points); pass manual_rim=...")
    z = ndimage.median_filter(z, size=5, mode="wrap")
    rim = np.column_stack([q[:, :2], z])
    return rim, {"snap_offset_mm": {"mean": float(np.mean(s_med)), "p95_abs": float(np.percentile(np.abs(s_med), 95)),
                                    "max_abs": float(np.abs(s_med).max())}, **loop_info}


def flood_inner_skin(mesh: trimesh.Trimesh, rim: np.ndarray, vote: np.ndarray, barrier_mm: float) -> np.ndarray:
    """Inner skin = the surface region on the cavity side of the collar.

    Faces within ``barrier_mm`` of the collar curve are removed (they cut the closed surface along the
    wall crest into an inner and an outer part); the inner part is the face-connected region holding
    most of the ray-voted inner area. This also collects the cavity floor and the faces in the
    unreliable band under the collar, which a ray vote cannot classify (the rays escape through the
    opening). Raises when the cut leaks, i.e. the 'inner' region swallows most of the surface.
    """
    # Faces are tested as triangles, not by their vertices: the wall top is often one strip of long thin
    # triangles whose vertices lie far from the collar point that crosses them.
    dense = _resample_closed(rim, 0.5 * barrier_mm)
    tree = mesh.triangles_tree
    tris = np.asarray(mesh.triangles)
    barrier = np.zeros(len(mesh.faces), bool)
    for pt in dense:
        cand = np.fromiter(tree.intersection(np.r_[pt - barrier_mm, pt + barrier_mm]), dtype=np.int64)
        if len(cand) == 0:
            continue
        cp = trimesh.triangles.closest_point(tris[cand], np.tile(pt, (len(cand), 1)))
        barrier[cand[np.linalg.norm(cp - pt, axis=1) <= barrier_mm]] = True
    adj = mesh.face_adjacency
    ok = ~barrier[adj[:, 0]] & ~barrier[adj[:, 1]]
    n = len(mesh.faces)
    g = sparse.coo_matrix((np.ones(ok.sum()), (adj[ok, 0], adj[ok, 1])), shape=(n, n))
    _, label = connected_components(g, directed=False)
    free = ~barrier
    seed_area = np.bincount(label[free & vote], weights=mesh.area_faces[free & vote], minlength=label.max() + 1)
    if seed_area.max() <= 0:
        raise EnvelopeError("no voted inner face survives the collar cut; pass manual_rim=...")
    inner = free & (label == int(np.argmax(seed_area)))
    if mesh.area_faces[inner].sum() > 0.75 * mesh.area_faces.sum():
        raise EnvelopeError("the collar cut leaks: the inner region covers most of the surface; "
                            "increase barrier_mm or pass manual_rim=...")
    return inner | (barrier & vote)  # barrier faces the vote called inner go with the cavity


# --------------------------------------------------------------------------- cap


def build_cap(rim: np.ndarray, interior_step: float = 2.0) -> CollarSurface:
    """Triangulated sealing surface bounded by ``rim``; interior heights solve a (weighted) graph Laplace
    equation with the collar heights fixed, so the cap is exact on the collar."""
    xy = rim[:, :2]
    tree = cKDTree(xy)
    lo, hi = xy.min(axis=0), xy.max(axis=0)
    gx, gy = np.meshgrid(np.arange(lo[0], hi[0] + interior_step, interior_step),
                         np.arange(lo[1], hi[1] + interior_step, interior_step), indexing="ij")
    g = np.column_stack([gx.ravel(), gy.ravel()])
    g = g[points_in_poly(g, xy)]
    g = g[tree.query(g)[0] >= 0.75 * interior_step]
    pts = np.vstack([xy, g])
    tri = Delaunay(pts).simplices
    cen = pts[tri].mean(axis=1)
    tri = tri[points_in_poly(cen, xy)]
    if len(tri) == 0:
        raise EnvelopeError("collar curve encloses no area")
    nb, n = len(xy), len(pts)
    z = np.zeros(n)
    z[:nb] = rim[:, 2]
    if n > nb:
        edges = np.vstack([tri[:, [0, 1]], tri[:, [1, 2]], tri[:, [2, 0]]])
        edges = np.unique(np.sort(edges, axis=1), axis=0)
        w = 1.0 / np.maximum(np.linalg.norm(pts[edges[:, 0]] - pts[edges[:, 1]], axis=1), 1e-6)
        W = sparse.coo_matrix((np.r_[w, w], (np.r_[edges[:, 0], edges[:, 1]], np.r_[edges[:, 1], edges[:, 0]])), shape=(n, n)).tocsr()
        lap = sparse.diags(np.asarray(W.sum(axis=1)).ravel()) - W
        free = np.arange(nb, n)
        fixed = np.arange(nb)
        rhs = -lap[free][:, fixed] @ z[fixed]
        try:
            z[free] = spsolve(lap[free][:, free].tocsc(), rhs)
        except Exception as e:  # singular when an interior point is not connected to the collar
            raise EnvelopeError(f"cap interpolation failed: {e}") from None
    verts = np.column_stack([pts, z])
    flip = np.cross(verts[tri[:, 1]] - verts[tri[:, 0]], verts[tri[:, 2]] - verts[tri[:, 0]])[:, 2] < 0
    tri[flip] = tri[flip][:, ::-1]
    cap = trimesh.Trimesh(verts, tri, process=False)
    interp = LinearNDInterpolator(pts, z)
    return CollarSurface(rim=rim, cap=cap, _tree=tree, _interp=interp)


# --------------------------------------------------------------------------- optional closing


def close_mesh(mesh: trimesh.Trimesh, radius: float, h: float) -> tuple[trimesh.Trimesh, ExportReport]:
    """Morphological closing (dilate then erode by ``radius`` mm) of the solid, for perforated templates.

    A hole of radius ``a`` through a wall of thickness ``t`` is only closed when
    ``t >= 2 * radius - 2 * sqrt(radius**2 - a**2)`` (dilation fills a lens of that thickness and the
    erosion removes it otherwise), so ``radius`` has to be 2-3 times the hole radius for thin walls;
    it must stay well below half the width of the narrowest cavity. The surface is rebuilt at voxel
    size ``h``.
    """
    grid = Grid.for_mesh(mesh, h, margin_voxels=int(np.ceil(radius / h)) + 3)
    phi = sdf_from_mesh(mesh, grid, band=radius + 3 * max(h, grid.dz))
    solid = phi < 0
    sp = grid.spacing
    pad = int(np.ceil(radius / min(sp))) + 2
    solid = np.pad(solid, pad, constant_values=False)
    dilated = ndimage.distance_transform_edt(~solid, sampling=sp) <= radius
    closed = ndimage.distance_transform_edt(dilated, sampling=sp) > radius
    closed = closed[pad:-pad, pad:-pad, pad:-pad] | (phi < 0)
    d_in = ndimage.distance_transform_edt(closed, sampling=sp)
    d_out = ndimage.distance_transform_edt(~closed, sampling=sp)
    field_ = np.where(closed, -(d_in - 0.5 * h), d_out - 0.5 * h).astype(np.float32)
    # keep the exact field where the original surface is untouched
    keep = (phi < 0) == closed
    field_[keep] = phi[keep]
    return sdf_to_mesh(field_, grid)


# --------------------------------------------------------------------------- public entry point


def envelope(
    mesh: trimesh.Trimesh,
    h: float = 1.0,
    *,
    grid: Grid | None = None,
    band: float = 8.0,
    closing_radius: float = 0.0,
    manual_rim: np.ndarray | None = None,
    n_rays: int = 5,
    cone_deg: float = 10.0,
    snap_mm: float = 10.0,
    rim_step: float = 1.0,
    barrier_mm: float = 1.5,
) -> Envelope:
    """Solid outer envelope phi_E of a hollow template shoe and its collar mask.

    ``mesh``: template in the canonical frame (mm, +x toe, +z up), watertight, with an old cavity
    open through the collar. ``h``: horizontal voxel size; z spacing follows ``Grid.dz_for``.
    ``grid``: reuse a shared grid instead of ``Grid.for_mesh(mesh, h)``. ``band``: half width (mm)
    in which phi_E is an exact distance; beyond it phi_E holds +-band. A given ``grid`` must cover the
    template bounds (``EnvelopeError`` otherwise); ``h`` is then only used for the optional closing.

    Assumption: nothing overhangs above the collar (tongue flap, pull tab, folded cuff). The collar
    is snapped to the crest of the top-down height field H(x, y), so an overhanging part would be
    taken for the wall crest.

    ``closing_radius`` > 0 first closes perforations (hollow/lattice templates, radius about 2-3 times the
    largest hole radius, see :func:`close_mesh`) and runs everything on the closed surface. ``manual_rim`` (K >= 4 points on
    the collar crest, any order) replaces the automatic coarse collar when the inner/outer split is
    jagged or fragmented; the crest snapping still runs on it. ``n_rays``/``cone_deg``: ray vote.
    ``snap_mm``: search distance of the crest snap. ``barrier_mm``: faces within this distance of
    the collar curve separate the inner from the outer skin when the inner skin is flood-filled.

    Returns an :class:`Envelope` (field, collar curve + cap, M_below via ``mask_below()``, report).
    Raises :class:`EnvelopeError` when no collar can be found; the message names the fallback.
    """
    if not mesh.is_watertight:
        raise EnvelopeError("template mesh is not watertight")
    closing = None
    if closing_radius > 0:
        src, rep = close_mesh(mesh, closing_radius, h)
        closing = {"radius_mm": float(closing_radius), "components": rep.n_components, "faces": rep.n_faces,
                   "volume_mm3": rep.kept_volume_mm3, "dropped_volume_mm3": rep.dropped_volume_mm3,
                   "added_volume_mm3": float(rep.kept_volume_mm3 - mesh.volume)}
    else:
        src = mesh

    vote = majority_filter(src, vote_inner_faces(src, n_rays, cone_deg))
    inner0 = largest_face_component(src, vote)
    if not inner0.any() and manual_rim is None:
        raise EnvelopeError("no inner skin detected (a solid mesh has no old cavity); nothing to fill")
    rim, diag = extract_collar(src, inner0, snap_mm, rim_step, manual_rim)

    inner = flood_inner_skin(src, rim, vote, barrier_mm)

    collar = build_cap(rim)
    skin = trimesh.Trimesh(src.vertices, src.faces[~inner], process=False)
    skin.remove_unreferenced_vertices()
    surface = trimesh.util.concatenate([skin, collar.cap])

    grid = grid or Grid.for_mesh(src, h)
    hi = np.asarray(grid.origin) + (np.asarray(grid.shape) - 1) * np.asarray(grid.spacing)
    if (src.bounds[0] < np.asarray(grid.origin)).any() or (src.bounds[1] > hi).any():
        raise EnvelopeError("grid does not cover the template bounds")
    phi = sdf_from_mesh(surface, grid, band=band)

    # overshoot: how far the material rises above z_T within 2 mm of the collar (roundI must not cut it)
    n = _outward_normals(rim)
    offs = np.arange(-2.0, 2.0 + 1e-9, 0.5)
    hh = _height_field(src, (rim[:, None, :2] + offs[None, :, None] * n[:, None, :]).reshape(-1, 2),
                       float(src.bounds[1, 2]) + 1.0).reshape(len(rim), len(offs))
    overshoot = np.nanmax(hh, axis=1) - rim[:, 2]
    voxel = grid.h * grid.h * grid.dz
    # material standing above the cap inside the collar (roundI at z_T would cut it)
    vin = collar.inside(src.vertices[:, :2])
    above = src.vertices[vin, 2] - collar.z_t(src.vertices[vin, :2])
    # inner faces must lie inside the envelope; a patch of outer skin wrongly flooded into the inner skin
    # leaves a hole in phi_E and shows up here (the cavity floor and walls are >= 1 mm deep)
    cen = src.triangles_center[inner]
    exposed = sample(phi, grid, cen) > -0.3
    exposed_frac = float(src.area_faces[inner][exposed].sum() / max(src.area_faces[inner].sum(), 1e-9))
    warnings = []
    if exposed_frac > 0.01:
        warnings.append(f"{exposed_frac:.1%} of the inner skin area is not inside the envelope: a patch of outer skin "
                        "may have leaked into the inner skin (check barrier_mm / closing_radius)")
    if diag.get("unclosed_loops") or diag.get("pinch_vertices"):
        warnings.append(f"collar boundary has {diag.get('pinch_vertices', 0)} pinch vertices and "
                        f"{diag.get('unclosed_loops', 0)} unclosed loops; verify the collar curve or pass manual_rim")
    report = {
        "faces": int(len(src.faces)),
        "inner_faces": int(inner.sum()),
        "inner_area_mm2": float(src.area_faces[inner].sum()),
        "vote_inner_faces": int(vote.sum()),
        "vote_area_in_inner_frac": float(src.area_faces[vote & inner].sum() / max(src.area_faces[vote].sum(), 1e-9)),
        "inner_area_not_voted_frac": float(src.area_faces[inner & ~vote].sum() / max(src.area_faces[inner].sum(), 1e-9)),
        "collar": {
            "points": int(len(rim)),
            "length_mm": float(_arclength(rim, 3)[-1]),
            "z_min": float(rim[:, 2].min()),
            "z_max": float(rim[:, 2].max()),
            "z_mean": float(rim[:, 2].mean()),
            "max_overshoot_mm": float(np.nanmax(overshoot)),
            "p95_overshoot_mm": float(np.nanpercentile(overshoot, 95)),
            **diag,
        },
        "cap": {"faces": int(len(collar.cap.faces)), "area_mm2": float(collar.cap.area)},
        "grid": {"shape": list(grid.shape), "h": grid.h, "dz": grid.dz},
        "volume_mm3": float((phi < 0).sum() * voxel),
        "closing": closing,
        "manual_rim": manual_rim is not None,
        "material_above_cap_mm": float(above.max()) if above.size else 0.0,
        "inner_area_exposed_frac": exposed_frac,
        "warnings": warnings,
    }
    return Envelope(grid=grid, phi=phi, collar=collar, inner_faces=inner, source=src, surface=surface, report=report)
