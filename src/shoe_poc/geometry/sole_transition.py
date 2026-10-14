"""Closed, circumferential actual-CAD guide band; no transverse cap roof ridge."""
import numpy as np
from scipy.interpolate import CubicSpline, PchipInterpolator

def _radial_guide(shape, center):
    from OCP.IntCurvesFace import IntCurvesFace_ShapeIntersector
    from OCP.gp import gp_Lin, gp_Pnt, gp_Dir
    from OCP.BRepGProp import BRepGProp_Face
    from OCP.gp import gp_Vec
    ray = IntCurvesFace_ShapeIntersector()
    ray.Load(shape.wrapped, 1e-07)

    def hit(theta, z):
        direction = np.array((np.cos(theta), np.sin(theta), 0.0))
        ray.Perform(gp_Lin(gp_Pnt(center[0], center[1], z), gp_Dir(*direction)), 0.0, 400.0)
        if not ray.IsDone() or not ray.NbPnt():
            raise ValueError('Actual radial CAD guide has no exit')
        index = min(range(1, ray.NbPnt() + 1), key=lambda i: ray.WParameter(i))
        p = ray.Pnt(index)
        point = np.array((p.X(), p.Y(), p.Z()))
        (p_normal, vector) = (gp_Pnt(), gp_Vec())
        BRepGProp_Face(ray.Face(index)).Normal(ray.UParameter(index), ray.VParameter(index), p_normal, vector)
        normal = np.array((vector.X(), vector.Y(), vector.Z()))
        normal /= np.linalg.norm(normal)
        radial = float(np.dot(normal, direction))
        if radial < 0.0001:
            raise ValueError('Non-star-shaped CAD guide requires template adaptation')
        tangent = np.r_[direction[:2] * (-normal[2] / radial), 1.0]
        return (point, normal, tangent)
    return hit

def _offset_guide(hit, theta, z, offset, rate):
    (point, normal, tangent) = hit(theta, z)
    delta = 0.001
    normal_derivative = (hit(theta, z + delta)[1] - hit(theta, z - delta)[1]) / (2 * delta)
    return (point + normal * offset, tangent + normal * rate + normal_derivative * offset)

def _periodic_body(parameters, sections, guide_normals=None):
    from build123d import Face, Wire, Shell, Solid
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.Geom import Geom_BSplineSurface
    from OCP.TColgp import TColgp_Array2OfPnt
    from OCP.TColStd import TColStd_Array1OfReal, TColStd_Array1OfInteger
    from OCP.gp import gp_Pnt
    derivative = CubicSpline(parameters, sections, axis=0, bc_type='periodic')(parameters, 1)
    if guide_normals is not None:
        for (i, normal) in enumerate(guide_normals):
            tangent = derivative[i, 1, 3]
            correction = -normal * np.dot(tangent, normal)
            # Move Q and both Q +/- D/3 handles together. Their shared
            # transverse tangent remains equal in every Hermite column.
            for (side, u) in [(1, 2), (1, 3), (2, 0), (2, 1)]:
                derivative[i, side, u] += correction
    poles = [sections[0]]
    for (i, h) in enumerate(np.diff(parameters)):
        poles.extend((sections[i] + derivative[i] * h / 3, sections[i + 1] - derivative[i + 1] * h / 3, sections[i + 1]))
    poles = np.asarray(poles)

    def real(values):
        a = TColStd_Array1OfReal(1, len(values))
        for (i, value) in enumerate(values, 1):
            a.SetValue(i, float(value))
        return a

    def integer(values):
        a = TColStd_Array1OfInteger(1, len(values))
        for (i, value) in enumerate(values, 1):
            a.SetValue(i, int(value))
        return a
    faces = []
    for side in range(sections.shape[1]):
        a = TColgp_Array2OfPnt(1, 4, 1, len(poles))
        for u in range(4):
            for v in range(len(poles)):
                a.SetValue(u + 1, v + 1, gp_Pnt(*poles[v, side, u]))
        surface = Geom_BSplineSurface(a, real([0, 1]), real(parameters), integer([4, 4]), integer([4] + [3] * (len(parameters) - 2) + [4]), 3, 3)
        surface.SetVPeriodic()
        # Reduce only redundant knots within 1e-8 mm; the constrained guide
        # rows retain C1, and the natural cubic rows can retain C2.
        for knot in range(surface.NbVKnots() - 1, 1, -1):
            if not surface.RemoveVKnot(knot, 1, 1e-08):
                surface.RemoveVKnot(knot, 2, 1e-08)
        maker = BRepBuilderAPI_MakeFace(surface, 1e-07)
        if not maker.IsDone():
            raise ValueError('Periodic sole band surface failed')
        faces.append(Face(maker.Face()))
    lower_edges = [e for e in faces[0].edges() if e.bounding_box().size.Z < 1e-05]
    if len(lower_edges) != 1:
        raise ValueError('Periodic band lower boundary is not one planar closed edge')
    faces.append(Face(Wire(lower_edges)))
    result = Solid(Shell(faces))
    if not result.is_valid or len(result.solids()) != 1 or result.volume <= 0:
        raise ValueError('Periodic band is not a valid positive single solid')
    return result

