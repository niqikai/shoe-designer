"""Actual solids distinguish thick support, thin support and lost clearance."""
from pathlib import Path
from types import SimpleNamespace

from build123d import Plane, Solid

from shoe_poc.schemas import ShoeSpec
from shoe_poc.validation.footbed import validate_underfoot_material


def _reference(bottom, clearance=1.):
    last = Solid.make_box(100, 40, 50, Plane(origin=(-50, -20, 0)))
    cavity = Solid.make_box(100+2*clearance, 40+2*clearance, 50+2*clearance,
                            Plane(origin=(-50-clearance, -20-clearance, -clearance)))
    body = Solid.make_box(108, 48, 55-bottom, Plane(origin=(-54, -24, bottom)))
    return last, SimpleNamespace(shape=body.cut(cavity), cavity=cavity)


def _spec():
    return ShoeSpec.load(Path(__file__).resolve().parents[2]/'input/specs/shoe_spec.json')


def test_thick_actual_support_can_exceed_upper_thickness():
    last, build = _reference(-16.)
    report = validate_underfoot_material(last, build, _spec())
    assert report['geometry_status'] == 'PASS'
    assert all(region['sample_count'] for region in report['regions'].values())
    assert not report['measurement_errors']
    assert report['minimum_measured_directional_material_mm'] == 15.
    assert report['minimum_measured_clearance_mm'] == 1.


def test_actual_underfoot_below_nominal_minus_allowance_fails():
    last, build = _reference(-2.5)
    report = validate_underfoot_material(last, build, _spec())
    assert report['geometry_status'] == 'FAIL'
    assert report['minimum_measured_directional_material_mm'] == 1.5
    assert report['minimum_directional_material_target_mm'] == 2.15
    assert all(sample['status'] == 'FAIL' for sample in report['samples'])


def test_containment_alone_does_not_qualify_clearance():
    last, build = _reference(-16., clearance=.5)
    report = validate_underfoot_material(last, build, _spec())
    assert report['geometry_status'] == 'FAIL'
    assert report['minimum_measured_clearance_mm'] == .5
    assert report['minimum_measured_directional_material_mm'] > 2.5


def test_unmeasurable_ray_is_reported_without_false_pass(monkeypatch):
    import shoe_poc.validation.footbed as footbed
    last, build = _reference(-16.)

    def failure(*args):
        raise ValueError('Injected native ray failure')

    monkeypatch.setattr(footbed, '_ray_hits', failure)
    report = validate_underfoot_material(last, build, _spec())
    assert report['geometry_status'] == 'NOT_VERIFIED'
    assert report['coverage']['selected_underfoot_site_count'] > 0
    assert len(report['measurement_errors']) == report['coverage']['selected_underfoot_site_count']
    assert not report['samples']
