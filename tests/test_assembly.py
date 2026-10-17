"""M0.8: shoe assembly on synthetic lasts (rounded, 30 mm heel rise, R = 50 hemisphere bottom).

Every shoe is built through the production path (``build_shoe_mesh``: graph -> lattice
parameter check -> block plan -> block-sparse marching cubes) at 1 mm voxels with the
M0.10 smoke-test lattice (cell 12 mm, strut radius 2 mm). Measurements are made on the
extracted mesh (ray casting) and, where a tighter tolerance is meaningful, on the graph's
field; references are independent of the graph's offset machinery (analytic last SDF,
scipy EDT). Numbers are printed in the "measured values" summary.

The 0.5 mm runs with the ``examples/m0.json`` lattice (radius 1 mm, cell 10 mm) are
marked ``slow``.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pytest
from build123d import export_step
from fixtures.synthetic_lasts import ALL_LASTS, R_HEMI, lower_envelope
from scipy import ndimage
from scipy.spatial import cKDTree

from shoegen.assembly import (
    NODE_IDS,
    AssemblyParamError,
    assembly_params_from_obj,
    build_shoe_graph,
    build_shoe_mesh,
    load_assembly_params,
    mesh_graph,
    shoe_layout,
    shoe_region,
)
from shoegen.graph import EXACT, compile_graph, loads_graph
from shoegen.last import FieldParams, LastLoader, build_last_fields, last_to_mesh
from shoegen.lattice import LatticeParameterError, check_lattice_params

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "m0.json"
PROFILE = "pbf-tpu-generic@0"
VOXEL = 1.0
SMOKE_LATTICE = {"cell_mm": 12.0, "radius_mm": 2.0}
# The envelope reaches 3 mm past the footprint; a 30 mm sheet margin only costs time.
FIELD_PARAMS = FieldParams(sheet_margin_mm=8.0)
COLLAR_MM = {"rounded": 30.0, "heel_rise": 45.0, "hemisphere": 65.0}
"""Collar height above the lowest point of each last (last heights: 40, 67.9, 80 mm)."""
LASTS = ("rounded", "heel_rise", "hemisphere")


# ------------------------------------------------------------------ helpers


def example_obj() -> dict:
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def case_params(name: str, lattice: dict | None = None):
    obj = example_obj()
    obj["collar_height_mm"] = COLLAR_MM[name]
    obj["lattice"].update(SMOKE_LATTICE if lattice is None else lattice)
    return assembly_params_from_obj(obj)


_FIELDS: dict[tuple[str, Path], tuple] = {}


def last_fields_of(name: str, work: Path):
    """Synthetic last and its fields, built once per module (a STEP export carries a
    timestamp, so a second export would miss the field cache)."""
    if (name, work) not in _FIELDS:
        _FIELDS[(name, work)] = _build_last_fields(name, work)
    return _FIELDS[(name, work)]


def _build_last_fields(name: str, work: Path):
    synthetic = ALL_LASTS[name]()
    path = work / f"{name}.step"
    export_step(synthetic.shape, path)
    loaded = LastLoader().load(path)
    mesh = last_to_mesh(
        loaded.shape, 0.01, angular_tolerance=0.05, source_sha256=loaded.source["sha256"]
    )
    return synthetic, build_last_fields(mesh, work / "cache", FIELD_PARAMS)


class RayCaster:
    """Exact ray / triangle-mesh intersection (Moller-Trumbore) with a KD-tree prefilter."""

    def __init__(self, mesh) -> None:
        v = mesh.vertices
        f = mesh.faces.astype(np.int64)
        self.a = v[f[:, 0]]
        self.e1 = v[f[:, 1]] - self.a
        self.e2 = v[f[:, 2]] - self.a
        self.normal = np.cross(self.e1, self.e2)  # outward: faces are CCW seen from outside
        centre = self.a + (self.e1 + self.e2) / 3.0
        corners = np.stack([v[f[:, i]] for i in range(3)], axis=1)
        self.radius = float(np.linalg.norm(corners - centre[:, None, :], axis=2).max()) + 1e-9
        self.tree3 = cKDTree(centre)
        self.tree2 = cKDTree(centre[:, :2])

    def cast(self, origin, direction, length):
        """Sorted hit distances and ``-1`` (entering material) / ``+1`` (leaving) flags."""
        o = np.asarray(origin, dtype=np.float64)
        d = np.asarray(direction, dtype=np.float64)
        d = d / np.linalg.norm(d)
        if d[0] == 0.0 and d[1] == 0.0:
            idx = self.tree2.query_ball_point(o[:2], self.radius)
        else:
            idx = self.tree3.query_ball_point(o + 0.5 * length * d, 0.5 * length + self.radius)
        idx = np.asarray(idx, dtype=np.int64)
        if idx.size == 0:
            return np.empty(0), np.empty(0, dtype=np.int64)
        e1, e2 = self.e1[idx], self.e2[idx]
        p = np.cross(d, e2)
        det = np.einsum("ij,ij->i", e1, p)
        ok = np.abs(det) > 1e-14
        inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
        tv = o - self.a[idx]
        u = np.einsum("ij,ij->i", tv, p) * inv
        q = np.cross(tv, e1)
        w = (q @ d) * inv
        t = np.einsum("ij,ij->i", e2, q) * inv
        hit = ok & (u >= 0) & (w >= 0) & (u + w <= 1) & (t >= 0) & (t <= length)
        t = t[hit]
        s = np.sign(self.normal[idx][hit] @ d).astype(np.int64)
        order = np.argsort(t, kind="stable")
        t, s = t[order], s[order]
        if t.size > 1:  # a ray through a shared edge hits both triangles
            keep = np.ones(t.size, dtype=bool)
            keep[1:] = ~((np.diff(t) < 1e-7) & (s[1:] == s[:-1]))
            t, s = t[keep], s[keep]
        return t, s

    def intervals(self, origin, direction, length):
        """Material intervals ``[(t_in, t_out), ...]`` of a ray that starts outside; a ray
        that ends inside material gets ``t_out = inf`` for its last interval."""
        t, s = self.cast(origin, direction, length)
        if t.size % 2:
            t, s = np.append(t, np.inf), np.append(s, 1)
        if np.any(s[0::2] != -1) or np.any(s[1::2] != 1):
            raise AssertionError(f"hits do not alternate in/out: {list(zip(t, s))}")
        return list(zip(t[0::2], t[1::2]))


def grad(prog, p, step=0.02):
    g = np.empty_like(p)
    for a in range(3):
        e = np.zeros(3)
        e[a] = step
        g[:, a] = (prog.eval(p + e) - prog.eval(p - e)) / (2 * step)
    return g


@dataclass
class Case:
    name: str
    last: object
    fields: object
    params: object
    build: object
    build_seconds: float
    _progs: dict = field(default_factory=dict)
    _caster: RayCaster | None = None

    def prog(self, nid: str):
        if nid not in self._progs:
            self._progs[nid] = compile_graph(self.build.graph, self.fields.store, root=nid)
        return self._progs[nid]

    @property
    def mesh(self):
        return self.build.mesh_result.mesh

    @property
    def caster(self) -> RayCaster:
        if self._caster is None:
            self._caster = RayCaster(self.mesh)
        return self._caster

    @property
    def layout(self):
        return self.build.layout

    def cavity_bottom(self, xy, z_hi):
        """Lowest z of the cavity (last offset by the clearance) above each xy, analytic."""
        c = self.params.clearance_mm
        return lower_envelope(lambda p: self.last.sdf(p) - c, xy, self.layout.z_ground, z_hi)


# ------------------------------------------------------------------ fixtures


@pytest.fixture(scope="module")
def work(tmp_path_factory):
    return tmp_path_factory.mktemp("assembly")


@pytest.fixture(scope="module", params=LASTS)
def case(request, work) -> Case:
    name = request.param
    synthetic, lf = last_fields_of(name, work)
    params = case_params(name)
    t0 = time.perf_counter()
    build = build_shoe_mesh(lf, params, VOXEL, PROFILE, workers=1)
    return Case(name, synthetic, lf, params, build, time.perf_counter() - t0)


@pytest.fixture(scope="module")
def rounded_fields(work):
    return last_fields_of("rounded", work)


# ------------------------------------------------------------------ parameters and graph


def test_example_parameters():
    p = load_assembly_params(EXAMPLE)
    assert p.clearance_mm == 1.0 and p.forefoot_sole_mm == 12.0
    assert p.skin_mm == 1.2 and p.footbed_mm == 1.5 and p.upper_thickness_mm == 2.0
    assert p.outline_margin_mm == 3.0 and p.height_surface_offset_mm == 3.0
    assert p.top_margin_mm == 3.0
    assert p.lattice.topology == "bcc" and p.lattice.fillet_k is None
    bad = example_obj()
    bad["skin"] = 1.0
    with pytest.raises(AssemblyParamError) as exc:
        assembly_params_from_obj(bad)
    assert exc.value.exit_code == 3


def test_graph_structure_metric_and_checks(rounded_fields):
    _synthetic, lf = rounded_fields
    obj = example_obj()
    obj["collar_height_mm"] = COLLAR_MM["rounded"]
    params = assembly_params_from_obj(obj)  # the example lattice (r = 1 mm, cell 10 mm)
    g = build_shoe_graph(lf, params)
    assert set(g.nodes) == set(NODE_IDS) and g.root == "shoe"
    # the only offsets are on exact sides (ADR 0001)
    assert g.metric("cavity").outside == EXACT
    assert g.metric("envelope").inside == EXACT
    assert g.metric("sole.core").inside == EXACT
    assert g.nodes["sole.core"].distance == -params.skin_mm
    # band reach (ADR 0003): last 1 + 2, height surface 1.2, both <= 6 - 1
    assert g.analysis.sampled_reach == {"envelope.cap": 1.2, "last": 3.0}
    # the example lattice passes the pbf profile at 0.5 mm, not at 1 mm (voxel <= 2r/4)
    check_lattice_params(g, PROFILE, 0.5)
    with pytest.raises(LatticeParameterError):
        check_lattice_params(g, PROFILE, 1.0)
    # deterministic and round-trips through graph.json
    h = g.graph_hash()
    assert build_shoe_graph(lf, params).graph_hash() == h
    assert loads_graph(g.canonical_json()).graph_hash() == h
    lay = shoe_layout(lf, params)
    assert lay.z_ground == pytest.approx(-12.0, abs=1e-6)
    assert lay.z_top == pytest.approx(13.0, abs=0.05)  # rounded bottom: h = 10 at the outline
    lo, hi = shoe_region(lf, params, 0.5)
    assert lo[2] < lay.z_ground and hi[2] > lay.z_collar


def test_refusals_exit_3(rounded_fields):
    _, lf = rounded_fields
    obj = example_obj()
    obj["collar_height_mm"] = 40.0  # = height of the rounded last: upper would close
    with pytest.raises(AssemblyParamError) as exc:
        build_shoe_graph(lf, assembly_params_from_obj(obj))
    assert exc.value.exit_code == 3
    thin = case_params("rounded", {"radius_mm": 0.5})  # 1 mm struts < 1.5 mm (pbf profile)
    with pytest.raises(LatticeParameterError) as exc:
        build_shoe_mesh(lf, thin, 0.25, PROFILE, workers=1)
    assert exc.value.exit_code == 3


# ------------------------------------------------------------------ whole-shoe mesh


def solid_bodies(validation):
    pos = [c for c in validation.components if c.volume_mm3 > 0]
    neg = [c for c in validation.components if c.volume_mm3 <= 0]
    return pos, neg


def test_mesh_watertight_single_body(case, eval_report):
    check_mesh_watertight_single_body(case, eval_report)


def check_mesh_watertight_single_body(case, eval_report):
    res = case.build.mesh_result
    val = res.validation
    assert val.ok and val.watertight and val.manifold
    assert not res.summary["open_at_region_boundary"]
    assert val.n_components == len(val.components)
    bodies, voids = solid_bodies(val)
    # One solid body. The lattice pores are sealed by the skin and the footbed, so the
    # remaining shells are internal void surfaces (negative volume), not separate parts.
    assert len(bodies) == 1
    assert all(c.volume_mm3 < 0 for c in voids)
    s = res.summary
    eval_report(
        f"{case.name}: {s['triangles']} triangles, solid bodies {len(bodies)}, void shells "
        f"{len(voids)} ({-sum(c.volume_mm3 for c in voids):.0f} mm3; largest "
        f"{-min((c.volume_mm3 for c in voids), default=0):.0f}), volume {s['volume_mm3']:.0f} "
        f"mm3, fragments removed {s['fragments_removed']}, blocks "
        f"{s['eval']['sampled_blocks']}/{s['eval']['total_blocks']}, build "
        f"{case.build_seconds:.1f}s (graph incl. height surface {case.build.seconds['graph']:.1f}"
        f"s, mesh {case.build.seconds['mesh']:.1f}s)"
    )


def test_no_material_inside_last(case, eval_report):
    check_no_material_inside_last(case, eval_report)


def check_no_material_inside_last(case, eval_report):
    r = np.random.default_rng(1)
    lo, hi = np.array(case.layout.last_min), np.array(case.layout.last_max)
    p = r.uniform(lo, hi, (60000, 3))
    p = p[case.last.sdf(p) < 0][:20000]
    d = case.prog("shoe").eval(p)
    # shoe = (...) - cavity, so d >= -cavity = clearance - last_sdf >= clearance
    assert d.min() >= case.params.clearance_mm - 0.05
    # on the mesh: a ray from an interior point up and out crosses the surface an even
    # number of times (point outside the solid)
    top = case.build.region_max[2] + 1.0
    odd = 0
    for q in p[:400]:
        t, _ = case.caster.cast(q, (0.0, 0.0, 1.0), top - q[2])
        odd += t.size % 2
    assert odd == 0
    eval_report(f"{case.name}: min shoe field inside the last {d.min():.3f} mm ({len(p)} pts)")


def test_collar_is_open(case, eval_report):
    check_collar_is_open(case, eval_report)


def check_collar_is_open(case, eval_report):
    vox = case.build.voxel_mm
    lay = case.layout
    lo, hi = lay.footprint_min, lay.footprint_max
    xs = np.arange(lo[0] + 0.137, hi[0], 2.0)
    ys = np.arange(lo[1] + 0.071, hi[1], 2.0)
    xy = np.stack(np.meshgrid(xs, ys, indexing="ij"), axis=-1).reshape(-1, 2)
    z0 = lay.z_collar
    inside = case.last.sdf(np.column_stack([xy, np.full(len(xy), z0)])) < -1.0
    xy = xy[inside]
    assert len(xy) > 100
    z_start = z0 + 5.0
    bottom = case.cavity_bottom(xy, z_start)
    first = np.full(len(xy), np.nan)
    for i, (x, y) in enumerate(xy):
        iv = case.caster.intervals((x, y, z_start), (0.0, 0.0, -1.0), z_start - lay.z_ground + 5)
        if iv:
            first[i] = z_start - iv[0][0]
    reached = np.abs(first - bottom) <= vox
    frac = float(np.mean(reached))
    eval_report(
        f"{case.name}: collar z = {z0:.1f}: {reached.sum()}/{len(xy)} rays = {100 * frac:.1f}% "
        f"reach the footbed first; |first hit - cavity bottom| median "
        f"{np.nanmedian(np.abs(first - bottom)):.3f} mm"
    )
    assert frac >= 0.90


def end_columns(case):
    lay = case.layout
    (x0, y0), (x1, y1) = lay.footprint_min, lay.footprint_max
    yc = 0.5 * (y0 + y1)
    if case.name == "hemisphere":
        heel, toe = -0.8 * R_HEMI, 0.8 * R_HEMI
        ys = (-8.0, 0.0, 8.0)
    else:
        heel, toe = x0 + 8.0, x1 - 8.0
        ys = (yc - 15.0, yc, yc + 15.0)
    return [("heel", heel, y) for y in ys] + [("toe", toe, y) for y in ys]


def test_sole_under_heel_and_toe(case, eval_report):
    check_sole_under_heel_and_toe(case, eval_report)


def check_sole_under_heel_and_toe(case, eval_report):
    vox = case.build.voxel_mm
    lay = case.layout
    cols = end_columns(case)
    xy = np.array([[x, y] for _, x, y in cols])
    bottom = case.cavity_bottom(xy, lay.last_max[2] + 2.0)
    lines = []
    for (where, x, y), zb in zip(cols, bottom, strict=True):
        z_start = zb + 3.0  # inside the cavity, above the cavity bottom
        iv = case.caster.intervals((x, y, z_start), (0.0, 0.0, -1.0), z_start - lay.z_ground + 5)
        assert iv, f"{where} ({x:.1f}, {y:.1f}): no material under the last"
        z_in = z_start - iv[0][0]
        z_out = z_start - iv[-1][1]
        solid = sum(b - a for a, b in iv)
        assert abs(z_in - zb) <= vox  # material starts at the cavity bottom (footbed)
        assert z_in > lay.z_ground + 2.0  # ... well before the ground
        assert abs(z_out - lay.z_ground) <= vox  # and the sole reaches the ground
        assert solid >= 2 * case.params.skin_mm
        lines.append(f"{where}({x:.0f},{y:.0f}) {z_in:.1f}->{z_out:.1f} solid {solid:.1f}")
    eval_report(f"{case.name}: z_ground {lay.z_ground:.1f}; " + "; ".join(lines))


# ------------------------------------------------------------------ skin


def outer_envelope_points(case, n_seed=8000, seed=3):
    """Points on the outer faces of the envelope (bottom, side wall, top cap), with the
    inward unit normal; the cavity face is excluded (footbed and upper sit there)."""
    env, cav = case.prog("envelope"), case.prog("cavity")
    r = np.random.default_rng(seed)
    lo = np.array(case.build.region_min)
    hi = np.array(case.build.region_max)
    hi[2] = case.layout.z_top + 1.0
    p = r.uniform(lo, hi, (n_seed, 3))
    for _ in range(4):
        g = grad(env, p)
        gg = np.maximum(np.einsum("ij,ij->i", g, g), 1e-12)
        p = p - (env.eval(p) / gg)[:, None] * g
    g = grad(env, p)
    gn = np.linalg.norm(g, axis=1)
    ok = (np.abs(env.eval(p)) < 1e-3) & (np.abs(gn - 1.0) < 0.05) & (cav.eval(p) > 0.5)
    return p[ok], -g[ok] / gn[ok][:, None]


def first_void_depth(shoe, env, p, n, t_max=3.0, dt=0.01):
    """Depth along ``n`` of the first pore: a point inside the envelope without material
    (linear interpolation of the shoe field). Rays that leave the envelope first (thin
    lips, edges) are not pores and do not count."""
    t = np.arange(0.1, t_max, dt)
    q = (p[:, None, :] + t[None, :, None] * n[:, None, :]).reshape(-1, 3)
    v = np.where(env.eval(q) < 0, shoe.eval(q), -1.0).reshape(len(p), len(t))
    out = np.full(len(p), np.nan)
    pos = v >= 0
    has = pos.any(axis=1)
    k = np.argmax(pos, axis=1)
    rows = np.flatnonzero(has & (k > 0))
    a, b = v[rows, k[rows] - 1], v[rows, k[rows]]
    out[rows] = t[k[rows] - 1] + dt * (-a) / (b - a)
    return out


def test_skin_thickness(case, eval_report):
    check_skin_thickness(case, eval_report)


def check_skin_thickness(case, eval_report):
    vox = case.build.voxel_mm
    p, n = outer_envelope_points(case)
    shoe, env, lat, cav = (case.prog(k) for k in ("shoe", "envelope", "sole.lattice", "cavity"))
    skin = case.params.skin_mm
    # exposed: the outer side of the point is air in the finished shoe
    exposed = shoe.eval(p - 0.5 * n) > 0.05
    p, n = p[exposed], n[exposed]
    # where the core abuts the skin: just past the skin the ray is in a lattice pore, the
    # envelope there is a plain face (no other face closer), and no footbed/upper material
    # (a pore at least a voxel deep, so the 1 mm grid resolves it)
    probes = skin - 0.1 + np.linspace(0.0, vox + 0.6, 8)
    q = (p[:, None, :] + probes[None, :, None] * n[:, None, :]).reshape(-1, 3)
    shells = case.params.clearance_mm + max(case.params.footbed_mm, case.params.upper_thickness_mm)
    pore = (lat.eval(q) > 0.25).reshape(len(p), -1).all(axis=1)
    plain = env.eval(p + (skin + 0.5) * n) <= -(skin + 0.4)
    clear = (cav.eval(q) > shells - case.params.clearance_mm + 0.3).reshape(len(p), -1).all(1)
    sel = pore & plain & clear
    assert sel.sum() >= 50, f"only {sel.sum()} rays where the core abuts the skin"

    # field: depth of the first void below the surface
    t_field = first_void_depth(shoe, env, p[sel], n[sel])
    err_field = np.abs(t_field - skin)
    # mesh: from 0.5 mm outside, first material interval = the skin
    t_in, thick = [], []
    for o, d in zip(p[sel] - 0.5 * n[sel], n[sel], strict=True):
        iv = case.caster.intervals(o, d, 4.0)
        t_in.append(iv[0][0] if iv else np.nan)
        thick.append(iv[0][1] - iv[0][0] if iv else np.nan)
    t_in, thick = np.array(t_in), np.array(thick)
    # no pore closer to the exposed outer faces than the skin, anywhere
    t_all = first_void_depth(shoe, env, p, n)
    eval_report(
        f"{case.name}: {sel.sum()} rays where core abuts skin: mesh thickness "
        f"{np.nanmin(thick):.3f}..{np.nanmax(thick):.3f} mm (mean {np.nanmean(thick):.3f}), field "
        f"{np.nanmin(t_field):.3f}..{np.nanmax(t_field):.3f} mm; min first-void depth over all "
        f"{len(p)} exposed outer points {np.nanmin(t_all):.3f} mm"
    )
    assert np.all(np.isfinite(thick)) and np.all(np.abs(t_in - 0.5) <= vox)
    assert np.all(np.abs(thick - skin) <= vox)
    assert np.all(np.isfinite(t_field)) and err_field.max() <= 0.1
    assert np.nanmin(t_all) >= skin - 0.1


# ------------------------------------------------------------------ lattice core


def reference_core(case, h=0.25, slab=96):
    """Envelope voxelized at ``h`` (sign of the graph's envelope field), shrunk by the skin
    with scipy's EDT, in padded x slabs (exact for a threshold below the padding).

    Returns (core volume, envelope volume, uncorrected core volume). The EDT measures to the
    nearest *outside voxel centre*, on average h/2 beyond the boundary, so the threshold
    is ``skin + h/2``; the uncorrected volume uses ``skin``. Voxel centres sit at
    ``(i + 1/2) h``, so faces on round coordinates (the synthetic lasts have many) lie
    exactly h/2 before the next centre instead of on a sample.
    """
    env = case.prog("envelope")
    skin = case.params.skin_mm
    lo = np.array(case.build.region_min)
    hi = np.array(case.build.region_max)
    hi[2] = case.layout.z_top + 2.0
    g0 = np.floor(lo / h).astype(np.int64)
    g1 = np.ceil(hi / h).astype(np.int64)
    pad = math.ceil((skin + h) / h) + 2
    core = env_count = raw = 0
    for x0 in range(g0[0], g1[0], slab):
        x1 = min(x0 + slab, g1[0])
        a, b = max(x0 - pad, g0[0]), min(x1 + pad, g1[0])
        start = (a, g0[1], g0[2])
        shape = (b - a, g1[1] - g0[1], g1[2] - g0[2])
        mask = env.eval_grid((h / 2,) * 3, h, start, shape) < 0
        edt = ndimage.distance_transform_edt(mask, sampling=h)
        inner = slice(x0 - a, x1 - a)
        core += int(np.count_nonzero(edt[inner] > skin + h / 2))
        raw += int(np.count_nonzero(edt[inner] > skin))
        env_count += int(np.count_nonzero(mask[inner]))
    return core * h**3, env_count * h**3, raw * h**3


def section_areas(case, h=0.25, step=2.0):
    lay = case.layout
    core, pores = case.prog("sole.core"), case.prog("sole.pores")
    lo, hi = np.array(case.build.region_min), np.array(case.build.region_max)
    ys = np.arange(lo[1], hi[1], h)
    zs = np.arange(lo[2], lay.z_top + 1.0, h)
    yy, zz = (a.ravel() for a in np.meshgrid(ys, zs, indexing="ij"))
    out = []
    for x in np.arange(lay.footprint_min[0], lay.footprint_max[0] + 1e-9, step):
        q = np.column_stack([np.full(yy.size, x), yy, zz])
        c = core.eval(q) < 0
        pr = pores.eval(q[c]) < 0
        out.append((x, c.sum() * h * h, pr.sum() * h * h))
    return out


# The EDT reference costs 8-15 s per last. The default run keeps the rounded last (one fast
# test of the criterion, plan 1.3 time budget); the heel-rise and hemisphere variants run
# under ``-m slow``, as do all three at 0.5 mm in ``test_example_lattice_half_mm``.
@pytest.mark.parametrize("case", ["rounded"], indirect=True)
def test_lattice_core_volume_and_sections(case, eval_report):
    check_lattice_core_volume_and_sections(case, eval_report)


@pytest.mark.slow
@pytest.mark.parametrize("case", ["heel_rise", "hemisphere"], indirect=True)
def test_lattice_core_volume_and_sections_slow_variants(case, eval_report):
    check_lattice_core_volume_and_sections(case, eval_report)


def check_lattice_core_volume_and_sections(case, eval_report):
    vox = case.build.voxel_mm
    _, res = mesh_graph(
        case.build.graph.subgraph("sole.core"),
        case.fields.store,
        case.build.region_min,
        case.build.region_max,
        vox,
        workers=1,
    )
    assert res.validation.watertight
    ours = res.validation.volume_mm3
    t0 = time.perf_counter()
    ref, env_vol, raw = reference_core(case)
    t_ref = time.perf_counter() - t0
    rel = ours / ref - 1.0
    sections = section_areas(case)
    min_core = min(s[1] for s in sections)
    min_pore = min(s[2] for s in sections)
    eval_report(
        f"{case.name}: core volume {ours:.0f} mm3 (mesh, {vox} mm) vs EDT reference "
        f"{ref:.0f} mm3 ({100 * rel:+.2f}%; uncorrected EDT {raw:.0f}), envelope {env_vol:.0f} "
        f"mm3, reference {t_ref:.1f}s; {len(sections)} sections: min core area "
        f"{min_core:.1f} mm2, min pore area {min_pore:.1f} mm2"
    )
    assert abs(rel) <= 0.05
    assert min_core > 0 and min_pore > 0


def test_hemisphere_core_survives_steep_rim(work, eval_report):
    """Regression: the R = 50 hemisphere bottom, where a height field scaled globally by
    1/sqrt(1+G^2) (G from the steep rim) would eat the core under the bowl (ADR 0001):
    a 1.2 mm offset of the scaled field is a ~1.2 sqrt(1+G^2) mm offset where the bottom is flat.

    The shoe is the parametrized ``case`` above; here the core of the graph is compared
    with the same graph whose cap term is slope-scaled, on a 1 mm grid.
    """
    _synthetic, lf = last_fields_of("hemisphere", work)
    params = case_params("hemisphere")
    g = build_shoe_graph(lf, params)
    store = lf.store
    hm = lf.height_map
    gx, gy = np.gradient(np.asarray(hm.values, dtype=np.float64), hm.h)
    slope = float(np.hypot(gx, gy).max())
    scale = 1.0 / math.sqrt(1.0 + slope**2)
    core = compile_graph(g, store, root="sole.core")
    # under the bowl centre: cavity bottom -51, ground -62, cap -47; the nearest face of
    # (0, 0, -56) is the cavity, 5 mm away, so the core field is -5 + 1.2
    assert core.eval(np.array([[0.0, 0.0, -56.0]]))[0] == pytest.approx(-3.8, abs=0.1)
    terms = [
        compile_graph(g, store, root=k) for k in ("envelope.outline", "envelope.cap", "cavity")
    ]
    lay = shoe_layout(lf, params)
    h = 1.0
    xs = np.arange(-54.0, 54.0, h) + h / 2
    zs = np.arange(lay.z_ground - 1.0, lay.z_top + 1.0, h) + h / 2
    yy, zz = (a.ravel() for a in np.meshgrid(xs, zs, indexing="ij"))
    vol = {"ours": 0.0, "scaled": 0.0, "ours_bowl": 0.0, "scaled_bowl": 0.0, "ours_rim": 0.0}
    for x in xs:
        q = np.column_stack([np.full(yy.size, x), yy, zz])
        rho = np.hypot(q[:, 0], q[:, 1])
        bowl, rim = rho < 0.3 * R_HEMI, rho > 0.8 * R_HEMI
        ours = core.eval(q) < 0
        outline, cap, cavity = (t.eval(q) for t in terms)
        scaled = np.maximum.reduce([outline, cap * scale, -cavity]) < -params.skin_mm
        for key, m in (("ours", ours), ("scaled", scaled)):
            vol[key] += m.sum() * h**3
            vol[key + "_bowl"] += (m & bowl).sum() * h**3
        vol["ours_rim"] += (ours & rim).sum() * h**3
    eval_report(
        f"height-map slope G = {slope:.1f}; core volume ours {vol['ours']:.0f} mm3 vs slope-"
        f"scaled cap {vol['scaled']:.0f} mm3; under the bowl centre (rho < 15 mm) {vol['ours_bowl']:.0f}"
        f" vs {vol['scaled_bowl']:.0f} mm3; ours at the steep rim (rho > 40 mm) "
        f"{vol['ours_rim']:.0f} mm3"
    )
    assert slope > 5.0
    assert vol["ours_bowl"] > 3000.0 and vol["scaled_bowl"] < 0.2 * vol["ours_bowl"]
    # the centre column: our core spans -60.8 .. -52.2 (8.6 mm); the scaled one is empty
    col = np.column_stack([np.zeros(2000), np.zeros(2000), np.linspace(-63.0, -45.0, 2000)])
    outline, cap, cavity = (t.eval(col) for t in terms)
    scaled_col = np.maximum.reduce([outline, cap * scale, -cavity]) < -params.skin_mm
    ours_col = core.eval(col) < 0
    dz = col[1, 2] - col[0, 2]
    assert ours_col.sum() * dz == pytest.approx(8.6, abs=0.1)
    assert scaled_col.sum() == 0
    assert vol["ours_rim"] > 0.1 * vol["ours"]


# ------------------------------------------------------------------ 0.5 mm (slow)


@pytest.mark.slow
@pytest.mark.parametrize("name", LASTS)
def test_example_lattice_half_mm(name, work, eval_report):
    """The ``examples/m0.json`` lattice (r = 1 mm, cell 10 mm) at 0.5 mm voxels, 4 workers:
    the same acceptance checks as the 1 mm runs."""
    synthetic, lf = last_fields_of(name, work)
    obj = example_obj()
    obj["collar_height_mm"] = COLLAR_MM[name]
    params = assembly_params_from_obj(obj)
    t0 = time.perf_counter()
    build = build_shoe_mesh(lf, params, 0.5, PROFILE, workers=4)
    case = Case(name, synthetic, lf, params, build, time.perf_counter() - t0)
    for check in (
        check_mesh_watertight_single_body,
        check_no_material_inside_last,
        check_collar_is_open,
        check_sole_under_heel_and_toe,
        check_skin_thickness,
        check_lattice_core_volume_and_sections,
    ):
        check(case, eval_report)
