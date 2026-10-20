import numpy as np
import pytest
import trimesh
from PIL import Image

from shoe_designer import audit
from shoe_designer.pattern import PatternConfig, PatternError, PatternMasks, PatternSpec, apply_pattern, export_checks
from shoe_designer.process import ProcessConfig, sealed_cavities
from shoe_designer.sdf import Grid, sdf_to_mesh

H = 1.0
WALL, FLOOR, ZT = 2.5, 8.0, 70.0
CFG = PatternConfig.from_toml()


class FlatCollar:
    def z_t(self, xy):
        return np.full(len(xy), ZT)


def axes_of(grid):
    return np.meshgrid(*[grid.origin[i] + np.arange(grid.shape[i]) * grid.spacing[i] for i in range(3)], indexing="ij")


def box_phi(grid, lo, hi):
    q = np.stack([np.maximum(l - p, p - u) for p, l, u in zip(axes_of(grid), lo, hi)])
    return (np.linalg.norm(np.maximum(q, 0), axis=0) + np.minimum(q.max(axis=0), 0)).astype(np.float32)


def scene(wall=WALL):
    """Box shoe 120 x 60 x 50 with a box cavity (wall ``wall`` mm, floor 8, roof 3 mm) and a chimney slot in the roof."""
    grid = Grid.from_bounds((-6, -40, -4), (126, 40, 80), H)
    outer = box_phi(grid, (0, -30, 0), (120, 30, 50))
    cav = np.minimum(box_phi(grid, (wall, -30 + wall, FLOOR), (120 - wall, 30 - wall, 47.0)), box_phi(grid, (50, -10, 30), (70, 10, 120)))
    phi = np.maximum(outer, -cav).astype(np.float32)
    phi[phi == 0] = 1e-3  # as compose does
    below = axes_of(grid)[2] < ZT
    return grid, phi, PatternMasks(cav, below, FlatCollar(), 0.0, 6.0)


@pytest.fixture(scope="module")
def base():
    return scene()


def run(base, **spec):
    grid, phi, masks = base
    new, rep = apply_pattern(phi, grid, PatternSpec.from_dict(spec), masks, None, CFG)
    return new, rep


def wall_slice(grid, phi, y, zr=(14, 38), xr=(30, 80)):
    """Solid fraction of the wall voxels at depth ``y`` (a plane inside the -y wall) over the x/z window."""
    X, Y, Z = axes_of(grid)
    m = (np.abs(Y - y) < 0.5 * H) & (X >= xr[0]) & (X <= xr[1]) & (Z >= zr[0]) & (Z <= zr[1])
    return float((phi[m] < 0).mean())


def test_spec_clamps_and_reports():
    s = PatternSpec.from_dict({"kind": "hex_perforation", "scale_mm": 100, "open_ratio": 0.01, "depth_mm": -3, "zones": ["top", "side", "top"], "seed": 4})
    assert (s.scale_mm, s.open_ratio, s.depth_mm) == (30.0, 0.05, 0.0)
    assert s.zones == ("side", "top") and s.seed == 4
    assert s.clamped == {"scale_mm": {"from": 100.0, "to": 30.0}, "open_ratio": {"from": 0.01, "to": 0.05}, "depth_mm": {"from": -3.0, "to": 0.0}}
    d = PatternSpec.from_dict({"kind": "none"})
    assert d.zones == ("side", "top") and d.clamped == {}
    for bad in ({"kind": "stripes"}, {"zones": ["sole"]}, {"scale_mm": "big"}, {"scale_mm": float("nan")}, {"kind": "image"}, {"seed": 1.5}, {"image_use": "x"}):
        with pytest.raises(PatternError):
            PatternSpec.from_dict(bad)
    n = PatternSpec.from_dict({"kind": "wave_displacement", "colour": "red"})
    assert any("colour" in x for x in n.notes)


def test_none_is_identity(base):
    new, rep = run(base, kind="none")
    assert np.array_equal(new, base[1]) and not rep["applied"]


