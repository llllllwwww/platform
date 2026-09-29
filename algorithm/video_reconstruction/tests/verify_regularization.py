"""Verify tunnel topology, normal variation, and evidence-preserving relocation."""
import json
from pathlib import Path
import sys
import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
import pycolmap
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from export_web import read_textured_mesh

ROOT=Path(__file__).resolve().parents[1];RUN=ROOT/'results/dvp_tunnel';OUT=RUN/'regularization'
report=json.loads((OUT/'report.json').read_text('utf-8'))
scene=json.loads((RUN/'scene.json').read_text('utf-8'))
with np.load(OUT/'surface_regularized.npz') as m:v=m['vertices'];f=m['faces']
with np.load(OUT/'surface_closed.npz') as m:cv=m['vertices'];cf=m['faces']
with np.load(RUN/'surface.npz') as m:rv=m['vertices'];rf=m['faces']

def edge_data(faces):
    directed=np.concatenate([faces[:,[0,1]],faces[:,[1,2]],faces[:,[2,0]]]);edges=np.sort(directed,axis=1)
    unique,inverse,count=np.unique(edges,axis=0,return_inverse=True,return_counts=True)
    return directed,unique,inverse,count

directed,unique,inverse,count=edge_data(f)
boundary=unique[count==1];boundary_vertices=np.unique(boundary)
assert np.all(np.bincount(boundary.ravel(),minlength=len(v))[boundary_vertices]==2), 'Boundary has junctions'
lookup=np.full(len(v),-1);lookup[boundary_vertices]=np.arange(len(boundary_vertices))
g=coo_matrix((np.ones(len(boundary)),(lookup[boundary[:,0]],lookup[boundary[:,1]])),shape=(len(boundary_vertices),len(boundary_vertices))).tocsr()
loops,labels=connected_components(g,directed=False);assert loops==2
origin=np.array(report['coordinate_origin']);axis=np.array(report['coordinate_basis_rows'])[2]
for loop in range(loops):
    stations=(v[boundary_vertices[labels==loop]]-origin)@axis
    assert np.ptp(stations)<2e-6
    assert min(abs(stations.mean()-x) for x in report['station_range'])<2e-6
body_graph=coo_matrix((np.ones(len(unique)),(unique[:,0],unique[:,1])),shape=(len(v),len(v))).tocsr()
assert connected_components(body_graph,directed=False)[0]==1
cd,ce,ci,cn=edge_data(cf)
assert np.all(cn==2), 'Closed mesh has boundary or nonmanifold edges'
signs=np.where(cd[:,0]<cd[:,1],1,-1)
assert np.all(np.bincount(ci,weights=signs)==0), 'Closed faces have inconsistent orientation'
tri=cv[cf];signed_volume=float(np.einsum('ij,ij->i',tri[:,0],np.cross(tri[:,1],tri[:,2])).sum()/6)
assert signed_volume>1e-5, 'Closed export must have outward face orientation'

def normal_variation(vertices,faces):
    tri=vertices[faces];n=np.cross(tri[:,1]-tri[:,0],tri[:,2]-tri[:,0]);n/=np.linalg.norm(n,axis=1,keepdims=True)
    _,_,inv,cnt=edge_data(faces);order=np.argsort(inv);starts=np.r_[0,np.cumsum(cnt)[:-1]];pairs=np.column_stack([order[starts[cnt==2]],order[starts[cnt==2]+1]])%len(faces)
    # Ignore winding signs in the raw mesher when assessing geometric variation.
    angle=np.degrees(np.arccos(np.clip(abs(np.sum(n[pairs[:,0]]*n[pairs[:,1]],axis=1)),0,1)))
    return {'median_deg':float(np.median(angle)),'p95_deg':float(np.quantile(angle,.95))}
rs=(rv[rf].mean(axis=1)-origin)@axis
raw_normals=normal_variation(rv,rf[(rs>=report['station_range'][0])&(rs<=report['station_range'][1])])
fitted_normals=normal_variation(v,f)
assert fitted_normals['p95_deg']<raw_normals['p95_deg']
tv,tf,uv=read_textured_mesh(OUT/'textured/mesh.ply');assert np.array_equal(tv,v) and np.array_equal(tf,f)
textured_faces=int(np.any(uv!=0,axis=(1,2)).sum());assert textured_faces==len(f)
loc=json.loads((OUT/'localized.json').read_text('utf-8'))
assert loc['surface_id']==report['surface_id'] and loc['scene_id']==scene['scene_id']
cams={c['image_name']:c for c in scene['cameras']}
hits=[(d,o) for d in loc['defects'] for o in d['observations'] if o['status']=='hit']
for d,o in hits:
    assert o['original_status']=='hit' and o['original_point'] is not None
    assert o['displacement_sfm_unit']<=loc['summary']['max_allowed_displacement_sfm_unit']
errors=[];plane=[]
for i in np.random.default_rng(91).choice(len(hits),400,replace=False):
    d,o=hits[i];c=cams[d['image_name']];m=np.array(c['world_to_camera']);p=np.array(o['point']);cp=m[:3,:3]@p+m[:3,3]
    camera=pycolmap.Camera(model=c['model'],width=c['width'],height=c['height'],params=c['params'])
    errors.append(float(np.linalg.norm(camera.img_from_cam(cp)-o['pixel'])))
    t=v[f[o['triangle_id']]];e=np.column_stack([t[1]-t[0],t[2]-t[0]])
    bc=np.linalg.lstsq(e,p-t[0],rcond=None)[0]
    assert min(*bc,1-bc.sum())>-.001
    normal=np.cross(e[:,0],e[:,1]);plane.append(float(abs((p-t[0])@normal)/np.linalg.norm(normal)))
assert max(errors)<.01 and max(plane)<1e-4
result={'status':'passed','surface_id':report['surface_id'],'body_components':1,'boundary_loops':loops,'only_segment_ends_have_boundary':True,
    'closed_mesh_every_edge_has_two_faces':True,'closed_face_orientation_consistent':True,'closed_signed_volume_sfm_unit3':signed_volume,
    'raw_adjacent_normal_variation':raw_normals,'fitted_adjacent_normal_variation':fitted_normals,
    'textured_faces':textured_faces,'accepted_associations':len(hits),'all_associations_have_original_mesh_evidence':True,
    'checked_associations':400,'max_roundtrip_pixel_error':max(errors),'max_triangle_plane_error_sfm_unit':max(plane),
    'limits':'Topology and internal consistency checks only; fitted geometry is not independently surveyed accuracy.'}
(OUT/'verification.json').write_text(json.dumps(result,indent=2),'utf-8');print(json.dumps(result,indent=2))
