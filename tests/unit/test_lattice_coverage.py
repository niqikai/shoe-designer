"""Coverage negatives: containment alone must not approve an absent toe."""
import numpy as np
import pytest
import trimesh

from shoe_poc.validation.lattice import validate_lattice_domain, validate_native_line_containment
from shoe_poc.validation.lattice_coverage import validate_lattice_coverage, NativeVerticalSoleSections

BOUNDS = [[0., -10., -12.], [60., 10., 16.]]


def _ramp_mesh(*, planar_clip=False, upper_intrusion=False):
    xz = [(0., -12.), (0., -2.), (60., 16.), (60., 6.)]
    if planar_clip:
        xz = [(0., -12.), (0., -2.), (2/.3, 0.), (12/.3, 0.)]
    if upper_intrusion:
        xz = [(0., -12.), (0., 3.), (60., 21.), (60., 6.)]
    return trimesh.convex.convex_hull(np.array([[x, y, z] for x, z in xz for y in (-10., 10.)]))


def _section(x, y):
    spans = [[.3*x-12., .3*x-2.]] if 0 < x < 60 and -10 < y < 10 else []
    return {'sole_intervals_mm': spans}


def _coverage(mesh, **kwargs):
    options = dict(native_sections=_section, bounds_mm=BOUNDS, period_mm=12.,
                   resolution_mm=.4, clearance_mm=2., start_fraction=0.)
    return validate_lattice_coverage(mesh, **(options | kwargs))


def test_actual_planar_clip_passes_containment_but_fails_rising_toe_coverage():
    clipped = _ramp_mesh(planar_clip=True)
    def points(p):
        allowed = ((p[:, 0] >= 0) & (p[:, 0] <= 60) & (np.abs(p[:, 1]) <= 10)
                   & (p[:, 2] >= .3*p[:, 0]-12-1e-6) & (p[:, 2] <= .3*p[:, 0]-2+1e-6))
        return {'inside_sole': allowed, 'inside_cavity': np.zeros(len(p), bool),
                'within_z_bounds': np.ones(len(p), bool)}
    def scanline(y, z):
        a, b = max(0., (z+2)/.3), min(60., (z+12)/.3)
        return {'sole_intervals_mm': [[a, b]] if a < b and abs(y) < 10 else [],
                'cavity_intervals_mm': [], 'within_z_bounds': True}
    containment = validate_lattice_domain(clipped, classify_points=points,
        native_line_probe=lambda m: validate_native_line_containment(m, scanline=scanline))
    assert containment['geometry_status'] == 'PASS'
    assert clipped.bounds[1, 0] == pytest.approx(40.)
    old = _coverage(clipped)
    assert old['geometry_status'] == 'FAIL'
    assert old['bins'][-1]['lattice_usable_length_sum_mm'] == 0
    assert old['bins'][-1]['coverage_required']
    assert old['bins'][-1]['usable_length_sum_mm'] > 0
    assert _coverage(_ramp_mesh())['geometry_status'] == 'PASS'


def test_missing_certified_domain_fails_even_if_exported_material_is_present():
    report = _coverage(_ramp_mesh(), domain_field=lambda p: np.where(p[:, 0] < 40, -1., 1.))
    assert report['geometry_status'] == 'FAIL'
    assert report['bins'][-1]['lattice_support_fraction'] == pytest.approx(1.)
    assert report['bins'][-1]['domain_support_fraction'] == 0
    assert 'certified-domain' in report['bins'][-1]['failure_reasons'][0]


def test_upper_intrusion_fails_independent_native_sole_intervals():
    report = _coverage(_ramp_mesh(upper_intrusion=True))
    assert report['geometry_status'] == 'FAIL'
    assert all(row['lattice_outside_intended_sole_length_sum_mm'] > 0 for row in report['bins'])


