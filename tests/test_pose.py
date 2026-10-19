import numpy as np
import pytest
import trimesh

from shoe_designer import pose as pz
from shoe_designer.sdf import Grid, sdf_from_mesh

LAST_EXT = (60.0, 24.0, 14.0)
MARGIN = 3.0  # ease + wall


def box(extents, center=(0, 0, 0), pitch=0.0, yaw=0.0):
    m = trimesh.creation.box(extents=extents)
    t = np.eye(4)
    t[:3, :3] = pz.rotation(pitch, yaw)
    t[:3, 3] = center
    m.apply_transform(t)
    return m


def last_box():
    """A 'last' with its lowest point at z=0 and x from 0 (like the canonical frame)."""
    m = box(LAST_EXT)
    m.apply_translation(-m.bounds[0])
    return m


def envelope(last, pose, m=MARGIN, h=1.0, s_min=6.0):
    """Tight envelope: the last placed with ``pose`` (as fit_pose would), faces offset by ``m``."""
    prob = pz._Problem(np.zeros((2, 2, 2)), Grid((0, 0, 0), 1, 1, (2, 2, 2)), np.zeros((2, 2, 2)),
                       last, 0.0, 0.0, s_min, 0.0, 16, 0, (50.0, 0.0))
    tf = prob.transform(pose)
    env = box([e + 2 * m for e in LAST_EXT])
    env.apply_translation(last.bounds.mean(axis=0))
    env.apply_transform(tf)
    grid = Grid.for_mesh(env, h, margin_voxels=4)
    return env, grid, sdf_from_mesh(env, grid), tf


def test_rotation_toe_down():
    assert pz.rotation(10, 0) @ [1, 0, 0] == pytest.approx([np.cos(np.radians(10)), 0, -np.sin(np.radians(10))])


def test_recovers_known_pose():
    last = last_box()
    truth = pz.Pose(dx=0.0, dy=0.0, lift=2.0, pitch=2.0, yaw=1.0)
    env, grid, phi, tf = envelope(last, truth)
    mask = np.ones(grid.shape, bool)
    res = pz.fit_pose(phi, grid, mask, last, ease=1.0, wall=2.0, xy_center=env.bounds.mean(axis=0)[:2],
                      n_points=6000, n_refine_points=3000)
    assert res.rms < 0.1
    assert res.baseline_cost > 10 * res.cost
    # pose recovered within tolerance (compare the transforms through displaced corners)
    corners = last.bounds[[0, 1]]
    corners = np.array([[x, y, z] for x in corners[:, 0] for y in corners[:, 1] for z in corners[:, 2]])
    a = corners @ res.transform[:3, :3].T + res.transform[:3, 3]
    b = corners @ tf[:3, :3].T + tf[:3, 3]
    assert np.abs(a - b).max() < 0.7
    assert abs(res.pose.pitch - 2.0) < 0.5 and abs(res.pose.yaw - 1.0) < 0.5
    assert not res.flags["sole_active"]
    assert max(s.cost for s in res.starts) < 2.0 * res.cost + 20.0  # every start reaches the same basin


def test_gap_map_localises_known_shortfall():
    last = last_box()
    env, grid, phi, tf = envelope(last, pz.Pose(0, 0, 0, 0, 0))
    mask = np.ones(grid.shape, bool)
    shift = np.eye(4)
    shift[:3, :3], shift[:3, 3] = tf[:3, :3], tf[:3, 3] + [0.0, 1.5, 0.0]
    g = pz.gap_map(phi, grid, mask, last, shift, ease=1.0, wall=2.0)
    s = g.summary()
    for third in pz.THIRDS:
        assert s[f"{third}/y+"]["max"] == pytest.approx(1.5, abs=0.2)
        assert s[f"{third}/y-"]["max"] < 0.4  # trilinear SDF over-reads the ridge where two faces meet
    assert s["y+"]["max"] == pytest.approx(1.5, abs=0.2)
    assert s["top"]["max"] < 1.6 and s["sole"]["max"] < 1.6
    # sole/top faces are flat and untouched except near the +y edge
    assert s["y-"]["frac_over_0p5"] == 0
    # x-shift: a 2 mm overshoot at the toe end only
    shift2 = shift.copy()
    shift2[:3, 3] = tf[:3, 3] + tf[:3, :3] @ [2.0, 0, 0]
    s2 = pz.gap_map(phi, grid, mask, last, shift2).summary()
    assert s2["toe"]["max"] == pytest.approx(2.0, abs=0.2)
    assert s2["heel"]["max"] < 0.4
    assert s2["toe"]["frac_over_0p5"] > 0 and s2["heel"]["frac_over_0p5"] == 0


