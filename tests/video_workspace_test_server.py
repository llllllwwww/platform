"""隔离的视频档案 UI 测试服务；复用已完成的真实候选与模型，不重复跑算法。"""
import argparse
import functools
import json
from pathlib import Path
import shutil
import sys
from http.server import ThreadingHTTPServer

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from video_inference_service import VideoInferenceService,LocalWorkBenchHandler
from video_reconstruction_service import write_atomic

class DeferredPool:
    def submit(self,*args):pass

p=argparse.ArgumentParser();p.add_argument('--qa-dir',required=True);args=p.parse_args()
root=Path(__file__).resolve().parents[1];qa=Path(args.qa_dir).resolve();allowed=(root/'tests'/'output').resolve()
if allowed not in qa.parents:raise ValueError('QA 目录必须在 tests/output 下')
qa.mkdir(parents=True,exist_ok=False);(qa/'.video-workspace-qa').write_text('owned temporary fixture','utf-8')
service=VideoInferenceService(root);service.pool.shutdown(wait=False);service.pool=DeferredPool();service.runtime_root=qa/'runtime';service.runtime_root.mkdir()
seeds={'qa-video-a':'20261008-100429-5e746739','qa-video-b':'20261008-133305-6d1e256a'}
def import_seed(id,original_id):
    source=root/'runtime'/'video_inference'/original_id;out=service.runtime_root/id;out.mkdir()
    review=json.loads((source/'review.json').read_text('utf-8'));filename=review['source']['filename']
    shutil.copytree(source/'input',out/'input');shutil.copytree(source/'detect',out/'detect');shutil.copytree(source/'frames',out/'frames')
    def replace(value):return json.loads(json.dumps(value,ensure_ascii=False).replace(original_id,id))
    review=replace(review);review['jobId']=id;review['videoId']=id;review.pop('sourceSha256',None);write_atomic(out/'review.json',review)
    native=replace(json.loads((out/'detect'/'detections.json').read_text('utf-8')));native['videoId']=id;native['sourceJobId']=id;native.pop('sourceSha256',None);write_atomic(out/'detect'/'detections.json',native)
    job={'id':id,'filename':filename,'batchId':'B202609','taskId':'T-001','status':'completed','progress':{'percent':100},'detector':review.get('detector',{}),'result':{'frameCount':len(review['frames']),'candidateCount':len(review['candidates']),'preview':review['candidates'][:18],'jsonUrl':f'/api/video-inference/file/{id}/review.json'}}
    write_atomic(out/'job.json',job);service.get(id);bundle=service.workspace.initialize(job,(out/'input'/filename).read_bytes())
    service.jobs[id]['sourceSha256']=bundle['manifest']['source']['sha256'];service.jobs[id]['evidenceSourceId']=bundle['manifest']['source']['evidenceSourceId'];service.persist(id)
    return out
for id,source_id in seeds.items():import_seed(id,source_id)
old_id='qa-reconstruction-1791440502656';verified=root/'tests'/'output'/'reconstruction-live-1791440502656'/'runtime'/old_id
record=json.loads((verified/'reconstruction.json').read_text('utf-8'));run=record['runId'];target=service.runtime_root/'qa-video-a';shutil.copytree(verified/'reconstruction',target/'reconstruction')
record=json.loads(json.dumps(record).replace(old_id,'qa-video-a'));record['sourceDetectionRevision']=service.workspace.refresh('qa-video-a')['manifest']['stages']['detection']['revision']
write_atomic(target/'reconstruction.json',record);service.workspace.refresh('qa-video-a',verify_source=True)
class Handler(LocalWorkBenchHandler):
    def do_POST(self):
        if self.path=='/__qa__/restart':
            # 浏览器验收模拟服务重启的会话变化，数据文件和已完成算法成果保持原样。
            from workbench_session_service import WorkbenchSessionService
            service.workbench = WorkbenchSessionService(service)
            self._send_json(200,service.workbench.session());return
        if self.path=='/__qa__/complete':
            fields=json.loads(self.rfile.read(int(self.headers['Content-Length'])));id=fields['id'];job=service.jobs[id];base=service.runtime_root/id
            # 当前上传的真实铁路视频沿用该视频已完成的候选文件，校验内容一致。
            seed=service.runtime_root/'qa-video-b'
            if (base/'input'/job['filename']).read_bytes()!=(seed/'input'/service.jobs['qa-video-b']['filename']).read_bytes():raise ValueError('fixture must be same source')
            for sub in ['frames','detect']:shutil.copytree(seed/sub,base/sub)
            review=json.loads((seed/'review.json').read_text('utf-8'));review=json.loads(json.dumps(review).replace('qa-video-b',id));review.update(jobId=id,videoId=id,sourceSha256=job['sourceSha256']);review['source']['filename']=job['filename'];write_atomic(base/'review.json',review)
            native=json.loads((base/'detect'/'detections.json').read_text('utf-8'));native.update(videoId=id,sourceJobId=id,sourceSha256=job['sourceSha256']);write_atomic(base/'detect'/'detections.json',native)
            result={**service.jobs['qa-video-b']['result'],'preview':review['candidates'][:18],'jsonUrl':f'/api/video-inference/file/{id}/review.json'}
            service._update(id,status='completed',result=result,progress={'percent':100,'phase':'QA 复用本视频已完成的真实候选'})
            self._send_json(200,service.get(id));return
        super().do_POST()
server=ThreadingHTTPServer(('127.0.0.1',0),functools.partial(Handler,service=service,directory=str(root)))
print('READY '+json.dumps({'port':server.server_port,'qa':str(qa),'videos':list(seeds)}),flush=True)
try:server.serve_forever()
finally:server.server_close()
