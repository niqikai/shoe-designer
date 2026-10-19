import math

import numpy as np
import pytest

from shoe_designer.sdf import Grid, sdf_to_mesh
from shoe_designer.support_closure import alpha_of, closure

SIN45 = math.sin(math.radians(45))


def axes_of(grid):
    return np.meshgrid(*[grid.origin[i] + np.arange(grid.shape[i]) * grid.spacing[i] for i in range(3)], indexing="ij")


def box_phi(grid, lo, hi):
    """Exact signed distance of an axis-aligned box on ``grid`` (negative inside)."""
    q = np.stack([np.maximum(l - p, p - u) for p, l, u in zip(axes_of(grid), lo, hi)])
    return (np.linalg.norm(np.maximum(q, 0), axis=0) + np.minimum(q.max(axis=0), 0)).astype(np.float32)


N_X = 60  # slab length in voxels


def slab(n_y, alpha, h=1.0, n_x=N_X, height=30.0, headroom=60.0):
    """Cavity with a flat ceiling: exactly ``n_x`` x ``n_y`` voxel centres in plan (n_y odd or even)."""
    grid = Grid.from_bounds((-5, -n_y / 2 - 5, -5), (n_x + 5, n_y / 2 + 5, height + headroom), h, alpha_deg=alpha)
    ox, oy = grid.origin[0], grid.origin[1]
    kx, ky = math.ceil((0 - ox) / h), math.ceil((-n_y / 2 - oy) / h)
    lo = (ox + (kx - 0.5) * h, oy + (ky - 0.5) * h, 0.0)
    hi = (ox + (kx + n_x - 0.5) * h, oy + (ky + n_y - 0.5) * h, height)
    return grid, box_phi(grid, lo, hi)


def overhang_area(mesh):
    """Area of cavity-mesh faces whose material side faces down more than 45 deg (normal out of cavity = up)."""
    return float(mesh.area_faces[mesh.face_normals[:, 2] > SIN45 + 1e-6].sum())


def films(phi):
    """Voxels that are material with cavity directly above and below (a horizontal film)."""
    inside = phi < 0
    return int((~inside[:, :, 1:-1] & inside[:, :, :-2] & inside[:, :, 2:]).sum())


@pytest.mark.parametrize("alpha", [40.0, 43.0, 50.0])
@pytest.mark.parametrize("n_y", [41, 40])
def test_ridge_height_and_no_films(alpha, n_y):
    grid, phi_c = slab(n_y, alpha)
    assert alpha_of(grid) == pytest.approx(alpha)
    res = closure(phi_c, grid)
    expect = (n_y * grid.h / 2) / math.tan(math.radians(alpha))  # half-width / tan(alpha_b)
    assert abs(res.report["ceiling_raise_max_mm"] - expect) <= grid.dz + 0.01  # report is rounded to 0.01 mm
    assert films(res.phi_c_star) == 0
    assert (res.phi_c_star <= phi_c).all()


@pytest.mark.parametrize("alpha", [40.0, 43.0])
@pytest.mark.parametrize("n_y", [41, 40])
def test_no_overhang_faces_after_marching_cubes(alpha, n_y):
    grid, phi_c = slab(n_y, alpha)
    mesh, _ = sdf_to_mesh(closure(phi_c, grid).phi_c_star, grid)
    assert mesh.is_watertight
    up = mesh.face_normals[:, 2]  # cavity-mesh normal up == material above faces down
    # Flat treads/apex (nz ~ 1): the <= 1-voxel-wide apex and marching-cubes slivers, about 1 mm^2 per layer.
    assert mesh.area_faces[up > 0.9].sum() <= 2 * grid.h * N_X
    # Rim facets where the ridge leaves the exact wall are ~41 deg from horizontal (nz ~ 0.75) at worst:
    # one voxel high, along the footprint perimeter only.
    assert mesh.area_faces[up > 0.80].sum() <= 2 * grid.h * N_X
    assert overhang_area(mesh) <= 1.5 * grid.h * 2 * (60 + n_y) + 2 * grid.h * N_X
    # control: without the closure the whole ceiling is an overhang
    flat, _ = sdf_to_mesh(phi_c, grid)
    assert overhang_area(flat) >= 0.9 * N_X * n_y * grid.h


def test_ridge_slope_is_alpha_b():
    grid, phi_c = slab(41, 50.0)
    mesh, _ = sdf_to_mesh(closure(phi_c, grid).phi_c_star, grid)
    nz = mesh.face_normals[:, 2]
    ridge = (nz > 0.3) & (nz < 0.95)
    assert ridge.sum() > 100
    assert np.median(nz[ridge]) == pytest.approx(math.sin(math.radians(50)), abs=0.03)


def test_open_top_cavity_is_untouched():
    grid = Grid.from_bounds((-5, -25, -5), (65, 25, 60), 1.0)
    phi_c = box_phi(grid, (0, -20, 0), (60, 20, 200))  # runs through the top of the grid
    res = closure(phi_c, grid)
    assert res.report["added_volume_mm3"] == 0
    assert np.array_equal(res.phi_c_star, phi_c)


