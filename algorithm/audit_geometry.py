"""Measure actual track support and parallax before spending time on dense reconstruction."""
import argparse
import json
import os
from pathlib import Path
import numpy as np
import pycolmap
ROOT=Path(__file__).resolve().parent

def audit(run):
    run=Path(run).resolve();model=pycolmap.Reconstruction(os.path.relpath(run/'model',ROOT))
    cameras={i:im.projection_center() for i,im in model.images.items() if im.has_pose}
    observations={i:0 for i in cameras};angles=[]
    for point in model.points3D.values():
        ids=[e.image_id for e in point.track.elements if e.image_id in cameras]
        for i in ids:observations[i]+=1
        if len(ids)<2:continue
        rays=np.stack([cameras[i]-point.xyz for i in ids]);norm=np.linalg.norm(rays,axis=1)
        if np.min(norm)<1e-10:continue
        rays/=norm[:,None];angles.append(float(np.rad2deg(np.arccos(np.clip(np.min(rays@rays.T),-1,1)))))
    counts=np.array(list(observations.values()));quality=json.loads((run/'quality.json').read_text('utf-8'))
    result={'registered_images':len(cameras),'tracks_per_registered_image':{'min':int(min(counts)),'median':float(np.median(counts)),'p10':float(np.quantile(counts,.1))},
            'point_max_triangulation_angle_degrees':{'median':float(np.median(angles)) if angles else None,'p10':float(np.quantile(angles,.1)) if angles else None},
            'frames_with_at_least_30_landmarks':int((counts>=30).sum()),
            'dense_readiness_heuristic':bool(quality.get('registration_fraction',0)>=.8 and (counts>=30).sum()/quality['input_images']>=.8 and np.median(counts)>=30 and angles and np.median(angles)>=1.5),
            'limits':'Track support and internal parallax checks do not establish ground-truth pose or surface accuracy.'}
    (run/'geometry_audit.json').write_text(json.dumps(result,indent=2,allow_nan=False),'utf-8');print(run.name,json.dumps(result),flush=True)
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--runs',nargs='+',required=True);args=p.parse_args()
    paths=[Path(p).resolve() for p in args.runs];os.chdir(ROOT)
    for path in paths:audit(path)
if __name__=='__main__':main()
