"""Exact local upper offsets with a continuously curved entry rim."""


def _volume(shape):
    return 0. if shape is None else sum(s.volume for s in shape.solids())


def _collar_tool(last_shape, outer, spec):
    from build123d import Edge, Wire, Face, Solid, Plane
    box=last_shape.bounding_box()
    opening_spec=spec.upper.opening_parameters
    x0=box.min.X+box.size.X*opening_spec.opening_start_x_fraction
    x1=box.min.X+box.size.X*opening_spec.opening_end_x_fraction
    pad=spec.upper.clearance_mm+spec.upper.thickness_mm+10
    if opening_spec.opening_start_x_fraction==0:
        x0-=pad
    height=box.min.Z+opening_spec.collar_height_mm
    transition=min(opening_spec.transition_mm,(x1-x0)/2)
    y=outer.bounding_box().min.Y-pad
    top=outer.bounding_box().max.Z+pad
    def end_height(x):
        section=outer.intersect(Plane(origin=(x,0,0),z_dir=(1,0,0)))
        if section is None or not section.vertices():
            return top
        # Numerical separation, rather than a 1 mm lift that moves the actual
        # opening rearward when the transition is lengthened.
        return max(height+1e-5,section.bounding_box().max.Z+1e-5)
    def blend(a,b,z0,z1):
        # Quintic Bezier has zero first and second height derivatives at both
        # ends, joining the level collar without a tangent/curvature break.
        controls=[(a+(b-a)*i/5,y,z0 if i<3 else z1) for i in range(6)]
        return Edge.make_bezier(*controls)
    edges=[]
    start_z=height
    level_start=x0
    if opening_spec.opening_start_x_fraction>0:
        start_z=end_height(x0)
        edges.append(blend(x0,x0+transition,start_z,height))
        level_start=x0+transition
    front_start=x1-transition
    if front_start>level_start:
        edges.append(Edge.make_line((level_start,y,height),(front_start,y,height)))
    front_height=end_height(x1)
    edges.append(blend(front_start,x1,height,front_height))
    edges.extend([
        Edge.make_line((x1,y,front_height),(x1,y,top)),
        Edge.make_line((x1,y,top),(x0,y,top)),
        Edge.make_line((x0,y,top),(x0,y,start_z)),
    ])
    roof=Face(Wire(edges))
    opening=Solid.extrude(roof,(0,outer.bounding_box().size.Y+2*pad,0))
    if not opening.is_valid or len(opening.solids())!=1:
        raise ValueError('Curved collar cutter is invalid')
    return opening,{'method':'Closed 3D rim from exact upper intersection with a quintic Bezier collar roof',
                    'transition_mm':transition,'requested_transition_mm':opening_spec.transition_mm,
                    'front_transition_x_mm':[front_start,x1],
                    'front_transition_z_mm':[height,front_height],
                    'opening_x_range_mm':[x0,x1]}


