import math
import os

import numpy as np
import pytest
import trimesh

from shoe_designer import sdf
from shoe_designer.sdf import Grid, SDFError


def roundtrip(mesh, h, alpha=43.0, band=None):
    grid = Grid.for_mesh(mesh, h, alpha)
    phi = sdf.sdf_from_mesh(mesh, grid, band=band)
    out, report = sdf.sdf_to_mesh(phi, grid)
    return grid, phi, out, report


def sample_error(a, b, n=4000, seed=0):
    """Max distance from points on surface a to surface b (one-sided Hausdorff estimate)."""
    pts = trimesh.sample.sample_surface(a, n, seed=seed)[0]
    return float(np.abs(sdf.signed_distance(b, pts)).max())


def test_grid_dz_matches_overhang_angle():
    g = Grid.from_bounds([0, 0, 0], [10, 10, 10], 1.0, 43.0)
    assert g.dz == pytest.approx(1 / math.tan(math.radians(43)))
    assert g.dz == pytest.approx(1.0724, abs=1e-3)


@pytest.mark.parametrize("alpha", [43.0, 60.0])
def test_sphere_roundtrip(alpha):
    h = 1.0
    sphere = trimesh.creation.icosphere(subdivisions=4, radius=20.0)
    grid, phi, out, report = roundtrip(sphere, h, alpha)
    assert out.is_watertight and out.volume > 0
    assert report.dropped_volume_mm3 == 0
    half = 0.5 * max(grid.spacing)
    # SDF is exact at nodes; trilinear sampling away from the medial axis stays close
    probe = np.array([[8, 0, 0], [10, 0, 0], [0, 0, 19.0], [0, 0, 22.0]])
    assert sdf.sample(phi, grid, probe) == pytest.approx(sdf.signed_distance(sphere, probe), abs=0.3)
    assert sample_error(sphere, out) <= half
    assert sample_error(out, sphere) <= half
    assert out.volume == pytest.approx(sphere.volume, rel=0.02)


@pytest.mark.parametrize("alpha", [43.0, 50.0])
def test_cube_roundtrip_anisotropic(alpha):
    cube = trimesh.creation.box(extents=[30.3, 21.7, 25.9])
    cube.apply_translation([5.2, -3.1, 7.7])
    grid, phi, out, report = roundtrip(cube, 1.0, alpha)
    assert grid.dz != grid.h
    assert out.is_watertight and out.volume > 0
    half = 0.5 * max(grid.spacing)
    diag = 0.5 * math.sqrt(sum(s * s for s in grid.spacing))
    # sharp edges/corners are rounded by marching cubes: allow half a voxel diagonal there,
    # half a voxel everywhere else (both directions)
    lo, hi = cube.bounds
    for a, b in ((cube, out), (out, cube)):
        pts = trimesh.sample.sample_surface(a, 4000, seed=0)[0]
        err = np.abs(sdf.signed_distance(b, pts))
        edge = np.sort(np.minimum(np.abs(pts - lo), np.abs(hi - pts)), axis=1)[:, 1]
        assert err.max() <= diag
        assert err[edge > 1.5 * max(grid.spacing)].max() <= half
    assert out.volume == pytest.approx(cube.volume, rel=0.02)


def test_inside_is_negative_and_sampling_is_trilinear():
    cube = trimesh.creation.box(extents=[10, 10, 10])
    grid = Grid.for_mesh(cube, 1.0)
    phi = sdf.sdf_from_mesh(cube, grid)
    assert sdf.sample(phi, grid, np.array([[0.37, 0.21, 3.7]]))[0] == pytest.approx(-1.3, abs=0.05)
    assert sdf.sample(phi, grid, np.array([[0, 0, 8.0]]))[0] == pytest.approx(3.0, abs=0.05)
    # grid nodes are reproduced exactly
    ijk = np.array([[7, 9, 6]])
    assert sdf.sample(phi, grid, grid.coords(ijk))[0] == pytest.approx(phi[7, 9, 6], abs=1e-5)


def test_band_matches_exact_inside_band():
    sphere = trimesh.creation.icosphere(subdivisions=3, radius=15.0)
    grid = Grid.for_mesh(sphere, 1.0, margin_voxels=8)
    exact = sdf.sdf_from_mesh(sphere, grid)
    banded = sdf.sdf_from_mesh(sphere, grid, band=4.0, coarse=4)
    near = np.abs(exact) < 4.0
    assert np.allclose(banded[near], exact[near], atol=1e-4)
    assert np.array_equal(np.sign(banded), np.sign(exact))
    assert np.abs(banded).max() <= exact.max() + 1e-6
    out, _ = sdf.sdf_to_mesh(banded, grid)
    assert out.is_watertight


