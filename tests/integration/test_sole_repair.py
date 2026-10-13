"""Independent actual-CAD checks for the bounded lower-foot sole repair."""
from pathlib import Path

import numpy as np
import pytest

from shoe_poc.last import LastLoader, normalize_last
from shoe_poc.schemas import ShoeSpec

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.real_last


@pytest.fixture(scope="module")
def sole_case():
    from shoe_poc.geometry.sole import SoleGenerator
    spec = ShoeSpec.load(ROOT / "input/specs/shoe_spec.json")
    if not Path(spec.last.file).is_file():
        pytest.skip("Licensed real STEP unavailable; actual sole repair NOT_TESTED")
    last, _ = normalize_last(LastLoader().load(spec.last.file).shape, spec.last.coordinate_system)
    first, metadata = SoleGenerator().generate(last, spec)
    alternate = spec.model_copy(deep=True)
    alternate.sole.height_mm += 5
    second, _ = SoleGenerator().generate(last, alternate)
    return last, spec, first, second, metadata


def _section(shape, x):
    from build123d import Plane
    result = shape.intersect(Plane(origin=(x, 0, 0), z_dir=(1, 0, 0)))
    assert result is not None and result.wires()
    return result


def test_actual_sole_is_bounded_and_height_changes_bottom_datum(sole_case):
    last, spec, first, second, metadata = sole_case
    assert first.is_valid and second.is_valid
    assert len(first.solids()) == len(second.solids()) == 1
    assert 0 < first.volume < second.volume
    a, b, source = first.bounding_box(), second.bounding_box(), last.bounding_box()
    assert a.min.Z == pytest.approx(source.min.Z-spec.sole.height_mm, abs=1e-6)
    assert b.min.Z == pytest.approx(a.min.Z-5, abs=1e-6)
    assert b.max.Z == pytest.approx(a.max.Z, abs=1e-6)
    assert a.size.Y <= source.size.Y+2*spec.sole.profile_parameters.margin_mm+1
    assert metadata['algorithm_version'] == 'cad-lower-foot-periodic-rocker-sole-v7'


def test_actual_station_bottoms_and_round_radius_match_controls(sole_case):
    """Actual CAD rocker minima and independent trimmed-face curvature evidence."""
    last, spec, first, _, metadata = sole_case
    box = last.bounding_box()
    profile = spec.sole.profile_parameters
    bottom = box.min.Z-spec.sole.height_mm
    for x in [metadata['station_x_mm'][0], metadata['station_x_mm'][len(metadata['station_x_mm'])//2], metadata['station_x_mm'][-1]]:
        t = (x-box.min.X)/box.size.X
        smooth = lambda q: q*q*q*(10+q*(-15+6*q))
        heel = smooth(np.clip((.22-t)/.22, 0, 1))*profile.heel_rise_mm
        toe = smooth(np.clip((t-.68)/.32, 0, 1))*profile.toe_rise_mm
        assert _section(first, x).bounding_box().min.Z == pytest.approx(bottom+heel+toe, abs=1e-5)
    measured = metadata['bottom_round_validation']
    assert measured['geometry_status'] == 'PASS'
    assert measured['sample_count'] > 100
    assert not measured['measurement_errors']
    assert measured['minimum_measured_radius_mm'] == pytest.approx(profile.edge_radius_mm, abs=.02)
    assert measured['maximum_measured_radius_mm'] == pytest.approx(profile.edge_radius_mm, abs=.02)
    points = np.asarray([record['point_mm'] for record in measured['records']])
    assert points[:, 0].min() < box.min.X+.08*box.size.X
    assert points[:, 0].max() > box.max.X-.08*box.size.X
    assert points[:, 1].min() < 0 < points[:, 1].max()


def test_round_end_widths_are_monotone_without_tip_buttons(sole_case):
    _, _, first, _, metadata = sole_case
    box = first.bounding_box()
    heel = metadata['station_x_mm'][0]
    toe = metadata['station_x_mm'][-1]
    heel_widths = [_section(first, x).bounding_box().size.Y for x in np.linspace(box.min.X+.1, heel, 12)]
    toe_widths = [_section(first, x).bounding_box().size.Y for x in np.linspace(toe, box.max.X-.1, 12)]
    assert np.all(np.diff(heel_widths) > 0)
    assert np.all(np.diff(toe_widths) < 0)


def test_actual_skin_normals_are_continuous_at_longitudinal_knots(sole_case):
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.gp import gp_Pnt, gp_Vec
    _, _, first, _, metadata = sole_case
    measured_faces = 0
    for face in first.faces():
        if face.geom_type.name != 'BSPLINE':
            continue
        measured_faces += 1
        surface = BRepAdaptor_Surface(face.wrapped, True)
        u = (surface.FirstUParameter()+surface.LastUParameter())/2
        native = surface.BSpline()
        # Use actual native longitudinal knots of the periodic stock and
        # generated fillet surfaces, never source X as a surface parameter.
        knots = [native.VKnot(i) for i in range(2, native.NbVKnots())]
        for v in knots[::max(1, len(knots)//12)]:
            normals = []
            for offset in [-1e-7, 1e-7]:
                p, du, dv = gp_Pnt(), gp_Vec(), gp_Vec()
                surface.D1(u, v+offset, p, du, dv)
                normal = du.Crossed(dv)
                normal.Normalize()
                normals.append(np.array((normal.X(), normal.Y(), normal.Z())))
            assert np.dot(*normals) > 1-1e-7
    assert measured_faces >= 3


def test_actual_final_exterior_junction_and_native_stl_are_qualified(sole_case, tmp_path):
    from shoe_poc.geometry.upper import UpperGenerator
    from shoe_poc.geometry.assembler import volume_of
    from shoe_poc.validation.junction import validate_junction
    from shoe_poc.exporting import export_mesh
    from shoe_poc.validation import validate_mesh
    last, spec, stock, _, _ = sole_case
    _, cavity, opening, outer, _ = UpperGenerator().generate(last, spec)
    final = outer.fuse(stock).cut(cavity, last, opening).clean()
    assert final.is_valid and len(final.solids()) == 1
    assert all(volume_of(final.intersect(tool)) <= .01 for tool in (last, cavity, opening))
    result = validate_junction(final, cavity, opening, last.bounding_box(),
                               maximum_z_mm=stock.bounding_box().max.Z+1)
    assert result['geometry_status'] == 'PASS', result['regions']
    assert result['measurement_error_count'] == 0
    assert all(r['sample_count'] > 0 and r['geometry_status'] == 'PASS' for r in result['regions'].values())
    assert result['excluded_boundary_sample_count'] > 0
    assert all(r['cavity_distance_mm'] > result['boundary_exclusion_distance_mm']
               and r['opening_distance_mm'] > result['boundary_exclusion_distance_mm']
               for r in result['records'])
    export_mesh(final, tmp_path/'actual_junction.stl')
    mesh = validate_mesh(tmp_path/'actual_junction.stl')
    assert mesh['geometry_status'] == 'PASS'
    assert mesh['euler_number'] == 2
