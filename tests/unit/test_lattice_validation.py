from pathlib import Path

import numpy as np
import pytest
import trimesh

from shoe_poc.validation.lattice import (validate_gyroid_residual,
    validate_lattice_domain, validate_lattice_mesh)


def check(report, name):
    return next(value for value in report["checks"] if value["name"] == name)


def test_arbitrary_genus_and_separate_closed_components_are_retained():
    first = trimesh.creation.torus(major_radius=5, minor_radius=1)
    other = trimesh.creation.box()
    other.apply_translation([20, 0, 0])
    report = validate_lattice_mesh(trimesh.util.concatenate([first, other]))
    assert report["geometry_status"] == "PASS"
    assert report["connected_components"] == 2
    assert report["physical_connectivity_to_shoe"] == "NOT_VERIFIED"


def test_open_lattice_boundary_detected():
    mesh = trimesh.creation.box()
    mesh.update_faces(np.arange(len(mesh.faces)-1))
    report = validate_lattice_mesh(mesh)
    assert check(report, "boundary_edges")["value"] > 0
    assert report["geometry_status"] == "FAIL"


def test_reversed_component_cannot_hide_behind_positive_total_volume():
    first = trimesh.creation.box(extents=[10, 10, 10])
    second = trimesh.creation.box()
    second.invert()
    second.apply_translation([20, 0, 0])
    report = validate_lattice_mesh(trimesh.util.concatenate([first, second]))
    assert report["volume_mm3"] > 0
    assert check(report, "shell_nesting_orientation")["status"] == "FAIL"


def test_inconsistent_winding_detected():
    mesh = trimesh.creation.box()
    mesh.faces[0] = mesh.faces[0, ::-1]
    report = validate_lattice_mesh(mesh)
    assert check(report, "consistent_winding")["status"] == "FAIL"


def test_nonmanifold_edge_and_point_contact_detected():
    mesh = trimesh.creation.box()
    mesh.faces = np.vstack([mesh.faces, mesh.faces[0]])
    assert check(validate_lattice_mesh(mesh), "nonmanifold_edges")["status"] == "FAIL"
    first = trimesh.creation.box()
    second = first.copy()
    second.apply_translation([1, 1, 1])
    touching = trimesh.util.concatenate([first, second])
    touching.merge_vertices()
    report = validate_lattice_mesh(touching)
    assert check(report, "nonmanifold_vertex_links")["value"] == 1


def test_actual_stl_roundtrip_and_coordinate_escape(tmp_path: Path):
    mesh = trimesh.creation.icosphere(subdivisions=1)
    mesh.apply_translation([200.123456, -30.654321, 12.987654])
    path = tmp_path/"lattice.stl"
    mesh.export(path)
    report = validate_lattice_mesh(path, reference_mesh=mesh)
    assert report["geometry_status"] == "PASS"
    assert report["roundtrip"]["maximum_coordinate_error_mm"] > 0
    mesh.apply_translation([.01, 0, 0])
    assert check(validate_lattice_mesh(path, reference_mesh=mesh), "STL_coordinate_roundtrip")["status"] == "FAIL"


def test_finite_and_zero_area_fail():
    mesh = trimesh.creation.box()
    mesh.vertices[0] = [np.nan, 0, 0]
    assert validate_lattice_mesh(mesh)["geometry_status"] == "FAIL"
    mesh = trimesh.creation.box()
    mesh.faces = np.vstack([mesh.faces, [0, 0, 1]])
    assert check(validate_lattice_mesh(mesh), "zero_area_triangles")["status"] == "FAIL"


def test_escaped_sole_and_cavity_detected_with_explicit_budget():
    mesh = trimesh.creation.box()
    def native(points):
        return {"inside_sole": points[:, 0] <= 0,
                "inside_cavity": points[:, 1] > 0,
                "within_z_bounds": np.ones(len(points), bool)}
    report = validate_lattice_domain(mesh, classify_points=native, max_samples=12)
    assert report["geometry_status"] == "FAIL"
    assert report["outside_sole_count"] > 0
    assert report["inside_cavity_count"] > 0
    assert report["sample_count"] == 12
    assert report["global_containment_status"] == "NOT_VERIFIED"


