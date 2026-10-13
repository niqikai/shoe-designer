"""Native ARC offset failure-stop at a real concave solid's self-collision."""
import pytest
from build123d import Plane, Solid

from shoe_poc.geometry.envelope import _local_arc_offset


def _concave_notched_solid(gap=2):
    # A closed U-shaped cross-section. The gap's opposite inner faces approach
    # each other by 2*distance under a positive outward normal offset.
    block = Solid.make_box(20, 20, 10)
    notch = Solid.make_box(gap, 18, 12, Plane(origin=((20-gap)/2, 4, -1)))
    return block.cut(notch)


def test_zero_offset_preserves_the_actual_source_object():
    source = _concave_notched_solid()
    assert _local_arc_offset(source, 0) is source


def test_native_concave_offset_valid_before_gap_self_collision():
    source = _concave_notched_solid()
    assert source.is_valid and len(source.solids()) == 1
    result = _local_arc_offset(source, 0.5)
    assert result.is_valid and len(result.solids()) == 1
    assert result.volume > source.volume
    outside = source.cut(result)
    assert outside is None or sum(s.volume for s in outside.solids()) <= 0.01


@pytest.mark.parametrize('distance', [1, 2, 3])
def test_actual_native_gap_self_collision_stops_offset(distance):
    source = _concave_notched_solid(gap=2)
    assert source.is_valid and len(source.solids()) == 1
    # At distance >= gap/2 the inward gap boundaries meet/cross. With the exact
    # production native operation arguments this yields a null CAD shape even
    # though OCCT may set IsDone=True. No mocked native flags or fallback bodies.
    with pytest.raises(ValueError, match='Local CAD ARC offset.*nonnull'):
        _local_arc_offset(source, distance)
