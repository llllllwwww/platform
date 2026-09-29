"""Fit a robust periodic cross-section surface to measured tunnel points.
A short, nearly straight tunnel is modeled as a smooth star-shaped radial surface.
No fixed diameter, circular cross section, metric scale, or unseen geological detail is assumed.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
from scipy.interpolate import BSpline
from scipy.ndimage import label
from scipy.spatial import cKDTree
from scipy.linalg import solve

ROOT=Path(__file__).resolve().parent

def write_ply(path,vertices,faces):
    with Path(path).open('wb') as f:
        f.write((f'ply\nformat binary_little_endian 1.0\nelement vertex {len(vertices)}\nproperty float x\nproperty float y\nproperty float z\nelement face {len(faces)}\nproperty list uchar int vertex_index\nend_header\n').encode())
        f.write(np.asarray(vertices,dtype='<f4').tobytes())
        block=np.empty(len(faces),dtype=[('n','u1'),('v','<i4',(3,))]);block['n']=3;block['v']=faces
        f.write(block.tobytes())

def topology(vertices,faces):
    edges=np.sort(np.concatenate([faces[:,[0,1]],faces[:,[1,2]],faces[:,[2,0]]]),axis=1)
    unique,count=np.unique(edges,axis=0,return_counts=True)
    tri=vertices[faces];e1=tri[:,1]-tri[:,0];e2=tri[:,2]-tri[:,0]
    lengths=np.stack([np.linalg.norm(e1,axis=1),np.linalg.norm(e2,axis=1),np.linalg.norm(tri[:,2]-tri[:,1],axis=1)],axis=1)
    area2=np.linalg.norm(np.cross(e1,e2),axis=1)
    return {'vertices':len(vertices),'triangles':len(faces),'boundary_edges':int((count==1).sum()),
      'nonmanifold_edges':int((count>2).sum()),'degenerate_faces':int((area2<1e-12).sum()),
      'edge_ratio_p95':float(np.quantile(lengths.max(1)/np.maximum(lengths.min(1),1e-15),.95)),
      'euler_characteristic':int(len(vertices)-len(unique)+len(faces))}

def coordinate_frame(cameras):
    centers=np.array([c['center'] for c in cameras]);origin=np.median(centers,axis=0)
    if len(centers)<3 or not np.isfinite(centers).all():raise ValueError('At least three finite camera centers required')
    _,singular,vt=np.linalg.svd(centers-centers.mean(0),full_matrices=False)
    if singular[0]<1e-8:raise ValueError('No measurable camera translation for an axial prior')
    if singular[1]/singular[0]>.15:raise ValueError('Trajectory is too curved for the straight-axis model; use segmented centerline fitting')
    t=vt[0];t*=1 if (centers[-1]-centers[0])@t>=0 else -1
    up=-np.mean([np.asarray(c['world_to_camera'])[1,:3] for c in cameras],axis=0)
    up-=t*(up@t)
    if np.linalg.norm(up)<1e-8:raise ValueError('Camera up vectors do not define a stable cross-section frame')
    up/=np.linalg.norm(up)
    u=np.cross(up,t);u/=np.linalg.norm(u)
    return origin,np.array([u,np.cross(t,u),t]),float(singular[1]/singular[0])

def angular_basis(theta,order):
    return np.column_stack([np.ones_like(theta)]+[fn(k*theta) for k in range(1,order+1) for fn in (np.cos,np.sin)])

def design(station,theta,knots,order):
    b=BSpline.design_matrix(station,knots,3).toarray()
    a=angular_basis(theta,order)
    return np.einsum('ij,ik->ijk',b,a).reshape(len(station),-1)

def fit_surface(points,domain,order=12,n_controls=7,angular_cells=120,station_cells=60):
    lo,hi=domain;step=(hi-lo)/station_cells
    theta=np.arctan2(points[:,1],points[:,0]);radius=np.linalg.norm(points[:,:2],axis=1)
    si=np.clip(((points[:,2]-lo)/step).astype(int),0,station_cells-1)
    ai=np.floor((theta+np.pi)/(2*np.pi)*angular_cells).astype(int)%angular_cells
    keys=si*angular_cells+ai;sort=np.argsort(keys);keys_s=keys[sort]
    starts=np.r_[0,np.flatnonzero(np.diff(keys_s))+1];ends=np.r_[starts[1:],len(sort)]
    rows=[]
    for start,end in zip(starts,ends):
        indices=sort[start:end]
        if len(indices)<3:continue
        key=keys[indices[0]];sbin,tbin=divmod(int(key),angular_cells)
        rows.append([(sbin+.5)*step+lo,(tbin+.5)/angular_cells*2*np.pi-np.pi,
                     np.median(radius[indices]),len(indices)])
    rows=np.asarray(rows)
    if len(rows)<500:raise ValueError('Insufficient observed cross-section support')
    knots=np.r_[[lo]*4,np.linspace(lo,hi,n_controls-2)[1:-1],[hi]*4]
    A=design(rows[:,0],rows[:,1],knots,order);nf=2*order+1
    ns=A.shape[1]//nf
    # Along-axis second differences and angular high-frequency shrinkage.
    axial=np.kron(np.diff(np.eye(ns),n=2,axis=0),np.eye(nf))*2.0
    frequencies=np.r_[0,np.repeat(np.arange(1,order+1),2)]
    angular=np.kron(np.eye(ns),np.diag(.025*frequencies**2))
    penalty=axial.T@axial+angular.T@angular+np.eye(A.shape[1])*1e-8
    rng=np.random.default_rng(42);heldout=rng.random(len(rows))<.2
    # Log-radial field: exp() of the fit is positive by construction, which keeps long
    # partially-observed domains from dipping into invalid negative radii, and matches
    # the multiplicative nature of MVS radial noise.
    def irls(which):
        x=A[which];y=np.log(rows[which,2]);base=np.sqrt(np.minimum(rows[which,3],20)/20)
        weights=base.copy();coef=np.zeros(x.shape[1])
        for _ in range(10):
            coef=solve(x.T@(weights[:,None]*x)+penalty,x.T@(weights*y),assume_a='pos')
            residual=x@coef-y;scale=max(float(np.median(abs(residual-np.median(residual)))*1.4826),.002)
            weights=base*np.minimum(1.,1.5*scale/np.maximum(abs(residual),1e-12))
        return coef,scale
    train,train_scale=irls(~heldout)
    hold_error=np.exp(A[heldout]@train)-rows[heldout,2]
    coef,scale=irls(np.ones(len(rows),dtype=bool))
    report={'cell_count':len(rows),'heldout_cells':int(heldout.sum()),'internal_holdout_median_absolute_radial_error':float(np.median(abs(hold_error))),
      'internal_holdout_p95_absolute_radial_error':float(np.quantile(abs(hold_error),.95)),
      'robust_cell_residual_scale':scale,'order':order,'control_stations':ns,
      'holdout_scope':'Random measured angular/station cells, internal consistency only; not external survey accuracy.'}
    return knots,coef,rows,report

def choose_domain(points,min_coverage=.7,min_points=150):
    low,high=np.quantile(points[:,2],[.0005,.995]);edges=np.linspace(low,high,121)
    rows=[]
    for i,(lo,hi) in enumerate(zip(edges[:-1],edges[1:])):
        q=points[(points[:,2]>=lo)&(points[:,2]<hi)]
        r=np.linalg.norm(q[:,:2],axis=1)
        angle=(np.floor((np.arctan2(q[:,1],q[:,0])+np.pi)/(2*np.pi)*72).astype(int)%72)
        cnt=np.bincount(angle,minlength=72)
        rows.append([len(q),np.mean(cnt>=2),np.median(r) if len(r) else 0])
    rows=np.asarray(rows)
    good=(rows[:,1]>=min_coverage)&(rows[:,0]>=min_points)
    labels,count=label(good)
    if count==0:raise ValueError('No contiguous tunnel-like section; refuse a generic tube fallback')
    selected=max(range(1,count+1),key=lambda k:np.sum(labels==k))
    indices=np.flatnonzero(labels==selected);first,last=indices[0],indices[-1]
    # Include adjacent partial observations near the entrance, but never extend beyond observed stations.
    max_extension=max(1,int(1.25*np.median(rows[indices,2])/(edges[1]-edges[0])))
    for _ in range(max_extension):
        if first==0 or rows[first-1,1]<.18 or rows[first-1,0]<40:break
        first-=1
    if last+1<len(rows) and rows[last+1,1]>.5:last+=1
    return (float(edges[first]),float(edges[last+1])),{'section_bin_width':float(edges[1]-edges[0]),'coverage_threshold':float(min_coverage),'min_points':int(min_points),'range_bins':[int(first),int(last)],'coverage':[r[1] for r in rows]}

def fit_circle_kasa(x,y):
    """Kasa algebraic circle fit; returns (cx,cy,R)."""
    A=np.column_stack([2*x,2*y,np.ones_like(x)]);b=x*x+y*y
    sol,_,_,_=np.linalg.lstsq(A,b,rcond=None)
    cx,cy,c=float(sol[0]),float(sol[1]),float(sol[2])
    R=c+cx*cx+cy*cy
    if R<=0:raise ValueError('Degenerate circle fit')
    return cx,cy,float(np.sqrt(R))

def estimate_circle(observed,domain,n_stations=32):
    """Per-station circle fits, then a robust median of centre/radius."""
    s=observed[:,2];edges=np.linspace(*domain,n_stations+1);fits=[]
    for lo,hi in zip(edges[:-1],edges[1:]):
        q=observed[(s>=lo)&(s<hi)]
        if len(q)<30:continue
        try:fits.append(fit_circle_kasa(q[:,0],q[:,1]))
        except ValueError:continue
    if len(fits)<3:raise ValueError('Not enough cross-section support for a circle estimate')
    arr=np.asarray(fits)
    return float(np.median(arr[:,0])),float(np.median(arr[:,1])),float(np.median(arr[:,2])),len(fits)

def estimate_box(observed,domain,n_stations=32):
    """Per-station axis-aligned extents (2%-98% quantiles), then a robust global median.

    Per-station estimation survives skewed coverage (dense side walls, sparse
    ceiling/floor, doorways) that would bias a single global quantile."""
    s=observed[:,2];edges=np.linspace(*domain,n_stations+1);fits=[]
    for lo,hi in zip(edges[:-1],edges[1:]):
        q=observed[(s>=lo)&(s<hi)]
        if len(q)<30:continue
        xlo,xhi=np.quantile(q[:,0],[.02,.98]);ylo,yhi=np.quantile(q[:,1],[.02,.98])
        fits.append([xhi-xlo,yhi-ylo,(xlo+xhi)/2,(ylo+yhi)/2])
    if len(fits)<3:raise ValueError('Not enough cross-section support for a rectangle estimate')
    arr=np.asarray(fits)
    return float(np.median(arr[:,0])),float(np.median(arr[:,1])),float(np.median(arr[:,2])),float(np.median(arr[:,3])),len(fits)

def box_profile(width,height,cx,cy,theta):
    """Uniform-perimeter rectangle parameterization; theta in [-pi,pi).

    Legs: bottom (left→right), right wall (bottom→top), top (right→left),
    left wall (top→bottom)."""
    t=(theta+np.pi)/(2*np.pi);per=2*(width+height);s=t*per
    x=np.where(s<width,cx-width/2+s,
      np.where(s<width+height,cx+width/2,
      np.where(s<2*width+height,cx+width/2-(s-width-height),
      np.where(s<2*width+2*height,cx-width/2,cx-width/2+(s-2*width-2*height)))))
    y=np.where(s<width,cy-height/2,
      np.where(s<width+height,cy-height/2+(s-width),
      np.where(s<2*width+height,cy+height/2,
      np.where(s<2*width+2*height,cy+height/2-(s-2*width-height),cy-height/2))))
    return x,y

def regularize(run,rings=180,angles=192,warp_axial=False,real_length=0.0,domain_coverage=.7,domain_points=150,max_unsupported=.45,fit_order=12,residual_gate=.3,control_stations=7,shape='prior',support_tol_fraction=.035):
    args=argparse.Namespace(rings=rings,angles=angles)
    if rings<4 or angles<12:raise ValueError('Insufficient structured-mesh resolution')
    run=Path(run);out=run/'regularization';out.mkdir(exist_ok=True)
    scene=json.loads((run/'scene.json').read_text('utf-8'))
    with np.load(run/'surface.npz') as m:vertices=m['vertices'].copy();faces=m['faces'].copy()
    origin,basis,curvature=coordinate_frame(scene['cameras']);points=(vertices-origin)@basis.T
    # Domain selection runs on the UNWARPED stations: observation coverage is a fact
    # about where the cameras actually looked, and the density/coverage gates must not
    # be distorted by the reparameterization itself. The selected interval is then
    # mapped through the warp curve so fitting happens in mileage-correct coordinates.
    domain,domain_info=choose_domain(points,domain_coverage,domain_points)
    warp_info=None
    if warp_axial:
        # Monocular COLMAP trajectories carry systematic axial drift (measured 1.4-2.7x
        # first-half vs second-half step ratio on multiscene runs). Re-parameterize the
        # observed stations to constant spacing (constant push-through speed assumption)
        # BEFORE fitting, so the structured rings interpolate equal real-world mileage
        # and the model length stops looking compressed. This trades exact pixel-ray
        # consistency for a stated display assumption; the curve is fully reported.
        from axial_reparam import station_targets, remap_axis, half_gap_report
        centers=np.array([c['center'] for c in scene['cameras']])
        s_cam=(centers-origin)@basis[2]
        order_idx=np.argsort(s_cam);s_sorted=s_cam[order_idx]
        u_sorted=station_targets(s_sorted,real_length)
        points,disp=remap_axis(points,origin,basis[2],s_sorted,u_sorted)
        domain=(float(np.interp(domain[0],s_sorted,u_sorted)),float(np.interp(domain[1],s_sorted,u_sorted)))
        warp_info={'applied':True,'assumption':'constant push-through speed; camera station spacing flattened to uniform',
            'real_length_requested':real_length,'station_range_before_warp':[float(s_sorted[0]),float(s_sorted[-1])],
            'max_observed_point_displacement':float(disp.max()),'median_observed_point_displacement':float(np.median(disp)),
            **half_gap_report(s_sorted,u_sorted)}
    crop=(points[:,2]>=domain[0])&(points[:,2]<=domain[1]);observed=points[crop]
    # Near-axis MVS debris (floating fragments around the camera path, measured down to
    # r=0.05 vs a wall at r~0.8) drags the radial field inward and inflates holdout
    # error. Gate on a robust radius band before fitting; report what was removed.
    _r=np.linalg.norm(observed[:,:2],axis=1);_rmed=float(np.median(_r))
    _band=( _r>0.35*_rmed)&(_r<2.5*_rmed)
    near_axis_removed=int((~_band).sum());observed=observed[_band]
    if shape in ('cylinder','box'):
        # Unity-primitive style: a single ideal cross-section estimated from the
        # observations (robust centre/radius or width/height), extruded along the
        # straight axis. The model deliberately does NOT follow measurement noise;
        # observation distances are reported for honesty, not driven into the shape.
        if shape=='cylinder':
            pcx,pcy,prad,station_fits=estimate_circle(observed,domain)
            fit={'shape':'cylinder','center_local':[pcx,pcy],'radius':prad,'stations_used':station_fits}
            st=np.linspace(*domain,args.rings);th=np.linspace(-np.pi,np.pi,args.angles,endpoint=False)
            ss,tt=np.meshgrid(st,th,indexing='ij')
            local=np.stack([pcx+prad*np.cos(tt),pcy+prad*np.sin(tt),ss],axis=-1).reshape(-1,3)
            radius=np.full_like(ss,prad)
        else:
            bw,bh,bcx,bcy,station_fits=estimate_box(observed,domain)
            fit={'shape':'box','width':bw,'height':bh,'center_local':[bcx,bcy],'stations_used':station_fits}
            st=np.linspace(*domain,args.rings);th=np.linspace(-np.pi,np.pi,args.angles,endpoint=False)
            ss,tt=np.meshgrid(st,th,indexing='ij')
            bx,by=box_profile(bw,bh,bcx,bcy,tt)
            local=np.stack([bx,by,ss],axis=-1).reshape(-1,3)
            radius=np.hypot(bx,by)
    else:
        knots,coef,cells,fit=fit_surface(observed,domain,order=fit_order,n_controls=control_stations)
        st=np.linspace(*domain,args.rings);th=np.linspace(-np.pi,np.pi,args.angles,endpoint=False)
        ss,tt=np.meshgrid(st,th,indexing='ij');radius=np.exp(design(ss.ravel(),tt.ravel(),knots,fit['order'])@coef).reshape(ss.shape)
        if not np.isfinite(radius).all() or radius.min()<=0:raise ValueError('Fitted radial function is not a positive valid cross section')
        local=np.stack([radius*np.cos(tt),radius*np.sin(tt),ss],axis=-1).reshape(-1,3)
    regular=local@basis+origin
    f=[]
    for i in range(args.rings-1):
        for j in range(args.angles):
            a=i*args.angles+j;b=(i+1)*args.angles+j;c=i*args.angles+(j+1)%args.angles;d=(i+1)*args.angles+(j+1)%args.angles
            f.extend([[a,b,c],[b,d,c]])
    f=np.asarray(f,dtype=np.int32)
    # Local measurement support, not probability of a defect or proof of dimensional accuracy.
    near=cKDTree(observed).query(local)[0]
    # prior-fitting tolerance is a surface-fit gate; primitive abstraction needs a wider band
    tol=max(support_tol_fraction*float(np.median(radius)),3*fit.get('robust_cell_residual_scale',0))
    supported=near<=tol
    if float((~supported).mean())>max_unsupported:raise ValueError(f'More than {max_unsupported:.0%} of fitted vertices lack local observation support')
    if shape=='prior' and fit['internal_holdout_p95_absolute_radial_error']>residual_gate*float(np.median(radius)):
        raise ValueError(f'Cross-section residual {fit["internal_holdout_p95_absolute_radial_error"]:.4f} exceeds {residual_gate:.2f}x median radius {float(np.median(radius)):.4f}; raise --fit-order or relax --residual-gate deliberately')
    geometry_id=hashlib.sha256(np.asarray(regular,dtype='<f4').tobytes()+f.tobytes()).hexdigest()[:20]
    np.savez_compressed(out/'surface_regularized.npz',vertices=regular.astype(np.float32),faces=f,
        scene_id=scene['scene_id'],surface_id=geometry_id,support=supported.astype(np.uint8),distance_to_observation=near.astype(np.float32))
    write_ply(out/'surface_regularized.ply',regular,f)
    centers=np.array([[0,0,domain[0]],[0,0,domain[1]]])@basis+origin
    closed_vertices=np.vstack([regular,centers]);caps=[]
    for j in range(args.angles):
        k=(j+1)%args.angles;last=(args.rings-1)*args.angles
        caps.extend([[len(regular),j,k],[len(regular)+1,last+k,last+j]])
    caps=np.asarray(caps,dtype=np.int32)
    # Interior viewing uses inward winding; the sealed volume export uses outward winding.
    closed_faces=np.vstack([f,caps])[:,[0,2,1]]
    closed_id=hashlib.sha256(np.asarray(closed_vertices,dtype='<f4').tobytes()+closed_faces.tobytes()).hexdigest()[:20]
    np.savez_compressed(out/'surface_closed.npz',vertices=closed_vertices.astype(np.float32),faces=closed_faces,
        scene_id=scene['scene_id'],surface_id=closed_id,cap_faces=caps[:,[0,2,1]])
    write_ply(out/'surface_closed.ply',closed_vertices,closed_faces)
    # Area-weighted smoothly interpolated inward normals for the display mesh.
    triangles=regular[f];fn=np.cross(triangles[:,1]-triangles[:,0],triangles[:,2]-triangles[:,0]);normals=np.zeros_like(regular)
    for col in range(3):np.add.at(normals,f[:,col],fn)
    normals/=np.linalg.norm(normals,axis=1,keepdims=True)
    np.savez_compressed(out/'display_attributes.npz',normals=normals.astype(np.float32),supported=supported.astype(np.uint8),
        caps_positions=closed_vertices[caps].astype(np.float32))
    raw_top=topology(vertices,faces);reg_top=topology(regular,f);closed_top=topology(closed_vertices,closed_faces)
    assert reg_top['boundary_edges']==2*args.angles and reg_top['nonmanifold_edges']==0
    assert closed_top['boundary_edges']==0 and closed_top['euler_characteristic']==2 and closed_top['degenerate_faces']==0
    kind={'cylinder':'primitive_cylinder','box':'primitive_box'}.get(shape,'regularized_tunnel')
    method={'cylinder':'ideal circle cross-section (robust centre/radius) extruded along the straight axis',
            'box':'ideal rectangle cross-section (robust extents) extruded along the straight axis'}.get(shape,
        'robust cubic B-spline along axis / periodic Fourier cross section, followed by structured remeshing')
    report={'scene_id':scene['scene_id'],'surface_id':geometry_id,'surface_kind':kind,'closed_surface_id':closed_id,'method':method,
      'coordinate_origin':origin.tolist(),'coordinate_basis_rows':basis.tolist(),'axis_bending_ratio':curvature,'station_range':domain,
      'axial_velocity_warp':warp_info or {'applied':False},
      'domain_gates':{'min_coverage':float(domain_coverage),'min_points':int(domain_points),'max_unsupported_fraction':float(max_unsupported)},
      'observed_vertices_used':len(observed),'observed_vertices_excluded_outside_range':int((~crop).sum()),'near_axis_points_removed':near_axis_removed,'robust_radius_band':[0.35,2.5],'domain_selection':domain_info,
      'fit':fit,'control_stations':control_stations,'knots':(knots.tolist() if shape=='prior' else []),'coefficients':(coef.tolist() if shape=='prior' else []),
      'ideal_section_distance':{'median':float(np.median(near)),'p95':float(np.quantile(near,.95)),'max':float(near.max()),
        'note':'distance from ideal-primitive vertices to nearest measured point; quantifies abstraction, not accuracy'},
      'raw_topology':raw_top,'regularized_topology':reg_top,'closed_topology':closed_top,
      'local_support_threshold_sfm_unit':tol,'support_tol_fraction':support_tol_fraction,'supported_vertices':int(supported.sum()),'inferred_vertices':int((~supported).sum()),
      'radial_range': [float(radius.min()),float(radius.max())],
      'limits':(['Idealised primitive geometry for simulation/display: the section is a single circle or rectangle estimated from measurements, and does NOT reproduce local deformation, lining joints or construction irregularities.',
                'Continuous sidewalls/floor are inferred where observations are absent; two modeled segment ends intentionally remain open, not necessarily actual portals.',
                'Closed export adds explicitly synthetic end caps and uses outward winding, not measured portal walls.',
                'Coordinates remain relative SfM units; no assumed physical diameter.'] if shape in ('cylinder','box') else [
                'Shape prior for a short nearly straight star-shaped tunnel; not a deformation measurement.',
                'Continuous sidewalls/floor are inferred where observations are absent; two modeled segment ends intentionally remain open, not necessarily actual portals.',
                'Closed export adds explicitly synthetic end caps and uses outward winding, not measured portal walls.',
                'Coordinates remain relative SfM units; no assumed physical diameter.'])}
    (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf-8')
    print(json.dumps({k:report[k] for k in ['station_range','fit','raw_topology','regularized_topology','closed_topology','supported_vertices','inferred_vertices']},ensure_ascii=False,indent=2))

    return report

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run',default=str(ROOT/'results/dvp_tunnel'))
    p.add_argument('--rings',type=int,default=180);p.add_argument('--angles',type=int,default=192)
    p.add_argument('--warp-axial',action='store_true',help='flatten camera station spacing before fitting (constant-velocity push-through assumption)')
    p.add_argument('--real-length',type=float,default=0.0,help='force axial span to this length in SfM units; 0 = keep current span')
    p.add_argument('--domain-coverage',type=float,default=.7,help='angle-coverage gate for domain selection; lower it to fit longer partially-observed reaches')
    p.add_argument('--domain-points',type=int,default=150)
    p.add_argument('--max-unsupported',type=float,default=.45,help='refuse if more than this fraction of fitted vertices lack observation support')
    p.add_argument('--fit-order',type=int,default=12,help='Fourier order of the cross-section; raise for rectangular/doorway profiles')
    p.add_argument('--residual-gate',type=float,default=.3,help='holdout p95 radial error limit as a fraction of median radius')
    p.add_argument('--control-stations',type=int,default=7,help='B-spline control stations along the axis; raise for doorways/structures')
    p.add_argument('--shape',choices=['prior','cylinder','box'],default='prior',help="prior=follow measurements with a smooth radial field; cylinder/box=Unity-primitive ideal geometry")
    p.add_argument('--support-tol-fraction',type=float,default=.035,help='observation-support band as a fraction of median radius; primitive abstraction needs ~0.12')
    args=p.parse_args();regularize(args.run,args.rings,args.angles,args.warp_axial,args.real_length,args.domain_coverage,args.domain_points,args.max_unsupported,args.fit_order,args.residual_gate,args.control_stations,args.shape,args.support_tol_fraction)

if __name__=='__main__':main()