def _lower_outline_reference(metadata, box, profile):
    """CAD reference contour from true low section widths, with shared end tangents."""
    from build123d import Edge, Wire, Face, Solid
    xs = np.array(metadata['station_x_mm'])
    data = metadata['lower_foot_station_profiles']
    yl = np.array([d['left_y_mm'] + profile.sidewall_inset_mm for d in data])
    yr = np.array([d['right_y_mm'] - profile.sidewall_inset_mm for d in data])
    (left, right) = (CubicSpline(xs, yl), CubicSpline(xs, yr))
    edges = []

    def point(x, y):
        return np.array((x, y, 0.0))

    def bezier(controls):
        return Edge.make_bezier(*controls)
    extent = xs[0] - (box.min.X - profile.margin_mm + profile.sidewall_inset_mm)
    cy = (yl[0] + yr[0]) / 2
    half = (yr[0] - yl[0]) / 2
    heel = point(xs[0] - extent, cy)
    edges.append(bezier([heel, heel + (0, -half * 0.5, 0), point(xs[0] - extent / 3, yl[0] - left(xs[0], 1) * extent / 3), point(xs[0], yl[0])]))
    for (i, dx) in enumerate(np.diff(xs)):
        edges.append(bezier([point(xs[i], yl[i]), point(xs[i] + dx / 3, yl[i] + left(xs[i], 1) * dx / 3), point(xs[i + 1] - dx / 3, yl[i + 1] - left(xs[i + 1], 1) * dx / 3), point(xs[i + 1], yl[i + 1])]))
    extent = box.max.X + profile.margin_mm - profile.sidewall_inset_mm - xs[-1]
    cy = (yl[-1] + yr[-1]) / 2
    half = (yr[-1] - yl[-1]) / 2
    toe = point(xs[-1] + extent, cy)
    edges.append(bezier([point(xs[-1], yl[-1]), point(xs[-1] + extent / 3, yl[-1] + left(xs[-1], 1) * extent / 3), toe + (0, -half * 0.5, 0), toe]))
    edges.append(bezier([toe, toe + (0, half * 0.5, 0), point(xs[-1] + extent / 3, yr[-1] + right(xs[-1], 1) * extent / 3), point(xs[-1], yr[-1])]))
    for i in range(len(xs) - 2, -1, -1):
        dx = xs[i + 1] - xs[i]
        edges.append(bezier([point(xs[i + 1], yr[i + 1]), point(xs[i + 1] - dx / 3, yr[i + 1] - right(xs[i + 1], 1) * dx / 3), point(xs[i] + dx / 3, yr[i] + right(xs[i], 1) * dx / 3), point(xs[i], yr[i])]))
    extent = xs[0] - (box.min.X - profile.margin_mm + profile.sidewall_inset_mm)
    half = (yr[0] - yl[0]) / 2
    edges.append(bezier([point(xs[0], yr[0]), point(xs[0] - extent / 3, yr[0] - right(xs[0], 1) * extent / 3), heel + (0, half * 0.5, 0), heel]))
    # This extrusion is a CAD ray reference, not the generated sole body.
    reference = Solid.extrude(Face(Wire(edges)), (0, 0, 1))
    if not reference.is_valid:
        raise ValueError('Lower contour reference is not valid')
    return reference

