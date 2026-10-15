import json

import pytest

from shoe_poc.validation.toe import _profile_descriptors, validate_toe_profile


def _source_box():
    from build123d import Plane, Solid
    return Solid.make_box(20, 10, 20, Plane(origin=(-10, -5, -10))).bounding_box()


def test_actual_flat_shelf_vertical_nose_and_round_curve_are_distinguished():
    from build123d import Plane, Solid
    box = Solid.make_box(20, 10, 20, Plane(origin=(-10, -5, -10)))
    rounded = Solid.make_cylinder(10, 10, Plane(origin=(0, -5, 0), z_dir=(0, 1, 0)))
    result = validate_toe_profile(rounded, _source_box(), toe_band_top_z_mm=0,
                                  baseline=box, window_mm=15, sample_step_mm=.2)
    assert result["geometry_status"] == "PASS"
    for change in result["baseline_comparison"]["lane_changes"]:
        assert change["maximum_terminal_floor_low_slope_run_mm"]["change_mm"] < -10
        assert change["maximum_front_near_vertical_run_mm"]["change_mm"] < -8
    assert result["appearance_acceptance"] == "PENDING"
    json.dumps(result, allow_nan=False)


def test_no_intersections_remain_explicitly_unverified(monkeypatch):
    from build123d import Solid
    monkeypatch.setattr("shoe_poc.validation.toe._ray_hits", lambda *args: [])
    result = validate_toe_profile(Solid.make_box(20, 10, 20), _source_box(), toe_band_top_z_mm=10)
    assert result["geometry_status"] == "NOT_VERIFIED"
    assert result["candidate"]["measurement_error_count"] == 3
    assert all(lane["geometry_status"] == "NOT_VERIFIED" for lane in result["candidate"]["lanes"])


def test_nonfinite_measurement_cannot_be_a_finite_or_successful_descriptor(monkeypatch):
    from build123d import Solid
    monkeypatch.setattr("shoe_poc.validation.toe._ray_hits", lambda *args: [float("nan")])
    result = validate_toe_profile(Solid.make_box(20, 10, 20), _source_box(), toe_band_top_z_mm=10)
    assert result["geometry_status"] == "NOT_VERIFIED"
    assert result["candidate"]["measurement_error_count"] > 3
    json.dumps(result, allow_nan=False)


def test_missing_one_planned_floor_ray_blocks_completion(monkeypatch):
    from build123d import Plane, Solid
    from shoe_poc.validation.surface import _ray_hits
    def drop_floor(probe, point, direction, length):
        if direction == (0, 0, 1) and -1 < point[0] < 1:
            return []
        return _ray_hits(probe, point, direction, length)
    monkeypatch.setattr("shoe_poc.validation.toe._ray_hits", drop_floor)
    shape = Solid.make_box(20, 10, 20, Plane(origin=(-10, -5, -10)))
    result = validate_toe_profile(shape, _source_box(), toe_band_top_z_mm=0, window_mm=15)
    assert result["geometry_status"] == "NOT_VERIFIED"
    assert any(error.get("probe") == "floor" for error in result["candidate"]["measurement_errors"])


def test_missing_top_of_front_band_cannot_silently_shorten_profile(monkeypatch):
    from build123d import Plane, Solid
    from shoe_poc.validation.surface import _ray_hits
    def drop_top(probe, point, direction, length):
        if direction == (1, 0, 0) and point[2] > -1:
            return []
        return _ray_hits(probe, point, direction, length)
    monkeypatch.setattr("shoe_poc.validation.toe._ray_hits", drop_top)
    shape = Solid.make_box(20, 10, 20, Plane(origin=(-10, -5, -10)))
    result = validate_toe_profile(shape, _source_box(), toe_band_top_z_mm=0, window_mm=15)
    assert result["geometry_status"] == "NOT_VERIFIED"
    assert any("Missing front material" in error["reason"] for error in result["candidate"]["measurement_errors"])


def test_invalid_input_cannot_measure_or_approve_appearance():
    result = validate_toe_profile(None, _source_box(), toe_band_top_z_mm=0)
    assert result["geometry_status"] == "FAIL"
    assert result["appearance_acceptance"] == "PENDING"


def test_nonfinite_and_duplicate_profile_points_are_rejected():
    with pytest.raises(ValueError, match="Nonfinite"):
        _profile_descriptors([[0, 0], [1, float("inf")], [2, 2]], independent_axis=0)
    with pytest.raises(ValueError, match="strictly increasing"):
        _profile_descriptors([[0, 0], [1, 0], [1, 2]], independent_axis=0)


@pytest.mark.parametrize("parameter,value", [("sample_step_mm", 0), ("window_mm", -1),
                                             ("sample_step_mm", float("nan"))])
def test_invalid_probe_parameters_are_rejected(parameter, value):
    with pytest.raises(ValueError):
        validate_toe_profile(None, _source_box(), toe_band_top_z_mm=0, **{parameter: value})