def test_classifier_errors_are_not_silent_success():
    def bad(points):
        raise RuntimeError("native classifier failed")
    report = validate_lattice_domain(trimesh.creation.box(), classify_points=bad)
    assert report["geometry_status"] == "NOT_VERIFIED"
    assert report["classification_errors"]


def test_gyroid_residual_is_dimensionless_and_caps_separate():
    # G(x, 0, 0)=sin(x); this is an exact analytic sheet, plus one
    # deliberately non-gyroid vertex marked as a domain cap.
    period, threshold = 10., .3
    coordinate = np.arcsin(threshold)*period/(2*np.pi)
    vertices = [[coordinate, 0, 0], [coordinate, 0, period],
                [coordinate, period, 0], [0, 0, 0]]
    mesh = trimesh.Trimesh(vertices=vertices, faces=[[0, 1, 2], [0, 1, 3]], process=False)
    report = validate_gyroid_residual(mesh, period_mm=period, threshold=threshold,
             domain_field=lambda p: np.where(p[:, 0] == 0, 0., -1.),
             cap_tolerance_mm=.001, residual_tolerance=1e-10)
    assert report["geometry_status"] == "PASS"
    assert report["interior"]["sample_count"] == 3
    assert report["domain_caps"]["maximum_absolute_residual"] == pytest.approx(.3)
    assert report["threshold_is_wall_thickness_mm"] is False


def test_native_lines_detect_cavity_and_sole_escape():
    from shoe_poc.validation.lattice import validate_native_line_containment
    mesh = trimesh.creation.box(extents=[2, 2, 2])
    good = lambda y, z: {"sole_intervals_mm": [[-2, 2]], "cavity_intervals_mm": [], "within_z_bounds": True}
    report = validate_native_line_containment(mesh, scanline=good, maximum_lines=4)
    assert report["geometry_status"] == "PASS"
    assert report["occupied_lattice_lines"] == 4
    assert report["samples"][0]["lattice_intervals_mm"][0] == pytest.approx([-1, 1])
    cavity = lambda y, z: {"sole_intervals_mm": [[-2, 2]], "cavity_intervals_mm": [[-.2, .2]], "within_z_bounds": True}
    assert validate_native_line_containment(mesh, scanline=cavity, maximum_lines=4)["geometry_status"] == "FAIL"
    small = lambda y, z: {"sole_intervals_mm": [[-.5, .5]], "cavity_intervals_mm": [], "within_z_bounds": True}
    assert validate_native_line_containment(mesh, scanline=small, maximum_lines=4)["geometry_status"] == "FAIL"


def test_native_lines_keep_measurement_errors():
    from shoe_poc.validation.lattice import validate_native_line_containment
    def error(y, z):
        raise RuntimeError("OCCT failed")
    report = validate_native_line_containment(trimesh.creation.box(), scanline=error, maximum_lines=4)
    assert report["geometry_status"] == "NOT_VERIFIED"
    assert len(report["measurement_errors"]) == 4


