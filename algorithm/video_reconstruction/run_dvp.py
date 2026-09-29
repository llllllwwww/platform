"""Portable command runner for the recorded DVP video reconstruction experiment."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parent
VIDEO_URL='https://upload.wikimedia.org/wikipedia/commons/4/41/DVP_Tunnel_at_Moccasin_Trail_Park_in_Toronto_%2820181008093109%29.webm'
WEIGHTS_URL='https://huggingface.co/ishaan1402/crack-seg/resolve/main/unet_v3.pth'

def commands(args):
    run=ROOT/'results'/args.run_name
    frames=ROOT/'data'/('frames_'+args.run_name)
    video=ROOT/'data/dvp_tunnel/source.webm'
    checkpoint=Path(args.checkpoint).resolve()
    gpu_python=shutil.which(args.gpu_python) or args.gpu_python
    colmap=shutil.which(args.colmap) or args.colmap
    colmap=str(Path(colmap).resolve()) if Path(colmap).is_file() else colmap
    calls=[]
    def py(script,*options,gpu=False):
        calls.append([gpu_python if gpu else sys.executable,str(ROOT/script),*map(str,options)])
    if not video.is_file():py('download_file.py',VIDEO_URL,video)
    if not checkpoint.is_file():py('download_file.py',WEIGHTS_URL,checkpoint)
    if not (ROOT/'third_party/crack-seg').is_dir():py('fetch_sources.py','crack-seg')
    py('reconstruct.py','video','--video',video,'--out',frames,'--sample-hz','2','--max-frames','150')
    py('reconstruct.py','sfm','--images',frames/'images','--out',run,'--matching','sequential','--overlap','8',
       '--source-kind','real_camera_video','--max-image-size','1600','--max-features','6000')
    py('dense_reconstruct.py','--colmap',colmap,'--images',frames/'images','--model',run/'model','--out',run/'dense')
    py('mesh_io.py','--ply',run/'dense/mesh.ply','--scene',run/'scene.json','--out',run/'surface.npz')
    py('defect_detect.py','--images',frames/'images','--scene',run/'scene.json','--out',run/'detect',
       '--checkpoint',checkpoint,'--device',args.device,'--overlay','--threshold','0.7','--min-area','60','--max-points','80',gpu=True)
    backend='gpu' if args.device=='cuda' else 'cpu'
    py('localize_defects.py','--scene',run/'scene.json','--detections',run/'detect/detections.json','--mesh',run/'surface.npz',
       '--out',run/'localized.json','--backend',backend,gpu=True)
    extra=[]
    if not args.skip_optimization:
        fit=run/'regularization'
        py('regularize_tunnel.py','--run',run)
        # Keep native COLMAP arguments ASCII-relative on Windows.
        relative=fit.relative_to(ROOT).as_posix()
        calls.append([colmap,'mesh_texturer','--input_path',relative+'/surface_regularized.ply',
                      '--output_path',relative+'/textured','--workspace_path',run.relative_to(ROOT).as_posix()+'/dense'])
        py('relocalize_regularized.py','--run',run,'--backend',backend,gpu=True)
        extra=['--regularized',fit]
    py('export_web.py','--scene',run/'scene.json','--localized',run/'localized.json','--detections',run/'detect/detections.json',
       '--textured-mesh',run/'dense/textured/mesh.ply','--texture',run/'dense/textured/texture.png',
       '--images',frames/'images','--frames',frames/'frames.json','--video',video,'--quality',run/'quality.json',
       '--out',ROOT/'web',*extra)
    return calls,run,frames

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--colmap',default=os.environ.get('COLMAP_BIN','colmap'),help='COLMAP 4.2 CUDA executable or command on PATH')
    p.add_argument('--gpu-python',default=sys.executable,help='Python with PyTorch and PyCOLMAP for inference/ray casting')
    p.add_argument('--checkpoint',default=str(ROOT/'data/crack_model/unet_v3.pth'))
    p.add_argument('--device',choices=['cpu','cuda'],default='cuda',help='Detection and ray device; dense MVS still requires CUDA')
    p.add_argument('--run-name',default='dvp_tunnel')
    p.add_argument('--skip-optimization',action='store_true')
    p.add_argument('--plan',action='store_true',help='Print commands without downloading or modifying anything')
    args=p.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_-]+',args.run_name):p.error('run-name must be ASCII letters, digits, underscores or hyphens')
    calls,run,frames=commands(args)
    if args.plan:
        print(json.dumps(calls,ensure_ascii=False,indent=2));return
    executable=calls[0][0]
    if not (shutil.which(args.colmap) or Path(args.colmap).is_file()):p.error('COLMAP executable not found; set --colmap or COLMAP_BIN')
    if not (shutil.which(args.gpu_python) or Path(args.gpu_python).is_file()):p.error('GPU Python executable not found')
    for path in [run,frames]:
        if path.exists() and any(path.iterdir()):p.error(f'Output is not empty: {path}; select a new --run-name')
    print('Source video: Sikander Iqbal / CC BY-SA 4.0. Model source and weights remain subject to upstream terms.',flush=True)
    for command in calls:
        print('RUN',json.dumps(command,ensure_ascii=False),flush=True)
        subprocess.run(command,cwd=ROOT,check=True)
    print('Completed:',ROOT/'web/index.html')

if __name__=='__main__':main()
