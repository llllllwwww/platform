"""Recast the same image rays onto a fitted surface, preserving original geometric evidence."""
import argparse
from collections import Counter
import json
from pathlib import Path
import numpy as np
from localize_defects import localize,load_ray_caster

ROOT=Path(__file__).resolve().parent

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run',default=str(ROOT/'results/dvp_tunnel'));p.add_argument('--backend',default='gpu',choices=['gpu','cpu','auto'])
    args=p.parse_args();run=Path(args.run);out=run/'regularization'
    scene=json.loads((run/'scene.json').read_text('utf-8'))
    detections=json.loads((run/'detect/detections.json').read_text('utf-8'))
    original=json.loads((run/'localized.json').read_text('utf-8'))
    report=json.loads((out/'report.json').read_text('utf-8'))
    with np.load(out/'surface_regularized.npz') as m:mesh={k:m[k] for k in m.files}
    raw=localize(scene,detections,mesh,load_ray_caster(args.backend))
    original_by_id={d['id']:d for d in original['defects']}
    tolerance=2*report['local_support_threshold_sfm_unit']
    axial=report.get('surface_kind','regularized_tunnel')=='regularized_tunnel'
    if axial:
        origin=np.array(report['coordinate_origin']);axis=np.array(report['coordinate_basis_rows'])[2]
        lo,hi=report['station_range']
    counts=Counter();displacements=[]
    for item in raw['defects']:
        reference=original_by_id[item['id']];points=[];item['supported_hit_count']=0;item['inferred_hit_count']=0
        assert len(reference['observations'])==len(item['observations'])
        for obs,ref in zip(item['observations'],reference['observations']):
            assert obs['pixel']==ref['pixel']
            obs['original_point']=ref['point'];obs['original_status']=ref['status']
            reason=obs['status']
            if ref['status']!='hit':reason='no_original_surface_evidence'
            elif axial and not lo<=float((np.array(ref['point'])-origin)@axis)<=hi:reason='outside_fitted_segment'
            elif obs['status']=='hit':
                distance=float(np.linalg.norm(np.array(obs['point'])-ref['point']))
                obs['displacement_sfm_unit']=distance
                if distance>tolerance:reason='excessive_relocation'
                else:
                    obs['local_support']=bool(np.mean(mesh['support'][mesh['faces'][obs['triangle_id']]])>=2/3)
                    reason='hit'
                    points.append(obs['point']);displacements.append(distance)
                    key='supported_hit_count' if obs['local_support'] else 'inferred_hit_count';item[key]+=1
            if reason!='hit':
                obs['rejected_fitted_point']=obs['point'];obs['point']=None;obs['triangle_id']=None
            obs['status']=reason;counts[reason]+=1
        item['points']=points;item['hit_count']=len(points)
        item['status']='partial' if points else 'no_accepted_fitted_hit'
    raw['surface_id']=report['surface_id'];raw['surface_kind']=report.get('surface_kind','regularized_tunnel')
    raw['summary'].update(hit_count=sum(d['hit_count'] for d in raw['defects']),
      observations_by_status=dict(counts),candidates_with_mapped_points=sum(bool(d['points']) for d in raw['defects']),
      supported_hit_count=sum(d['supported_hit_count'] for d in raw['defects']),inferred_hit_count=sum(d['inferred_hit_count'] for d in raw['defects']),
      max_allowed_displacement_sfm_unit=tolerance,
      accepted_displacement_median=float(np.median(displacements)) if displacements else None,
      accepted_displacement_p95=float(np.quantile(displacements,.95)) if displacements else None)
    raw['limits']+=['Accepted points require an original mesh hit on the same image ray, within the fitted segment and bounded displacement.',
                     'Mapping to a regularized surface is a display association; it does not improve calibrated defect coordinate accuracy.']
    (out/'localized.json').write_text(json.dumps(raw,ensure_ascii=False,allow_nan=False,separators=(',',':')),'utf-8')
    print(json.dumps(raw['summary'],indent=2))
if __name__=='__main__':main()