def _rocker_cutter(box, bottom, profile):
    """Exact quintic smoothstep floor: constant datum between heel and toe."""
    from build123d import Edge, Wire, Face, Solid
    (x0, x1) = (box.min.X - 20, box.max.X + 20)
    heel = box.min.X + 0.22 * box.size.X
    toe = box.min.X + profile.toe_start_fraction * box.size.X
    y = box.min.Y - 20
    low = bottom - 40.0

    def p(x, z):
        return (x, y, z)
    edges = [Edge.make_line(p(x0, low), p(x1, low)), Edge.make_line(p(x1, low), p(x1, bottom + profile.toe_rise_mm)), Edge.make_line(p(x1, bottom + profile.toe_rise_mm), p(box.max.X, bottom + profile.toe_rise_mm))]
    edges.append(Edge.make_bezier(*[p(x, z) for (x, z) in zip([box.max.X - (box.max.X - toe) * i / 5 for i in range(6)], [bottom + profile.toe_rise_mm] * 3 + [bottom] * 3)]))
    edges.append(Edge.make_line(p(toe, bottom), p(heel, bottom)))
    edges.append(Edge.make_bezier(*[p(x, z) for (x, z) in zip([heel - (heel - box.min.X) * i / 5 for i in range(6)], [bottom] * 3 + [bottom + profile.heel_rise_mm] * 3)]))
    edges.extend([Edge.make_line(p(box.min.X, bottom + profile.heel_rise_mm), p(x0, bottom + profile.heel_rise_mm)), Edge.make_line(p(x0, bottom + profile.heel_rise_mm), p(x0, low))])
    return Solid.extrude(Face(Wire(edges)), (0, box.size.Y + 40, 0))

def _round_bottom(shape, cutter, radius):
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet
    from build123d import Compound
    operation = BRepAlgoAPI_Cut(shape.wrapped, cutter.wrapped)
    operation.Build()
    clipped = Compound.cast(operation.Shape())
    if not clipped.is_valid or len(clipped.solids()) != 1 or clipped.volume <= 0:
        raise ValueError('Rocker-trimmed stock is not one valid positive solid')
    if radius == 0:
        return clipped, 0
    floor_faces = [face for original in cutter.faces() for face in operation.Modified(original.wrapped) if face.ShapeType().name == 'TopAbs_FACE']
    wall_faces = [face for original in shape.faces() for face in operation.Modified(original.wrapped) if face.ShapeType().name == 'TopAbs_FACE']
    wall_faces.extend((face.wrapped for face in shape.faces() if not operation.IsDeleted(face.wrapped) and (not operation.Modified(face.wrapped))))
    floor_edges = [e for f in floor_faces for e in Compound.cast(f).edges()]
    wall_edges = [e for f in wall_faces for e in Compound.cast(f).edges()]
    edges = [e for e in clipped.edges() if any((e.wrapped.IsSame(k.wrapped) for k in floor_edges)) and any((e.wrapped.IsSame(k.wrapped) for k in wall_edges))]
    if not edges:
        raise ValueError('Actual bottom/wall intersection has no fillet edges')
    fillet = BRepFilletAPI_MakeFillet(clipped.wrapped)
    for edge in edges:
        fillet.Add(radius, edge.wrapped)
    fillet.Build()
    if not fillet.IsDone():
        raise ValueError('Configured native bottom round failed')
    result = Compound.cast(fillet.Shape())
    if not result.is_valid or len(result.solids()) != 1 or result.volume <= 0:
        raise ValueError('Native bottom round is not one valid solid')
    return (result, len(edges))