def test_sole_constraint_pushes_deficit_to_the_top():
    last = last_box()
    # the envelope wants the last's lowest point at 3 mm but the sole needs >= 6 mm
    env, grid, phi, _ = envelope(last, pz.Pose(0, 0, 0, 0, 0), s_min=3.0)
    mask = np.ones(grid.shape, bool)
    res = pz.fit_pose(phi, grid, mask, last, ease=1.0, wall=2.0, s_min=6.0, xy_center=env.bounds.mean(axis=0)[:2],
                      n_points=6000, n_refine_points=3000)
    assert res.flags["sole_active"]
    low = (np.asarray(last.vertices) @ res.transform[:3, :3].T + res.transform[:3, 3])[:, 2].min()
    assert low >= 6.0 - 1e-6
    top = res.gap.summary()["top"]
    assert top["max"] == pytest.approx(3.0, abs=0.5)
    assert res.gap.summary()["sole"]["max"] < 0.3


def test_pitch_is_traded_against_the_sole():
    # a pitched envelope with the sole constraint active: the optimum may not violate the sole
    # for any pitch, and a pose that ignores the envelope tilt beyond the bound is reported
    last = last_box()
    truth = pz.Pose(0, 0, 0, 4.0, 0.0)
    env, grid, phi, _ = envelope(last, truth)
    mask = np.ones(grid.shape, bool)
    res = pz.fit_pose(phi, grid, mask, last, pitch_max_deg=2.0, xy_center=env.bounds.mean(axis=0)[:2],
                      n_points=6000, n_refine_points=3000)
    assert res.flags["pitch_at_bound"]
    assert res.pose.pitch == pytest.approx(2.0, abs=0.1)
    low = (np.asarray(last.vertices) @ res.transform[:3, :3].T + res.transform[:3, 3])[:, 2].min()
    assert low >= 6.0 - 1e-6


def test_mask_excludes_points_above_the_mouth():
    base = last_box()
    stalk = box([6, 6, 80], center=(30, 12, 40))  # pokes far out of the envelope
    last = trimesh.util.concatenate([base, stalk])
    truth = pz.Pose(0, 0, 1.0, 0.0, 0.0)
    env, grid, phi, _ = envelope(base, truth)
    z = grid.origin[2] + np.arange(grid.shape[2]) * grid.dz
    mask = np.broadcast_to(z < 6.0 + 1.0 + LAST_EXT[2] + 0.5, grid.shape)  # the mouth sits at the block top
    res = pz.fit_pose(phi, grid, mask, last, xy_center=env.bounds.mean(axis=0)[:2], n_points=8000,
                      n_refine_points=4000)
    assert res.pose.lift == pytest.approx(1.0, abs=0.7)
    assert res.rms < 0.3
    # without the mask the stalk drags the pose off
    full = pz.fit_pose(phi, grid, np.ones(grid.shape, bool), last, xy_center=env.bounds.mean(axis=0)[:2],
                       n_points=8000, n_refine_points=4000)
    assert full.rms > res.rms


def test_shape_mismatch_rejected():
    last = last_box()
    grid = Grid((0, 0, 0), 1.0, 1.0, (4, 4, 4))
    with pytest.raises(ValueError):
        pz.fit_pose(np.zeros((3, 3, 3), np.float32), grid, np.ones((4, 4, 4)), last)
