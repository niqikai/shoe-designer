import numpy as np
import pytest
import trimesh

from shoe_designer import envelope as env
from shoe_designer.envelope import EnvelopeError
from shoe_designer.sdf import Grid, sample, sdf_to_mesh

A, B, WALL, FLOOR = 60.0, 28.0, 3.0, 4.0
Z_SIDE, Z_RISE = 30.0, 14.0  # collar height: 30 at the sides, 44 at the heel and the toe (saddle)


def zrim(x, y):
    t = np.arctan2(y / B, x / A)
    return Z_SIDE + Z_RISE * np.cos(t) ** 2


def saddle_shoe(holes=False, h=0.7):
    """Elliptical hollow 'shoe' whose collar is a saddle: high at both ends, low at the sides."""
    grid = Grid.from_bounds([-A - 4, -B - 4, -3], [A + 4, B + 4, Z_SIDE + Z_RISE + 4], h, 43.0)
    ax = [grid.origin[i] + np.arange(n) * s for i, (n, s) in enumerate(zip(grid.shape, grid.spacing))]
    x, y, z = np.meshgrid(*ax, indexing="ij")
    ell = lambda a, b: (np.sqrt((x / a) ** 2 + (y / b) ** 2) - 1.0) * min(a, b)
    body = np.maximum(np.maximum(ell(A, B), z - zrim(x, y)), -z)
    cavity = np.maximum(ell(A - WALL, B - WALL), FLOOR - z)
    phi = np.maximum(body, -cavity)
    if holes:  # perforations through both side walls
        for hx in (-30.0, 0.0, 30.0):
            hole = np.maximum(np.sqrt((x - hx) ** 2 + (z - 15.0) ** 2) - 3.0, -1e3 + 0 * y)
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
    pts = [[0, 0, 12], [0, 0, 25], [-50, 0, 36], [50, 0, 38], [20, 10, 20]]
    for x, y in [(-45, 5), (45, -5), (-30, 0)]:  # just under the seal, above the lowest collar point
        pts.append([x, y, float(result.collar.z_t(np.array([[x, y]]))[0]) - 2.0])
    assert (phi_at(result, pts) < 0).all()
    assert pts[-2][2] > Z_SIDE + 3  # the seal really is higher than the side collar there
    assert (phi_at(result, [[0, 0, 60], [55, 0, 52], [0, 0, -6], [0, 45, 10]]) > 0).all()


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

    assert at(0, 0, 10) and not at(0, 0, 44)
    assert at(0, 20, 25) and not at(0, 20, 35)  # low side
    assert at(-52, 0, 40) and not at(-52, 0, 48)  # high heel


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
    assert (phi_at(out, [[0, 0, 25], [-50, 0, 36]]) < 0).all()


def test_closing_fills_perforated_walls():
    shoe = saddle_shoe(holes=True)
    # holes of radius 3 in a 3 mm wall need r ~ 8: dilation only fills a lens of thickness t + 2*sqrt(r^2 - a^2),
    # and the erosion removes it again unless that is >= 2r
    out = env.envelope(shoe, h=1.0, closing_radius=8.0)
    assert out.report["closing_radius_mm"] == 8.0
    assert (phi_at(out, [[0, 0, 20], [-30, 0, 15], [30, 5, 25]]) < 0).all()
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
