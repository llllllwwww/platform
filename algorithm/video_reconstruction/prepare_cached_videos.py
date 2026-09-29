"""Import explicitly supplied cached public-video files and record their actual hashes."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import cv2
ROOT=Path(__file__).resolve().parent
EXPECTED_SHA256={
    'dvp_handheld':'0189d862b01ad628649a21b137f22cde5eb31bddba498298aad1960c589d0b9c',
    'rail_train':'b8f4e7d82c978a56de32b9b3cba78a3c784726696dc4e97beec3815cd786bbb9',
    'rail_test':'71aa201973d44bfd3cb3f1eb8ad21d671b5d5210bd94dcb2717e4127d338dc9e'}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',required=True);args=p.parse_args();source=Path(args.source)
    files=[('dvp_handheld',source/'dvp_tunnel/source.webm','https://commons.wikimedia.org/wiki/File:DVP_Tunnel_at_Moccasin_Trail_Park_in_Toronto_(20181008093109).webm','handheld Pixel 2; metadata identifies hardware'),
        ('rail_train',source/'videos/Trainingvideos_2_1280_720.mp4','https://github.com/leven87/tunel-abnormal-detection','forward-looking railway tunnel footage; camera hardware not documented'),
        ('rail_test',source/'videos/Test-2_1280_720.mp4','https://github.com/leven87/tunel-abnormal-detection','second railway tunnel clip; hardware and independence from train recording are unknown')]
    records=[]
    for name,path,url,camera in files:
        data=path.read_bytes();digest=hashlib.sha256(data).hexdigest();dest=ROOT/'data/captures'/name/('source'+path.suffix);dest.parent.mkdir(parents=True,exist_ok=True)
        if digest!=EXPECTED_SHA256[name]:raise ValueError('Cached file differs from the recorded public capture: '+name)
        if dest.exists() and hashlib.sha256(dest.read_bytes()).hexdigest()!=digest:raise ValueError('Existing input differs')
        if not dest.exists():shutil.copy2(path,dest)
        cap=cv2.VideoCapture(str(dest));fps=cap.get(cv2.CAP_PROP_FPS);count=cap.get(cv2.CAP_PROP_FRAME_COUNT)
        rec={'id':name,'path':dest.relative_to(ROOT).as_posix(),'source_url':url,'camera_description':camera,'camera_model':'SIMPLE_RADIAL',
             'camera_model_origin':'estimated, no provided intrinsics','sha256':digest,'width':int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
             'height':int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),'fps':fps,'frames':int(count),'duration_s':count/fps}
        cap.set(cv2.CAP_PROP_POS_MSEC,min(5,rec['duration_s']/2)*1000);ok,frame=cap.read();cap.release()
        if ok:cv2.imencode('.png',frame)[1].tofile(dest.parent/'preview.png')
        records.append(rec);print(json.dumps(rec,ensure_ascii=False),flush=True)
    out=ROOT/'experiments';out.mkdir(exist_ok=True);registry=out/'captures.json'
    existing={r['id']:r for r in json.loads(registry.read_text('utf-8'))} if registry.exists() else {}
    existing.update({r['id']:r for r in records});registry.write_text(json.dumps(list(existing.values()),ensure_ascii=False,indent=2),'utf-8')
if __name__=='__main__':main()