class UpperGenerator:
    def generate(self,last_shape,spec):
        from build123d import Solid,Plane
        from .envelope import section_envelopes
        clearance=spec.upper.clearance_mm
        thickness=spec.upper.thickness_mm
        source=last_shape.solids()[0]
        cavity,outer,envelope_metadata=section_envelopes(source,clearance,thickness)
        last_outside_cavity=_volume(source.cut(cavity))
        cavity_outside_outer=_volume(cavity.cut(outer))
        last_outside_outer=_volume(source.cut(outer))
        if max(last_outside_cavity,cavity_outside_outer,last_outside_outer)>.01:
            raise ValueError(f'Envelope containment failed: last outside cavity={last_outside_cavity}, cavity outside outer={cavity_outside_outer}, last outside outer={last_outside_outer} mm3')
        opening,collar_metadata=_collar_tool(last_shape,outer,spec)
        rim_metrics=_collar_rim_metrics(outer,opening)
        if not rim_metrics['one_closed_entry_loop']:
            raise ValueError('Curved collar does not have exactly one closed exterior entry rim')
        expected_front=collar_metadata['opening_x_range_mm'][1]
        front_error=abs(rim_metrics['front_x_mm']-expected_front)
        if front_error>.25:
            raise ValueError(f'Actual collar front moved {front_error:.6f} mm from the configured front')
        # Experimental CAD shape tolerances, separate from manufacturing limits.
        if (rim_metrics['unmeasurable_tangent_joints']
                or rim_metrics['maximum_tangent_discontinuity_degrees'] is None
                or rim_metrics['maximum_tangent_discontinuity_degrees']>.5
                or rim_metrics['maximum_curve_endpoint_vertex_error_mm']>.25):
            raise ValueError('Actual collar rim has a corner or an unqualified CAD tangent joint')
        collar_metadata.update({'actual_rim':rim_metrics,'actual_front_position_error_mm':front_error})
        collar_metadata['cad_shape_tolerances']={'front_position_mm':.25,
                                                 'endpoint_curve_vertex_error_mm':.25,
                                                 'tangent_discontinuity_degrees':.5}
        entry_common=cavity.intersect(opening)
        entry_count=len(entry_common.solids()) if entry_common is not None else 0
        if entry_count!=1 or _volume(entry_common)<=1.:
            raise ValueError('Curved collar does not reach exactly one connected cavity entry region')
        collar_metadata['cavity_entry_connected_components']=entry_count
        collar_metadata['cavity_entry_common_volume_mm3']=_volume(entry_common)
        upper=outer.cut(cavity,opening).clean()
        if not upper.is_valid or len(upper.solids())!=1:
            raise ValueError('Upper is not a valid single solid after the curved collar cut')
        # No artificial partition cap or zero-clearance continuation may survive
        # into the wearable shell. Unsupported collar configurations fail here.
        threshold=envelope_metadata['patch_split_z_mm']-(clearance+thickness)-1.
        b=outer.bounding_box()
        mask=Solid.make_box(b.size.X+2,b.size.Y+2,b.size.Z+2,
                            Plane(origin=(b.min.X-1,b.min.Y-1,threshold)))
        cap_residual=_volume(upper.intersect(mask))
        if cap_residual>.01:
            raise ValueError(f'REQUIRES_TEMPLATE_ADAPTATION: collar exposes {cap_residual:.6f} mm3 of artificial cap/ankle continuation')
        return upper,cavity,opening,outer,{
            'method':'Exact local CAD normal offsets, Boolean cavity and quintic collar-roof subtraction',
            'envelope':envelope_metadata,'collar':collar_metadata,
            'last_outside_cavity_volume_mm3':last_outside_cavity,
            'cavity_outside_outer_volume_mm3':cavity_outside_outer,
            'last_outside_outer_volume_mm3':last_outside_outer,
            'nominal_thickness_mm':thickness,'clearance_mm':clearance,
            'collar_height_above_last_base_mm':spec.upper.opening_parameters.collar_height_mm,
            'opening_x_range_mm':collar_metadata['opening_x_range_mm'],
            'removed_partition_guard_z_mm':threshold,
            'retained_partition_region_volume_mm3':cap_residual,
            'minimum_wall_thickness_status':'NOT_VERIFIED',
        }


