"""Video keyframes -> COLMAP SfM -> camera poses and sparse scene.
All geometry comes from image correspondences. No procedural tunnel fallback.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import time
import numpy as np

ROOT = Path(__file__).resolve().parent
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.tif', '.tiff', '.bmp'}

def native_path(path):
    # COLMAP 3.13 Windows C++ file APIs fail on UTF-8 absolute paths.
    # Relative ASCII paths work even when the process cwd has a Chinese name.
    value = os.path.relpath(Path(path).resolve(), Path.cwd())
    if os.name == 'nt' and not value.isascii():
        raise ValueError('COLMAP on Windows needs ASCII relative filenames. Put inputs/results under this module using ASCII names.')
    return value

def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), 'utf-8')

def new_directory(path):
    path = Path(path).resolve()
    if path.exists() and any(path.iterdir()):
        raise ValueError(f'Output must be empty to avoid mixing runs: {path}')
    path.mkdir(parents=True, exist_ok=True)
    return path

def image_paths(directory):
    directory = Path(directory).resolve()
    if not directory.is_dir():
        raise ValueError(f'Image directory does not exist: {directory}')
    return sorted(p for p in directory.iterdir() if p.suffix.lower() in IMAGE_EXTENSIONS)

def prepare_video(args):
    import cv2
    if args.sample_hz <= 0 or args.max_frames < 2 or args.min_sharpness < 0:
        raise ValueError('sample-hz > 0, max-frames >= 2, min-sharpness >= 0 required')
    cap = cv2.VideoCapture(str(Path(args.video).resolve()))
    if not cap.isOpened():
        raise ValueError('Cannot open video')
    fps = float(cap.get(cv2.CAP_PROP_FPS))
    if not np.isfinite(fps) or fps <= 0:
        cap.release()
        raise ValueError('Video has no valid frame rate; transcode with preserved timestamps first')
    out = new_directory(args.out)
    images = out / 'images'
    images.mkdir()
    stride = max(1, round(fps / args.sample_hz))
    entries = []
    index = accepted = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if index % stride == 0:
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                # Resolution dependent diagnostic: not a universal blur detector.
                sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
                black = float((gray < 8).mean())
                white = float((gray > 247).mean())
                keep = sharpness >= args.min_sharpness
                name = f'frame_{index:08d}.png'
                timestamp_ms = float(cap.get(cv2.CAP_PROP_POS_MSEC))
                entries.append({'image_name': name, 'frame_index': index,
                                'timestamp_ms_decoder': timestamp_ms,
                                'nominal_time_s': index / fps,
                                'sharpness_laplacian_variance': sharpness,
                                'dark_fraction': black, 'saturated_fraction': white,
                                'accepted': keep})
                if keep:
                    success, buffer = cv2.imencode('.png', frame)
                    if not success:
                        raise RuntimeError(f'Cannot encode frame {index}')
                    (images / name).write_bytes(buffer.tobytes())
                    accepted += 1
                if accepted >= args.max_frames:
                    break
            index += 1
    finally:
        cap.release()
    gaps = np.diff([e['nominal_time_s'] for e in entries if e['accepted']])
    report = {'source_kind': 'real_camera_video', 'video': str(Path(args.video).resolve()),
              'fps_reported': fps, 'sample_stride': stride, 'accepted_frames': accepted,
              'max_accepted_gap_s': float(gaps.max()) if len(gaps) else None,
              'frames': entries, 'notes': ['Original pixel geometry retained; no electronic stabilization or crop.',
              'Decoder timestamps recorded; nominal_time_s assumes constant frame rate.',
              'Blur rejection can break overlap. Review gaps before SfM.',
              'This is quality-screened temporal sampling, not a learned keyframe selector.']}
    write_json(out / 'frames.json', report)
    if accepted < 2:
        raise ValueError('Fewer than two accepted frames; see frames.json')
    print(json.dumps({'images': str(images), 'accepted': accepted}, ensure_ascii=False))

def read_calibration(path, files):
    import pycolmap
    from PIL import Image
    config = json.loads(Path(path).read_text('utf-8'))
    required = {'model', 'width', 'height', 'params'}
    if not required <= config.keys():
        raise ValueError(f'Calibration requires {required}')
    for file in files:
        with Image.open(file) as image:
            if image.size != (config['width'], config['height']):
                raise ValueError(f'Calibration resolution differs from image: {file.name}')
    camera = pycolmap.Camera(model=config['model'], width=config['width'],
                            height=config['height'], params=config['params'])
    if not camera.verify_params() or not np.isfinite(camera.params).all():
        raise ValueError('Invalid camera calibration')
    if camera.focal_length_x <= 0 or camera.focal_length_y <= 0:
        raise ValueError('Calibration focal lengths must be positive')
    return config

def resolve_scale(rec, anchor_file):
    if anchor_file is None:
        return {'status': 'arbitrary', 'unit': 'sfm_unit', 'factor': 1.0,
                'reason': 'Monocular images alone do not determine metric scale.'}
    anchor = json.loads(Path(anchor_file).read_text('utf-8'))
    distance = float(anchor['distance_m'])
    if not np.isfinite(distance) or distance <= 0:
        raise ValueError('distance_m must be finite and positive')
    if anchor['kind'] == 'point_distance':
        ids = anchor['point_ids']
        if len(ids) != 2 or ids[0] == ids[1]:
            raise ValueError('Two different reconstructed point IDs required')
        positions = [rec.points3D[int(i)].xyz for i in ids]
    elif anchor['kind'] == 'camera_distance':
        names = anchor['image_names']
        if len(names) != 2 or names[0] == names[1]:
            raise ValueError('Two different registered camera names required')
        by_name = {im.name: im.projection_center() for im in rec.images.values() if im.has_pose}
        positions = [by_name[name] for name in names]
    else:
        raise ValueError('anchor kind must be point_distance or camera_distance')
    estimated = float(np.linalg.norm(positions[0] - positions[1]))
    if estimated <= 1e-10 or not np.isfinite(estimated):
        raise ValueError('Scale anchor has zero/invalid reconstructed baseline')
    return {'status': 'metric', 'unit': 'm', 'factor': distance / estimated,
            'anchor': anchor, 'unscaled_distance': estimated,
            'reason': 'Scale anchored to supplied measurement; accuracy still requires independent validation.'}

def scene_identifier(source_kind, rec, scale):
    """内容派生的稳定 scene_id。

    localize_defects.py 要求 scene 与 mesh 的 scene_id 完全一致；用内容哈希而不是
    随机 UUID，同一输入重跑得到同一 ID，不需要人工在两个文件之间传值。
    """
    camera_signature = [{'name': im.name, 'model': rec.cameras[im.camera_id].model.name,
                         'params': rec.cameras[im.camera_id].params.tolist(),
                         'world_to_camera': im.cam_from_world().matrix().tolist()}
                        for im in sorted(rec.images.values(), key=lambda i: i.name) if im.has_pose]
    payload = json.dumps({'source_kind': source_kind, 'cameras': camera_signature,
                          'scale': scale,
                          'points': [(int(k), rec.points3D[k].xyz.tolist()) for k in sorted(rec.points3D)]},
                         ensure_ascii=False, sort_keys=True, allow_nan=False)
    return 'scene-' + hashlib.sha256(payload.encode('utf-8')).hexdigest()[:16]


def export_scene(rec, out, source_kind, input_count=None, anchor_file=None, max_points=100000):
    import pycolmap
    if max_points < 1:
        raise ValueError('max-points must be positive')
    if rec.num_points3D() == 0 or rec.num_reg_images() < 2:
        raise ValueError('Reconstruction has no useful geometry')
    scale = resolve_scale(rec, anchor_file)
    if scale['factor'] != 1.0:
        rec.transform(pycolmap.Sim3d(scale=scale['factor'], rotation=pycolmap.Rotation3d(),
                                   translation=np.zeros(3)))
    out.mkdir(parents=True, exist_ok=True)
    model = out / 'model'
    model.mkdir(exist_ok=True)
    rec.write(native_path(model))
    rec.write_text(native_path(model))
    rec.export_PLY(native_path(out / 'sparse.ply'))
    cameras = []
    for image in sorted(rec.images.values(), key=lambda x: x.name):
        if not image.has_pose:
            continue
        camera = rec.cameras[image.camera_id]
        matrix = np.eye(4)
        matrix[:3] = image.cam_from_world().matrix()
        cameras.append({'image_name': image.name, 'image_id': image.image_id,
                        'width': camera.width, 'height': camera.height,
                        'model': camera.model.name, 'params': camera.params.tolist(),
                        'world_to_camera': matrix.tolist(),
                        'center': image.projection_center().tolist()})
    ids = sorted(rec.points3D.keys())
    if len(ids) > max_points:
        ids = [ids[i] for i in np.linspace(0, len(ids)-1, max_points, dtype=int)]
    xyz = np.array([rec.points3D[i].xyz for i in ids])
    if not np.isfinite(xyz).all():
        raise ValueError('Non-finite reconstructed coordinates')
    scene = {'schema_version': 1, 'scene_id': scene_identifier(source_kind, rec, scale),
             'source_kind': source_kind, 'geometry_kind': 'sparse_points',
             'scale': scale, 'coordinate_convention': 'COLMAP world; camera x right, y down, z forward; pixel centers at 0.5',
             'points': xyz.tolist(), 'point_ids': ids,
             'colors': [rec.points3D[i].color.tolist() for i in ids], 'cameras': cameras,
             'defects': [], 'surface_available': False,
             'warnings': ['Sparse landmarks are not a watertight tunnel surface.',
                          'No defect positions or tunnel dimensions inferred from sparse point gaps.']}
    write_json(out / 'scene.json', scene)
    errors = np.array([p.error for p in rec.points3D.values()])
    tracks = [p.track.length() for p in rec.points3D.values()]
    quality = {'source_kind': source_kind, 'input_images': input_count,
               'registered_images': rec.num_reg_images(),
               'registration_fraction': rec.num_reg_images()/input_count if input_count else None,
               'points3D': rec.num_points3D(), 'viewer_points': len(ids),
               'point_mean_reprojection_error_px': float(errors.mean()),
               'point_median_reprojection_error_px': float(np.median(errors)),
               'point_p95_reprojection_error_px': float(np.quantile(errors, .95)),
               'mean_track_length': float(np.mean(tracks)), 'scale': scale,
               'limits': ['Reprojection residual is internal fit, not metric accuracy.',
                          'No held-out tunnel video or survey ground truth was evaluated by this export.']}
    write_json(out / 'quality.json', quality)
    return quality

def run_sfm(args):
    import pycolmap
    from PIL import Image
    files = image_paths(args.images)
    if len(files) < 3:
        raise ValueError('At least three images required for this baseline')
    if args.overlap < 2 or args.max_image_size < 64 or args.max_features < 128 or args.threads < 1:
        raise ValueError('Invalid feature/matching resource limits')
    # One physical camera, same pixel geometry. Focal changes require separate calibration groups.
    sizes = set()
    for file in files:
        with Image.open(file) as image:
            sizes.add(image.size)
    if args.camera_mode == 'single' and len(sizes) != 1:
        raise ValueError('Single-camera baseline requires equal image resolutions; use per_image for mixed public photos')
    calibration = read_calibration(args.calibration, files) if args.calibration else None
    out = new_directory(args.out)
    start = time.perf_counter()
    options = pycolmap.ImageReaderOptions()
    options.camera_model = calibration['model'] if calibration else 'SIMPLE_RADIAL'
    if calibration:
        options.camera_params = ','.join(map(str, calibration['params']))
    sift = pycolmap.FeatureExtractionOptions()
    sift.max_image_size = args.max_image_size
    sift.sift.max_num_features = args.max_features
    sift.num_threads = args.threads
    database = native_path(out / 'database.db')
    pycolmap.set_random_seed(0)
    pycolmap.extract_features(database_path=database, image_path=native_path(args.images),
                             image_names=[p.name for p in files],
                             camera_mode=pycolmap.CameraMode.SINGLE if args.camera_mode == 'single' else pycolmap.CameraMode.PER_IMAGE,
                             camera_model=options.camera_model,
                             reader_options=options, extraction_options=sift, device=pycolmap.Device.cpu)
    match_options = pycolmap.FeatureMatchingOptions()
    match_options.num_threads = args.threads
    match_options.guided_matching = True
    if args.matching == 'exhaustive':
        pycolmap.match_exhaustive(database, matching_options=match_options, device=pycolmap.Device.cpu)
    else:
        sequential = pycolmap.SequentialPairingOptions()
        sequential.overlap = args.overlap
        sequential.loop_detection = False
        pycolmap.match_sequential(database, matching_options=match_options,
                                 pairing_options=sequential, device=pycolmap.Device.cpu)
    mapper = pycolmap.IncrementalPipelineOptions()
    mapper.num_threads = args.threads
    mapper.ba_refine_focal_length = calibration is None
    mapper.ba_refine_extra_params = calibration is None
    mapper.ba_refine_principal_point = False
    # Keep standard geometric thresholds. Do not weaken initialization to force a model.
    maps = pycolmap.incremental_mapping(database_path=database, image_path=native_path(args.images),
                                       output_path=native_path(out / 'components'), options=mapper)
    if not maps:
        write_json(out / 'failure.json', {'status': 'no_reconstruction',
                   'input_images': len(files), 'elapsed_s': time.perf_counter()-start,
                   'advice': 'Inspect overlap, blur, texture, calibration, pure rotation and rolling shutter.'})
        raise RuntimeError('COLMAP could not initialize any component; no synthetic fallback is generated')
    key = max(maps, key=lambda k: (maps[k].num_reg_images(), maps[k].num_points3D()))
    quality = export_scene(maps[key], out, args.source_kind, len(files), args.scale_anchor, args.max_points)
    quality.update({'backend': f'pycolmap {pycolmap.__version__} / CPU SIFT',
                    'elapsed_s': time.perf_counter()-start, 'selected_component': int(key),
                    'components': [{'id': int(k), 'registered_images': m.num_reg_images(),
                                    'points3D': m.num_points3D()} for k, m in maps.items()],
                    'unregistered_images': sorted(set(p.name for p in files) -
                        {im.name for im in maps[key].images.values() if im.has_pose}),
                    'configuration': {'matching': args.matching, 'overlap': args.overlap,
                                      'max_image_size': args.max_image_size,
                                      'max_features': args.max_features, 'calibration_fixed': calibration is not None},
                    'note': 'Only largest connected component exported; components are not silently merged.'})
    write_json(out / 'quality.json', quality)
    print(json.dumps(quality, ensure_ascii=False, indent=2))

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    video = commands.add_parser('video', help='Decode video without changing pixel geometry')
    video.add_argument('--video', required=True)
    video.add_argument('--out', required=True)
    video.add_argument('--sample-hz', type=float, default=3)
    video.add_argument('--max-frames', type=int, default=150)
    video.add_argument('--min-sharpness', type=float, default=0)
    video.set_defaults(func=prepare_video)
    sfm = commands.add_parser('sfm', help='Run CPU COLMAP baseline on images')
    sfm.add_argument('--images', required=True)
    sfm.add_argument('--out', required=True)
    sfm.add_argument('--calibration')
    sfm.add_argument('--camera-mode', choices=['single', 'per_image'], default='single')
    sfm.add_argument('--matching', choices=['sequential', 'exhaustive'], default='sequential')
    sfm.add_argument('--overlap', type=int, default=10)
    sfm.add_argument('--max-image-size', type=int, default=1600)
    sfm.add_argument('--max-features', type=int, default=8192)
    sfm.add_argument('--threads', type=int, default=4)
    sfm.add_argument('--max-points', type=int, default=100000)
    sfm.add_argument('--scale-anchor')
    sfm.add_argument('--source-kind', choices=['real_camera_video', 'real_images', 'public_example', 'synthetic_test'], default='real_images')
    sfm.set_defaults(func=run_sfm)
    export = commands.add_parser('export', help='Import existing COLMAP/hloc reconstruction')
    export.add_argument('--model', required=True)
    export.add_argument('--out', required=True)
    export.add_argument('--scale-anchor')
    export.add_argument('--max-points', type=int, default=100000)
    export.add_argument('--source-kind', choices=['real_camera_video', 'real_images', 'public_example', 'synthetic_test'], default='real_images')
    def do_export(args):
        import pycolmap
        out = new_directory(args.out)
        print(json.dumps(export_scene(pycolmap.Reconstruction(native_path(args.model)), out, args.source_kind,
                                       anchor_file=args.scale_anchor, max_points=args.max_points), ensure_ascii=False))
    export.set_defaults(func=do_export)
    args = parser.parse_args()
    for key in ('images', 'out', 'video', 'model', 'calibration', 'scale_anchor'):
        value = getattr(args, key, None)
        if value is not None:
            setattr(args, key, str(Path(value).resolve()))
    os.chdir(ROOT)
    args.func(args)

if __name__ == '__main__':
    main()
