"""Collect public dataset metadata for the multi-scene experiment (no code execution)."""
import json
from pathlib import Path
import urllib.request
ROOT=Path(__file__).resolve().parent
OUT=ROOT/'research_sources/multiscene';OUT.mkdir(parents=True,exist_ok=True)
SOURCES={
 'tum_vi':'https://cvg.cit.tum.de/data/datasets/visual-inertial-dataset',
 'oivio':'https://arpg.colorado.edu/oivio/',
 'ctcd_api':'https://huggingface.co/api/datasets/shiweiluo99/tunnel-crack-segmentation-dataset/tree/main?recursive=true&limit=1000',
 'ctcd_card':'https://huggingface.co/datasets/shiweiluo99/tunnel-crack-segmentation-dataset/raw/main/README.md',
 'ttd_api':'https://huggingface.co/api/datasets/TACK-project/TACK_Tunnel_Data/tree/main?limit=1000',
 'leven_api':'https://api.github.com/repos/leven87/tunel-abnormal-detection/contents',
}
for name,url in SOURCES.items():
    try:
        req=urllib.request.Request(url,headers={'User-Agent':'tunnel-camera-research/1.0'})
        with urllib.request.urlopen(req,timeout=25) as response:
            data=response.read();kind=response.headers.get_content_type();final_url=response.url
        path=OUT/(name+('.json' if 'json' in kind else '.txt'));path.write_bytes(data)
        print(json.dumps({'name':name,'status':'ok','bytes':len(data),'url':final_url,'path':str(path)},ensure_ascii=False),flush=True)
    except Exception as error:print(json.dumps({'name':name,'status':'failed','error':str(error)},ensure_ascii=False),flush=True)