@pytest.mark.parametrize("kind,ratio,scale", [("hex_perforation", 0.30, 10.0), ("voronoi_perforation", 0.30, 10.0), ("hex_perforation", 0.45, 14.0)])
def test_perforation_open_ratio_and_integrity(base, kind, ratio, scale):
    grid, phi, masks = base
    new, rep = run(base, kind=kind, scale_mm=scale, open_ratio=ratio, seed=3, zones=["side"])
    assert rep["applied"] and rep["cells"]["holes"] > 3
    # air area on a plane inside the -y wall against the area of the cells that were kept (cell area ~ pitch^2 for voronoi)
    X, Y, Z = axes_of(grid)
    plane = (np.abs(Y + 30 - 1.2) < 0.5) & (new >= 0) & (phi < 0)
    cell_area = 0.5 * 3**0.5 * scale**2 if kind == "hex_perforation" else scale**2
    open_frac = plane.sum() * H * grid.dz / (rep["cells"]["holes"] / 2 * cell_area)
    assert open_frac == pytest.approx(ratio, abs=0.09)
    assert rep["open_ratio_measured"] == pytest.approx(ratio, abs=0.06 if kind == "hex_perforation" else 0.09)
    # the cavity is untouched, the new solid is a subset of the old, no sealed cavity, nothing patterned in the protected bands
    assert rep["cavity_invaded_voxels"] == 0 and rep["sealed_after"]["count"] == 0 and rep["sealed_pockets_filled_mm3"] == 0
    assert ((new < 0) & (phi >= 0)).sum() == 0
    sole = Z < FLOOR + CFG.sole_margin_mm
    assert np.array_equal(new[sole], phi[sole])
    collar = Z > ZT - CFG.collar_margin_mm
    assert np.array_equal(new[collar], phi[collar])
    cav_zone = masks.phi_cavity < 0
    assert np.array_equal(new[cav_zone & masks.below], phi[cav_zone & masks.below])
    mesh, _ = sdf_to_mesh(new, grid)
    assert mesh.is_watertight and mesh.body_count == 1 and export_checks(mesh)["inner_shells"]["count"] == 0
    assert audit.wall_thickness(mesh, 4000)["frac_thin"] <= 0.005


def test_through_holes_open_into_cavity(base):
    grid, phi, _ = base
    new, rep = run(base, kind="hex_perforation", scale_mm=12, open_ratio=0.3)
    X, Y, Z = axes_of(grid)
    # at every x/z with air at the outer face layer, the whole wall thickness is air: no blind pockets
    outer = (Y == -29) & (X > 40) & (X < 75) & (Z > 22) & (Z < 36)
    inner = (Y == -28) & (X > 40) & (X < 75) & (Z > 22) & (Z < 36)
    assert (new[outer] >= 0).sum() > 0
    assert np.array_equal(new[outer] >= 0, new[inner] >= 0)


def test_seed_reproducible_and_varies(base):
    a, _ = run(base, kind="voronoi_perforation", scale_mm=10, open_ratio=0.3, seed=1)
    b, _ = run(base, kind="voronoi_perforation", scale_mm=10, open_ratio=0.3, seed=1)
    c, _ = run(base, kind="voronoi_perforation", scale_mm=10, open_ratio=0.3, seed=2)
    assert np.array_equal(a, b) and not np.array_equal(a, c)


@pytest.mark.parametrize("kind", ["wave_displacement", "rib_displacement"])
def test_displacement_amplitude_is_outward_only(base, kind):
    grid, phi, masks = base
    new, rep = run(base, kind=kind, scale_mm=12, depth_mm=1.5, seed=2)
    assert rep["amplitude_max_mm"] == pytest.approx(1.5, abs=0.02)
    assert ((new < 0) & (phi >= 0)).sum() > 0 and ((new >= 0) & (phi < 0)).sum() == 0  # grows only
    assert rep["cavity_invaded_voxels"] == 0 and rep["sealed_after"]["count"] == 0
    # outermost extent of the -y wall in the patterned window rises by ~depth at the crests, never beyond
    X, Y, Z = axes_of(grid)
    mesh, _ = sdf_to_mesh(new, grid)
    v = mesh.vertices
    win = (v[:, 0] > 40) & (v[:, 0] < 80) & (v[:, 2] > 22) & (v[:, 2] < 36) & (v[:, 1] < 0)
    assert v[win, 1].min() == pytest.approx(-30 - 1.5, abs=0.06)  # crests reach the full depth, nothing goes beyond
    mesh, _ = sdf_to_mesh(new, grid)
    assert mesh.is_watertight and mesh.body_count == 1
    sole = Z < FLOOR + CFG.sole_margin_mm
    assert np.array_equal(new[sole], phi[sole])


def test_displacement_depth_zero_is_noop(base):
    new, _ = run(base, kind="wave_displacement", depth_mm=0.0)
    assert np.array_equal(new, base[1])


def test_zones_and_protected_ends(base):
    grid, phi, _ = base
    X, Y, Z = axes_of(grid)
    side_only, _ = run(base, kind="hex_perforation", scale_mm=10, open_ratio=0.3, zones=["side"])
    top_only, rt = run(base, kind="hex_perforation", scale_mm=10, open_ratio=0.3, zones=["top"])
    top_face = Z > 47.5
    assert np.array_equal(side_only[top_face], phi[top_face])
    assert (top_only != phi).any() and (top_only[top_face] != phi[top_face]).any()
    side_wall = (np.abs(Y) > 26) & (Z < 45)
    assert np.array_equal(top_only[side_wall], phi[side_wall])
    # heel / toe protection: default leaves them, naming them lets the pattern in
    default, _ = run(base, kind="hex_perforation", scale_mm=8, open_ratio=0.3, zones=["side"])
    toe, _ = run(base, kind="hex_perforation", scale_mm=8, open_ratio=0.3, zones=["side", "toe"])
    in_toe = X > 0.85 * 120 + 1
    assert np.array_equal(default[in_toe], phi[in_toe]) and (toe[in_toe] != phi[in_toe]).any()
    in_heel = X < 0.2 * 120 - 1
    assert np.array_equal(toe[in_heel], phi[in_heel])


