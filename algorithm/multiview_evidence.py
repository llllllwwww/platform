"""Associate candidate observations using calibrated reprojection and MVS visibility.

Repeated model responses are evidence to review, not proof of a crack class. Missing,
occluded and weak-parallax views do not count as negative observations. Source points
come from the original measured mesh, never from synthetic caps or a fitted tunnel.
"""
from __future__ import annotations
import argparse
from collections import Counter,defaultdict
from functools import lru_cache
import json
import os
from pathlib import Path
import numpy as np
from PIL import Image
import pycolmap
from scipy import ndimage
ROOT=Path(__file__).resolve().parent


def read_depth(path):
    with Path(path).open('rb') as stream:
        header=bytearray()
        while header.count(b'&')<3:
            value=stream.read(1)
            if not value or len(header)>100:raise ValueError('Invalid COLMAP depth header')
            header.extend(value)
        width,height,channels=map(int,header.decode('ascii').split('&')[:3])
        values=np.frombuffer(stream.read(),dtype='<f4')
    if channels!=1 or values.size!=width*height:raise ValueError('Unexpected COLMAP depth dimensions')
    return values.reshape((width,height),order='F').T.copy()


def project(points,camera,transform):
    xyz=points@transform[:3,:3].T+transform[:3,3]
    uv=np.asarray(camera.img_from_cam(xyz));valid=np.isfinite(uv).all(1)&(xyz[:,2]>1e-8)
    valid&=(uv[:,0]>=0)&(uv[:,0]<camera.width)&(uv[:,1]>=0)&(uv[:,1]<camera.height)
    return uv,xyz[:,2],valid


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run',required=True,type=Path);p.add_argument('--frames',required=True,type=Path)
    p.add_argument('--valid-mask',type=Path);p.add_argument('--neighbors',type=int,default=2);p.add_argument('--max-samples',type=int,default=16)
    p.add_argument('--pixel-tolerance',type=float,default=3.);p.add_argument('--min-parallax-deg',type=float,default=1.)
    args=p.parse_args();run=args.run.resolve();frames_path=args.frames.resolve();valid_mask_path=args.valid_mask.resolve() if args.valid_mask else None;os.chdir(ROOT)
    if args.neighbors<1 or args.max_samples<5:raise ValueError('Need neighboring views and at least five samples')
    scene=json.loads((run/'scene.json').read_text('utf-8'));localized=json.loads((run/'localized.json').read_text('utf-8'))
    detection=json.loads((run/'detect/detections.json').read_text('utf-8'));timing=json.loads(frames_path.read_text('utf-8'))
    if not scene['scene_id']==localized['scene_id']==detection['scene_id']:raise ValueError('Evidence scene IDs differ')
    source={d['id']:d for d in detection['detections']};observations={d['id']:d for d in localized['defects']}
    time_by_name={f['image_name']:f['nominal_time_s'] for f in timing['frames'] if f['accepted']}
    cameras={c['image_name']:c for c in scene['cameras']};names=sorted(cameras,key=lambda name:time_by_name[name]);positions={name:i for i,name in enumerate(names)}
    original={name:pycolmap.Camera(model=c['model'],width=c['width'],height=c['height'],params=c['params']) for name,c in cameras.items()}
    dense=pycolmap.Reconstruction(os.path.relpath(run/'dense/sparse',ROOT));dense_images={im.name:im for im in dense.images.values() if im.has_pose}
    by_image=defaultdict(list)
    for d in source.values():by_image[d['image_name']].append(d)
    with np.load(run/'surface.npz') as mesh:
        vertices=mesh['vertices'];faces=mesh['faces'];edge=np.linalg.norm(vertices[faces[:,0]]-vertices[faces[:,1]],axis=1)
        surface_tolerance=3*float(np.median(edge[edge>0]))
    validity=np.array(Image.open(valid_mask_path).convert('L'))>0 if valid_mask_path else None

    @lru_cache(maxsize=8)
    def target_data(name):
        binary=np.array(Image.open(run/'detect/masks'/(Path(name).stem+'.png')).convert('L'))>0
        labels,_=ndimage.label(binary,structure=np.ones((3,3),np.uint8));component_to_id={}
        for item in by_image[name]:
            x,y=np.floor(item['pixels'][0]).astype(int);component=int(labels[y,x])
            if component:component_to_id[component]=item['id']
        if binary.any():distance,nearest=ndimage.distance_transform_edt(~binary,return_indices=True)
        else:distance=np.full(binary.shape,np.inf);nearest=np.zeros((2,*binary.shape),np.int32)
        depth=read_depth(run/'dense/stereo/depth_maps'/(name+'.geometric.bin'))
        image=dense_images[name];transform=np.eye(4);transform[:3]=image.cam_from_world().matrix()
        return labels,component_to_id,distance,nearest,depth,dense.cameras[image.camera_id],transform

    details={};edges=[];totals=Counter()
    for number,(identity,item) in enumerate(observations.items(),1):
        points=np.asarray(item['points'],dtype=float).reshape(-1,3);name=item['image_name'];views=[]
        if len(points)>=5 and name in cameras:
            points=points[np.linspace(0,len(points)-1,min(len(points),args.max_samples),dtype=int)]
            source_ray=points-np.asarray(cameras[name]['center']);source_ray/=np.linalg.norm(source_ray,axis=1,keepdims=True)
            origin_index=positions[name]
            for index in range(max(0,origin_index-args.neighbors),min(len(names),origin_index+args.neighbors+1)):
                target=names[index]
                if target==name:continue
                camera=cameras[target];transform=np.asarray(camera['world_to_camera'])
                uv,_,visible=project(points,original[target],transform)
                labels,component_ids,distance,nearest,depth,depth_camera,depth_transform=target_data(target)
                duv,dz,depth_valid=project(points,depth_camera,depth_transform)
                # Depth maps can be smaller than the undistorted camera's stored resolution.
                duv*=np.array([depth.shape[1]/depth_camera.width,depth.shape[0]/depth_camera.height])
                safe_uv=np.nan_to_num(uv,nan=-1,posinf=-1,neginf=-1);xy=np.floor(safe_uv).astype(int)
                dxy=np.floor(np.nan_to_num(duv,nan=-1,posinf=-1,neginf=-1)).astype(int)
                inside_depth=depth_valid&(dxy[:,0]>=0)&(dxy[:,0]<depth.shape[1])&(dxy[:,1]>=0)&(dxy[:,1]<depth.shape[0])
                observed_depth=np.zeros(len(points));good=np.flatnonzero(inside_depth);observed_depth[good]=depth[dxy[good,1],dxy[good,0]]
                verified=visible&inside_depth&(observed_depth>0)&(np.abs(observed_depth-dz)<=surface_tolerance+.025*observed_depth)
                rays=points-np.asarray(camera['center']);rays/=np.linalg.norm(rays,axis=1,keepdims=True)
                parallax=np.rad2deg(np.arccos(np.clip(np.einsum('ij,ij->i',rays,source_ray),-1,1)))
                verified&=parallax>=args.min_parallax_deg
                if validity is not None:
                    if validity.shape!=(camera['height'],camera['width']):raise ValueError('Camera validity-mask shape mismatch')
                    valid_indices=np.flatnonzero(visible);allowed=np.zeros(len(points),bool);allowed[valid_indices]=validity[xy[valid_indices,1],xy[valid_indices,0]];verified&=allowed
                good=np.flatnonzero(verified);record={'image_name':target,'time_s':time_by_name[target],'verified_visible_samples':len(good),'status':'geometry_inconclusive'}
                if len(good)>=5:
                    hits=distance[xy[good,1],xy[good,0]]<=args.pixel_tolerance;fraction=float(hits.mean())
                    record['positive_pixel_fraction']=fraction;record['status']='supports_candidate' if fraction>=.5 else ('not_repeated' if fraction<=.2 else 'ambiguous_response')
                    if record['status']=='supports_candidate':
                        matches=Counter()
                        for j in good[hits]:
                            x,y=xy[j];target_component=int(labels[nearest[0,y,x],nearest[1,y,x]])
                            if target_component in component_ids:matches[component_ids[target_component]]+=1
                        if matches:
                            other,votes=matches.most_common(1)[0]
                            if votes>=max(3,.5*len(good)):
                                edges.append((votes/len(good),identity,other));record['associated_observation']=other
                totals[record['status']]+=1;views.append(record)
        details[identity]={'id':identity,'image_name':name,'supporting_views':sum(v['status']=='supports_candidate' for v in views),
            'not_repeated_views':sum(v['status']=='not_repeated' for v in views),'tested_views':views,
            'status':'repeated_candidate' if any(v['status']=='supports_candidate' for v in views) else ('single_view_candidate' if len(points) else 'unlocalized_candidate')}
        if number%100==0:print('MULTIVIEW',number,'/',len(observations),flush=True)
    parent={identity:identity for identity in observations};image_sets={i:{d['image_name']} for i,d in observations.items()}
    def find(i):
        while parent[i]!=i:parent[i]=parent[parent[i]];i=parent[i]
        return i
    conflicts=0
    for score,a,b in sorted(edges,reverse=True):
        ra,rb=find(a),find(b)
        if ra==rb:continue
        if image_sets[ra]&image_sets[rb]:conflicts+=1;continue
        parent[rb]=ra;image_sets[ra]|=image_sets[rb]
    members=defaultdict(list)
    for identity in observations:members[find(identity)].append(identity)
    groups=[]
    for index,ids in enumerate(sorted(members.values(),key=lambda ids:min(ids)),1):
        group_id=f'evidence-{index:05d}'
        for identity in ids:details[identity]['group_id']=group_id
        groups.append({'id':group_id,'observation_ids':sorted(ids),'image_names':sorted({observations[i]['image_name'] for i in ids}),
            'observation_count':len(ids),'review_status':'unreviewed','status':'linked_multiview_candidates' if len(ids)>1 else 'single_observation'})
    result={'schema_version':1,'scene_id':scene['scene_id'],'geometry_source':'Original measured mesh points checked against COLMAP geometric MVS depth maps',
        'parameters':{'neighbor_frames_each_side':args.neighbors,'sample_cap':args.max_samples,'pixel_tolerance':args.pixel_tolerance,
                      'minimum_parallax_degrees':args.min_parallax_deg,'depth_tolerance_relative':.025,'depth_tolerance_absolute_sfm_unit':surface_tolerance},
        'observations':list(details.values()),'groups':groups,'summary':{'candidate_observations':len(observations),'evidence_groups':len(groups),
            'groups_with_multiple_observations':sum(len(g['observation_ids'])>1 for g in groups),'observations_with_repeated_pixel_evidence':sum(d['supporting_views']>0 for d in details.values()),
            'same_frame_merge_conflicts_rejected':conflicts,'view_checks':dict(totals)},
        'limits':['Persistent seams, lettering and stains can also repeat across frames; every group remains unreviewed.',
                  'A negative model response is not proof of no crack. Occlusion, missing depth and weak parallax are inconclusive.',
                  'Groups are association hypotheses, not a verified count of distinct defects.']}
    (run/'multiview.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False),'utf-8');print(json.dumps(result['summary'],indent=2),flush=True)
if __name__=='__main__':main()
