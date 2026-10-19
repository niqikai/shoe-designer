import os

import numpy as np
import pytest
import trimesh

from shoe_designer import envelope as env
from shoe_designer.envelope import EnvelopeError
from shoe_designer.sdf import Grid, sample, sdf_to_mesh

A, B, WALL, FLOOR = 40.0, 20.0, 3.0, 4.0
Z_SIDE, Z_RISE = 22.0, 10.0  # collar height: 22 at the sides, 32 at the heel and the toe (saddle)


def zrim(x, y):
    t = np.arctan2(y / B, x / A)
    return Z_SIDE + Z_RISE * np.cos(t) ** 2


def saddle_shoe(holes=False, h=1.0):
    """Elliptical hollow 'shoe' whose collar is a saddle: high at both ends, low at the sides."""
    grid = Grid.from_bounds([-A - 4, -B - 4, -3], [A + 4, B + 4, Z_SIDE + Z_RISE + 4], h, 43.0)
    ax = [grid.origin[i] + np.arange(n) * s for i, (n, s) in enumerate(zip(grid.shape, grid.spacing))]
    x, y, z = np.meshgrid(*ax, indexing="ij")
    ell = lambda a, b: (np.sqrt((x / a) ** 2 + (y / b) ** 2) - 1.0) * min(a, b)
    body = np.maximum(np.maximum(ell(A, B), z - zrim(x, y)), -z)
    cavity = np.maximum(ell(A - WALL, B - WALL), FLOOR - z)
    phi = np.maximum(body, -cavity)
    if holes:  # perforations through both side walls
        for hx in (-20.0, 0.0, 20.0):
            hole = np.maximum(np.sqrt((x - hx) ** 2 + (z - 12.0) ** 2) - 3.0, -1e3 + 0 * y)
            phi = np.maximum(phi, -hole)
    mesh, _ = sdf_to_mesh(phi.astype(np.float32), grid)
    return mesh


@pytest.fixture(scope="module")
def shoe():
    return saddle_shoe()


@pytest.fixture(scope="module")
def result(shoe):
    return env.envelope(shoe, h=1.0)


def phi_at(r, pts):
    return sample(r.phi, r.grid, np.asarray(pts, float))


def test_old_cavity_is_filled_including_saddle_ends(result):
    # centre of the cavity, and cavity points at the heel/toe that are above the lowest collar point
    pts = [[0, 0, 10], [0, 0, 18], [-34, 0, 26], [34, 0, 28], [14, 6, 14]]
    for x, y in [(-30, 4), (30, -4), (-22, 0)]:  # just under the seal, above the lowest collar point
        pts.append([x, y, float(result.collar.z_t(np.array([[x, y]]))[0]) - 2.0])
    assert (phi_at(result, pts) < 0).all()
    assert pts[-2][2] > Z_SIDE + 2  # the seal really is higher than the side collar there
    assert (phi_at(result, [[0, 0, 45], [38, 0, 40], [0, 0, -6], [0, 35, 10]]) > 0).all()


def test_collar_height_matches_template(result):
    rim = result.collar.rim
    truth = zrim(rim[:, 0], rim[:, 1])
    assert np.abs(rim[:, 2] - truth).max() < 1.0
    assert result.report["collar"]["z_min"] == pytest.approx(Z_SIDE, abs=1.0)
    assert result.report["collar"]["z_max"] == pytest.approx(Z_SIDE + Z_RISE, abs=1.0)
    # crest of the wall: the collar sits mid-wall, not inside the cavity or outside the part
    r = np.hypot(rim[:, 0] / A, rim[:, 1] / B)
    assert (r > 1 - WALL / min(A, B) - 0.02).all() and (r < 1.02).all()
    assert result.report["collar"]["max_overshoot_mm"] <= 0.8


def test_collar_is_closed_counter_clockwise_loop(result):
    rim = result.collar.rim
    x, y = rim[:, 0], rim[:, 1]
    assert 0.5 * np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y) > 0
    assert np.linalg.norm(np.roll(rim, -1, axis=0) - rim, axis=1).max() < 2.0