def test_actual_native_cad_mesh_probes_detect_cavity_escape(tmp_path):
    from build123d import Box, Pos
    from shoe_poc.lattice.domain import NativeDomainProbe
    from shoe_poc.validation.lattice import validate_native_line_containment
    sole, cavity = Box(10, 10, 10), Pos(0, 0, 3)*Box(2, 2, 2)
    native = NativeDomainProbe(sole, cavity, -5, 5)
    mesh = trimesh.creation.box(extents=[1, 1, .8])
    mesh.apply_translation([0, 0, -3])
    path = tmp_path/"native.stl"
    mesh.export(path)
    lines = lambda m: validate_native_line_containment(m, scanline=native.scanline, maximum_lines=4)
    assert validate_lattice_domain(path, classify_points=native.classify_points, native_line_probe=lines)["geometry_status"] == "PASS"
    mesh.apply_translation([0, 0, 6])
    mesh.export(path)
    failed = validate_lattice_domain(path, classify_points=native.classify_points, native_line_probe=lines)
    assert failed["geometry_status"] == "FAIL"
    assert failed["inside_cavity_count"] > 0
    assert failed["native_line_probes"]["geometry_status"] == "FAIL"
    mesh.apply_translation([6, 0, -6])
    mesh.export(path)
    assert validate_lattice_domain(path, classify_points=native.classify_points)["outside_sole_count"] > 0


def test_actual_extracted_gyroid_residual_and_volume_refine(tmp_path):
    from shoe_poc.lattice.gyroid import GyroidParameters, SamplingGrid, sample_gyroid
    from shoe_poc.lattice.meshing import extract_lattice_mesh
    low, high = np.array([21., -7., 4.]), np.array([33., 5., 16.])
    domain_field = lambda points: np.maximum(low-points, points-high).max(axis=1)
    measurements = []
    for spacing in (.6, .4, .3):
        grid = SamplingGrid.from_bounds([low, high], spacing)
        axes = grid.axes
        distances = [np.maximum(low[i]-axes[i], axes[i]-high[i]) for i in range(3)]
        domain = np.maximum(np.maximum(distances[0][:, None, None], distances[1][None, :, None]),
                            distances[2][None, None, :]).astype(np.float32)
        field = sample_gyroid(GyroidParameters(12, .6, spacing), grid, domain_signed_field=domain)
        result = extract_lattice_mesh(field)
        path = tmp_path/f"gyroid-{spacing}.stl"
        result.mesh.export(path)
        mesh_report = validate_lattice_mesh(path, reference_mesh=result.mesh)
        assert mesh_report["geometry_status"] == "PASS"
        residual = validate_gyroid_residual(path, period_mm=12, threshold=.6,
                     domain_field=domain_field, cap_tolerance_mm=spacing, residual_tolerance=.02)
        assert residual["geometry_status"] == "PASS"
        assert residual["interior"]["sample_count"] > 1000
        assert residual["domain_caps"]["sample_count"] > 0
        measurements.append((mesh_report["volume_mm3"], residual["interior"]["maximum_absolute_residual"]))
    # Measured geometry, rather than metadata: analytic interpolation residual
    # decreases and the change in actual extracted volume shrinks on refinement.
    assert measurements[2][1] < measurements[1][1] < measurements[0][1]
    assert abs(measurements[2][0]-measurements[1][0]) < abs(measurements[1][0]-measurements[0][0])


def _complete_native_points(points):
    return {"inside_sole": np.ones(len(points), bool),
            "inside_cavity": np.zeros(len(points), bool),
            "within_z_bounds": np.ones(len(points), bool)}


def test_z_slab_escape_fails_even_inside_the_source_shoe():
    def native(points):
        result = _complete_native_points(points)
        result["within_z_bounds"] = points[:, 2] < 0
        return result
    report = validate_lattice_domain(trimesh.creation.box(), classify_points=native)
    assert report["geometry_status"] == "FAIL"
    assert report["outside_sole_count"] == 0
    assert report["inside_cavity_count"] == 0
    assert report["outside_z_bounds_count"] > 0
    assert report["outside_z_bounds_examples_mm"]


@pytest.mark.parametrize("field,value", [
    ("inside_sole", np.nan), ("inside_sole", "inside"),
    ("inside_cavity", ""), ("within_z_bounds", 1),
])
def test_unknown_or_coerced_classifier_states_cannot_pass(field, value):
    def native(points):
        result = _complete_native_points(points)
        result[field] = np.full(len(points), value)
        return result
    report = validate_lattice_domain(trimesh.creation.box(), classify_points=native)
    assert report["point_probe_status"] == "NOT_VERIFIED"
    assert report["classification_errors"]


