"""Native CAD floor probes protect the optional continuous toe roll."""
from types import SimpleNamespace
import numpy as np
import pytest
from build123d import Vector, Edge
from shoe_poc.geometry.sole import _rocker
from shoe_poc.geometry.sole_transition import _rocker_cutter


def geometry(roll):
    box = SimpleNamespace(min=Vector(0,-50,0), max=Vector(276.66,50,130),
                          size=Vector(276.66,100,130))
    profile = SimpleNamespace(toe_roll_radius_mm=roll, toe_start_fraction=.6,
        toe_rise_mm=12., heel_rise_mm=2., margin_mm=4.5,
        sidewall_inset_mm=2., sidewall_bulge_mm=2.)
    return box, profile


def test_rolled_toe_rises_through_source_tip_without_plateau():
    box, profile = geometry(15.)
    xs = np.linspace(box.max.X-15., box.max.X+2., 81)
    slopes = np.diff([_rocker(x, box, profile) for x in xs])/np.diff(xs)
    assert np.all(slopes > .3)
    assert slopes[-1] > slopes[0]
    assert _rocker(100., box, profile) == 0.


@pytest.mark.parametrize('roll', [0., 15.])
def test_floor_evaluator_matches_native_cutter(roll):
    box, profile = geometry(roll)
    cutter = _rocker_cutter(box, -16., profile)
    assert cutter.is_valid
    for x in [10., 100., 190., 240., 270., 278.]:
        common = cutter.intersect(Edge.make_line((x,0,-50),(x,0,30)))
        assert common is not None
        actual_floor = max(v.Z for v in common.vertices())
        assert actual_floor == pytest.approx(-16.+_rocker(x,box,profile), abs=1e-6)


def test_disabled_roll_keeps_legacy_terminal_plateau():
    box, profile = geometry(0.)
    assert _rocker(box.max.X, box, profile) == 12.
    assert _rocker(box.max.X+4., box, profile) == 12.


def test_cutter_closure_stays_outside_large_margin_roll():
    box, profile = geometry(15.)
    profile.margin_mm = 20.
    profile.sidewall_bulge_mm = 5.
    cutter = _rocker_cutter(box, -16., profile)
    assert cutter.is_valid
    assert cutter.bounding_box().max.X > box.max.X+25.


def test_roll_rejects_short_ramp_with_reversed_control_order():
    box, profile = geometry(15.)
    profile.toe_start_fraction = .99
    with pytest.raises(ValueError, match='monotone'):
        _rocker_cutter(box, -16., profile)
