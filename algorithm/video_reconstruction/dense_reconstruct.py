"""Run checked COLMAP CUDA stages and retain commands, logs and timings."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parent

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--colmap',required=True)
    parser.add_argument('--images',required=True)
    parser.add_argument('--model',required=True)
    parser.add_argument('--out',required=True)
    parser.add_argument('--max-image-size',type=int,default=1000)
    parser.add_argument('--iterations',type=int,default=3)
    args=parser.parse_args()
    executable=Path(args.colmap).resolve()
    paths={k:Path(getattr(args,k)).resolve() for k in ['images','model','out']}
    os.chdir(ROOT)
    rel={k:os.path.relpath(v,ROOT) for k,v in paths.items()}
    if any(not v.isascii() for v in rel.values()):
        raise ValueError('Use ASCII relative filenames under module root for COLMAP Windows')
    if not executable.is_file(): raise FileNotFoundError(executable)
    if args.iterations < 1 or args.max_image_size < 64: raise ValueError('Invalid MVS parameters')
    out=paths['out']
    if out.exists() and any(out.iterdir()): raise FileExistsError('Use a fresh dense output directory')
    out.mkdir(parents=True,exist_ok=True)
    d=rel['out']
    stages=[
      ['image_undistorter','--image_path',rel['images'],'--input_path',rel['model'],'--output_path',d,'--output_type','COLMAP','--max_image_size','1600'],
      ['patch_match_stereo','--workspace_path',d,'--workspace_format','COLMAP','--PatchMatchStereo.geom_consistency','1','--PatchMatchStereo.max_image_size',str(args.max_image_size),'--PatchMatchStereo.num_iterations',str(args.iterations)],
      ['stereo_fusion','--workspace_path',d,'--workspace_format','COLMAP','--input_type','geometric','--output_path',d+'/fused.ply'],
      ['advancing_front_mesher','--input_path',d,'--output_path',d+'/mesh.ply'],
      ['mesh_texturer','--input_path',d+'/mesh_oriented.ply','--output_path',d+'/textured','--workspace_path',d],
    ]
    report={'executable':str(executable),'stages':[],'status':'running'}
    for command in stages:
        log=out/(command[0]+'.log')
        print('Starting',command[0],flush=True)
        started=time.perf_counter()
        with log.open('wb') as handle:
            result=subprocess.run([str(executable),*command],cwd=ROOT,stdout=handle,stderr=subprocess.STDOUT,check=False)
        step={'command':command,'returncode':result.returncode,'elapsed_s':time.perf_counter()-started,'log':log.name}
        report['stages'].append(step)
        if result.returncode:
            report['status']='failed'
        (out/'run.json').write_text(json.dumps(report,indent=2), 'utf-8')
        if result.returncode: raise RuntimeError(f'{command[0]} failed: see {log}')
        print('Finished',command[0],round(step['elapsed_s'],2),'s',flush=True)
        if command[0]=='advancing_front_mesher':
            from orient_surface import orient
            orient(out/'mesh.ply',out/'fused.ply',out/'mesh_oriented.ply')
    report['status']='completed'
    (out/'run.json').write_text(json.dumps(report,indent=2), 'utf-8')

if __name__=='__main__': main()
