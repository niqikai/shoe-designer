import json

import numpy as np
import pytest

from shoe_poc.lattice.gyroid import (
    GyroidParameters, SamplingGrid, gyroid_values, gyroid_wall_field, sample_gyroid, save_field_npz,
)


def box_domain(grid, low=(0, 0, 0), high=(12, 12, 12)):
    axes = grid.axes
    distances = [np.maximum(low[i] - axes[i], axes[i] - high[i]) for i in range(3)]
    return np.maximum(np.maximum(distances[0][:, None, None], distances[1][None, :, None]),
                      distances[2][None, None, :]).astype(np.float32)


def test_analytic_gyroid_formula_and_period_are_physical_mm():
    points = np.array([[1.7, 3.1, 4.3], [2.3, 5.1, 7.2]])
    p = points * (2 * np.pi / 12)
    expected = np.sin(p[:, 0]) * np.cos(p[:, 1]) + np.sin(p[:, 1]) * np.cos(p[:, 2]) + np.sin(p[:, 2]) * np.cos(p[:, 0])
    assert np.allclose(gyroid_values(points, 12), expected)
    assert np.allclose(gyroid_values(points + [12, 0, 0], 12), expected)
    assert np.allclose(gyroid_wall_field(points, 12, .3), np.abs(expected) - .3)
    assert not np.allclose(gyroid_values(points, 12), gyroid_values(points, 14))
    assert not np.allclose(gyroid_values(points, 12), gyroid_values(points, 12, (1, 0, 0)))


def test_nonzero_origin_xyz_clipping_and_saved_metadata(tmp_path):
    low, high = (21, -7, 4), (33, 5, 16)
    grid = SamplingGrid.from_bounds([low, high], .4)
    domain = box_domain(grid, low, high)
    parameters = GyroidParameters(12, .6, .4, (2, 3, 4))
    field = sample_gyroid(parameters, grid, domain_signed_field=domain)
    i, j, k = (6, 8, 10)
    point = [axis[index] for axis, index in zip(grid.axes, (i, j, k))]
    expected = max(float(gyroid_wall_field(point, 12, .6, (2, 3, 4))), domain[i, j, k] / .4)
    assert field.values[i, j, k] == pytest.approx(expected)
    assert np.all(field.values[domain > 0] > 0)
    assert field.metadata["threshold_units"] == "dimensionless"
    output = save_field_npz(field, tmp_path / "gyroid_field.npz")
    with np.load(output, allow_pickle=False) as data:
        assert np.array_equal(data["values"], field.values)
        assert np.allclose(data["origin_mm"], grid.origin_mm)
        metadata = json.loads(str(data["metadata_json"]))
        assert metadata["shape_xyz"] == list(field.shape)
        assert metadata["phase_origin_mm"] == [2, 3, 4]


def test_period_threshold_and_phase_change_actual_sampled_field():
    grid = SamplingGrid.from_bounds([[0, 0, 0], [12, 12, 12]], .4)
    domain = box_domain(grid)
    fields = [sample_gyroid(params, grid, domain_signed_field=domain).values for params in (
        GyroidParameters(12, .3, .4), GyroidParameters(14, .3, .4),
        GyroidParameters(12, .6, .4), GyroidParameters(12, .3, .4, (1, 2, 3)))]
    assert all(not np.allclose(fields[0], other) for other in fields[1:])


@pytest.mark.parametrize("period,threshold,spacing", [(0, .3, .4), (12, 0, .4), (12, 1.5, .4),
    (12, .3, 0), (float("nan"), .3, .4), (12, float("inf"), .4)])
def test_invalid_parameters_rejected(period, threshold, spacing):
    with pytest.raises(ValueError):
        GyroidParameters(period, threshold, spacing)


def test_alias_screen_rejects_m1_placeholder():
    grid = SamplingGrid.from_bounds([[0, 0, 0], [12, 12, 12]], 1)
    with pytest.raises(ValueError, match="Underresolved"):
        sample_gyroid(GyroidParameters(12, .3, 1), grid, domain_signed_field=box_domain(grid))


def test_budget_rejected_before_axis_or_volume_allocation(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("A huge grid allocation was attempted before resource validation")
    monkeypatch.setattr(np, "arange", forbidden)
    monkeypatch.setattr(np, "empty", forbidden)
    with pytest.raises(ValueError, match="voxel cap"):
        SamplingGrid.from_bounds([[0, 0, 0], [10000, 10000, 10000]], .1)
    with pytest.raises(ValueError, match="memory"):
        SamplingGrid.from_bounds([[0, 0, 0], [10, 10, 10]], .4, max_memory_bytes=100)


def test_invalid_open_domain_and_empty_zero_contour_rejected():
    grid = SamplingGrid.from_bounds([[0, 0, 0], [12, 12, 12]], .4)
    domain = box_domain(grid)
    domain[0] = -1
    with pytest.raises(ValueError, match="positive padded"):
        sample_gyroid(GyroidParameters(12, .6, .4), grid, domain_signed_field=domain)
    with pytest.raises(ValueError, match="no valid zero contour"):
        sample_gyroid(GyroidParameters(12, .6, .4), grid, domain_signed_field=np.ones(grid.shape, np.float32))
