"""Convert a real timestamped TUM VI cam0 segment to lossless video for the common runner.
Spatial pixels remain 512x512. A fixed gamma maps 16-bit intensity to 8-bit; original
nanosecond timestamps are preserved separately. Kalibr principal point gains 0.5 pixels
when converted from OpenCV's zero-centered convention to COLMAP pixel centers.
"""
import hashlib
import json
from pathlib import Path
import cv2
import numpy as np
import yaml
ROOT=Path(__file__).resolve().parent

def main():
    source=ROOT/'data/tumvi/corridor4';out=ROOT/'data/captures/tumvi_fisheye';out.mkdir(parents=True,exist_ok=True)
    video=out/'source.avi'
    if video.exists():raise FileExistsError(video)
    config=yaml.safe_load((source/'dso/camchain.yaml').read_text('utf-8'))['cam0']
    if config['camera_model']!='pinhole' or config['distortion_model']!='equidistant':raise ValueError('Unsupported calibration')
    fx,fy,cx,cy=config['intrinsics'];k=config['distortion_coeffs'];w,h=config['resolution']
    camera={'model':'OPENCV_FISHEYE','width':w,'height':h,'params':[fx,fy,cx+.5,cy+.5,*k],
            'source':'TUM VI provided dso/camchain.yaml, cam0','principal_point_conversion':'OpenCV center 0 -> COLMAP center 0.5',
            'metric_scale':'not established; only one camera is reconstructed'}
    (out/'calibration.json').write_text(json.dumps(camera,indent=2),'utf-8')
    # Limit the valid ray cone to forward-facing rays usable by this monocular pipeline.
    theta=np.deg2rad(85);distorted=theta*(1+sum(k[i]*theta**(2*i+2) for i in range(4)))
    y,x=np.mgrid[:h,:w];radius=np.sqrt(((x-cx)/fx)**2+((y-cy)/fy)**2);mask=(radius<distorted).astype(np.uint8)*255
    cv2.imencode('.png',mask)[1].tofile(out/'valid_mask.png')
    rows=np.genfromtxt(source/'mav0/cam0/data.csv',delimiter=',',comments='#',dtype=str)
    stamps=rows[:,0].astype(np.int64);times=(stamps-stamps[0])/1e9;indices=np.flatnonzero((times>=35)&(times<65))
    writer=cv2.VideoWriter(str(video),cv2.VideoWriter_fourcc(*'FFV1'),20,(w,h),False)
    if not writer.isOpened():raise RuntimeError('FFV1 lossless video encoder unavailable')
    records=[];reference=None
    try:
        for index in indices:
            path=source/'mav0/cam0/data'/rows[index,1]
            image=cv2.imdecode(np.fromfile(path,dtype=np.uint8),cv2.IMREAD_UNCHANGED)
            if image.dtype!=np.uint16 or image.shape!=(h,w):raise ValueError('Unexpected source pixel format')
            display=np.round(255*np.power(image.astype(np.float32)/65535,1/2.2)).clip(0,255).astype(np.uint8)
            writer.write(display)
            if reference is None:reference=display.copy()
            records.append({'frame_index':len(records),'timestamp_ns':str(stamps[index]),'source_image':rows[index,1]})
    finally:writer.release()
    cap=cv2.VideoCapture(str(video));ok,first=cap.read();cap.release()
    if not ok or not np.array_equal(first[:,:,0],reference):raise ValueError('Lossless encoding changed intensity values')
    timing={'encoding':'FFV1 grayscale, lossless after fixed 16-bit -> 8-bit gamma 1/2.2','spatial_transform':'none',
            'source_segment_seconds':[35,65],'nominal_fps':20,'frames':records}
    (out/'source_timestamps.json').write_text(json.dumps(timing,indent=2),'utf-8')
    path=ROOT/'experiments/captures.json';captures=json.loads(path.read_text('utf-8'))
    item={'id':'tumvi_fisheye','path':video.relative_to(ROOT).as_posix(),
        'source_url':'https://cvg.cit.tum.de/data/datasets/visual-inertial-dataset','license':'CC BY 4.0',
        'camera_description':'TUM VI calibrated monochrome wide-angle cam0, indoor corridor; original stereo/IMU not fused',
        'camera_model':'OPENCV_FISHEYE','camera_model_origin':'provided calibration with pixel-center convention conversion',
        'calibration':(out/'calibration.json').relative_to(ROOT).as_posix(),'image_mask':(out/'valid_mask.png').relative_to(ROOT).as_posix(),
        'timestamps':(out/'source_timestamps.json').relative_to(ROOT).as_posix(),'sha256':hashlib.sha256(video.read_bytes()).hexdigest(),
        'width':w,'height':h,'fps':20,'frames':len(records),'duration_s':len(records)/20,'geometry_prior':'none'}
    captures=[x for x in captures if x['id']!=item['id']]+[item];path.write_text(json.dumps(captures,indent=2),'utf-8')
    print(json.dumps(item,indent=2),flush=True)
if __name__=='__main__':main()
