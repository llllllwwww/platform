"""Download CTCD pixel labels from public dataset metadata, checking every LFS SHA-256."""
from concurrent.futures import ThreadPoolExecutor,as_completed
import hashlib
import json
from pathlib import Path,PurePosixPath
import time
from urllib.parse import quote
import urllib.request
ROOT=Path(__file__).resolve().parent
REPO='shiweiluo99/tunnel-crack-segmentation-dataset'

def request(url):
    return urllib.request.Request(url,headers={'User-Agent':'tunnel-camera-research/1.0'})

def main():
    metadata=ROOT/'research_sources/multiscene/ctcd_api.json'
    if not metadata.exists():
        metadata.parent.mkdir(parents=True,exist_ok=True)
        with urllib.request.urlopen(request(f'https://huggingface.co/api/datasets/{REPO}/tree/main?recursive=true&limit=1000'),timeout=40) as r:metadata.write_bytes(r.read())
    files=[x for x in json.loads(metadata.read_text('utf-8')) if x['type']=='file' and x['path'].endswith('.bmp')]
    target=ROOT/'data/ctcd';target.mkdir(parents=True,exist_ok=True)
    def fetch(item):
        rel=PurePosixPath(item['path'])
        if len(rel.parts)!=3 or rel.parts[1] not in {'train','trainannot','val','valannot'} or '..' in rel.parts:
            raise ValueError('Unexpected CTCD file layout: '+item['path'])
        path=target/Path(*rel.parts[1:])
        if not path.resolve().is_relative_to(target.resolve()):raise ValueError('Unsafe dataset path')
        path.parent.mkdir(parents=True,exist_ok=True)
        expected=item.get('lfs',{}).get('oid')
        if path.is_file() and path.stat().st_size==item['size'] and (not expected or hashlib.sha256(path.read_bytes()).hexdigest()==expected):
            return {'path':path.relative_to(ROOT).as_posix(),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'bytes':path.stat().st_size,'status':'cached'}
        url=f'https://huggingface.co/datasets/{REPO}/resolve/main/'+quote(item['path'],safe='/')
        for attempt in range(4):
            try:
                with urllib.request.urlopen(request(url),timeout=40) as response:data=response.read()
                digest=hashlib.sha256(data).hexdigest()
                if len(data)!=item['size'] or (expected and digest!=expected):raise ValueError('Size or SHA-256 mismatch')
                part=path.with_suffix(path.suffix+'.part');part.write_bytes(data);part.replace(path)
                return {'path':path.relative_to(ROOT).as_posix(),'sha256':digest,'bytes':len(data),'status':'downloaded'}
            except Exception:
                if attempt==3:raise
                time.sleep(1+attempt)
    records=[];failed=[]
    print(f'CTCD: {len(files)} files, {sum(x["size"] for x in files)/1e6:.1f} MB',flush=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        work={pool.submit(fetch,x):x for x in files}
        for task in as_completed(work):
            item=work[task]
            try:records.append(task.result())
            except Exception as e:failed.append({'path':item['path'],'error':str(e)});print('FAILED',item['path'],str(e),flush=True)
            if (len(records)+len(failed))%40==0:print('Progress',len(records),'ok',len(failed),'failed',flush=True)
    manifest={'source':f'https://huggingface.co/datasets/{REPO}','license':'CC BY 4.0 as stated in dataset card','files':sorted(records,key=lambda x:x['path']),'failures':failed}
    (target/'manifest.json').write_text(json.dumps(manifest,indent=2),'utf-8')
    print('Finished',len(records),'files;',len(failed),'failures',flush=True)
    if failed:raise SystemExit(1)
if __name__=='__main__':main()