@pytest.mark.parametrize("field", ["inside_sole", "inside_cavity", "within_z_bounds"])
def test_missing_classifier_coverage_cannot_pass(field):
    def native(points):
        result = _complete_native_points(points)
        del result[field]
        return result
    report = validate_lattice_domain(trimesh.creation.box(), classify_points=native)
    assert report["point_probe_status"] == "NOT_VERIFIED"
    assert report["geometry_status"] == "NOT_VERIFIED"


def test_incomplete_and_inconsistent_point_arrays_cannot_pass():
    def native(points):
        result = _complete_native_points(points)
        result["within_z_bounds"] = np.ones(len(points)-1, bool)
        return result
    assert validate_lattice_domain(trimesh.creation.box(), classify_points=native)["point_probe_status"] == "NOT_VERIFIED"
    def inconsistent(points):
        return {**_complete_native_points(points), "allowed": np.zeros(len(points), bool)}
    report = validate_lattice_domain(trimesh.creation.box(), classify_points=inconsistent)
    assert report["point_probe_status"] == "NOT_VERIFIED"
    assert "disagrees" in report["classification_errors"][0]["reason"]


def test_point_only_success_requires_independent_native_lines_for_pass():
    report = validate_lattice_domain(trimesh.creation.box(), classify_points=_complete_native_points)
    assert report["point_probe_status"] == "PASS"
    assert report["geometry_status"] == "NOT_VERIFIED"
    assert report["native_line_probes"]["measurement_errors"]
    assert report["global_containment_status"] == "NOT_VERIFIED"


@pytest.mark.parametrize("evidence", [None, {"geometry_status": "PASS"},
    {"geometry_status": "PASS", "planned_lines": 4, "measured_lines": 3, "occupied_lattice_lines": 3},
    {"geometry_status": "PASS", "planned_lines": 4, "measured_lines": 4, "occupied_lattice_lines": 4,
     "measurement_errors": ["one unresolved native state"]}])
def test_native_line_false_success_or_missing_evidence_cannot_pass(evidence):
    report = validate_lattice_domain(trimesh.creation.box(), classify_points=_complete_native_points,
                                    native_line_probe=lambda mesh: evidence)
    assert report["geometry_status"] == "NOT_VERIFIED"
    assert report["native_line_probes"]["measurement_errors"]


@pytest.mark.parametrize("override", [
    {"within_z_bounds": False}, {"within_z_bounds": None}, {"within_z_bounds": float("nan")},
    {"sole_intervals_mm": [float("nan"), 2]},
    {"sole_intervals_mm": [0, 2, 4, 6]}, {"sole_intervals_mm": [[2, -2]]},
])
def test_native_lines_reject_z_escape_and_malformed_native_evidence(override):
    from shoe_poc.validation.lattice import validate_native_line_containment
    def native(y, z):
        return {"sole_intervals_mm": [[-2, 2]], "cavity_intervals_mm": [], "within_z_bounds": True, **override}
    report = validate_native_line_containment(trimesh.creation.box(), scanline=native, maximum_lines=4)
    assert report["geometry_status"] == ("FAIL" if override.get("within_z_bounds") is False else "NOT_VERIFIED")
    assert report["global_containment_status"] == "NOT_VERIFIED"


def test_native_lines_missing_z_and_empty_or_open_mesh_cannot_pass():
    from shoe_poc.validation.lattice import validate_native_line_containment
    native = lambda y, z: {"sole_intervals_mm": [[-2, 2]], "cavity_intervals_mm": []}
    missing = validate_native_line_containment(trimesh.creation.box(), scanline=native, maximum_lines=4)
    assert missing["geometry_status"] == "NOT_VERIFIED"
    assert len(missing["measurement_errors"]) == 4
    opened = trimesh.creation.box()
    opened.update_faces(np.arange(len(opened.faces)-1))
    for mesh in [trimesh.Trimesh(), opened]:
        report = validate_native_line_containment(mesh, scanline=native)
        assert report["geometry_status"] == "NOT_VERIFIED"
        assert report["measured_lines"] == 0