def periodic_transition(last, outer, bottom, metadata, spec):
    from .envelope import section_envelopes
    box = last.bounding_box()
    center = np.array((box.min.X + box.size.X * 0.5, 0.0, 20.0))
    if outer is None:
        (_, outer, _) = section_envelopes(last.solids()[0], spec.upper.clearance_mm, spec.upper.thickness_mm)
    outer_hit = _radial_guide(outer, center)
    reference = _lower_outline_reference(metadata, box, spec.sole.profile_parameters)
    stock_hit = _radial_guide(reference, center)
    heights = PchipInterpolator(metadata['station_x_mm'], [d['connection_z_mm'] for d in metadata['lower_foot_station_profiles']], extrapolate=False)
    parameters = np.linspace(0, 2 * np.pi, 513)
    sections = []
    guide_normals = []
    for theta in parameters[:-1]:
        (seed, _, _) = outer_hit(theta, 20.0)
        (first, last_x) = (metadata['station_x_mm'][0], metadata['station_x_mm'][-1])
        x = float(np.clip(seed[0], first, last_x))
        join = float(heights(x))

        def smooth(q):
            return q * q * q * (10 + q * (-15 + 6 * q))
        if x < first + 12.0:
            q = np.clip((x - first) / 12.0, 0.0, 1.0)
            join = float(heights(first)) + (join - float(heights(first))) * smooth(q)
        elif x > last_x - 12.0:
            q = np.clip((last_x - x) / 12.0, 0.0, 1.0)
            join = float(heights(last_x)) + (join - float(heights(last_x))) * smooth(q)
        h = 2.0
        points = []
        derivatives = []
        guide_normals.append(outer_hit(theta, join)[1])
        for (q, offset, rate) in [(-1, 0.249, -0.375), (0, -0.001, 0.0), (1, -0.251, -0.375)]:
            (point, tangent) = _offset_guide(outer_hit, theta, join + q * h, offset, rate)
            points.append(point)
            derivatives.append(tangent)
        (start, _, _) = stock_hit(theta, 0.5)
        start[2] = bottom - 5.0
        (minus, middle, plus) = points
        (dm, dc, dp) = derivatives
        bulge = spec.sole.profile_parameters.sidewall_bulge_mm
        lower_control = start + (0, 0, 3.0)
        if bulge:
            radial = np.array((np.cos(theta), np.sin(theta), 0.))
            lower_control = start + radial*bulge
            lower_control[2] = start[2]+(minus[2]-start[2])*.35
        lower = np.array([start, lower_control, minus - dm * h / 3, minus])
        band0 = np.array([minus, minus + dm * h / 3, middle - dc * h / 3, middle])
        band1 = np.array([middle, middle + dc * h / 3, plus - dp * h / 3, plus])
        roof = np.array([plus + (center - plus) * q for q in (0, 1 / 3, 2 / 3, 1)])
        sections.append(np.array([lower, band0, band1, roof]))
    sections.append(sections[0].copy())
    guide_normals.append(guide_normals[0].copy())
    body = _periodic_body(parameters, np.asarray(sections), guide_normals)
    cutter = _rocker_cutter(box, bottom, spec.sole.profile_parameters)
    (result, round_edge_count) = _round_bottom(body, cutter, spec.sole.profile_parameters.edge_radius_mm)
    return (result, {'method': 'One closed periodic lower-foot contour stock; actual CAD exterior band, exact quintic rocker cut and native bottom round', 'angular_station_count': len(parameters) - 1, 'band_half_height_mm': 2.0, 'join_inset_mm': 0.001, 'upper_guide_inset_mm': 0.251, 'edge_radius_target_mm': spec.sole.profile_parameters.edge_radius_mm, 'native_bottom_fillet_radius_mm': spec.sole.profile_parameters.edge_radius_mm, 'native_bottom_fillet_edge_count': round_edge_count, 'guide_z_method': 'Actual radial exterior seed at 20 mm and blended source-floor guide; three true CAD exterior rings', 'end_height_guide_blend_mm': 12.0, 'lower_outline_method': 'Measured lower-foot widths, cubic side contours and heel/toe caps with matched endpoint tangents; margin minus sidewall inset', 'sidewall_bulge_mm': spec.sole.profile_parameters.sidewall_bulge_mm, 'sidewall_control_method': 'Positive radial lower-wall Bezier control displacement, at 35% of vertical span; zero preserves the original control', 'toe_start_fraction': spec.sole.profile_parameters.toe_start_fraction, 'representation_knot_reduction_tolerance_mm': 1e-08, 'normal_method': 'Actual oriented CAD normals, radial dR/dZ, finite CAD normal derivatives at plus/minus 0.001 mm; common Q and Q plus/minus D/3 longitudinal handles preserve shared G1 while constraining the guide tangent plane'})
