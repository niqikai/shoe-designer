"""A native, doubly curved collar cutter for the explicit design refinement.

The original M1 quintic roof remains in ``upper``. This optional surface changes
the real entry boundary; it neither changes the source last nor its cavity.
"""
from __future__ import annotations

import numpy as np


def _quintic_values(first, last, length, first_slope=0., last_slope=0.):
    """Bezier height controls with specified endpoint slopes and zero curvature."""
    return np.asarray([first, first+first_slope*length/5,
                       first+2*first_slope*length/5,
                       last-2*last_slope*length/5,
                       last-last_slope*length/5, last], dtype=float)


def sculpted_collar_tool(last_shape, outer, spec):
    """Cut a flowing X profile and a transverse saddle from the actual exterior.

    A quintic longitudinal surface lowers the sides, lifts the heel, and gives
    the front roof a bounded descending slope derived from the real vamp. A
    quadratic transverse field provides actual three-dimensional rim variation.
    The cutter's front stays above the true exterior by a numerical separation;
    its vertical end face must lie outside the retained shell.
    """
    from build123d import Edge, Wire, Face, Shell, Solid, Plane
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.Geom import Geom_BSplineSurface
    from OCP.TColgp import TColgp_Array2OfPnt
    from OCP.TColStd import TColStd_Array1OfInteger, TColStd_Array1OfReal
    from OCP.gp import gp_Pnt

    box = last_shape.bounding_box()
    exterior_box = outer.bounding_box()
    opening_spec = spec.upper.opening_parameters
    pad = spec.upper.clearance_mm+spec.upper.thickness_mm+10.
    x0 = box.min.X+box.size.X*opening_spec.opening_start_x_fraction
    x1 = box.min.X+box.size.X*opening_spec.opening_end_x_fraction
    if opening_spec.opening_start_x_fraction == 0:
        x0 -= pad
    height = box.min.Z+opening_spec.collar_height_mm
    transition = min(opening_spec.transition_mm, (x1-x0)/2)
    front_start = x1-transition
    rear_end = min(box.min.X+.38*box.size.X, front_start)
    if rear_end <= x0:
        raise ValueError('Sculpted collar has no rear profile interval')
    heel_lift = opening_spec.heel_lift_mm
    side_dip = opening_spec.side_dip_mm

    def highest(x):
        section = outer.intersect(Plane(origin=(x, 0, 0), z_dir=(1, 0, 0)))
        if section is None or not section.vertices():
            raise ValueError('Sculpted collar front has no actual exterior section')
        return section.bounding_box().max.Z

    front_height = max(height+1e-5, highest(x1)+1e-5)
    step = min(.25, transition/20)
    vamp_slope = (highest(x1+step)-highest(x1-step))/(2*step)
    front_slope = -min(.25, max(0., -vamp_slope)*.35)
    if opening_spec.opening_start_x_fraction > 0:
        start_height = max(height+heel_lift, highest(x0)+1e-5)
    else:
        start_height = height+heel_lift

    intervals = [(x0, rear_end, start_height, height, 0., 0., 0., 1.)]
    if front_start > rear_end+1e-7:
        intervals.append((rear_end, front_start, height, height, 0., 0., 1., 1.))
    intervals.append((front_start, x1, height, front_height, 0., front_slope, 1., 0.))
    longitudinal = []
    breaks = [x0]
    # This scale governs a physical transverse height field, not a width scale
    # on the last. It is recorded so this template-specific choice is reviewable.
    transverse_scale = max(25., exterior_box.size.Y*.35)
    span = max(abs(exterior_box.min.Y), abs(exterior_box.max.Y))+pad
    for a, b, z0, z1, slope0, slope1, w0, w1 in intervals:
        z_controls = _quintic_values(z0, z1, b-a, slope0, slope1)
        weight_controls = _quintic_values(w0, w1, b-a)
        rows = []
        for x, z, weight in zip(np.linspace(a, b, 6), z_controls, weight_controls):
            amplitude = side_dip*weight*(span/transverse_scale)**2
            rows.append([(x, -span, z-amplitude), (x, 0., z+amplitude),
                         (x, span, z-amplitude)])
        longitudinal.extend(rows if not longitudinal else rows[1:])
        breaks.append(b)

    def array(kind, values):
        result = kind(1, len(values))
        for index, value in enumerate(values, 1):
            result.SetValue(index, value)
        return result

    poles = TColgp_Array2OfPnt(1, len(longitudinal), 1, 3)
    for i, row in enumerate(longitudinal, 1):
        for j, point in enumerate(row, 1):
            poles.SetValue(i, j, gp_Pnt(*point))
    surface = Geom_BSplineSurface(
        poles, array(TColStd_Array1OfReal, breaks),
        array(TColStd_Array1OfReal, [0., 1.]),
        array(TColStd_Array1OfInteger, [6]+[5]*(len(breaks)-2)+[6]),
        array(TColStd_Array1OfInteger, [3, 3]), 5, 2)
    for index in range(surface.NbUKnots()-1, 1, -1):
        surface.RemoveUKnot(index, 3, 1e-8)
    roof_maker = BRepBuilderAPI_MakeFace(surface, 1e-7)
    if not roof_maker.IsDone():
        raise ValueError('Sculpted collar native surface failed')
    roof = Face(roof_maker.Face())
    top = exterior_box.max.Z+pad
    faces = [roof]
    for edge in roof.edges():
        curve = BRepAdaptor_Curve(edge.wrapped)
        p0, p1 = curve.Value(curve.FirstParameter()), curve.Value(curve.LastParameter())
        a, b = (p0.X(), p0.Y(), p0.Z()), (p1.X(), p1.Y(), p1.Z())
        at, bt = (a[0], a[1], top), (b[0], b[1], top)
        faces.append(Face(Wire([edge, Edge.make_line(b, bt),
                                Edge.make_line(bt, at), Edge.make_line(at, a)])))
    faces.append(Face(Wire.make_polygon([(x0, -span, top), (x1, -span, top),
                                         (x1, span, top), (x0, span, top)])))
    opening = Solid(Shell(faces))
    if not opening.is_valid or len(opening.solids()) != 1 or opening.volume <= 0:
        raise ValueError('Sculpted collar cutter is not one valid positive solid')
    return opening, {
        'method': 'Actual exterior cut by a quintic longitudinal / quadratic transverse native CAD saddle roof',
        'collar_style': 'sculpted_3d', 'transition_mm': transition,
        'requested_transition_mm': opening_spec.transition_mm,
        'front_transition_x_mm': [front_start, x1],
        'front_transition_z_mm': [height, front_height],
        'opening_x_range_mm': [x0, x1], 'rear_profile_end_x_mm': rear_end,
        'center_rear_roof_z_mm': start_height, 'heel_lift_mm': heel_lift,
        'side_dip_mm': side_dip, 'transverse_height_field_scale_mm': transverse_scale,
        'actual_vamp_max_z_slope': vamp_slope, 'front_roof_slope': front_slope,
        'front_slope_method': '35% of actual descending exterior section-height derivative, capped at 0.25 absolute dZ/dX',
        'roof_degrees': [5, 2], 'roof_longitudinal_breaks_mm': breaks,
        'limitations': ['The height field changes the entry rim only; actual rim, cavity entry and final mesh require independent checks.',
                        'The transverse scale and front-slope cap are experimental template design controls, not fit or manufacturing criteria.'],
    }