def _collar_rim_metrics(outer, opening):
    """Measure the actual exterior/cutter intersection, independently of roof data.

    This finite CAD curve assessment establishes loop topology and observes
    tangent agreement at CAD subdivisions. It does not certify global curvature
    continuity, appearance, insertion or wearability.
    """
    import math
    import numpy as np
    from build123d import Compound, Wire
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Section
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.gp import gp_Pnt, gp_Vec
    from OCP.TopExp import TopExp
    from OCP.BRep import BRep_Tool

    section = BRepAlgoAPI_Section(outer.wrapped, opening.wrapped, False)
    section.Approximation(True)
    section.Build()
    if not section.IsDone():
        raise ValueError('Actual collar rim CAD section failed')
    rim = Compound(section.Shape())
    edges = list(rim.edges())
    if not edges:
        raise ValueError('Actual collar has no exterior entry rim')
    wires = Wire.combine(edges, tol=1e-5)
    # Read exact parameter endpoints directly. build123d's normalized position
    # path integrates arc length even at 0/1 and can overshoot a trimmed spline
    # endpoint by hundredths of a millimetre, producing false unmatched joints.
    def cad_endpoint(edge, endpoint, with_tangent=True):
        curve = BRepAdaptor_Curve(edge.wrapped)
        parameter = curve.FirstParameter() if endpoint == 0 else curve.LastParameter()
        point = curve.Value(parameter)
        # Topological shared vertices are authoritative for connectivity. The
        # source STEP can have nonzero edge/vertex tolerances, so trimmed 3D
        # curve endpoints need not coincide with those canonical vertices.
        vertex = (TopExp.FirstVertex_s(edge.wrapped, False) if endpoint == 0
                  else TopExp.LastVertex_s(edge.wrapped, False))
        canonical = BRep_Tool.Pnt_s(vertex)
        p = np.asarray([canonical.X(), canonical.Y(), canonical.Z()])
        actual = np.asarray([point.X(), point.Y(), point.Z()])
        if not with_tangent:
            return p, None, float(np.linalg.norm(actual-p))
        tangent = gp_Vec()
        curve.D1(parameter, gp_Pnt(), tangent)
        t = np.asarray([tangent.X(), tangent.Y(), tangent.Z()])
        length = np.linalg.norm(t)
        if length <= 1e-12 or not np.isfinite(length):
            raise ValueError('Undefined CAD endpoint tangent')
        return p, t/length, float(np.linalg.norm(actual-p))

    endpoint_samples = [(index, t, cad_endpoint(edge, t, with_tangent=False)[0])
                        for index, edge in enumerate(edges) for t in (0., 1.)]
    angles, unmeasurable = [], []
    visited = set()
    for index, endpoint, point in endpoint_samples:
        matches = [(other, t) for other, t, q in endpoint_samples
                   if (other, t) != (index, endpoint) and np.linalg.norm(point-q) <= 1e-4]
        pair = tuple(sorted(((index, endpoint), *matches)))
        if pair in visited:
            continue
        visited.add(pair)
        if len(matches) != 1:
            unmeasurable.append({'edge_index': index, 'endpoint': endpoint,
                                 'reason': 'Branch or unmatched CAD endpoint',
                                 'matching_endpoints': len(matches)})
            continue
        other, other_endpoint = matches[0]
        try:
            tangent = cad_endpoint(edges[index], endpoint)[1]
            other_tangent = cad_endpoint(edges[other], other_endpoint)[1]
            # Tangents pointing away from a smooth shared endpoint oppose each
            # other. This catches a doubled-back cusp as well as ordinary corners.
            tangent *= 1 if endpoint == 0 else -1
            other_tangent *= 1 if other_endpoint == 0 else -1
            angle = math.degrees(math.acos(float(np.clip(-np.dot(tangent, other_tangent), -1, 1))))
            if not math.isfinite(angle):
                raise ValueError('Nonfinite CAD tangent')
            angles.append({'edge_indices': [index, other], 'point_mm': point.tolist(),
                           'tangent_discontinuity_degrees': angle})
        except (ValueError, RuntimeError) as exc:
            unmeasurable.append({'edge_index': index, 'endpoint': endpoint, 'reason': str(exc)})
    box = rim.bounding_box()
    return {
        'method': 'Actual OCCT B-Rep exterior/cutter section and endpoint CAD tangents',
        'entry_loop_count': len(wires),
        'closed_loop_count': sum(bool(wire.is_closed) for wire in wires),
        'one_closed_entry_loop': len(wires) == 1 and bool(wires[0].is_closed),
        'rim_edge_count': len(edges), 'rim_length_mm': sum(edge.length for edge in edges),
        'front_x_mm': box.max.X, 'rear_x_mm': box.min.X,
        'maximum_tangent_discontinuity_degrees': max((a['tangent_discontinuity_degrees'] for a in angles), default=None),
        'measured_tangent_joints': len(angles), 'tangent_joints': angles,
        'maximum_curve_endpoint_vertex_error_mm': max(cad_endpoint(edge, t, with_tangent=False)[2] for edge in edges for t in (0., 1.)),
        'unmeasurable_tangent_joints': unmeasurable,
        'global_curvature_continuity_status': 'NOT_VERIFIED',
        'visual_acceptance_status': 'NOT_VERIFIED',
    }