def test_actual_native_z_slab_escape_is_caught_by_points_and_lines():
    from build123d import Box, Pos
    from shoe_poc.lattice.domain import NativeDomainProbe
    from shoe_poc.validation.lattice import validate_native_line_containment
    native = NativeDomainProbe(Box(10, 10, 10), Pos(0, 0, 4)*Box(1, 1, 1), -5, -2)
    report = validate_lattice_domain(trimesh.creation.box(), classify_points=native.classify_points,
        native_line_probe=lambda mesh: validate_native_line_containment(mesh, scanline=native.scanline, maximum_lines=4))
    assert report["outside_sole_count"] == 0
    assert report["inside_cavity_count"] == 0
    assert report["outside_z_bounds_count"] == report["sample_count"]
    assert report["point_probe_status"] == "FAIL"
    assert report["native_line_probes"]["geometry_status"] == "FAIL"
    assert report["geometry_status"] == "FAIL"


def test_caps_outside_gyroid_material_cannot_hide_bad_surface_vertices():
    period, threshold = 10., .3
    coordinate = np.arcsin(threshold)*period/(2*np.pi)
    # One exact interior vertex and an invalid cap where |G|=1 > threshold.
    mesh = trimesh.Trimesh(vertices=[[coordinate, 0, 0], [period/4, 0, 0],
                                     [coordinate, 0, period]], faces=[[0, 1, 2]], process=False)
    report = validate_gyroid_residual(mesh, period_mm=period, threshold=threshold,
        domain_field=lambda p: np.where(p[:, 0] > 1, 0., -1),
        cap_tolerance_mm=.01, residual_tolerance=.001)
    assert report["geometry_status"] == "FAIL"
    assert report["cap_vertices_outside_gyroid_material"] == 1


def test_residual_surface_outside_domain_cannot_pass_on_analytic_gyroid_alone():
    coordinate = np.arcsin(.3)*10/(2*np.pi)
    mesh = trimesh.Trimesh(vertices=[[coordinate, 0, 0]], faces=[], process=False)
    report = validate_gyroid_residual(mesh, period_mm=10, threshold=.3,
        domain_field=lambda p: np.ones(len(p)), cap_tolerance_mm=.01, residual_tolerance=.001)
    assert report["geometry_status"] == "FAIL"
    assert report["vertices_outside_domain_tolerance"] == 1


def test_gyroid_numeric_overflow_cannot_produce_passing_nan_residuals():
    mesh = trimesh.creation.box()
    with pytest.raises(ValueError, match="finite numeric range"):
        validate_gyroid_residual(mesh, period_mm=1e-320, threshold=.3,
            domain_field=lambda points: -np.ones(len(points)),
            cap_tolerance_mm=.01, residual_tolerance=.01)


def _sphere_shell(radius, *, inward=False, translation=(0, 0, 0)):
    mesh = trimesh.creation.icosphere(subdivisions=1, radius=radius)
    mesh.apply_translation(translation)
    if inward:
        mesh.invert()
    return mesh


def test_legal_closed_void_and_material_island_follow_nesting_parity():
    outer = _sphere_shell(5)
    cavity = _sphere_shell(3, inward=True)
    island = _sphere_shell(1)
    report = validate_lattice_mesh(trimesh.util.concatenate([outer, cavity, island]))
    assert report["geometry_status"] == "PASS"
    nesting = report["shell_nesting"]
    assert nesting["cross_shell_intersection_status"] == "PASS"
    assert sorted(shell["nesting_depth"] for shell in nesting["shells"]) == [0, 1, 2]
    assert nesting["negative_void_shell_count"] == 1
    assert nesting["orientation_violation_count"] == 0
    assert report["volume_mm3"] == pytest.approx(outer.volume+cavity.volume+island.volume)
    assert report["connected_components"] == 3


