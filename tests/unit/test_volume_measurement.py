"""Real CAD volume checks with analytic and independent 1D references."""
import json
import math

import numpy as np
import pytest
from build123d import Axis, Compound, Edge, Face, Plane, Solid, Wire

from shoe_poc.validation.volume import measure_cad_volume


@pytest.mark.parametrize("reference", ["point", "xy_plane"])
@pytest.mark.parametrize("kind", ["box", "sphere"])
def test_translated_analytic_solid_volume_and_declared_accuracy(kind, reference):
    shape = Solid.make_box(10, 20, 30) if kind == "box" else Solid.make_sphere(10)
    expected = 6000 if kind == "box" else 4*math.pi*10**3/3
    shape = shape.translate((23, -17, 5))
    measured = measure_cad_volume(shape, reference=reference, refine=True)
    assert measured["volume_mm3"] == pytest.approx(expected, rel=1e-12)
    assert measured["refinement"]["volume_mm3"] == pytest.approx(expected, rel=1e-12)
    assert measured["requested_relative_error"] == 1e-12
    assert measured["refinement"]["requested_relative_error"] == 1e-10
    assert measured["bspline_span_splitting"] is True
    assert measured["estimated_relative_quadrature_error"] >= 0
    assert measured["absolute_accuracy"] == "NOT_VERIFIED"
    json.dumps(measured, allow_nan=False)


@pytest.mark.parametrize("reference", ["point", "xy_plane"])
def test_many_knot_revolution_matches_independent_meridian_integral(reference):
    """Default mass is inaccurate here; integrate pi*r²*dz independently."""
    from scipy.integrate import quad
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.gp import gp_Pnt, gp_Vec
    zs = np.linspace(0, 40, 81)
    radii = 10+2*np.sin(zs*.8)
    curve = Edge.make_spline([(float(r), 0, float(z)) for r, z in zip(radii, zs)])
    boundary = Wire([curve, Edge.make_line((radii[-1], 0, 40), (0, 0, 40)),
                     Edge.make_line((0, 0, 40), (0, 0, 0)),
                     Edge.make_line((0, 0, 0), (radii[0], 0, 0))])
    shape = Solid.revolve(Face(boundary), 360, Axis.Z)
    assert shape.is_valid and len(shape.solids()) == 1
    meridian = BRepAdaptor_Curve(curve.wrapped)

    def cross_section_volume(parameter):
        point, derivative = gp_Pnt(), gp_Vec()
        meridian.D1(parameter, point, derivative)
        return math.pi*point.X()**2*derivative.Z()

    spline = meridian.BSpline()
    expected, reference_error = quad(cross_section_volume, meridian.FirstParameter(),
        meridian.LastParameter(), epsabs=1e-7, epsrel=1e-12, limit=1000,
        points=[spline.Knot(i) for i in range(1, spline.NbKnots()+1)])
    assert reference_error < 1e-7
    assert abs(shape.volume-expected)/expected > 1e-4
    measured = measure_cad_volume(shape, reference=reference)
    assert measured["volume_mm3"] == pytest.approx(expected, rel=1e-8)


def test_material_void_and_distinct_compound_solid_volumes_are_preserved():
    hollow = Solid.make_box(10, 10, 10).cut(
        Solid.make_box(4, 4, 4, Plane(origin=(3, 3, 3))))
    second = Solid.make_box(10, 10, 10, Plane(origin=(20, 0, 0)))
    assert measure_cad_volume(hollow)["volume_mm3"] == pytest.approx(936, rel=1e-12)
    assert measure_cad_volume(Compound([hollow, second]))["volume_mm3"] == pytest.approx(1936, rel=1e-12)


def test_open_surface_cannot_be_measured_as_closed_material():
    face = Face(Wire.make_polygon([(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0)]))
    assert face.is_valid
    with pytest.raises(ValueError, match="closed solid"):
        measure_cad_volume(face)
