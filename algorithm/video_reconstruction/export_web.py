"""Package measured mesh, camera timeline and localized model candidates for an offline WebGL viewer."""
import argparse
import base64
import json
import shutil
from pathlib import Path
import numpy as np
from PIL import Image
from mesh_io import read_ply

ROOT=Path(__file__).resolve().parent

def packed(values, dtype='<f4'):
    a=np.ascontiguousarray(values,dtype=dtype)
    return base64.b64encode(a.tobytes()).decode('ascii')

def read_textured_mesh(path):
    """COLMAP 4.2 binary mesh: XYZ vertices; triangle indices and six per-face UV values."""
    with Path(path).open('rb') as f:
        header=[]
        while True:
            raw=f.readline()
            if not raw:raise ValueError('Missing PLY end_header')
            line=raw.decode('ascii').strip(); header.append(line)
            if line=='end_header':break
        if 'format binary_little_endian 1.0' not in header:raise ValueError('Expected COLMAP binary mesh')
        nv=int(next(l for l in header if l.startswith('element vertex ')).split()[-1])
        nf=int(next(l for l in header if l.startswith('element face ')).split()[-1])
        expected=['property float x','property float y','property float z',
                  'property list uchar int vertex_indices','property list uchar float texcoord']
        if [l for l in header if l.startswith('property ')] != expected:
            raise ValueError('Unsupported textured PLY layout; refuse ambiguous UV interpretation')
        vertices=np.frombuffer(f.read(nv*12),dtype='<f4').reshape(nv,3).copy()
        dtype=np.dtype([('nv','u1'),('face','<i4',(3,)),('nu','u1'),('uv','<f4',(6,))])
        faces=np.frombuffer(f.read(),dtype=dtype,count=nf)
        if np.any(faces['nv']!=3) or np.any(faces['nu']!=6):raise ValueError('Invalid face/UV count')
        indices=faces['face'].copy(); uv=faces['uv'].reshape(-1,3,2).copy()
        if indices.min()<0 or indices.max()>=nv:raise ValueError('Invalid mesh indices')
        if not np.isfinite(vertices).all() or not np.isfinite(uv).all():raise ValueError('Non-finite mesh')
        return vertices,indices,uv

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['scene','localized','detections','textured-mesh','texture','images','frames','video','quality','out']:
        parser.add_argument('--'+name,required=True)
    parser.add_argument('--title',default='DVP 人行隧道 · 视频重建')
    parser.add_argument('--regularized',help='Optional fitted-surface output directory for comparison')
    args=parser.parse_args()
    out=Path(args.out).resolve();out.mkdir(parents=True,exist_ok=True)
    scene=json.loads(Path(args.scene).read_text('utf-8'))
    local=json.loads(Path(args.localized).read_text('utf-8'))
    detections=json.loads(Path(args.detections).read_text('utf-8'))
    frames=json.loads(Path(args.frames).read_text('utf-8'))
    quality=json.loads(Path(args.quality).read_text('utf-8'))
    if not scene['scene_id']==local['scene_id']==detections['scene_id']:
        raise ValueError('Scene/detections/localization IDs differ')
    vertices,faces,uv=read_textured_mesh(args.textured_mesh)
    if len(faces)==0:raise ValueError('No surface')
    # Preserve every reconstructed triangle. Viewer framing may ignore extremes but data stays unchanged.
    extent=np.quantile(vertices,[.01,.99],axis=0)
    center=extent.mean(axis=0);radius=float(np.linalg.norm(extent[1]-extent[0])/2)
    assets=out/'assets';assets.mkdir(exist_ok=True)
    shutil.copy2(args.texture,assets/'texture.png')
    suffix=Path(args.video).suffix
    shutil.copy2(args.video,assets/('source'+suffix))
    times={f['image_name']:f.get('timestamp_ms_decoder',f['nominal_time_s']*1000)/1000 for f in frames['frames'] if f['accepted']}
    cameras=scene['cameras']
    for camera in cameras:
        camera['time_s']=times[camera['image_name']]
        image=Image.open(Path(args.images)/camera['image_name']).convert('RGB')
        image.thumbnail((1280,1280))
        filename=Path(camera['image_name']).stem+'.jpg'
        image.save(assets/filename,quality=88)
        camera['image_url']='assets/'+filename
        mask_path=Path(args.detections).parent/'masks'/(Path(camera['image_name']).stem+'.png')
        if mask_path.exists():
            mask=Image.open(mask_path).convert('L').resize(image.size,Image.Resampling.NEAREST)
            rgba=np.zeros((image.height,image.width,4),dtype=np.uint8)
            rgba[:,:,0]=255;rgba[:,:,1]=60;rgba[:,:,2]=65
            rgba[:,:,3]=(np.asarray(mask)>0).astype(np.uint8)*155
            mask_name=Path(camera['image_name']).stem+'_mask.png'
            Image.fromarray(rgba).save(assets/mask_name)
            camera['mask_url']='assets/'+mask_name
    cameras.sort(key=lambda c:c['time_s'])
    original={d['id']:d for d in detections['detections']}
    candidates=[]
    for defect in local['defects']:
        source=original[defect['id']]
        points=np.asarray(defect['points'],dtype=float).reshape(-1,3)
        observations=defect['observations']
        candidates.append({'id':defect['id'],'image_name':defect['image_name'],
          'label':'疑似裂缝 / 待复核','confidence':defect['confidence'],'status':defect['status'],
          'hit_count':defect['hit_count'],'requested_count':defect['requested_count'],
          'mask_area_px':source.get('mask_area_px'), 'pixels':source['pixels'],
          'points':points.tolist(), 'center':np.median(points,axis=0).tolist() if len(points) else None,
          'pixel_hits':[o['status']=='hit' for o in observations]})
    source_metadata=ROOT/'research_sources/dvp_metadata.json'
    meta=json.loads(source_metadata.read_text('utf-8')) if source_metadata.exists() else {}
    data={'title':args.title,'scene_id':scene['scene_id'],'scale':scene['scale'],'quality':quality,
       'cameras':cameras,'defects':candidates,'localization':local['summary'],
       'video_url':'assets/source'+suffix,'texture_url':'data:image/png;base64,'+base64.b64encode(Path(args.texture).read_bytes()).decode('ascii'),
       'mesh':{'positions':packed(vertices[faces]),'uv':packed(uv),'vertices':len(vertices),'triangles':len(faces)},
       'bounds':{'center':center.tolist(),'radius':radius},
       'source':{'url':meta.get('descriptionurl',''),'author':'Sikander Iqbal','license':'CC BY-SA 4.0'},
       'limits':['这是公开隧道录像的离线重建；相机轨迹由画面估计。',
                 '单目尺度未标定，坐标单位为相对重建单位。',
                 '红色标记是模型候选，施工缝、涂写、树枝等可能误报；尚无人工真值。',
                 '网格保留观测缺口；未测量定位误差和裂缝实际宽度。']}
    if args.regularized:
        folder=Path(args.regularized)
        report=json.loads((folder/'report.json').read_text('utf-8'))
        localized=json.loads((folder/'localized.json').read_text('utf-8'))
        if not report['scene_id']==localized['scene_id']==scene['scene_id'] or report['surface_id']!=localized['surface_id']:
            raise ValueError('Regularized geometry and localization do not share an identity')
        rv,rf,ruv=read_textured_mesh(folder/'textured/mesh.ply')
        with np.load(folder/'surface_regularized.npz') as surface:
            if not np.array_equal(rv,surface['vertices']) or not np.array_equal(rf,surface['faces']):
                raise ValueError('Regularized texture and localization geometry differ')
        with np.load(folder/'display_attributes.npz') as attrs:
            mesh={'positions':packed(rv[rf]),'uv':packed(ruv),'vertices':len(rv),'triangles':len(rf),
                  'normals':packed(attrs['normals'][rf]),'support':packed(attrs['supported'][rf]),
                  'caps':packed(attrs['caps_positions'])}
        bounds={'center':((rv.min(0)+rv.max(0))/2).tolist(),'radius':float(np.linalg.norm(rv.max(0)-rv.min(0))/2)}
        data['regularized']={'mesh':mesh,'bounds':bounds,'report':report,'localization':localized['summary'],
           'texture_url':'data:image/png;base64,'+base64.b64encode((folder/'textured/texture.png').read_bytes()).decode('ascii')}
        by_id={d['id']:d for d in localized['defects']}
        for d in data['defects']:
            item=by_id[d['id']];points=np.array(item['points']).reshape(-1,3)
            deltas=[o['displacement_sfm_unit'] for o in item['observations'] if o['status']=='hit']
            d['fitted']={'points':points.tolist(),'center':np.median(points,axis=0).tolist() if len(points) else None,
                'hit_count':item['hit_count'],'status':item['status'],'supported_hit_count':item['supported_hit_count'],
                'inferred_hit_count':item['inferred_hit_count'],
                'displacement_median':float(np.median(deltas)) if deltas else None}
        data['limits']=['规则化表面通过实际点云断面拟合和平滑补全，原始网格可切换对照。',
            '区段两端保留开口；端盖为人为封闭，补全区域可着色查看。',
            '单目尺度未标定，红色为待复核候选；规则化位置是显示关联，不代表精度提升。']
    (out/'scene_data.js').write_text('window.TUNNEL_DATA='+json.dumps(data,ensure_ascii=False,allow_nan=False,separators=(',',':'))+';','utf-8')
    for name in ['index.html','viewer.js','viewer.css']:
        shutil.copy2(ROOT/'web_template'/name,out/name)
    report={'scene_id':scene['scene_id'],'vertices':len(vertices),'triangles':len(faces),
            'registered_frames':len(cameras),'candidates':len(candidates),
            'candidates_with_3d':sum(bool(d['points']) for d in candidates),
            'hit_count':local['summary']['hit_count'],'requested_count':local['summary']['requested_count'],
            'entry':str(out/'index.html'),'frame_evidence_count':len(cameras)}
    if 'regularized' in data:
        report['regularized']={'surface_id':data['regularized']['report']['surface_id'],
                              'triangles':data['regularized']['mesh']['triangles'],
                              'mapping':data['regularized']['localization']}
    (out/'package_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf-8')
    print(json.dumps(report,ensure_ascii=False))

if __name__=='__main__':main()