def test_period_bins_allow_individual_phase_gaps():
    # A sheet intersects just one of three X stations in each period. Requiring
    # material on each column would reject a legitimate periodic phase gap.
    sheets = []
    for x in np.arange(6., 60., 12.):
        box = trimesh.creation.box(extents=[2., 20., 10.])
        box.vertices[:, 2] += .3*(box.vertices[:, 0]+x)-7.
        box.vertices[:, 0] += x
        sheets.append(box)
    report = _coverage(trimesh.util.concatenate(sheets))
    assert report['geometry_status'] == 'PASS'
    assert any(not sample['lattice_intervals_mm'] for sample in report['samples'])
    assert all(row['lattice_support_fraction'] > 0 for row in report['bins'])


def test_thin_terminal_solid_reserve_is_measured_without_false_missingtoe_failure():
    def thin_tip(x, y):
        if x > 48:
            return {'sole_intervals_mm': [[.3*x-12, .3*x-8]]}
        return _section(x, y)
    # Only reserve-thick terminal material is expected: there is no space for
    # a lattice after the two reserves and voxel guard.
    mesh = _ramp_mesh()
    mesh = trimesh.convex.convex_hull(np.array([[x, y, z] for x, z in
        [(0, -12), (0, -2), (48, 12.4), (48, 2.4)] for y in (-10, 10)]))
    report = _coverage(mesh, native_sections=thin_tip)
    assert report['geometry_status'] == 'PASS'
    tip = report['bins'][-1]
    assert tip['thin_reserve_column_count'] == 15
    assert tip['usable_length_sum_mm'] == 0
    assert not tip['coverage_required']
    assert tip['status'] == 'MEASURED_RESERVE_OR_EDGE'


@pytest.mark.parametrize('sampler', [lambda p: np.full(len(p), np.nan), lambda p: np.zeros((len(p), 1))])
def test_unresolved_domain_samples_cannot_pass(sampler):
    report = _coverage(_ramp_mesh(), domain_field=sampler)
    assert report['geometry_status'] == 'NOT_VERIFIED'
    assert report['measurement_errors']


def test_native_boolean_sections_measure_material_below_curved_roof():
    from build123d import Box, Pos
    sections = NativeVerticalSoleSections(Box(10, 10, 10), Pos(0, 0, 3)*Box(4, 4, 4),
                                         upper_z=lambda x: 2+.1*x, z_min_mm=-4.)
    native = sections(.6, .3)
    assert np.array(native['sole_intervals_mm']) == pytest.approx(np.array([[-4., 1.]]))
    assert np.array(native['cavity_intervals_mm']) == pytest.approx(np.array([[1., 5.]]))
    assert native['upper_z_mm'] == pytest.approx(2.06)


def test_missing_cad_measurements_and_open_mesh_cannot_pass():
    def broken(x, y):
        raise RuntimeError('native CAD Boolean unavailable')
    failed = _coverage(_ramp_mesh(), native_sections=broken)
    assert failed['geometry_status'] == 'NOT_VERIFIED'
    opened = _ramp_mesh()
    opened.update_faces(np.arange(len(opened.faces)-1))
    assert _coverage(opened)['geometry_status'] == 'NOT_VERIFIED'


def test_raised_toe_support_is_checked_separately_from_remaining_lower_material():
    # The material below Z=0 reaches the complete footprint, so coarse bounds
    # and a little occupied material per bin would conceal the missing rise.
    def native(x, y):
        return {'sole_intervals_mm': [[-12., 6.+.1*x]]}
    planar = trimesh.creation.box(extents=[60., 20., 12.])
    planar.apply_translation([30., 0., -6.])
    report = _coverage(planar, native_sections=native, reference_datum_mm=0.)
    assert report['geometry_status'] == 'FAIL'
    assert report['raised_coverage_required_bin_count'] > 0
    assert all(row['lattice_support_fraction'] > 0 for row in report['bins'])
    assert all(row['raised_lattice_support_fraction'] == 0 for row in report['bins'])
    assert any('above the reference datum' in reason for row in report['bins']
               for reason in row['failure_reasons'])
