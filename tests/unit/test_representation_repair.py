"""Native CAD repair cannot silently change a qualified shoe's material."""
import pytest
from build123d import Compound, Plane, Solid

from shoe_poc.geometry.representation import repair_parameterization


def test_native_repair_preserves_closed_box():
    result, report = repair_parameterization(Solid.make_box(10, 10, 10))
    assert result.is_valid and len(result.solids()) == 1
    assert report["bbox_max_delta_mm"] == 0
    assert report["symmetric_difference_volume_mm3"] == 0


def test_repair_rejects_disconnected_input():
    shape = Compound(children=[Solid.make_box(10, 10, 10),
        Solid.make_box(10, 10, 10, Plane(origin=(20, 0, 0)))])
    with pytest.raises(ValueError, match="one valid input solid"):
        repair_parameterization(shape)


@pytest.mark.parametrize("changed", [
    Solid.make_box(10, 10, 10, Plane(origin=(1, 0, 0))),
    Solid.make_box(10, 10, 10).cut(Solid.make_box(2, 2, 2, Plane(origin=(4, 4, 0)))),
])
def test_repair_rejects_material_change_even_with_unchanged_outer_bounds(monkeypatch, changed):
    import OCP.ShapeFix

    class ChangedRepair:
        def __init__(self, shape): pass
        def SetPrecision(self, precision): pass
        def SetMaxTolerance(self, tolerance): pass
        def Perform(self): return True
        def Shape(self): return changed.wrapped

    monkeypatch.setattr(OCP.ShapeFix, "ShapeFix_Shape", ChangedRepair)
    with pytest.raises(ValueError, match="changed bounded geometry"):
        repair_parameterization(Solid.make_box(10, 10, 10))
