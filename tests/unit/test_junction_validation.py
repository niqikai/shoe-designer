import pytest

from shoe_poc.validation.junction import _oriented_normal_angle, validate_junction


def test_opposite_oriented_normals_are_a_turn_not_continuity():
    assert _oriented_normal_angle((1, 0, 0), (-1, 0, 0)) == pytest.approx(180.)
    assert _oriented_normal_angle((1, 0, 0), (1, 0, 0)) == pytest.approx(0.)


def test_native_face_orientation_changes_the_measured_normal():
    from build123d import Face, Solid
    from OCP.TopoDS import TopoDS
    face = Solid.make_box(10, 10, 10).faces()[0]
    reverse = Face(TopoDS.Face_s(face.wrapped.Reversed()))
    point = face.center()
    assert _oriented_normal_angle(tuple(face.normal_at(point)), tuple(reverse.normal_at(point))) == pytest.approx(180.)


def test_real_cad_exterior_box_corners_fail_in_all_regions():
    from build123d import Solid
    body = Solid.make_box(10, 10, 10)
    result = validate_junction(body, None, None, body.bounding_box(),
                               minimum_z_mm=-1, maximum_z_mm=11)
    assert result["geometry_status"] == "FAIL"
    assert result["maximum_normal_angle_degrees"] == pytest.approx(90.)
    assert all(region["sample_count"] and region["geometry_status"] == "FAIL"
               for region in result["regions"].values())
    assert result["measurement_error_count"] == 0


def test_actual_cutter_boundary_exclusion_does_not_create_false_pass():
    from build123d import Solid
    body = Solid.make_box(10, 10, 10)
    result = validate_junction(body, body, None, body.bounding_box(),
                               minimum_z_mm=-1, maximum_z_mm=11)
    assert result["geometry_status"] == "NOT_VERIFIED"
    assert result["exterior_sample_count"] == 0
    assert result["excluded_boundary_sample_count"] > 0
    assert all(r["cavity_distance_mm"] == 0 for r in result["excluded_boundary_records"])


def test_normal_measurement_failure_is_retained_and_blocks_pass(monkeypatch):
    from build123d import Face, Solid
    body = Solid.make_box(10, 10, 10)
    def fail(*args, **kwargs):
        raise ValueError("test undefined normal")
    monkeypatch.setattr(Face, "normal_at", fail)
    result = validate_junction(body, None, None, body.bounding_box(),
                               minimum_z_mm=-1, maximum_z_mm=11)
    assert result["geometry_status"] == "NOT_VERIFIED"
    assert result["measurement_error_count"] > 0
    assert all("undefined normal" in error["reason"] for error in result["measurement_errors"])


def test_round_probe_reads_geometry_instead_of_declaring_target_radius():
    from types import SimpleNamespace
    from build123d import Solid, Plane
    from shoe_poc.validation.junction import measure_bottom_round
    sphere = Solid.make_sphere(1, Plane(origin=(0, 0, 1)))
    source_box = Solid.make_box(2, 2, 1, Plane(origin=(-1, -1, 1))).bounding_box()
    spec = SimpleNamespace(sole=SimpleNamespace(height_mm=1, profile_parameters=SimpleNamespace(
        edge_radius_mm=2, heel_rise_mm=0, toe_rise_mm=0)))
    result = measure_bottom_round(sphere, source_box, spec)
    assert result['geometry_status'] == 'FAIL'
    assert result['sample_count'] > 0
    assert result['minimum_measured_radius_mm'] == pytest.approx(1)
    assert result['maximum_measured_radius_mm'] == pytest.approx(1)