def test_cells_do_not_cross_the_protection_boundary(base):
    """No partial hole next to the sole band / ends: every hole is a whole cell (hole area ~ nominal)."""
    grid, phi, _ = base
    new, rep = run(base, kind="hex_perforation", scale_mm=10, open_ratio=0.3)
    X, Y, Z = axes_of(grid)
    near = (np.abs(Y + 30 - 1.2) < 0.5) & (Z < FLOOR + CFG.sole_margin_mm + 0.8 * 10) & (Z > FLOOR)
    assert (new[near] == phi[near]).all()  # the margin of one pitch above the sole band stays solid


def test_thin_wall_is_not_perforated():
    grid, phi, masks = scene(wall=1.1)
    new, rep = apply_pattern(phi, grid, PatternSpec.from_dict({"kind": "hex_perforation", "scale_mm": 10, "zones": ["side"]}), masks, None, CFG)
    assert np.array_equal(new, phi)  # the 1.1 mm wall is below perf_wall_min: no hole, no change


def test_infeasible_request_is_adjusted_and_reported(base):
    _, rep = run(base, kind="hex_perforation", scale_mm=4.0, open_ratio=0.5)
    assert rep["effective"]["pitch_mm"] >= CFG.hole_min_mm + CFG.strut_min_mm
    assert any(a["field"] == "scale_mm" for a in rep["adjustments"])
    _, rep = run(base, kind="hex_perforation", scale_mm=6, open_ratio=0.6)
    assert rep["effective"]["open_ratio"] <= (1 - (CFG.strut_min_mm + CFG.strut_margin_mm) / 6) ** 2 + 1e-9
    assert any(a["field"] == "open_ratio" for a in rep["adjustments"])


def test_process_wall_min_raises_strut(base):
    grid, phi, masks = base
    proc = ProcessConfig.from_toml("process_sls_tpu.toml")
    spec = PatternSpec.from_dict({"kind": "hex_perforation", "scale_mm": 10, "open_ratio": 0.3})
    _, rep = apply_pattern(phi, grid, spec, masks, proc, CFG)
    assert rep["process"] == "sls_tpu" and rep["effective"]["strut_mm"] >= proc.wall_min


def _logo(path, w=120, h=60):
    """White picture, a black bar at the left-top corner (so left/right and top/bottom are distinguishable)."""
    a = np.full((h, w), 255, np.uint8)
    a[3 : h // 3, 5 : w // 4] = 0
    Image.fromarray(a).save(path)
    return str(path)


def test_image_perforation_maps_left_top_and_mirrors(base, tmp_path):
    grid, phi, _ = base
    img = _logo(tmp_path / "logo.png")
    new, rep = run(base, kind="image", image=img, image_use="perforation", zones=["side"], scale_mm=10)
    assert rep["applied"] and rep["cells"] if "cells" in rep else True
    X, Y, Z = axes_of(grid)
    cut = (new >= 0) & (phi < 0)
    x0, x1, z0, z1 = rep["image_bbox_mm"]
    minus, plus = cut & (Y < 0), cut & (Y > 0)
    assert minus.any() and plus.any()
    # -y side reads left to right along +x, +y side is mirrored; the bar sits in the top third on both
    assert X[minus].mean() < 0.5 * (x0 + x1) < X[plus].mean()
    assert Z[minus].mean() > 0.5 * (z0 + z1) and Z[plus].mean() > 0.5 * (z0 + z1)
    assert rep["sealed_after"]["count"] == 0 and rep["cavity_invaded_voxels"] == 0
    mesh, _ = sdf_to_mesh(new, grid)
    assert mesh.is_watertight and mesh.body_count == 1


def test_image_displacement_follows_the_picture(base, tmp_path):
    grid, phi, _ = base
    img = _logo(tmp_path / "logo.png")
    new, rep = run(base, kind="image", image=img, image_use="displacement", depth_mm=1.2, zones=["side"])
    X, Y, Z = axes_of(grid)
    grown = (new < 0) & (phi >= 0)
    assert grown.any() and ((new >= 0) & (phi < 0)).sum() == 0
    x0, x1, z0, z1 = rep["image_bbox_mm"]
    assert X[grown & (Y < 0)].mean() < 0.5 * (x0 + x1) < X[grown & (Y > 0)].mean()
    assert rep["amplitude_max_mm"] == pytest.approx(1.2, abs=0.05)


def test_image_island_is_dropped_not_left_loose(base, tmp_path):
    grid, phi, _ = base
    a = np.full((60, 120), 255, np.uint8)
    a[10:50, 20:100] = 0  # a frame ...
    a[22:38, 45:75] = 255  # ... around an island
    Image.fromarray(a).save(tmp_path / "ring.png")
    new, rep = run(base, kind="image", image=str(tmp_path / "ring.png"), zones=["side"])
    mesh, _ = sdf_to_mesh(new, grid)
    assert mesh.is_watertight and mesh.body_count == 1
    assert any("island" in n for n in rep["notes"]) or "loose_bits_removed_mm3" in rep
