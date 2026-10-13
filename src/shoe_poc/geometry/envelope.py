"""Smooth reconstruction of genuine CAD/plane sections, with bounded refinement.

No convex hull, overlapping slab or whole-last scale is used. Each patch must have one closed section boundary; unsupported topology fails explicitly.
"""
import numpy as np
from scipy.interpolate import CubicSpline

ALGORITHM_VERSION = 'cad-transverse-normal-loft-v2'


def _section_points(shape, x, count=128):
    from build123d import Plane
    section = shape.intersect(Plane(origin=(float(x), 0, 0), z_dir=(1, 0, 0)))
    if section is None or not section.wires():
        raise ValueError(f'Empty CAD section at X={x:.6f}')
    wires=section.wires()
    if len(wires)>1:
        from build123d import Face
        faces=[Face(w) for w in wires]
        outer_index=max(range(len(faces)),key=lambda i:faces[i].area)
        outer_face=faces[outer_index]
        for i,face in enumerate(faces):
            if i!=outer_index:
                difference=face.cut(outer_face)
                if difference is not None and sum(f.area for f in difference.faces())>1e-6:
                    raise ValueError(f'Disconnected CAD section contours at X={x:.6f}; another patch is required')
        wire=wires[outer_index]
    else:
        wire=wires[0]
    # Dense arc-length samples retain concave features of the exact CAD wire.
    from OCP.GCPnts import GCPnts_UniformAbscissa
    adaptor=wire.geom_adaptor()
    samples=GCPnts_UniformAbscissa(adaptor,max(512,int(wire.length/.15))+1,1e-6)
    if not samples.IsDone(): raise ValueError('CAD section arc-length sampling failed')
    dense=np.asarray([(adaptor.Value(samples.Parameter(i)).Y(),adaptor.Value(samples.Parameter(i)).Z())
                      for i in range(1,samples.NbPoints())])

    # Arc-length correspondence preserves re-entrant/concave boundary portions.
    # Anchor each loop at the positive-Y intersection with its mid-height ray.
    if np.sum(dense[:,0]*np.roll(dense[:,1],-1)-dense[:,1]*np.roll(dense[:,0],-1))<0:
        dense=dense[::-1]
    zmid=(dense[:,1].min()+dense[:,1].max())/2
    following=np.roll(dense,-1,axis=0)
    crossings=np.flatnonzero((dense[:,1]<=zmid)&(following[:,1]>zmid))
    if not len(crossings): raise ValueError(f'Cannot anchor CAD section at X={x}')
    anchor=max(crossings,key=lambda i:dense[i,0])
    fraction=(zmid-dense[anchor,1])/(following[anchor,1]-dense[anchor,1])
    positions=(anchor+fraction+np.arange(count)*len(dense)/count)%len(dense)
    i=np.floor(positions).astype(int); f=positions-i
    yz=dense[i]*(1-f[:,None])+dense[(i+1)%len(dense)]*f[:,None]
    return np.column_stack((np.full(count,x),yz))


def _loft(rings, poles=None):
    import time
    started=time.monotonic()
    from build123d import Edge, Wire, Solid, Vertex
    from OCP.BRepOffsetAPI import BRepOffsetAPI_ThruSections
    builder = BRepOffsetAPI_ThruSections(True, False, 1e-6)
    builder.SetMaxDegree(3)
    builder.CheckCompatibility(False)
    if poles is not None: builder.AddVertex(Vertex(*poles[0]).wrapped)
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
    for ring in rings:
        curve=Edge.make_spline(ring.tolist(), periodic=True, parameters=np.linspace(0,1,len(ring)+1).tolist()).geom_adaptor().BSpline()
        # Exact parameter subdivision retains the same smooth curve while
        # avoiding a single enormous spline face in downstream CAD Booleans.
        edges=[Edge(BRepBuilderAPI_MakeEdge(curve,i/8,(i+1)/8).Edge()) for i in range(8)]
        builder.AddWire(Wire(edges).wrapped)
    if poles is not None: builder.AddVertex(Vertex(*poles[1]).wrapped)
    print(f'loft start: {len(rings)} rings x {len(rings[0])} points',flush=True)
    builder.Build()
    print(f'loft built: {time.monotonic()-started:.2f}s',flush=True)
    if not builder.IsDone():
        raise ValueError('Smooth transverse loft failed')
    result = Solid(builder.Shape())
    if not result.is_valid or len(result.solids()) != 1 or result.volume <= 0:
        raise ValueError('Smooth transverse loft is not a valid single solid')
    print(f'loft validated: {time.monotonic()-started:.2f}s',flush=True)
    return result


