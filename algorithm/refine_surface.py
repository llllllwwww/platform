"""Choose a supported tunnel prior, or conservatively smooth the observed mesh.

The fallback preserves boundaries and creates no missing walls/end caps. Vertex movement
is bounded by the measured local edge spacing; this is display refinement, not metrology.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
from scipy.sparse import coo_matrix,diags
from scipy.sparse.csgraph import connected_components
from regularize_tunnel import regularize,write_ply,topology


def bounded_smooth(vertices,faces,iterations=8,fraction=.3):
    vertices=np.asarray(vertices,dtype=np.float64);faces=np.asarray(faces,dtype=np.int32)
    if len(faces)<1 or not np.isfinite(vertices).all():raise ValueError('No finite observed surface')
    edges=np.sort(np.concatenate([faces[:,[0,1]],faces[:,[1,2]],faces[:,[2,0]]]),axis=1)
    edges,count=np.unique(edges,axis=0,return_counts=True);boundary=np.unique(edges[count==1])
    row=np.r_[edges[:,0],edges[:,1]];col=np.r_[edges[:,1],edges[:,0]]
    adjacency=coo_matrix((np.ones(len(row)),(row,col)),shape=(len(vertices),len(vertices))).tocsr()
    degree=np.maximum(np.asarray(adjacency.sum(1)).ravel(),1);weights=diags(1/degree)@adjacency
    lengths=np.linalg.norm(vertices[row]-vertices[col],axis=1)
    local=np.bincount(row,weights=lengths,minlength=len(vertices))/degree
    limits=np.maximum(local,1e-12)*fraction;result=vertices.copy();pinned=np.zeros(len(vertices),bool);pinned[boundary]=True
    original_normals=np.cross(vertices[faces[:,1]]-vertices[faces[:,0]],vertices[faces[:,2]]-vertices[faces[:,0]])
    for _ in range(iterations):
        for factor in [.5,-.53]:
            candidate=result+factor*(weights@result-result);displacement=candidate-vertices
            length=np.linalg.norm(displacement,axis=1);displacement*=np.minimum(1,limits/np.maximum(length,1e-15))[:,None]
            candidate=vertices+displacement;candidate[pinned]=vertices[pinned]
            for guard in range(20):
                normals=np.cross(candidate[faces[:,1]]-candidate[faces[:,0]],candidate[faces[:,2]]-candidate[faces[:,0]])
                flipped=np.einsum('ij,ij->i',original_normals,normals)<=0
                if not flipped.any():break
                pinned[np.unique(faces[flipped])]=True;candidate[pinned]=vertices[pinned]
            else:candidate=vertices.copy();pinned[:]=True
            result=candidate
    # A reverted neighbor can affect an adjacent face; fail rather than exporting a fold.
    normals=np.cross(result[faces[:,1]]-result[faces[:,0]],result[faces[:,2]]-result[faces[:,0]])
    if np.any(np.einsum('ij,ij->i',original_normals,normals)<=0):raise ValueError('Smoothing would invert a face')
    return result,limits,boundary


def observed_refinement(run,reason):
    run=Path(run);out=run/'regularization';out.mkdir(exist_ok=True)
    scene=json.loads((run/'scene.json').read_text('utf-8'))
    with np.load(run/'surface.npz') as data:
        vertices=data['vertices'].copy();faces=data['faces'].astype(np.int32)
        filled=data['filled_vertices'].astype(bool) if 'filled_vertices' in data.files else np.zeros(len(vertices),bool)
    edges=np.concatenate([faces[:,[0,1]],faces[:,[1,2]],faces[:,[2,0]]]);rows=np.r_[edges[:,0],edges[:,1]];columns=np.r_[edges[:,1],edges[:,0]]
    adjacency=coo_matrix((np.ones(len(rows)),(rows,columns)),shape=(len(vertices),len(vertices))).tocsr()
    count,labels=connected_components(adjacency,directed=False)
    area=.5*np.linalg.norm(np.cross(vertices[faces[:,1]]-vertices[faces[:,0]],vertices[faces[:,2]]-vertices[faces[:,0]]),axis=1)
    component_area=np.bincount(labels[faces[:,0]],weights=area,minlength=count)
    retain=component_area>=max(component_area)*.0001;retain[np.argmax(component_area)]=True
    keep=retain[labels[faces[:,0]]];kept_faces=faces[keep];used=np.unique(kept_faces);index=np.full(len(vertices),-1,dtype=np.int32);index[used]=np.arange(len(used))
    kept_faces=index[kept_faces];original=vertices[used];refined,limits,boundary=bounded_smooth(original,kept_faces)
    distance=np.linalg.norm(refined-original,axis=1);identity=hashlib.sha256(np.asarray(refined,dtype='<f4').tobytes()+kept_faces.tobytes()).hexdigest()[:20]
    np.savez_compressed(out/'surface_regularized.npz',vertices=refined.astype(np.float32),faces=kept_faces,
        scene_id=scene['scene_id'],surface_id=identity,support=(~filled[used]).astype(np.uint8),distance_to_observation=distance.astype(np.float32))
    write_ply(out/'surface_regularized.ply',refined,kept_faces)
    triangle=refined[kept_faces];face_normals=np.cross(triangle[:,1]-triangle[:,0],triangle[:,2]-triangle[:,0]);normals=np.zeros_like(refined)
    for i in range(3):np.add.at(normals,kept_faces[:,i],face_normals)
    normals/=np.maximum(np.linalg.norm(normals,axis=1,keepdims=True),1e-15)
    np.savez_compressed(out/'display_attributes.npz',normals=normals.astype(np.float32),supported=(~filled[used]).astype(np.uint8),caps_positions=np.empty((0,3,3),np.float32))
    report={'scene_id':scene['scene_id'],'surface_id':identity,'surface_kind':'observed_smoothed','closed_surface_id':None,
        'method':'Boundary-pinned, displacement-bounded Taubin smoothing of measured mesh; tiny isolated components removed',
        'prior_rejection_reason':reason,'station_range':None,'coordinate_origin':None,'coordinate_basis_rows':None,
        'raw_topology':topology(vertices,faces),'regularized_topology':topology(refined,kept_faces),'closed_topology':None,
        'components_before':int(count),'components_retained':int(retain.sum()),'removed_area_fraction':float(area[~keep].sum()/max(area.sum(),1e-15)),
        'support_definition':'Each vertex is within 30% of its original local edge spacing; relocation uses the median local bound. Hole-filled vertices (surface.npz filled_vertices) are marked inferred.',
        'local_support_threshold_sfm_unit':float(np.median(limits)),'supported_vertices':int((~filled[used]).sum()),'inferred_vertices':int(filled[used].sum()),
        'pinned_boundary_vertices':len(boundary),'displacement_median':float(np.median(distance)),'displacement_max':float(max(distance)),
        'maximum_local_edge_fraction':.3,'boundary_positions_unchanged':bool(np.array_equal(refined[boundary],original[boundary])),
        'limits':['No axial/circular prior is imposed on unsupported scenes; open boundaries remain open.',
                  'All output vertices are bounded refinements of observed vertices; this does not establish true defect deformation or metric accuracy.',
                  'No closed volume or synthetic caps are exported for this mode.']}
    (out/'report.json').write_text(json.dumps(report,indent=2,allow_nan=False),'utf-8');return report


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run',required=True,type=Path)
    p.add_argument('--prior',choices=['auto','straight_tunnel','observed_only'],default='auto')
    p.add_argument('--rings',type=int,default=120);p.add_argument('--angles',type=int,default=64)
    p.add_argument('--warp-axial',action='store_true');p.add_argument('--real-length',type=float,default=0.0)
    p.add_argument('--domain-coverage',type=float,default=.7);p.add_argument('--domain-points',type=int,default=150)
    p.add_argument('--max-unsupported',type=float,default=.45)
    p.add_argument('--fit-order',type=int,default=12);p.add_argument('--residual-gate',type=float,default=.3)
    p.add_argument('--control-stations',type=int,default=7)
    p.add_argument('--shape',choices=['prior','cylinder','box'],default='prior')
    p.add_argument('--support-tol-fraction',type=float,default=.035)
    args=p.parse_args()
    out=args.run/'regularization'
    if out.exists() and any(out.iterdir()):raise FileExistsError('Use a new run; refinement output already exists')
    if args.prior=='observed_only':report=observed_refinement(args.run,'Capture profile requests observed geometry only')
    else:
        try:report=regularize(args.run,args.rings,args.angles,args.warp_axial,args.real_length,args.domain_coverage,args.domain_points,args.max_unsupported,args.fit_order,args.residual_gate,args.control_stations,args.shape,args.support_tol_fraction)
        except ValueError as error:
            if args.prior=='straight_tunnel':raise
            # All fit gates execute before regularized/capped surface files are written.
            report=observed_refinement(args.run,str(error))
    print(json.dumps({'surface_kind':report['surface_kind'],'surface_id':report['surface_id'],'topology':report['regularized_topology']},indent=2))
if __name__=='__main__':main()
