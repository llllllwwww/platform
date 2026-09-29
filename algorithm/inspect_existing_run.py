"""Read-only audit of existing run; records diagnostics under workspace."""
import json
from pathlib import Path
import hashlib
import numpy as np
import cv2
from mesh_io import read_ply
ROOT=Path(__file__).resolve().parent
run=Path('F:/data/WB/scratch/tunnel_run1')
scene=json.loads((run/'scene/scene.json').read_text('utf-8'))
localized=json.loads((run/'localized.json').read_text('utf-8'))
frames=json.loads((ROOT/'data/frames_tunnel/frames.json').read_text('utf-8'))
video=ROOT/'data/videos/Trainingvideos_2_1280_720.mp4'
cap=cv2.VideoCapture(str(video))
comparison=[]
for item in frames['frames'][::10]:
    cap.set(cv2.CAP_PROP_POS_FRAMES,item['frame_index'])
    ok, frame=cap.read()
    original=cv2.imdecode(np.fromfile(ROOT/'data/frames_tunnel/images'/item['image_name'],dtype=np.uint8),cv2.IMREAD_COLOR)
    comparison.append({'frame':item['frame_index'],'pixel_max_abs_difference':int(np.abs(frame.astype(int)-original.astype(int)).max()) if ok else None})
cap.release()
headers={}
for name in ['dense/mesh.ply','dense/textured/mesh.ply','dense/fused.ply']:
    lines=[]
    with (run/name).open('rb') as f:
        while True:
            line=f.readline().decode('ascii').strip()
            lines.append(line)
            if line=='end_header':break
    headers[name]=lines
props,faces=read_ply(run/'dense/mesh.ply')
report={'video_sha256':hashlib.sha256(video.read_bytes()).hexdigest(),'scene_id':scene['scene_id'],
        'registered':len(scene['cameras']),'frames':frames['accepted_frames'],
        'source_frame_checks':comparison,'mesh_vertices':len(props['x']),'mesh_faces':len(faces),
        'localized_summary':localized['summary'],'headers':headers}
(ROOT/'results/existing_run_audit.json').write_text(json.dumps(report,indent=2), 'utf-8')
print('audit saved')
