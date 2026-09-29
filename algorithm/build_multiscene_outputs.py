"""Complete measured surface refinement, inspection and offline packages for selected runs."""
import argparse
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import time
from mesh_io import export_npz
ROOT=Path(__file__).resolve().parent


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--selected',default='experiments/selected_runs.json')
    p.add_argument('--captures',default='experiments/captures.json');p.add_argument('--cases',nargs='+')
    p.add_argument('--colmap',required=True);p.add_argument('--gpu-python',default=sys.executable)
    p.add_argument('--repo',default='third_party/crack-seg');p.add_argument('--model-profile',default='results/ctcd_adaptation_grouped_v2/model_profile.json')
    p.add_argument('--stage',choices=['geometry','inspection','web','all'],default='all');args=p.parse_args()
    colmap=shutil.which(args.colmap) or str(Path(args.colmap).resolve())
    gpu_python=shutil.which(args.gpu_python) or str(Path(args.gpu_python).resolve());repo=str(Path(args.repo).resolve())
    selected=json.loads((ROOT/args.selected).read_text('utf-8'));captures={x['id']:x for x in json.loads((ROOT/args.captures).read_text('utf-8'))}
    profile=ROOT/args.model_profile;profile_data=json.loads(profile.read_text('utf-8'))
    os.chdir(ROOT)
    for case in selected:
        if args.cases and case['id'] not in args.cases:continue
        run=ROOT/case['run'];frames=ROOT/case['frames'];capture=captures[case['id']];scene=json.loads((run/'scene.json').read_text('utf-8'))
        steps=[]
        def execute(name,command):
            log=run/(name+'.log');print('BUILD',case['id'],name,flush=True);start=time.perf_counter()
            with log.open('w',encoding='utf-8') as handle:process=subprocess.run(command,cwd=ROOT,stdout=handle,stderr=subprocess.STDOUT)
            steps.append({'stage':name,'returncode':process.returncode,'elapsed_s':time.perf_counter()-start})
            if process.returncode:raise RuntimeError(f'{case["id"]}/{name} failed; inspect {log}')
        def python(script,arguments,gpu=False):return [gpu_python if gpu else sys.executable,'-X','utf8',str(ROOT/script),*map(str,arguments)]
        if args.stage in ('geometry','all'):
            audit=json.loads((run/'geometry_audit.json').read_text('utf-8'))
            if not audit['dense_readiness_heuristic']:raise ValueError('Selected model did not pass geometric support checks')
            if not (run/'dense/filtered/postprocess_report.json').exists():execute('mesh_postprocess',python('mesh_postprocess.py',['--run',run,'--colmap',colmap]))
            if not (run/'surface.npz').exists():export_npz(run/'dense/mesh_oriented.ply',run/'surface.npz',scene['scene_id'])
            if not (run/'dense/textured/mesh.ply').exists():execute('raw_texture',[colmap,'mesh_texturer','--input_path',case['run']+'/dense/mesh_oriented.ply','--output_path',case['run']+'/dense/textured','--workspace_path',case['run']+'/dense'])
            if not (run/'regularization/report.json').exists():
                refine_args=['--run',run,'--prior',case['prior'],'--rings',case.get('rings',120),'--angles',case.get('angles',64),'--warp-axial']
                if case.get('real_length'):refine_args+=['--real-length',case['real_length']]
                if case.get('domain_coverage'):refine_args+=['--domain-coverage',case['domain_coverage'],'--domain-points',case.get('domain_points',60)]
                if case.get('max_unsupported'):refine_args+=['--max-unsupported',case['max_unsupported']]
                if case.get('fit_order'):refine_args+=['--fit-order',case['fit_order']]
                if case.get('control_stations'):refine_args+=['--control-stations',case['control_stations']]
                if case.get('residual_gate'):refine_args+=['--residual-gate',case['residual_gate']]
                execute('refinement',python('refine_surface.py',refine_args))
            if not (run/'regularization/textured/mesh.ply').exists():
                execute('refined_texture',[colmap,'mesh_texturer','--input_path',case['run']+'/regularization/surface_regularized.ply',
                    '--output_path',case['run']+'/regularization/textured','--workspace_path',case['run']+'/dense'])
        if args.stage in ('inspection','all'):
            if (run/'detect/detections.json').exists():
                detection=json.loads((run/'detect/detections.json').read_text('utf-8'))
                if (detection['scene_id']!=scene['scene_id'] or detection['detector'].get('checkpoint_sha256')!=profile_data['checkpoint_sha256']
                    or detection['detector']['threshold']!=profile_data['decision_threshold']):
                    raise ValueError('Existing inference uses different geometry/model; use a new run')
            else:
                arguments=['--images',frames/'images','--out',run/'detect','--repo',repo,'--model-profile',profile,'--scene',run/'scene.json',
                           '--device','cuda','--overlay']
                if capture.get('image_mask'):arguments+=['--valid-mask',ROOT/capture['image_mask']]
                execute('detection',python('defect_detect.py',arguments,True))
            if not (run/'localized.json').exists():execute('localization',python('localize_defects.py',
                ['--scene',run/'scene.json','--mesh',run/'surface.npz','--detections',run/'detect/detections.json','--out',run/'localized.json','--backend','gpu'],True))
            if not (run/'regularization/localized.json').exists():execute('refined_localization',python('relocalize_regularized.py',['--run',run,'--backend','gpu'],True))
            if not (run/'multiview.json').exists():
                arguments=['--run',run,'--frames',frames/'frames.json']
                if capture.get('image_mask'):arguments+=['--valid-mask',ROOT/capture['image_mask']]
                execute('multiview',python('multiview_evidence.py',arguments,True))
        if args.stage in ('web','all'):
            source={**capture,'url':capture['source_url'],'author':case['author'],'license':case['license'],'review_notes':case.get('review_notes',[])}
            (run/'source_metadata.json').write_text(json.dumps(source,ensure_ascii=False,indent=2),'utf-8')
            arguments=['--scene',run/'scene.json','--localized',run/'localized.json','--detections',run/'detect/detections.json',
                '--textured-mesh',run/'dense/textured/mesh.ply','--texture',run/'dense/textured/texture.png',
                '--images',frames/'images','--frames',frames/'frames.json','--quality',run/'quality.json',
                '--regularized',run/'regularization','--source-metadata',run/'source_metadata.json','--multiview',run/'multiview.json',
                '--title',case['title'],'--out',ROOT/'web/multiscene'/case['id']]
            if case['browser_video']:arguments+=['--video',ROOT/capture['path']]
            execute('web_export',python('export_web.py',arguments))
        (run/('build_'+args.stage+'.json')).write_text(json.dumps({'case':case['id'],'scene_id':scene['scene_id'],'steps':steps},indent=2),'utf-8')
        print('BUILT',case['id'],args.stage,flush=True)
if __name__=='__main__':main()
