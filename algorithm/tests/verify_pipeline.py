"""Independent checks of exported real-video geometry, image coordinates and surface hits.
These are consistency checks, not an external accuracy benchmark.
"""
import hashlib
import json
from pathlib import Path
import sys
import numpy as np
import pycolmap
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from export_web import read_textured_mesh

ROOT=Path(__file__).resolve().parents[1]
RUN=ROOT/'results/dvp_tunnel'
scene=json.loads((RUN/'scene.json').read_text('utf-8'))
loc=json.loads((RUN/'localized.json').read_text('utf-8'))
det=json.loads((RUN/'detect/detections.json').read_text('utf-8'))
assert scene['scene_id']==loc['scene_id']==det['scene_id']
assert scene['scale']['status']=='arbitrary' and scene['scale']['unit']=='sfm_unit'
mesh=np.load(RUN/'surface.npz',allow_pickle=False)
verts,faces=mesh['vertices'],mesh['faces']
assert str(mesh['scene_id'].item())==scene['scene_id']
texture_mesh=RUN/'dense/textured_oriented/mesh.ply'
if not texture_mesh.is_file():texture_mesh=RUN/'dense/textured/mesh.ply'
tv,tf,uv=read_textured_mesh(texture_mesh)
assert np.array_equal(tv,verts), 'Texturing changed geometric vertices'
assert np.array_equal(np.sort(tf,axis=1),np.sort(faces,axis=1)), 'Texture triangle geometry differs from localization surface'
assert np.isfinite(verts).all() and np.isfinite(uv).all()
cams={c['image_name']:c for c in scene['cameras']}
assert len(cams)==40
hits=[(d,o) for d in loc['defects'] for o in d['observations'] if o['status']=='hit']
assert len(hits)==loc['summary']['hit_count']
assert sum(d['requested_count'] for d in loc['defects'])==loc['summary']['requested_count']
selected=np.random.default_rng(42).choice(len(hits),min(400,len(hits)),replace=False)
errors=[]; plane_errors=[]; min_bary=1.; masks={}
for i in selected:
    d,o=hits[i];c=cams[d['image_name']]
    point=np.asarray(o['point']);m=np.asarray(c['world_to_camera'])
    camera=pycolmap.Camera(model=c['model'],width=c['width'],height=c['height'],params=c['params'])
    cp=m[:3,:3]@point+m[:3,3]
    assert cp[2]>0
    pixel=camera.img_from_cam(cp)
    errors.append(float(np.linalg.norm(pixel-o['pixel'])))
    tri=verts[faces[o['triangle_id']]]
    a,b=tri[1]-tri[0],tri[2]-tri[0];n=np.cross(a,b)
    plane_errors.append(float(abs((point-tri[0])@n)/np.linalg.norm(n)))
    bc=np.linalg.lstsq(np.column_stack([a,b]),point-tri[0],rcond=None)[0]
    min_bary=min(min_bary,float(bc.min()),float(1-bc.sum()))
    if d['image_name'] not in masks:
        masks[d['image_name']]=np.array(Image.open(RUN/'detect/masks'/d['image_name']))
    x,y=np.floor(o['pixel']).astype(int)
    assert masks[d['image_name']][y,x]>0, 'Localized pixel not in model mask'
assert max(errors)<.1, f'Projection mismatch: {max(errors)} px'
assert max(plane_errors)<1e-4, f'Intersection outside mesh plane: {max(plane_errors)}'
assert min_bary>-.001, f'Intersection outside triangle: {min_bary}'
report={'status':'passed','scene_id':scene['scene_id'],'registered_images':len(cams),
 'mesh_vertices':len(verts),'mesh_triangles':len(faces),'localized_pixels':len(hits),
 'checked_hits':len(selected),'max_roundtrip_pixel_error':max(errors),
 'max_point_to_triangle_plane_distance_sfm_unit':max(plane_errors),
 'minimum_barycentric_coordinate':min_bary,'all_sampled_hits_in_model_mask':True,
 'texture_mesh_matches_localization_mesh':True,
 'source_video_sha256':hashlib.sha256((ROOT/'data/dvp_tunnel/source.webm').read_bytes()).hexdigest(),
 'checkpoint_sha256':hashlib.sha256((ROOT/'data/crack_model/unet_v3.pth').read_bytes()).hexdigest(),
 'limits':['Internal coordinate and geometry consistency only; no defect ground truth or surveyed metric accuracy.']}
(RUN/'verification.json').write_text(json.dumps(report,indent=2),'utf-8')
print(json.dumps(report,indent=2))