def test_mask_below_follows_saddle(result):
    m = result.mask_below()
    g = result.grid

    def at(x, y, z):
        i = np.round(g.to_index(np.array([[x, y, z]]))[0]).astype(int)
        return bool(m[tuple(i)])

    assert at(0, 0, 10) and not at(0, 0, 33)
    assert at(0, 14, 15) and not at(0, 14, 28)  # low side
    assert at(-36, 0, 28) and not at(-36, 0, 34)  # high heel


def test_envelope_exports_watertight_solid(result):
    mesh, rep = result.to_mesh()
    assert mesh.is_watertight and mesh.volume > 0 and rep.dropped_volume_mm3 == 0
    assert rep.n_components >= 1
    # solid ellipse filled up to ~ the collar height: between the saddle extremes
    area = np.pi * A * B
    assert area * Z_SIDE * 0.95 < mesh.volume < area * (Z_SIDE + Z_RISE) * 1.05


def test_inner_skin_detected(shoe, result):
    inner = result.inner_faces
    c = shoe.triangles_center[inner]
    assert inner.sum() > 100
    assert (np.hypot(c[:, 0] / (A - WALL), c[:, 1] / (B - WALL)) < 1.05).all()  # all faces on the cavity side
    outer_c = shoe.triangles_center[~inner]
    assert not (np.hypot(outer_c[:, 0] / (A - WALL), outer_c[:, 1] / (B - WALL)) < 0.97)[outer_c[:, 2] > FLOOR + 2].any()


def test_manual_rim_fallback(shoe, result):
    ts = np.linspace(0, 2 * np.pi, 10, endpoint=False)
    r = 1 - 0.5 * WALL / min(A, B)
    pts = np.column_stack([A * r * np.cos(ts), B * r * np.sin(ts), zrim(A * np.cos(ts), B * np.sin(ts))])
    out = env.envelope(shoe, h=1.0, manual_rim=pts)
    assert out.report["manual_rim"]
    assert np.abs(out.collar.rim[:, 2] - zrim(out.collar.rim[:, 0], out.collar.rim[:, 1])).max() < 1.0
    assert (phi_at(out, [[0, 0, 15], [-34, 0, 26]]) < 0).all()


def test_closing_fills_perforated_walls():
    shoe = saddle_shoe(holes=True)
    # holes of radius 3 in a 3 mm wall need r ~ 8: dilation only fills a lens of thickness t + 2*sqrt(r^2 - a^2),
    # and the erosion removes it again unless that is >= 2r
    out = env.envelope(shoe, h=1.0, closing_radius=8.0)
    assert out.report["closing"]["radius_mm"] == 8.0
    assert (phi_at(out, [[0, 0, 15], [-20, 0, 12], [20, 4, 18]]) < 0).all()
    mesh, _ = out.to_mesh()
    assert mesh.is_watertight


def test_solid_template_has_no_cavity_to_fill():
    with pytest.raises(EnvelopeError, match="no inner skin"):
        env.envelope(trimesh.creation.box(extents=[100, 50, 40]), h=2.0)


def test_open_mesh_is_rejected():
    m = trimesh.creation.box(extents=[10, 10, 10])
    m.update_faces(np.arange(len(m.faces)) != 0)
    with pytest.raises(EnvelopeError, match="watertight"):
        env.envelope(m)


def kidney_rim(n=240):
    t = np.linspace(0, 2 * np.pi, n, endpoint=False)
    r = 30 - 24 * np.exp(-((np.angle(np.exp(1j * t))) / 0.5) ** 2)  # deep notch at t = 0
    x, y = r * np.cos(t), r * np.sin(t)
    return np.column_stack([x, y, 50 + 0.1 * x + 0.05 * y])


