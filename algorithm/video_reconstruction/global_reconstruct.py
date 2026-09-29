"""Recover a globally optimized model from a separate copy of measured feature matches."""
import argparse
import json
import os
from pathlib import Path
import shutil
import time
import pycolmap
from reconstruct import export_scene,native_path,image_paths,new_directory
ROOT=Path(__file__).resolve().parent

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--database',required=True,type=Path)
    p.add_argument('--images',required=True,type=Path);p.add_argument('--out',required=True,type=Path)
    p.add_argument('--fixed-intrinsics',action='store_true');args=p.parse_args()
    database=args.database.resolve();images=args.images.resolve();out=args.out.resolve();os.chdir(ROOT)
    if not hasattr(pycolmap,'global_mapping'):raise RuntimeError('Global reconstruction requires PyCOLMAP 4.2 or compatible newer API')
    new_directory(out);shutil.copy2(database,out/'database.db');start=time.perf_counter()
    calibration={'provided_intrinsics_fixed':args.fixed_intrinsics}
    if not args.fixed_intrinsics:
        with pycolmap.Database.open(native_path(out/'database.db')) as db:
            calibration['before']=[{'camera_id':c.camera_id,'model':c.model.name,'params':c.params.tolist()} for c in db.read_all_cameras()]
        calibration['view_graph_calibration_succeeded']=bool(pycolmap.calibrate_view_graph(native_path(out/'database.db')))
        with pycolmap.Database.open(native_path(out/'database.db')) as db:
            calibration['after']=[{'camera_id':c.camera_id,'model':c.model.name,'params':c.params.tolist()} for c in db.read_all_cameras()]
    (out/'self_calibration.json').write_text(json.dumps(calibration,indent=2),'utf-8')
    options=pycolmap.GlobalPipelineOptions();options.num_threads=4;options.random_seed=0
    options.mapper.num_threads=4;options.mapper.random_seed=0
    options.mapper.global_positioning.use_gpu=False
    options.mapper.bundle_adjustment.ceres.use_gpu=False
    options.mapper.bundle_adjustment.ceres.solver_options.num_threads=4
    options.mapper.bundle_adjustment.refine_focal_length=not args.fixed_intrinsics
    options.mapper.bundle_adjustment.refine_extra_params=not args.fixed_intrinsics
    options.mapper.bundle_adjustment.refine_principal_point=False
    maps=pycolmap.global_mapping(native_path(out/'database.db'),native_path(images),native_path(out/'components'),options)
    if not maps:raise RuntimeError('Global reconstruction also failed; no inferred camera trajectory is substituted')
    key=max(maps,key=lambda k:(maps[k].num_reg_images(),maps[k].num_points3D()));model=maps[key]
    quality=export_scene(model,out,'real_camera_video',len(image_paths(images)))
    quality.update({'backend':f'pycolmap {pycolmap.__version__} / global SfM (GLOMAP)','elapsed_s':time.perf_counter()-start,
        'calibration_fixed':args.fixed_intrinsics,'components':[{'id':int(k),'registered_images':m.num_reg_images(),'points3D':m.num_points3D()} for k,m in maps.items()],
        'unregistered_images':sorted(set(p.name for p in image_paths(images))-{im.name for im in model.images.values() if im.has_pose}),
        'note':'Global recovery uses a copied feature database; the incremental failure and its inputs are retained.'})
    (out/'quality.json').write_text(json.dumps(quality,indent=2),'utf-8');print(json.dumps(quality,indent=2),flush=True)
if __name__=='__main__':main()
