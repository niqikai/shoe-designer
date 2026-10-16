"""Curved sole roofs are exact polyline contracts, including cell reserves."""
import math
from types import SimpleNamespace

import numpy as np
import pytest
from build123d import Align, Box, Pos

from shoe_poc.lattice.domain import NativeDomainProbe, sample_sole_domain
from shoe_poc.lattice.envelope import SoleEnvelope, build_sole_envelope
from shoe_poc.schemas import ShoeSpec
from pathlib import Path


def envelope(points=((0, 8), (10, 8), (30, 18))):
    return SoleEnvelope({'version': 'lower-foot-roof-polyline-v1',
        'lower_datum_z_mm': 0., 'upper_points_xz_mm': [list(p) for p in points]})


def solids():
    return (Box(30, 20, 24, align=(Align.MIN,)*3),
            Pos(40, 40, 40)*Box(1, 1, 1))


def test_roof_distance_is_normal_distance_not_vertical_gap():
    roof = envelope()
    assert roof.boundary_distance([20, 10, 10]) == pytest.approx(3/math.sqrt(1.25))
    assert roof.upper_z(20) == 13
    assert roof.x_intervals(13) == [[20., 30.]]
    assert roof.contains(np.array([[20, 10, 12], [20, 10, 14]])).tolist() == [True, False]


def test_curved_probe_recovers_raised_toe_and_rejects_upper_and_cavity():
    material, _ = solids()
    cavity = Pos(24, 7, 9)*Box(2, 2, 2, align=(Align.MIN,)*3)
    roof = envelope()
    native = NativeDomainProbe(material, cavity, *roof.z_bounds, envelope=roof)
    points = np.array([[23, 10, 11], [5, 10, 11], [23, 10, 17], [25, 8, 10]])
    assert native.classify_points(points)['allowed'].tolist() == [True, False, False, False]
    assert native.scanline(10, 13)['sole_intervals_mm'] == [[20., 30.]]
    planar = NativeDomainProbe(material, cavity, 0, 8)
    assert not planar.classify_points(points[:1])['allowed'][0]


def test_curved_certificate_keeps_all_cell_corners_below_sloped_roof():
    material, cavity = solids()
    roof = envelope()
    domain = sample_sole_domain(material, cavity, z_min_mm=0, z_max_mm=18,
        spacing_mm=.5, clearance_mm=.5, envelope=roof)
    indices = np.argwhere(domain.field_mm < 0)
    points = np.column_stack([domain.plan.axes[i][indices[:, i]] for i in range(3)])
    assert np.any(points[:, 2] > 8.5)
    for dx in (-.5, .5):
        for dz in (-.5, .5):
            assert roof.contains(points+[dx, 0, dz]).all()
    # Raw material under a narrow end remains omitted when no certificate fits.
    near_roof = np.array([[26., 10., 15.8]])
    assert roof.contains(near_roof)[0]
    idx = tuple(np.argmin(abs(domain.plan.axes[i]-near_roof[0, i])) for i in range(3))
    assert domain.field_mm[idx] > 0
    assert domain.metadata['envelope'] == roof.metadata


def test_curved_probe_rejects_mismatched_global_bounds():
    with pytest.raises(ValueError, match='disagree'):
        NativeDomainProbe(*solids(), 0, 8, envelope=envelope())


@pytest.mark.parametrize('points', [[[0, 8], [0, 10]], [[0, 8], [1, float('nan')]], [[0, 8], [1, -2]]])
def test_invalid_roofs_fail_closed(points):
    with pytest.raises(ValueError, match='contract'):
        envelope(points)


def test_roof_factory_uses_frozen_guide_and_nominal_rocker_cap():
    from shoe_poc.geometry.sole import _rocker
    spec = ShoeSpec.load(Path(__file__).parents[2]/'input/specs/shoe_spec_m2_toe_repair.json')
    last_box = SimpleNamespace(min=SimpleNamespace(X=0., Z=0.), max=SimpleNamespace(X=100.), size=SimpleNamespace(X=100.))
    shoe_box = SimpleNamespace(min=SimpleNamespace(X=-3.), max=SimpleNamespace(X=104.))
    shape = SimpleNamespace(bounding_box=lambda: shoe_box)
    last = SimpleNamespace(bounding_box=lambda: last_box)
    metadata = {'sole': {'actual_profile_parameters': spec.sole.profile_parameters.model_dump(),
        'station_x_mm': [0., 50., 100.],
        'lower_foot_station_profiles': [{'connection_z_mm': z} for z in [12., 14., 18.]],
        'transition': {'band_half_height_mm': 2.}}}
    roof = build_sole_envelope(shape, last, spec, metadata, source_sha256='bound-guide')
    points = roof.points
    rocker = np.array([_rocker(x, last_box, spec.sole.profile_parameters) for x in points[:, 0]])
    guide = np.interp(points[:, 0], [0., 50., 100.], [12., 14., 18.])-2
    np.testing.assert_allclose(points[:, 1], np.minimum(rocker, guide))
    assert np.diff(points[:, 0]).max() <= 1.
    assert roof.metadata['source_geometry_report_sha256'] == 'bound-guide'
    assert roof.z_bounds[0] == -16.
    metadata['sole']['actual_profile_parameters']['toe_rise_mm'] += 1
    with pytest.raises(ValueError, match='parameters'):
        build_sole_envelope(shape, last, spec, metadata, source_sha256='bound-guide')