def test_closure_only_adds_cavity():
    grid, phi_c = slab(41, 43.0)
    res = closure(phi_c, grid)
    assert ((res.phi_c_star < 0) | ~(phi_c < 0)).all()  # C stays inside
    deep = phi_c < -3  # well inside the original cavity: exactly the old field there or deeper
    assert (res.phi_c_star[deep] <= phi_c[deep]).all()
    assert res.report["added_volume_mm3"] > 0 and not res.report["shell_clipped"]
    assert res.phi_c_star.shape == phi_c.shape and res.phi_c_star.dtype == np.float32


def test_dome_cavity_gets_a_cone():
    grid = Grid.from_bounds((-30, -30, -5), (30, 30, 90), 1.0)
    x, y, z = axes_of(grid)
    phi_c = (np.sqrt(x**2 + y**2 + (z - 20) ** 2) - 20).astype(np.float32)  # sphere r=20, top at z=40
    res = closure(phi_c, grid)
    mesh, _ = sdf_to_mesh(res.phi_c_star, grid)
    assert res.report["added_volume_mm3"] > 0
    assert films(res.phi_c_star) == 0
    flat, _ = sdf_to_mesh(phi_c, grid)
    assert overhang_area(mesh) < 0.2 * overhang_area(flat)


def test_heel_loss_only_when_steeper_than_alpha():
    def heel(beta_deg):
        grid = Grid.from_bounds((-5, -25, -5), (75, 25, 120), 1.0)
        x, _, z = axes_of(grid)
        b = math.radians(beta_deg)  # heel wall leans inward going up: x_min(z) = z * tan(beta)
        half = (z * math.tan(b) - x) * math.cos(b)
        phi = np.maximum(box_phi(grid, (0, -20, 0), (70, 20, 100)), half).astype(np.float32)
        return closure(phi, grid).report

    assert heel(30.0)["heel_loss_volume_mm3"] == 0
    steep = heel(60.0)
    assert steep["heel_loss_volume_mm3"] > 0 and steep["heel_loss_depth_mm"] > 1.0


def test_shell_protrusion_and_b_req():
    grid, phi_c = slab(41, 43.0)
    env = box_phi(grid, (-3, -23.5, -3), (63, 23.5, 33))  # slab grown by 3 mm: ridge must stick out
    rep = closure(phi_c, grid, t=2.0, phi_e=env).report
    ridge = rep["ceiling_raise_max_mm"]
    assert rep["shell_protrusion_max_mm"] == pytest.approx(ridge + 2.0 - 3.0, abs=2 * grid.dz)
    assert rep["b_req_max_mm"] == pytest.approx(2 * rep["shell_protrusion_max_mm"] * math.tan(math.radians(43)), rel=0.02)
    assert rep["shell_out_of_envelope_max_mm"] > 0


def test_region_limits_the_report_not_the_fields():
    grid, phi_c = slab(41, 43.0)
    full = closure(phi_c, grid)
    z = grid.origin[2] + np.arange(grid.shape[2]) * grid.dz
    below = np.broadcast_to((z < 40.0)[None, None, :], phi_c.shape)  # ridge starts at 30; cut it at 40
    cut = closure(phi_c, grid, region=below)
    assert np.array_equal(cut.phi_c_star, full.phi_c_star)
    assert 0 < cut.report["added_volume_in_region_mm3"] < full.report["added_volume_in_region_mm3"]
    assert cut.report["ceiling_raise_max_mm"] <= 10.0 + grid.dz
    none = closure(phi_c, grid, region=np.zeros(phi_c.shape, bool))
    assert none.report["added_volume_in_region_mm3"] == 0 and none.report["ceiling_raise_max_mm"] == 0


def test_flat_ceiling_ridge_is_not_heel_loss():
    grid, phi_c = slab(41, 43.0)  # ridge grows over the whole slab, including the heel-zone x range
    rep = closure(phi_c, grid).report
    assert rep["added_volume_in_region_mm3"] > 0
    assert rep["heel_loss_volume_mm3"] == 0 and rep["heel_loss_depth_mm"] == 0


def test_clip_flags_when_headroom_is_too_small():
    grid, phi_c = slab(41, 43.0, headroom=12.0)  # ridge is ~22 mm high
    rep = closure(phi_c, grid).report
    assert rep["ridge_hit_grid_top"] and rep["shell_clipped"]
    g2, p2 = slab(41, 43.0)
    ok = closure(p2, g2).report
    assert not ok["ridge_hit_grid_top"] and not ok["shell_clipped"]


def test_no_envelope_keys_without_phi_e():
    grid, phi_c = slab(41, 43.0)
    rep = closure(phi_c, grid, region=np.ones(phi_c.shape, bool)).report
    assert not any(k.startswith(("shell_protrusion", "b_req")) for k in rep)
