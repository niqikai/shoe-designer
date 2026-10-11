"""Geometric regressions for concavity-preserving section reconstruction."""
import numpy as np
import pytest
from shoe_poc.geometry.envelope import _section_points, _contour_error, _normals


def test_exact_plane_sections_preserve_reentrant_boundary():
    from build123d import Face, Wire, Solid
    # Re-entrant notch: its area is deliberately smaller than its convex hull.
    contour=np.array([[-4,-3],[4,-3],[4,3],[1,3],[1,0],[-1,0],[-1,3],[-4,3]])
    face=Face(Wire.make_polygon([(0,y,z) for y,z in contour]))
    source=Solid.extrude(face,(12,0,0))
    ring=_section_points(source,6,512)
    yz=ring[:,1:]
    area=abs(np.sum(yz[:,0]*np.roll(yz[:,1],-1)-yz[:,1]*np.roll(yz[:,0],-1)))/2
    assert area==pytest.approx(42,abs=.03)
    assert _contour_error(yz,contour)<.04
    assert np.allclose(ring[:,0],6)


def test_contour_error_ignores_start_phase_but_detects_displacement():
    theta=np.linspace(0,2*np.pi,256,endpoint=False)
    circle=np.column_stack((np.cos(theta),np.sin(theta)))
    assert _contour_error(circle,np.roll(circle,17,axis=0))<1e-12
    assert _contour_error(circle,circle+[.5,0])==pytest.approx(.5,abs=1e-6)


def test_normals_include_longitudinal_slope():
    theta=np.linspace(0,2*np.pi,64,endpoint=False)
    rings=np.asarray([np.column_stack((np.full(64,x),(2+x/5)*np.cos(theta),(2+x/5)*np.sin(theta))) for x in [0,1,2,3]])
    normals=_normals(rings)
    assert np.allclose(np.linalg.norm(normals,axis=2),1)
    assert np.all(normals[:,:,0]<0)
    assert normals[1,0,1]>0
