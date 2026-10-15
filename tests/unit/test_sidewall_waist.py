"""Native curve constraints and mixed-degree surfaces for the waist branch."""
import numpy as np
import pytest
from build123d import Edge
from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.gp import gp_Pnt, gp_Vec

from shoe_poc.geometry.sole_transition import _waisted_wall_controls, _periodic_body


def _point_derivative(edge, parameter):
    curve = BRepAdaptor_Curve(edge.wrapped)
    point, derivative = gp_Pnt(), gp_Vec()
    curve.D1(parameter, point, derivative)
    return np.array(point.Coord()), np.array(derivative.Coord())


@pytest.mark.parametrize('theta', [0., .7, np.pi/2, 3*np.pi/2])
def test_native_quintic_waist_preserves_endpoints_and_derivatives(theta):
    radial = np.array((np.cos(theta), np.sin(theta), 0.))
    original = np.array([radial*10+(0,0,-5), radial*12+(0,0,5),
                         radial*11+(0,0,15), radial*11+(0,0,16)])
    controls = _waisted_wall_controls(original, theta, 2.)
    assert len(controls) == 6
    old, new = Edge.make_bezier(*original), Edge.make_bezier(*controls)
    for parameter in (0., 1.):
        old_point, old_derivative = _point_derivative(old, parameter)
        new_point, new_derivative = _point_derivative(new, parameter)
        np.testing.assert_allclose(new_point, old_point, atol=1e-13, rtol=0)
        np.testing.assert_allclose(new_derivative, old_derivative, atol=1e-13, rtol=0)
    for parameter in np.linspace(0, 1, 41):
        old_point, _ = _point_derivative(old, parameter)
        new_point, derivative = _point_derivative(new, parameter)
        np.testing.assert_allclose(new_point-old_point,
            -16*2.*parameter**2*(1-parameter)**2*radial, atol=1e-13, rtol=0)
        assert derivative[2] > 0
    np.testing.assert_allclose(_point_derivative(new,.5)[0]-_point_derivative(old,.5)[0],
                               -2.*radial, atol=1e-13, rtol=0)


def _station_body():
    parameters = np.linspace(0,2*np.pi,17)
    sections=[]
    for theta in parameters:
        radial=np.array((np.cos(theta),np.sin(theta),0.))
        height=8+.4*np.cos(theta)
        lower=np.array([radial*10+(0,0,-2),radial*10+(0,0,1),radial*11+(0,0,height-2/3),radial*11+(0,0,height)])
        band0=np.array([radial*11+(0,0,z) for z in (height,height+2/3,height+2-2/3,height+2)])
        band1=np.array([radial*11+(0,0,z) for z in (height+2,height+2+2/3,height+4-2/3,height+4)])
        plus=radial*11+(0,0,height+4);center=np.array((0,0,10))
        roof=np.array([plus+(center-plus)*q for q in (0,1/3,2/3,1)])
        sections.append([lower,band0,band1,roof])
    sections[-1]=sections[0]
    return parameters,np.asarray(sections)


def test_zero_waist_keeps_original_cubic_and_native_surface_definitions():
    cubic=np.arange(12,dtype=float).reshape(4,3)
    assert _waisted_wall_controls(cubic,.7,0.) is cubic
    parameters,sections=_station_body()
    old=_periodic_body(parameters,sections)
    zero=_periodic_body(parameters,sections,waist_mm=0.)
    for first,second in zip(old.faces()[:4],zero.faces()[:4]):
        a,b=BRep_Tool.Surface_s(first.wrapped),BRep_Tool.Surface_s(second.wrapped)
        assert a.UDegree()==b.UDegree()==3
        assert (a.NbUPoles(),a.NbVPoles())==(b.NbUPoles(),b.NbVPoles())
        for i in range(1,a.NbUPoles()+1):
            for j in range(1,a.NbVPoles()+1):
                assert a.Pole(i,j).Coord()==b.Pole(i,j).Coord()


def test_only_lower_wall_changes_degree_and_upper_band_stays_identical():
    parameters,sections=_station_body()
    old=_periodic_body(parameters,sections)
    new=_periodic_body(parameters,sections,waist_mm=2.)
    assert new.is_valid and len(new.solids())==1
    assert BRep_Tool.Surface_s(new.faces()[0].wrapped).UDegree()==5
    for first,second in zip(old.faces()[1:4],new.faces()[1:4]):
        a,b=BRep_Tool.Surface_s(first.wrapped),BRep_Tool.Surface_s(second.wrapped)
        assert a.UDegree()==b.UDegree()==3
        for i in range(1,a.NbUPoles()+1):
            for j in range(1,a.NbVPoles()+1):
                assert a.Pole(i,j).Coord()==b.Pole(i,j).Coord()
