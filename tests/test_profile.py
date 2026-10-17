import pytest
from pydantic import ValidationError

from shoegen.profile import ManufacturingProfile, load_profile


@pytest.mark.parametrize("profile_id", ["pbf-tpu-generic@0", "dls-epu-generic@0"])
def test_profile_loads_and_is_placeholder(profile_id):
    profile = load_profile(profile_id)
    assert isinstance(profile, ManufacturingProfile)
    assert profile.id == profile_id
    assert profile.status == "placeholder"
    # Every numeric field starts unverified.
    for field in (
        "min_strut_diameter_mm",
        "min_wall_mm",
        "min_pore_mm",
        "min_fragment_volume_mm3",
        "build_volume_mm",
    ):
        assert profile.sources[field] is None


def test_dls_requires_drain_holes():
    profile = load_profile("dls-epu-generic@0")
    assert profile.requires_drain_holes is True
    assert profile.min_strut_diameter_mm == 1.0


def test_pbf_build_volume():
    profile = load_profile("pbf-tpu-generic@0")
    assert profile.build_volume_mm == (380.0, 284.0, 380.0)


def test_extra_field_rejected():
    data = load_profile("pbf-tpu-generic@0").model_dump()
    data["unexpected"] = 1
    with pytest.raises(ValidationError):
        ManufacturingProfile.model_validate(data)


def test_unknown_profile_raises():
    with pytest.raises(FileNotFoundError):
        load_profile("no-such-process@0")


def test_invalid_profile_id_rejected():
    with pytest.raises(ValueError):
        load_profile("../etc/passwd")
