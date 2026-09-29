"""Select original video frames by local image quality without warping camera geometry."""
from __future__ import annotations
import json
from pathlib import Path
import cv2
import numpy as np


def measure(frame):
    gray=cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY)
    ratio=min(1.,640/max(gray.shape));small=cv2.resize(gray,None,fx=ratio,fy=ratio,interpolation=cv2.INTER_AREA)
    sharp=float(cv2.Laplacian(small,cv2.CV_64F).var())
    dark=float((small<8).mean());saturated=float((small>247).mean())
    contrast=float(np.quantile(small,.9)-np.quantile(small,.1))
    corners=cv2.goodFeaturesToTrack(small,maxCorners=400,qualityLevel=.01,minDistance=8)
    features=0 if corners is None else len(corners)
    score=float(np.log1p(sharp)+.35*np.log1p(features)-4*(dark+saturated))
    return {'sharpness_normalized_640':sharp,'dark_fraction':dark,'saturated_fraction':saturated,
            'contrast_p90_p10':contrast,'trackable_corners':features,'selection_score':score},small


def motion_between(previous,current):
    points=cv2.goodFeaturesToTrack(previous,300,.01,8)
    if points is None or len(points)<10:return None
    following,status,_=cv2.calcOpticalFlowPyrLK(previous,current,points,None,winSize=(21,21),maxLevel=3)
    if following is None:return None
    back,back_status,_=cv2.calcOpticalFlowPyrLK(current,previous,following,None,winSize=(21,21),maxLevel=3)
    if back is None:return None
    valid=(status.ravel()>0)&(back_status.ravel()>0)&(np.linalg.norm(back-points,axis=2).ravel()<1.)
    if valid.sum()<10:return None
    return float(np.median(np.linalg.norm(following[valid]-points[valid],axis=2)))


def prepare_quality_video(args):
    out=Path(args.out)
    if out.exists() and any(out.iterdir()):raise ValueError('Output must be empty to avoid mixing runs')
    if args.sample_hz<=0 or args.max_frames<2:raise ValueError('sample-hz must be positive and max-frames >= 2')
    images=out/'images';images.mkdir(parents=True,exist_ok=True)
    cap=cv2.VideoCapture(str(Path(args.video).resolve()))
    if not cap.isOpened():raise ValueError('Cannot open video')
    fps=float(cap.get(cv2.CAP_PROP_FPS))
    if not np.isfinite(fps) or fps<=0:raise ValueError('Invalid video frame rate')
    probe_stride=max(1,int(round(fps/min(fps,args.sample_hz*6))))
    records=[];chosen=[];previous=None;last_time=None;best=None;window=None;index=0
    def flush(candidate):
        nonlocal previous,last_time
        if candidate is None:return
        frame,small,entry=candidate
        movement=motion_between(previous,small) if previous is not None else None
        entry['motion_from_previous_640px']=movement
        if movement is not None and movement<1.5 and last_time is not None and entry['nominal_time_s']-last_time<1.:
            entry['reason']='near_duplicate';return
        success,buffer=cv2.imencode('.png',frame)
        if not success:raise RuntimeError('PNG encoding failed')
        (images/entry['image_name']).write_bytes(buffer.tobytes())
        entry['accepted']=True;entry['reason']='best_local_quality';chosen.append(entry)
        previous=small;last_time=entry['nominal_time_s']
    try:
        while len(chosen)<args.max_frames:
            ok,frame=cap.read()
            if not ok:break
            if index%probe_stride:
                index+=1;continue
            time_s=index/fps;current_window=int(np.floor(time_s*args.sample_hz+1e-8))
            if window is not None and current_window!=window:
                flush(best);best=None
                if len(chosen)>=args.max_frames:break
            window=current_window
            metrics,small=measure(frame)
            entry={'image_name':f'frame_{index:08d}.png','frame_index':index,'nominal_time_s':time_s,
                'timestamp_ms_decoder':float(cap.get(cv2.CAP_PROP_POS_MSEC)),'accepted':False,'reason':'lower_local_quality',**metrics}
            records.append(entry)
            usable=metrics['dark_fraction']<.97 and metrics['saturated_fraction']<.97 and metrics['contrast_p90_p10']>=4
            if not usable:entry['reason']='insufficient_exposure_or_contrast'
            elif best is None or metrics['selection_score']>best[2]['selection_score']:best=(frame.copy(),small,entry)
            index+=1
        if len(chosen)<args.max_frames:flush(best)
    finally:cap.release()
    gaps=np.diff([x['nominal_time_s'] for x in chosen])
    report={'source_kind':'real_camera_video','video':str(Path(args.video).resolve()),'fps_reported':fps,
        'selection':'quality_window_and_motion','target_sample_hz':args.sample_hz,'probe_stride':probe_stride,
        'accepted_frames':len(chosen),'max_accepted_gap_s':float(max(gaps)) if len(gaps) else None,'frames':records,
        'notes':['Quality is measured at a common 640-pixel maximum dimension; output retains original pixels.',
                 'Frames are selected within time windows; no denoising, sharpening, resizing, stabilization or cropping of saved frames.',
                 'Optical flow rejects near-duplicates only; it does not establish metric displacement or rolling-shutter correction.',
                 'Decoder and nominal timestamps are both recorded; nominal windows assume constant frame rate.']}
    (out/'frames.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False),'utf-8')
    if len(chosen)<3:raise ValueError('Fewer than three usable keyframes')
    print(json.dumps({'images':str(images),'accepted':len(chosen),'scanned_candidates':len(records),'selection':report['selection']}))