def test_export_drops_small_fragment_and_reports():
    big = trimesh.creation.box(extents=[20, 20, 20])
    speck = trimesh.creation.box(extents=[3, 3, 3])
    speck.apply_translation([40, 0, 0])
    both = trimesh.util.concatenate([big, speck])
    grid = Grid.for_mesh(both, 1.0)
    phi = sdf.sdf_from_mesh(both, grid)
    out, report = sdf.sdf_to_mesh(phi, grid)
    assert report.n_components == 2
    assert report.dropped_volume_mm3 == pytest.approx(27, rel=0.35)
    assert out.volume == pytest.approx(8000, rel=0.05)


def test_export_errors_on_large_fragment():
    a = trimesh.creation.box(extents=[20, 20, 20])
    b = trimesh.creation.box(extents=[12, 12, 12])
    b.apply_translation([40, 0, 0])
    both = trimesh.util.concatenate([a, b])
    grid = Grid.for_mesh(both, 1.0)
    phi = sdf.sdf_from_mesh(both, grid)
    with pytest.raises(SDFError, match="fragments"):
        sdf.sdf_to_mesh(phi, grid)


@pytest.mark.parametrize("cavity", [8.0, 20.0])
def test_internal_cavity_is_kept(cavity):
    outer = trimesh.creation.box(extents=[30, 30, 30])
    inner = trimesh.creation.box(extents=[cavity] * 3)
    inner.invert()
    both = trimesh.util.concatenate([outer, inner])
    grid = Grid.for_mesh(both, 1.0)
    out, report = sdf.sdf_to_mesh(sdf.sdf_from_mesh(both, grid), grid)
    assert report.n_components == 2 and report.dropped_volume_mm3 == 0
    assert out.volume == pytest.approx(30**3 - cavity**3, rel=0.02)


def test_alpha_out_of_range_errors():
    for bad in (0.0, 90.0, -5.0, 120.0):
        with pytest.raises(SDFError, match="alpha_deg"):
            Grid.dz_for(1.0, bad)


def test_nan_field_errors():
    grid = Grid.from_bounds([0, 0, 0], [5, 5, 5], 1.0)
    phi = -np.ones(grid.shape, np.float32)
    phi[2, 2, 2] = np.nan
    with pytest.raises(SDFError, match="NaN"):
        sdf.sdf_to_mesh(phi, grid)


def test_band_far_cells_need_consistent_sign():
    # an open (non-watertight) box: the winding number is fuzzy near the hole, but the banded
    # field must still equal the exact one wherever |phi| < band
    box = trimesh.creation.box(extents=[20, 20, 20])
    open_box = trimesh.Trimesh(box.vertices, box.faces[2:], process=False)
    grid = Grid.for_mesh(open_box, 1.0, margin_voxels=8)
    exact = sdf.sdf_from_mesh(open_box, grid)
    banded = sdf.sdf_from_mesh(open_box, grid, band=3.0, coarse=4)
    near = np.abs(exact) < 3.0
    assert np.allclose(banded[near], exact[near], atol=1e-4)


def test_empty_field_errors():
    grid = Grid.from_bounds([0, 0, 0], [5, 5, 5], 1.0)
    with pytest.raises(SDFError):
        sdf.sdf_to_mesh(np.ones(grid.shape, np.float32), grid)


# --- real data (D7: solid 43 and the sneaker last only) -------------------------------------

needs_assets = pytest.mark.skipif(not os.environ.get("SHOE_ASSETS_ROOT"), reason="SHOE_ASSETS_ROOT not set")


@needs_assets
@pytest.mark.parametrize("asset_id", ["sneaker_last", "dlp_solid_43_right"])
def test_real_asset_roundtrip_1mm(asset_id):
    from shoe_designer.assets import DEFAULT_MANIFEST, load_asset, load_catalog

    mesh = load_asset(asset_id, load_catalog(DEFAULT_MANIFEST)).mesh
    grid = Grid.for_mesh(mesh, 1.0)
    phi = sdf.sdf_from_mesh(mesh, grid, band=6.0)
    out, report = sdf.sdf_to_mesh(phi, grid)
    assert out.is_watertight
    assert report.dropped_volume_mm3 <= sdf.MAX_DROPPED_MM3
    half = 0.5 * max(grid.spacing)
    pts = trimesh.sample.sample_surface(out, 3000, seed=0)[0]
    err = np.abs(sdf.signed_distance(mesh, pts))
    assert np.percentile(err, 99) <= half  # the max sits on sharp features / thin tips
    # and the other direction: detail in the input must not vanish in the output
    pts_in = trimesh.sample.sample_surface(mesh, 3000, seed=1)[0]
    err_in = np.abs(sdf.signed_distance(out, pts_in))
    assert np.percentile(err_in, 99) <= half
    assert out.volume == pytest.approx(mesh.volume, rel=0.01)