def test_z_t_outside_a_concave_collar_is_nearest_collar_height():
    rim = kidney_rim()
    collar = env.build_cap(rim)
    # inside the notch: outside the loop but inside its convex hull
    probe = np.array([[20.0, 0.7], [15.0, 1.3], [22.0, -2.1]])  # off the symmetry axis: no ties
    assert not collar.inside(probe).any()
    d = np.linalg.norm(probe[:, None, :] - rim[None, :, :2], axis=2)
    assert collar.z_t(probe) == pytest.approx(rim[d.argmin(axis=1), 2])
    inside = np.array([[0.0, 0.0], [-10.0, 5.0]])
    assert collar.inside(inside).all()
    assert collar.z_t(inside) == pytest.approx(50 + 0.1 * inside[:, 0] + 0.05 * inside[:, 1], abs=0.5)


def test_manual_rim_keeps_a_simple_input_order_and_rejects_degenerate_input():
    ts = np.linspace(0, 2 * np.pi, 8, endpoint=False)
    pts = np.column_stack([30 * np.cos(ts), 20 * np.sin(ts), np.full(8, 10.0)])
    cw = env._manual_rim(pts[::-1])  # clockwise but simple: order kept, no resorting
    assert np.allclose(cw[0, :2], pts[-1, :2], atol=1e-6)
    with pytest.raises(EnvelopeError, match="at least 4"):
        env._manual_rim(pts[:3])
    dup = np.vstack([pts, pts[:1], pts[:1]])
    assert len(env._manual_rim(dup)) == 400


def test_grid_must_cover_the_template(shoe):
    small = Grid.from_bounds([-10, -10, 0], [10, 10, 10], 1.0)
    with pytest.raises(EnvelopeError, match="cover"):
        env.envelope(shoe, grid=small)


def test_report_has_no_warnings_on_a_clean_template(result):
    assert result.report["warnings"] == []
    assert result.report["inner_area_exposed_frac"] < 0.01
    assert result.report["collar"]["pinch_vertices"] == 0
    assert result.report["material_above_cap_mm"] < 1.0


def test_closing_report_is_kept():
    out = env.envelope(saddle_shoe(holes=True), h=1.0, closing_radius=8.0)
    c = out.report["closing"]
    assert c["components"] >= 1 and c["dropped_volume_mm3"] == 0 and c["added_volume_mm3"] > 0


def _has_embree():
    try:
        import embreex  # noqa: F401
        return True
    except ImportError:
        return False


needs_real = pytest.mark.skipif(
    not os.environ.get("SHOE_ASSETS_ROOT") or not _has_embree(),
    reason="needs SHOE_ASSETS_ROOT and embreex (428k-face ray queries)",
)


@needs_real
def test_real_template_dlp_solid_43_right():
    from shoe_designer.assets import load_asset, load_catalog

    mesh = load_asset("dlp_solid_43_right", load_catalog()).mesh
    out = env.envelope(mesh, h=1.0, closing_radius=12.0)
    rep = out.report
    rim = out.collar.rim
    assert rep["warnings"] == []
    # collar height per 10 mm of x against the highest mesh vertex of the same slab (design doc 0, solid 43):
    # heel 107-108, sides 82-83, front 101-104. The two steep flanks (x 40-50, x 110-120) read ~1 mm low
    # because the slab maximum is taken at its steep edge; everything else is within 0.25 mm.
    for x0 in range(10, 130, 10):
        s = (mesh.vertices[:, 0] >= x0) & (mesh.vertices[:, 0] < x0 + 10)
        r = (rim[:, 0] >= x0) & (rim[:, 0] < x0 + 10)
        assert abs(rim[r, 2].max() - mesh.vertices[s, 2].max()) <= (1.3 if x0 in (40, 110) else 0.25), x0
    assert rep["collar"]["z_min"] == pytest.approx(81.3, abs=1.0)
    assert rep["collar"]["z_max"] == pytest.approx(107.6, abs=1.0)
    assert rep["collar"]["length_mm"] == pytest.approx(294.5, rel=0.03)
    assert rep["collar"]["max_overshoot_mm"] <= 0.5  # design doc 1.4: roundI must not cut the template
    assert rep["material_above_cap_mm"] <= 0.8
    assert rep["volume_mm3"] == pytest.approx(1.5405e6, rel=0.02)
    mesh_out, export = out.to_mesh()
    assert mesh_out.is_watertight and export.n_components == 1 and export.dropped_volume_mm3 == 0