@pytest.mark.parametrize("inward_flags", [(False, False), (True, True), (True, False)])
def test_nested_shells_with_incorrect_orientation_fail(inward_flags):
    shells = [_sphere_shell(radius, inward=inward) for radius, inward in zip([5, 2], inward_flags)]
    report = validate_lattice_mesh(trimesh.util.concatenate(shells))
    assert report["geometry_status"] == "FAIL"
    assert report["shell_nesting"]["orientation_violation_count"] > 0


def test_negative_shell_in_torus_hole_is_not_inside_torus_material():
    # The tiny negative shell is strictly inside the torus bbox but lies in its
    # through-hole: bbox inclusion alone cannot authorize its inward orientation.
    torus = trimesh.creation.torus(major_radius=5, minor_radius=1)
    inverted = _sphere_shell(.3, inward=True)
    report = validate_lattice_mesh(trimesh.util.concatenate([torus, inverted]))
    assert report["geometry_status"] == "FAIL"
    negative = next(shell for shell in report["shell_nesting"]["shells"] if shell["signed_volume_mm3"] < 0)
    assert negative["nesting_depth"] == 0
    assert negative["orientation_status"] == "FAIL"


@pytest.mark.parametrize("translation", [(1, 0, 0), (1.9, .1, .1)])
def test_crossing_shells_fail_even_when_individually_closed_and_positive(translation):
    first = trimesh.creation.box(extents=[2, 2, 2])
    second = first.copy()
    second.apply_translation(translation)
    report = validate_lattice_mesh(trimesh.util.concatenate([first, second]))
    assert report["geometry_status"] == "FAIL"
    assert report["shell_nesting"]["cross_shell_contact_examples"]


def test_coplanar_shell_contact_fails_without_changing_or_welding_input():
    first = trimesh.creation.box(extents=[2, 2, 2])
    second = first.copy()
    second.apply_translation([2, 0, 0])
    mesh = trimesh.util.concatenate([first, second])
    original = mesh.faces.copy()
    report = validate_lattice_mesh(mesh)
    assert report["geometry_status"] == "FAIL"
    assert report["shell_nesting"]["cross_shell_contact_examples"]
    assert np.array_equal(mesh.faces, original)


def test_nested_boxes_are_resolved_without_ray_edge_guessing():
    outer = trimesh.creation.box(extents=[10, 10, 10])
    inner = trimesh.creation.box(extents=[2, 2, 2])
    inner.invert()
    report = validate_lattice_mesh(trimesh.util.concatenate([outer, inner]))
    assert report["geometry_status"] == "PASS"
    inner_report = next(shell for shell in report["shell_nesting"]["shells"] if shell["nesting_depth"] == 1)
    assert len(inner_report["witness_points_mm"]) == 3


def test_shell_nesting_candidate_budget_exhaustion_cannot_pass():
    from shoe_poc.validation.lattice import _validate_shell_nesting
    outer, inner = _sphere_shell(5), _sphere_shell(2, inward=True)
    mesh = trimesh.util.concatenate([outer, inner])
    components = trimesh.graph.connected_components(mesh.face_adjacency,
        nodes=np.arange(len(mesh.faces)), min_len=1, engine="scipy")
    volumes = np.array([outer.volume, inner.volume])
    pair = _validate_shell_nesting(mesh, components, volumes, maximum_pair_candidates=1)
    assert pair["geometry_status"] == "NOT_VERIFIED"
    assert pair["measurement_errors"]
    ray = _validate_shell_nesting(mesh, components, volumes, maximum_ray_candidates=1)
    assert ray["geometry_status"] == "NOT_VERIFIED"
    assert ray["measurement_errors"]
