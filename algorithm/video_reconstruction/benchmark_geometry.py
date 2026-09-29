"""Reproducible multi-camera SfM experiments with immutable code and support-based selection.

Higher sampling rates are explicit experiments, not silently counted as a fixed-budget gain.
A registered camera without adequate landmark observations is not a successful reconstruction.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
ROOT=Path(__file__).resolve().parent


def reconstruction_score(quality):
    supported=quality.get('observation_support',{}).get('supported_registration_fraction',0) or 0
    return (supported,quality.get('registration_fraction',0),quality.get('points3D',0),-quality.get('point_mean_reprojection_error_px',1000))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--captures',default=str(ROOT/'experiments/captures.json'))
    p.add_argument('--cases',nargs='+');p.add_argument('--modes',nargs='+',choices=['baseline','calibrated','optimized'],default=['baseline','optimized'])
    p.add_argument('--global-python',help='Optional Python with PyCOLMAP 4.2 for optimized-mode global recovery')
    p.add_argument('--experiment',default='multiscene_v3');p.add_argument('--max-frames',type=int,default=60)
    p.add_argument('--sample-hz',type=float,default=2);p.add_argument('--matching',choices=['sequential','exhaustive'],default='sequential')
    args=p.parse_args()
    if args.sample_hz<=0 or args.max_frames<3:raise ValueError('Invalid sampling budget')
    captures=json.loads(Path(args.captures).read_text('utf-8'));output=ROOT/'results'/args.experiment
    output.mkdir(parents=True,exist_ok=True);reports=[]
    source_files={name:(ROOT/name).read_bytes() for name in ['reconstruct.py','keyframes.py','global_reconstruct.py','audit_geometry.py','benchmark_geometry.py']}
    implementation=hashlib.sha256(b''.join(source_files[k] for k in sorted(source_files))).hexdigest()
    snapshot=output/'_source'/implementation[:16];snapshot.mkdir(parents=True,exist_ok=True)
    for name,content in source_files.items():
        path=snapshot/name
        if path.exists() and path.read_bytes()!=content:raise ValueError('Immutable experiment source was modified')
        if not path.exists():path.write_bytes(content)
    for capture in captures:
        if args.cases and capture['id'] not in args.cases:continue
        with (ROOT/capture['path']).open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
        if capture.get('sha256') and capture['sha256']!=digest:raise ValueError('Capture file differs from its declared SHA-256')
        capture['sha256']=digest
        for asset in ['calibration','image_mask','timestamps']:
            if capture.get(asset):capture[asset+'_sha256']=hashlib.sha256((ROOT/capture[asset]).read_bytes()).hexdigest()
        for mode in args.modes:
            case=output/(capture['id']+'__'+mode);frames=case/'frames';run=case/'reconstruction'
            config={'capture':capture,'mode':mode,'max_frames':args.max_frames,'sample_hz':args.sample_hz,'matching':args.matching,
                    'feature_limit':6000,'image_max_size':1200,'overlap':12 if mode=='optimized' else 8,
                    'implementation_sha256':implementation,'global_recovery_enabled':bool(args.global_python),
                    'support_rule':'At least 30 landmark observations per camera; >=80% of input cameras supported for complete status.'}
            fingerprint=hashlib.sha256(json.dumps(config,sort_keys=True).encode()).hexdigest();report_path=case/'experiment.json'
            if report_path.exists():
                previous=json.loads(report_path.read_text('utf-8'))
                if previous['fingerprint']!=fingerprint:raise ValueError('Existing experiment configuration differs; use a new experiment name')
                if previous['status'] in ('complete','partial','failed'):
                    reports.append(previous);print('CACHED',capture['id'],mode,previous['status'],flush=True);continue
                raise ValueError('Incomplete experiment exists; inspect it and use a new experiment name')
            case.mkdir(parents=True,exist_ok=True);report={'fingerprint':fingerprint,'configuration':config,'status':'running','stages':[]}
            report_path.write_text(json.dumps(report,indent=2),'utf-8');start=time.perf_counter()
            def execute(stage,command):
                log=case/(stage+'.log');print('RUN',capture['id'],mode,stage,flush=True);started=time.perf_counter()
                with log.open('w',encoding='utf-8') as handle:process=subprocess.run(command,cwd=ROOT,stdout=handle,stderr=subprocess.STDOUT)
                report['stages'].append({'stage':stage,'returncode':process.returncode,'elapsed_s':time.perf_counter()-started,'log':log.relative_to(ROOT).as_posix()})
                return process.returncode
            try:
                command=[sys.executable,'-X','utf8',str(snapshot/'reconstruct.py'),'video','--video',str(ROOT/capture['path']),
                    '--out',str(frames),'--sample-hz',str(args.sample_hz),'--max-frames',str(args.max_frames),
                    '--selection','quality' if mode=='optimized' else 'uniform']
                if execute('video',command):raise RuntimeError('Video preparation failed; see video.log')
                frame_data=json.loads((frames/'frames.json').read_text('utf-8'))
                if capture.get('timestamps'):
                    timing=json.loads((ROOT/capture['timestamps']).read_text('utf-8'))
                    for entry in frame_data['frames']:entry['source_timestamp_ns']=timing['frames'][entry['frame_index']]['timestamp_ns']
                    (frames/'frames.json').write_text(json.dumps(frame_data,indent=2),'utf-8')
                times=[x['nominal_time_s'] for x in frame_data['frames'] if x['accepted']]
                report['temporal_coverage_s']=[min(times),max(times)]
                command=[sys.executable,'-X','utf8',str(snapshot/'reconstruct.py'),'sfm','--images',str(frames/'images'),'--out',str(run),
                    '--source-kind','real_camera_video','--matching',args.matching,'--overlap',str(config['overlap']),
                    '--max-features','6000','--max-image-size','1200',
                    '--camera-model',capture.get('camera_model','SIMPLE_RADIAL') if mode!='baseline' else 'SIMPLE_RADIAL']
                if mode=='optimized':command.append('--affine-sift')
                if mode!='baseline':
                    if capture.get('calibration'):command.extend(['--calibration',str(ROOT/capture['calibration'])])
                    if capture.get('image_mask'):command.extend(['--image-mask',str(ROOT/capture['image_mask'])])
                status=execute('sfm',command);candidates=[]
                if not status:candidates.append((run,json.loads((run/'quality.json').read_text('utf-8'))))
                weak=not candidates or reconstruction_score(candidates[0][1])[0]<.8
                if weak and mode=='optimized' and args.global_python and (run/'database.db').is_file():
                    global_run=case/'global_reconstruction'
                    command=[args.global_python,'-X','utf8',str(snapshot/'global_reconstruct.py'),'--database',str(run/'database.db'),
                        '--images',str(frames/'images'),'--out',str(global_run)]
                    if capture.get('calibration'):command.append('--fixed-intrinsics')
                    if not execute('global_sfm',command):candidates.append((global_run,json.loads((global_run/'quality.json').read_text('utf-8'))))
                if not candidates:raise RuntimeError('No reconstruction produced; retain failure logs and inspect camera/overlap conditions')
                selected,quality=max(candidates,key=lambda item:reconstruction_score(item[1]))
                report['candidates']=[{'path':path.relative_to(ROOT).as_posix(),'quality':q} for path,q in candidates]
                report['reconstruction_path']=selected.relative_to(ROOT).as_posix();report['quality']=quality
                if execute('audit',[args.global_python or sys.executable,'-X','utf8',str(snapshot/'audit_geometry.py'),'--runs',str(selected)]):raise RuntimeError('Geometry audit failed')
                audit=json.loads((selected/'geometry_audit.json').read_text('utf-8'));report['geometry_audit']=audit
                report['status']='complete' if reconstruction_score(quality)[0]>=.8 and audit['dense_readiness_heuristic'] else 'partial'
            except Exception as error:report['status']='failed';report['error']=str(error)
            report['elapsed_s']=time.perf_counter()-start;report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf-8')
            reports.append(report);(output/'summary.json').write_text(json.dumps(reports,ensure_ascii=False,indent=2),'utf-8')
            print('RESULT',capture['id'],mode,report['status'],round(report['elapsed_s'],1),flush=True)
    (output/'summary.json').write_text(json.dumps(reports,ensure_ascii=False,indent=2),'utf-8')
if __name__=='__main__':main()