def _contour_error(a,b):
    def directed(points,poly):
        starts=poly; vectors=np.roll(poly,-1,axis=0)-poly
        delta=points[:,None,:]-starts[None,:,:]
        fraction=np.clip(np.sum(delta*vectors[None,:,:],axis=2)/np.sum(vectors*vectors,axis=1)[None,:],0,1)
        return np.linalg.norm(delta-fraction[:,:,None]*vectors[None,:,:],axis=2).min(axis=1).max()
    return max(directed(a,b),directed(b,a))


def _loft_section_points(shape,x,count=512):
    """Sample the actual CAD surface/plane intersection by scalar root solving.

    Generated loft faces have monotone longitudinal V. Evaluating their real
    OCCT surfaces avoids a costly Boolean with a temporary plane; every sample
    still lies on the actual CAD face and X plane to numerical tolerance.
    """
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from scipy.optimize import brentq
    pieces=[]
    for face in shape.faces():
        surface=BRepAdaptor_Surface(face.wrapped,True)
        u0,u1=surface.FirstUParameter(),surface.LastUParameter()
        v0,v1=surface.FirstVParameter(),surface.LastVParameter()
        u=(u0+u1)/2
        if (surface.Value(u,v0).X()-x)*(surface.Value(u,v1).X()-x)>=0:
            continue
        piece=[]
        for u in np.linspace(u0,u1,max(8,count//8)+1):
            v=brentq(lambda v:surface.Value(float(u),v).X()-x,v0,v1,xtol=1e-11)
            p=surface.Value(float(u),v)
            piece.append((p.X(),p.Y(),p.Z()))
        pieces.append(np.asarray(piece))
    if not pieces: raise ValueError(f'No CAD loft face crosses X={x}')
    ordered=[pieces.pop(0)]
    while pieces:
        choices=[(np.linalg.norm(ordered[-1][-1]-piece[end]),i,end) for i,piece in enumerate(pieces) for end in [0,-1]]
        distance,i,end=min(choices)
        if distance>1e-4: raise ValueError('CAD loft section contains a gap between faces')
        piece=pieces.pop(i)
        ordered.append(piece if end==0 else piece[::-1])
    if np.linalg.norm(ordered[-1][-1]-ordered[0][0])>1e-4:
        raise ValueError('CAD loft section is open')
    return np.concatenate([piece[:-1] for piece in ordered])


def _normals(rings):
    xs = rings[:,0,0]
    dx = CubicSpline(xs, rings, axis=0)(xs, 1)
    theta = np.linspace(0, 2*np.pi, rings.shape[1]+1)
    around = CubicSpline(theta, np.concatenate((rings,rings[:,:1]),axis=1), axis=1, bc_type='periodic')(theta[:-1],1)
    normal = np.cross(around, dx)
    magnitudes=np.linalg.norm(normal,axis=2)
    if not np.isfinite(magnitudes).all() or np.any(magnitudes<1e-10):
        raise ValueError('Degenerate reconstruction surface normal')
    normal /= magnitudes[:,:,None]
    return normal


def _patch_envelopes(shape, clearance, thickness, radial_count=512, caps=False):
    box = shape.bounding_box(); xmin,xmax = box.min.X, box.max.X
    length=xmax-xmin
    # Three millimetre initial stations, with progressively smaller end intervals.
    ends = np.array([.01,.03,.08,.2,.5,1.,1.8])
    ends = ends[ends<length/4]
    xs=np.unique(np.r_[xmin+ends, np.arange(xmin+3,xmax-2,3), xmax-ends[::-1]])
    cache={}
    import os, hashlib
    from pathlib import Path
    debug_cache=os.environ.get('SHOE_SECTION_DEBUG_CACHE')
    cache_path=None
    if debug_cache:
        identity=repr((shape.volume,tuple(box.min),tuple(box.max),radial_count)).encode()
        cache_path=Path(debug_cache)/hashlib.sha256(identity).hexdigest()[:16]
        cache_path.mkdir(parents=True,exist_ok=True)
    def profile(x):
        key=float(x)
        if key not in cache:
            filename=cache_path/(key.hex()+'.npy') if cache_path else None
            if filename and filename.exists(): cache[key]=np.load(filename)
            else:
                cache[key]=_section_points(shape,key,radial_count)
                if filename: np.save(filename,cache[key])
        return cache[key]
    refinements=[]; fit_error=0.
    # Holdout true CAD sections test longitudinal interpolation, independently of
    # the input sections. Tight radial sampling is included in the error budget.
    for attempt in range(4):
        rings=np.asarray([profile(x) for x in xs])
        spline=CubicSpline(xs,rings[:,:,1:],axis=0)
        mids=(xs[:-1]+xs[1:])/2
        errors=np.asarray([_contour_error(spline(x),profile(x)[:,1:]) for x in mids])
        fit_error=float(errors.max())
        bad_indices=np.flatnonzero(errors>.20)
        bad=np.asarray([xs[i]+(xs[i+1]-xs[i])*f for i in bad_indices for f in [.25,.5,.75]])
        print(f'patch refinement {attempt}: {len(xs)} stations, max error {fit_error:.4f} mm, bad={len(bad)}, intervals={mids[bad_indices].tolist()}',flush=True)
        refinements.append({'pass':attempt,'station_count':len(xs),'max_holdout_error_mm':fit_error,'inserted_stations':len(bad) if attempt<3 else 0})
        if not len(bad): break
        if attempt==3: raise ValueError(f'Section reconstruction exceeds .25 mm fit target after three refinement passes: {fit_error}')
        xs=np.unique(np.r_[xs,bad])
    normals=_normals(rings)
    centers=np.asarray([rings[0,:,1:].mean(axis=0),rings[-1,:,1:].mean(axis=0)])
    def build(distance):
        profiles=rings+distance*normals
        poles=[(xmin-distance,*centers[0]),(xmax+distance,*centers[1])]
        if caps:
            first,last=profiles[0].copy(),profiles[-1].copy()
            first[:,0]=xmin-distance; last[:,0]=xmax+distance
            return _loft([first,*profiles,last])
        return _loft(profiles,poles)
    # Validate the actual OCCT zero-offset loft at independent held-out planes.
    # This is bounded sampled evidence, not a global Hausdorff certificate.
    reconstructed=build(0.)
    selected=np.unique(np.r_[np.linspace(0,len(mids)-1,min(16,len(mids))).astype(int),np.argsort(errors)[-8:]])
    cad_errors=[]
    for idx in selected:
        x=float(mids[idx])
        actual=_loft_section_points(reconstructed,x,radial_count)
        cad_errors.append({'x_mm':x,'error_mm':float(_contour_error(actual[:,1:],profile(x)[:,1:]))})
    actual_error=max(entry['error_mm'] for entry in cad_errors)
    print(f'actual CAD loft sampled fit: {actual_error:.4f} mm at {len(cad_errors)} planes',flush=True)
    if actual_error>.25:
        raise ValueError(f'Actual CAD loft sampled fit exceeds .25 mm: {actual_error}')
    cavity=build(clearance)
    outer=build(clearance+thickness)
    if cache_path:
        from build123d import export_step
        export_step(cavity,cache_path/'cavity.step')
        export_step(outer,cache_path/'outer.step')
    return cavity,outer,{
        'algorithm_version':ALGORITHM_VERSION,
        'method':'Exact CAD transverse plane sections, concavity-preserving periodic splines, smooth cubic loft and surface-normal displacement',
        'initial_station_spacing_mm':3.,'section_count':len(xs),'samples_per_section':radial_count,
        'fit_error_mm':actual_error,'interpolant_fit_error_mm':fit_error,'fit_target_mm':.25,
        'actual_cad_fit_samples':cad_errors,'station_x_mm':xs.tolist(),
        'fit_error_method':'Maximum symmetric sampled contour distance: actual zero-offset OCCT loft against exact source CAD sections at systematic and worst-interpolant midpoint planes; sampled, not global Hausdorff',
        'refinement_passes':len(refinements)-1,'adaptive_counts':refinements,
        'nominal_section_offset_difference_mm':thickness,
        'limitations':['Global CAD surface Hausdorff error and minimum wall thickness require independent verification.', 'Arc-length correspondence uses the positive-Y mid-height section crossing.'],
    }


def reconstructed_section_envelopes(shape, clearance, thickness, section_count=None, radial_count=512):
    """Reconstruct foot and ankle patches to retain the real ankle overhang."""
    from build123d import Solid, Plane, Axis
    box=shape.bounding_box()
    split_z=box.min.Z+box.size.Z*.66
    pad=10.
    lower_box=Solid.make_box(box.size.X+2*pad,box.size.Y+2*pad,split_z-box.min.Z+pad,
                            Plane(origin=(box.min.X-pad,box.min.Y-pad,box.min.Z-pad)))
    lower=shape.intersect(lower_box)
    upper=shape.cut(lower_box).rotate(Axis.Y,90)
    ci,co,lm=_patch_envelopes(lower,clearance,thickness,radial_count)
    ai,ao,am=_patch_envelopes(upper,clearance,thickness,radial_count,caps=True)
    ai=ai.rotate(Axis.Y,-90); ao=ao.rotate(Axis.Y,-90)
    print('fusing cavity patches',flush=True)
    cavity=ci.fuse(ai).clean()
    print('fusing outer patches',flush=True)
    outer=co.fuse(ao).clean()
    print('validating fused patches',flush=True)
    for name,result in [('cavity',cavity),('outer',outer)]:
        if not result.is_valid or len(result.solids())!=1 or result.volume<=0:
            raise ValueError(f'Merged {name} envelope is not a valid single solid')
    return cavity,outer,{
      'algorithm_version':ALGORITHM_VERSION,
      'method':'Two genuine CAD-section spline-loft patches: transverse foot and horizontal ankle, fused across overlapping offset caps',
      'patch_split_z_mm':split_z,'foot_patch':lm,'ankle_patch':am,
      'fit_error_mm':max(lm['fit_error_mm'],am['fit_error_mm']),
      'fit_target_mm':.25,'fit_error_method':'Actual zero-offset OCCT loft vs exact CAD contour at bounded independent planes; excludes nested ankle-top recess outlines; not global Hausdorff',
      'section_count':lm['section_count']+am['section_count'],
      'adaptive_counts':{'foot':lm['adaptive_counts'],'ankle':am['adaptive_counts']},
      'nominal_section_offset_difference_mm':thickness,
      'limitations':['Patch join must be verified inside the actual removed opening for each configuration; global minimum wall thickness requires measurement.'],
    }


def _local_arc_offset(foot, distance):
    """Run the selected native offset, rejecting failed or null CAD results.

    Native topology validity is not a global self-intersection certificate.
    Unsupported collisions must fail rather than selecting another algorithm.
    """
    from build123d import Solid
    from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeOffsetShape
    from OCP.BRepOffset import BRepOffset_Skin
    from OCP.GeomAbs import GeomAbs_Arc
    if distance == 0:
        return foot
    operation = BRepOffsetAPI_MakeOffsetShape()
    operation.PerformByJoin(foot.wrapped, distance, 1e-5, BRepOffset_Skin,
                            False, False, GeomAbs_Arc, True)
    if not operation.IsDone() or operation.Shape().IsNull():
        raise ValueError('Local CAD ARC offset failed to produce a nonnull shape')
    result = Solid(operation.Shape())
    if not result.is_valid or len(result.solids()) != 1:
        raise ValueError('Local CAD ARC offset is not a valid single solid')
    return result


def section_envelopes(shape, clearance, thickness, section_count=None, radial_count=512):
    """Exact local CAD offsets; the removed ankle retains the original source.

    Whole-last offsets fail on the engraved ankle. Splitting above the wearable
    domain makes OCCT ARC offsets robust without approximating the actual foot.
    The caller must verify that the continuation boundary is removed by collar.
    """
    from build123d import Solid,Plane
    box=shape.bounding_box()
    split_z=box.min.Z+.66*box.size.Z
    clip=Solid.make_box(box.size.X+20,box.size.Y+20,split_z-box.min.Z+10,
                       Plane(origin=(box.min.X-10,box.min.Y-10,box.min.Z-10)))
    foot=shape.intersect(clip)
    continuation_clip=Solid.make_box(box.size.X+20,box.size.Y+20,box.size.Z+10,
                       Plane(origin=(box.min.X-10,box.min.Y-10,split_z-2)))
    continuation=shape.intersect(continuation_clip)
    def offset(distance):
        result=_local_arc_offset(foot,distance)
        joined=result.fuse(continuation).clean()
        if not joined.is_valid or len(joined.solids())!=1:
            raise ValueError('Local CAD offset and exact ankle continuation do not form a valid single solid')
        return joined
    cavity=offset(clearance);outer=offset(clearance+thickness)
    return cavity,outer,{
        'algorithm_version':'local-exact-cad-offset-v3',
        'method':'Exact imported foot B-Rep, clipped above wearable domain, OCCT ARC normal offsets, exact-source cutaway ankle continuation',
        'patch_split_z_mm':split_z,
        'continuation_start_z_mm':split_z-2,
        'continuation_bbox_mm':{'min':list(continuation.bounding_box().min),'max':list(continuation.bounding_box().max)},
        'fit_error_mm':0.,'fit_target_mm':.25,
        'fit_error_method':'No source reconstruction: the original CAD foot surfaces are retained exactly. Offset clearance accuracy is separately measured by CAD face-normal probes.',
        'adaptive_counts':[], 'section_count':0,
        'nominal_section_offset_difference_mm':thickness,
        'cad_offset_tolerance_mm':1e-5,
        'ankle_continuation_clearance_status':'NOT_VERIFIED — source continuation must lie wholly in removed collar domain',
        'limitations':['No fit or wall-thickness claim is made for the cutaway ankle continuation.','Local exact offset was selected after valid smooth reconstruction patches proved impractical to fuse.'],
    }
