"""T04/T18: executable protocol validation, including malformed inputs."""

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from shoe_poc.schemas import ShoeSpec


SPEC_PATH = Path(__file__).resolve().parents[2] / "input/specs/shoe_spec.json"


@pytest.fixture
def spec_data():
    return json.loads(SPEC_PATH.read_text(encoding="utf-8"))


def change(data, keys, value):
    changed = copy.deepcopy(data)
    current = changed
    for key in keys[:-1]:
        current = current[key]
    current[keys[-1]] = value
    return changed


def test_sample_is_valid_and_unknown_identity_is_honest(spec_data):
    spec = ShoeSpec.model_validate(spec_data)
    assert spec.schema_version == "0.1"
    assert spec.unit == "mm"
    assert spec.last.side == "unknown"
    assert spec.last.coordinate_system.orientation_confirmed is False
    assert spec.shoe.size is None
    assert spec.upper.pattern_parameters.type == "none"
    assert spec.manufacturing.rules_profile.status == "EXPERIMENTAL"
    assert spec.manufacturing.material_profile.status == "EXPERIMENTAL"


def test_load_resolves_paths_against_spec_directory(tmp_path, spec_data, monkeypatch):
    folder = tmp_path / "project/input/specs"
    folder.mkdir(parents=True)
    source = folder / "custom.json"
    source.write_text(json.dumps(spec_data), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    spec = ShoeSpec.load(source)
    assert Path(spec.last.file) == tmp_path / "project/input/lasts/sneaker_last.stp"
    assert Path(spec.manufacturing.rules_profile.file) == tmp_path / "project/config/manufacturing_rules.json"


def test_absolute_step_path_is_preserved(tmp_path, spec_data):
    absolute = tmp_path / "another.step"
    spec_data["last"]["file"] = str(absolute)
    source = tmp_path / "config.json"
    source.write_text(json.dumps(spec_data), encoding="utf-8")
    assert ShoeSpec.load(source).last.file == str(absolute)


def test_json_round_trip(spec_data):
    original = ShoeSpec.model_validate(spec_data)
    assert ShoeSpec.model_validate_json(original.model_dump_json()) == original


@pytest.mark.parametrize(
    ("keys", "value"),
    [
        (("schema_version",), "0.2"),
        (("design_id",), "../unsafe name"),
        (("unit",), "inch"),
        (("last", "file"), "asset.stl"),
        (("last", "side"), "either"),
        (("last", "coordinate_system", "orientation_confirmed"), "false"),
        (("last", "coordinate_system", "orientation_confirmed"), 0),
        (("last", "coordinate_system", "rotation"), [[1, 0, 0], [0, 1, 0]]),
        (("last", "coordinate_system", "rotation"), [[1, 0, 0], [0, 1, 0], [0, 0]]),
        (("last", "coordinate_system", "rotation"), [[2, 0, 0], [0, 1, 0], [0, 0, 1]]),
        (("last", "coordinate_system", "rotation"), [[1, 0, 0], [0, 1, 0], [0, 0, -1]]),
        (("last", "coordinate_system", "rotation"), [[1, 0, 0], [1, 0, 0], [0, 0, 1]]),
        (("last", "coordinate_system", "translation_mm"), [0, 0]),
        (("last", "coordinate_system", "translation_mm"), [10001, 0, 0]),
        (("shoe", "template"), "unimplemented"),
        (("upper", "thickness_mm"), 0),
        (("upper", "thickness_mm"), 8.01),
        (("upper", "thickness_mm"), "2.5"),
        (("upper", "thickness_mm"), True),
        (("upper", "thickness_mm"), float("nan")),
        (("upper", "thickness_mm"), float("inf")),
        (("upper", "clearance_mm"), -1),
        (("upper", "opening_parameters", "collar_height_mm"), 9),
        (("upper", "opening_parameters", "opening_start_x_fraction"), 0.6),
        (("upper", "opening_parameters", "opening_end_x_fraction"), 1.1),
        (("upper", "pattern_parameters", "type"), "hexagonal"),
        (("sole", "height_mm"), 4),
        (("sole", "height_mm"), 61),
        (("sole", "profile_parameters", "margin_mm"), 3.4),
        (("lattice", "period_mm"), 4),
        (("lattice", "threshold"), 0),
        (("lattice", "threshold"), 2),
        (("lattice", "resolution_mm"), 0.1),
        (("lattice", "resolution_mm"), 4),
        (("manufacturing", "process"), "FDM"),
        (("manufacturing", "material_profile", "status"), "PRODUCTION"),
        (("manufacturing", "rules_profile", "status"), "PASS"),
        (("manufacturing", "rules_profile", "file"), "rules.txt"),
    ],
)
def test_invalid_values_are_rejected(spec_data, keys, value):
    with pytest.raises(ValidationError):
        ShoeSpec.model_validate(change(spec_data, keys, value))


@pytest.mark.parametrize(
    "keys",
    [(), ("last",), ("last", "coordinate_system"), ("shoe",), ("upper",),
     ("upper", "opening_parameters"), ("upper", "pattern_parameters"),
     ("sole",), ("sole", "profile_parameters"), ("lattice",),
     ("manufacturing",), ("manufacturing", "rules_profile"),
     ("manufacturing", "material_profile")],
)
def test_unknown_fields_rejected_at_each_level(spec_data, keys):
    node = spec_data
    for key in keys:
        node = node[key]
    node["typo_parameter"] = 123
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ShoeSpec.model_validate(spec_data)


def test_proper_nonidentity_rotation_is_accepted(spec_data):
    spec_data["last"]["coordinate_system"]["rotation"] = [[0, -1, 0], [1, 0, 0], [0, 0, 1]]
    spec_data["last"]["coordinate_system"]["translation_mm"] = [1, -2, 3]
    spec = ShoeSpec.model_validate(spec_data)
    assert spec.last.coordinate_system.rotation[0] == [0.0, -1.0, 0.0]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
@pytest.mark.parametrize("field", ["rotation", "translation_mm"])
def test_nonfinite_transform_is_rejected(spec_data, field, value):
    if field == "rotation":
        spec_data["last"]["coordinate_system"][field][0][0] = value
    else:
        spec_data["last"]["coordinate_system"][field][0] = value
    with pytest.raises(ValidationError):
        ShoeSpec.model_validate(spec_data)


def test_valid_integer_lengths_are_numeric_not_strings(spec_data):
    spec_data["sole"]["height_mm"] = 25
    assert ShoeSpec.model_validate(spec_data).sole.height_mm == 25.0


def test_load_does_not_mask_invalid_json(tmp_path):
    source = tmp_path / "invalid.json"
    source.write_text('{"schema_version":', encoding="utf-8")
    with pytest.raises(ValidationError):
        ShoeSpec.load(source)


def test_legacy_v01_loads_repair_defaults(spec_data):
    spec = ShoeSpec.model_validate(spec_data)
    assert spec.schema_version == "0.1"
    assert spec.upper.opening_parameters.transition_mm == 60.0
    profile = spec.sole.profile_parameters
    assert (profile.sidewall_inset_mm, profile.toe_rise_mm,
            profile.heel_rise_mm, profile.edge_radius_mm) == (2.0, 6.0, 2.0, 2.0)
    assert ShoeSpec.model_validate_json(spec.model_dump_json()) == spec


def test_legacy_shape_controls_do_not_change_silently(spec_data):
    spec = ShoeSpec.model_validate(spec_data)
    opening = spec.upper.opening_parameters
    profile = spec.sole.profile_parameters
    assert opening.collar_style == "quintic_roof"
    assert opening.heel_lift_mm == opening.side_dip_mm == 0
    assert profile.toe_start_fraction == .68
    assert profile.sidewall_bulge_mm == 0
    assert profile.toe_roll_radius_mm == 0


def test_annotated_design_spec_preserves_last_and_upper_constraints():
    original = ShoeSpec.load(SPEC_PATH)
    refined = ShoeSpec.load(SPEC_PATH.with_name("shoe_spec_design_refine.json"))
    assert refined.last == original.last
    assert refined.upper.clearance_mm == original.upper.clearance_mm
    assert refined.upper.thickness_mm == original.upper.thickness_mm
    assert refined.upper.opening_parameters.collar_style == "sculpted_3d"
    assert 0 < refined.sole.height_mm <= original.sole.height_mm
    assert refined.sole.profile_parameters.toe_rise_mm > original.sole.profile_parameters.toe_rise_mm
    assert refined.sole.profile_parameters.toe_start_fraction < original.sole.profile_parameters.toe_start_fraction


@pytest.mark.parametrize("radius", [-0.1, 15.1, "8", True, float("nan")])
def test_invalid_toe_roll_radius_rejected(spec_data, radius):
    spec_data["sole"]["profile_parameters"]["toe_roll_radius_mm"] = radius
    with pytest.raises(ValidationError):
        ShoeSpec.model_validate(spec_data)


@pytest.mark.parametrize(("keys", "value"), [
    (("upper", "opening_parameters", "collar_style"), "sculpted"),
    (("upper", "opening_parameters", "side_dip_mm"), 5),
    (("upper", "opening_parameters", "heel_lift_mm"), 2),
    (("sole", "profile_parameters", "toe_start_fraction"), .2),
    (("sole", "profile_parameters", "toe_start_fraction"), .9),
    (("sole", "profile_parameters", "sidewall_bulge_mm"), -1),
])
def test_incompatible_or_unbounded_design_controls_fail(spec_data, keys, value):
    with pytest.raises(ValidationError):
        ShoeSpec.model_validate(change(spec_data, keys, value))


@pytest.mark.parametrize(("keys", "value"), [
    (("upper", "opening_parameters", "transition_mm"), 0),
    (("upper", "opening_parameters", "transition_mm"), 60.1),
    (("upper", "opening_parameters", "transition_mm"), "15"),
    (("sole", "profile_parameters", "sidewall_inset_mm"), -0.1),
    (("sole", "profile_parameters", "sidewall_inset_mm"), 10.1),
    (("sole", "profile_parameters", "toe_rise_mm"), 20.1),
    (("sole", "profile_parameters", "heel_rise_mm"), 15.1),
    (("sole", "profile_parameters", "edge_radius_mm"), 10.1),
    (("sole", "profile_parameters", "edge_radius_mm"), True),
])
def test_invalid_repair_profile_values(spec_data, keys, value):
    with pytest.raises(ValidationError):
        ShoeSpec.model_validate(change(spec_data, keys, value))


def test_radius_and_inset_must_fit_sole_height(spec_data):
    spec_data["sole"]["height_mm"] = 5
    spec_data["sole"]["profile_parameters"]["edge_radius_mm"] = 3
    with pytest.raises(ValidationError, match="half"):
        ShoeSpec.model_validate(spec_data)
    spec_data["sole"]["profile_parameters"]["edge_radius_mm"] = 2
    spec_data["sole"]["profile_parameters"]["sidewall_inset_mm"] = 5
    with pytest.raises(ValidationError, match="less than"):
        ShoeSpec.model_validate(spec_data)


def test_toe_rise_is_not_arbitrarily_constrained_by_flat_height(spec_data):
    spec_data["sole"]["height_mm"] = 5
    spec_data["sole"]["profile_parameters"]["toe_rise_mm"] = 20
    assert ShoeSpec.model_validate(spec_data).sole.profile_parameters.toe_rise_mm == 20


def test_checked_in_schema_matches_runtime_and_optional_defaults():
    checked_in = json.loads((SPEC_PATH.parents[2] / "config/shoe_spec.schema.json").read_text())
    assert checked_in == ShoeSpec.model_json_schema()
    assert "transition_mm" not in checked_in["$defs"]["OpeningParameters"]["required"]
    assert checked_in["$defs"]["OpeningParameters"]["properties"]["transition_mm"]["default"] == 60.0
