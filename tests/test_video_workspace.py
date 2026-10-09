"""视频档案归属、缺失状态、跨视频防护、替换清理与媒体读取验证。
使用隔离目录和合成 JSON，不启动耗时的检测 / SfM。
"""
import functools
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from video_inference_service import VideoInferenceService, VideoInferenceBusyError, LocalWorkBenchHandler
from video_reconstruction_service import write_atomic

class DeferredPool:
    def __init__(self): self.calls=[]
    def submit(self,*args): self.calls.append(args)

class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.service=VideoInferenceService(self.root);self.service.pool.shutdown(wait=False);self.service.pool=DeferredPool()
    def tearDown(self):
        for job in self.service.jobs.values(): job['cleanupStatus']='cancelled'
        self.tmp.cleanup()
    def seed(self, id='video-a', data=b'original-a', context=('B1','T1'), count=1):
        d=self.service.runtime_root/id;(d/'input').mkdir(parents=True);(d/'detect').mkdir()
        (d/'input'/'same-name.mp4').write_bytes(data)
        prefix=f'/api/video-inference/file/{id}/'
        candidates=[{'id':f'frame_0001-d{x+1}','imageName':'frame_0001.jpg','timeSec':2,'confidence':.8,'imageUrl':prefix+'frames/frame_0001.jpg','overlayUrl':prefix+'detect/overlay/frame_0001.jpg','maskUrl':prefix+'detect/masks/frame_0001.png'} for x in range(count)]
        write_atomic(d/'review.json',{'jobId':id,'source':{'filename':'same-name.mp4'},'frames':[{'name':'frame_0001.jpg'}],'candidates':candidates})
        write_atomic(d/'detect'/'detections.json',{'coordinate_space':'original_image_pixels','detections':[{'id':x['id'],'image_name':x['imageName']} for x in candidates]})
        job={'id':id,'filename':'same-name.mp4','batchId':context[0],'taskId':context[1],'status':'completed','progress':{'percent':100},'result':{'candidateCount':count,'frameCount':1,'preview':candidates,'jsonUrl':prefix+'review.json'}}
        write_atomic(d/'job.json',job)
        self.service.get(id)
        self.service.workspace.initialize(job,data)
        return d
    def geometry(self,id='video-a'):
        d=self.service.runtime_root/id;run='r-owned';out=d/'reconstruction'/run
        (out/'sfm').mkdir(parents=True);(out/'web').mkdir()
        (out/'web'/'index.html').write_text('test model','utf-8')
        scene='scene-'+id
        native=json.loads((d/'detect'/'detections.json').read_text('utf-8'))
        write_atomic(out/'sfm'/'scene.json',{'scene_id':scene})
        write_atomic(out/'localized_defects.json',{'scene_id':scene,'defects':[{'id':r['id']} for r in native['detections']]})
        write_atomic(out/'result.json',{'sourceJobId':id,'sceneId':scene,'localizedCandidates':len(native['detections'])})
        b=self.service.workspace.refresh(id)
        write_atomic(d/'reconstruction.json',{'id':id,'sourceJobId':id,'runId':run,'filename':'same-name.mp4','batchId':'B1','taskId':'T1','status':'completed','progress':{},'sourceDetectionRevision':b['manifest']['stages']['detection']['revision'],'result':{}})
        return out
    def review_fields(self,id):
        b=self.service.workspace.refresh(id,verify_source=True);m=b['manifest']
        return {'videoId':id,'batchId':m['context']['batchId'],'taskId':m['context']['taskId'],'sourceSha256':m['source']['sha256'],'detectionRevision':m['stages']['detection']['revision'],'decisions':{'frame_0001-d1':'confirmed_candidate'},'notes':'仅此视频'}
    def test_stages_in_one_directory_with_truthful_pending_assessment(self):
        d=self.seed();b=self.service.workspace.refresh('video-a')
        for path in ['source.json','manifest.json','acquisition/annotations.json','review/decisions.json','twin/state.json','health/assessment.json','operations/state.json','simulation/state.json','reports/report.json','reports/report.html']:
            self.assertTrue((d/path).is_file(),path)
        self.assertIsNone(b['health']['shi']);self.assertIsNone(b['health']['risk']);self.assertIsNone(b['twin']['modelUrl'])
        self.assertEqual(b['operations']['alerts'],[])
    def test_same_filename_different_video_ids_never_share_data(self):
        self.seed();self.seed('video-b',b'original-b',count=3);self.geometry()
        a=self.service.workspace.refresh('video-a');b=self.service.workspace.refresh('video-b')
        self.assertNotEqual(a['manifest']['source']['sha256'],b['manifest']['source']['sha256'])
        self.assertEqual(a['manifest']['stages']['detection']['candidateCount'],1);self.assertEqual(b['manifest']['stages']['detection']['candidateCount'],3)
        self.assertIn('/video-a/',a['twin']['modelUrl']);self.assertIsNone(b['twin']['modelUrl'])
    def test_zero_candidate_detection_is_valid(self):
        self.seed(count=0);b=self.service.workspace.refresh('video-a')
        self.assertEqual(b['manifest']['errors'],[]);self.assertEqual(b['candidates'],[])
    def test_other_video_review_is_quarantined(self):
        d=self.seed();r=json.loads((d/'review.json').read_text('utf-8'));r['jobId']='video-b';write_atomic(d/'review.json',r)
        b=self.service.workspace.refresh('video-a');self.assertEqual(b['manifest']['stages']['detection']['status'],'invalid')
        self.assertIsNone(b['inference']['result']);self.assertIsNone(b['twin']['modelUrl']);self.assertIsNone(b['health']['shi'])
    def test_other_video_candidate_image_url_is_rejected(self):
        d=self.seed();r=json.loads((d/'review.json').read_text('utf-8'));r['candidates'][0]['overlayUrl']='/api/video-inference/file/video-b/detect/overlay/x.jpg';write_atomic(d/'review.json',r)
        self.assertTrue(self.service.workspace.refresh('video-a')['manifest']['errors'])
    def test_other_video_geometry_is_quarantined(self):
        self.seed();out=self.geometry();r=json.loads((out/'result.json').read_text('utf-8'));r['sourceJobId']='video-b';write_atomic(out/'result.json',r)
        b=self.service.workspace.refresh('video-a');self.assertEqual(b['manifest']['stages']['reconstruction']['status'],'invalid');self.assertIsNone(b['twin']['modelUrl'])
    def test_geometry_candidate_ids_must_match_detection(self):
        self.seed();out=self.geometry();write_atomic(out/'localized_defects.json',{'scene_id':'scene-video-a','defects':[{'id':'foreign'}]})
        self.assertEqual(self.service.workspace.refresh('video-a')['manifest']['stages']['reconstruction']['status'],'invalid')
    def test_replaced_original_invalidates_old_results(self):
        d=self.seed();(d/'input'/'same-name.mp4').write_bytes(b'some-other-original')
        b=self.service.workspace.refresh('video-a',verify_source=True)
        self.assertTrue(b['manifest']['source']['sourceMismatch']);self.assertIsNone(b['inference']['result'])
    def test_revision_change_invalidates_geometry_and_review(self):
        d=self.seed();self.geometry();self.service.workspace.save_review(self.review_fields('video-a'))
        r=json.loads((d/'detect'/'detections.json').read_text('utf-8'));r['changed']=True;write_atomic(d/'detect'/'detections.json',r)
        b=self.service.workspace.refresh('video-a');self.assertEqual(b['review']['decisions'],{});self.assertIsNone(b['twin']['modelUrl'])
    def test_review_save_rejects_wrong_task_and_stale_version(self):
        self.seed();fields=self.review_fields('video-a')
        for changed in [{'taskId':'T2'},{'sourceSha256':'foreign'},{'detectionRevision':'old'},{'decisions':{'foreign':'rejected'}}]:
            with self.assertRaises(ValueError):self.service.workspace.save_review({**fields,**changed})
    def test_review_persists_by_id_across_service_restart(self):
        self.seed();self.service.workspace.save_review(self.review_fields('video-a'))
        restored=VideoInferenceService(self.root)
        try:
            b=restored.workspace.refresh('video-a');self.assertEqual(b['review']['notes'],'仅此视频');self.assertEqual(b['review']['decisions']['frame_0001-d1'],'confirmed_candidate')
        finally:restored.pool.shutdown(wait=False)
    def test_annotations_require_same_video_identity(self):
        self.seed();b=self.service.workspace.refresh('video-a');m=b['manifest'];record={'id':'E1','batchId':'B1','taskId':'T1','anchors':[{'kind':'video','sourceId':m['source']['evidenceSourceId']}]}
        fields={'videoId':'video-a','batchId':'B1','taskId':'T1','sourceSha256':m['source']['sha256'],'records':[record]}
        saved=self.service.workspace.save_annotations(fields);self.assertEqual(len(saved['acquisition']['records']),1)
        record['anchors'][0]['sourceId']='VF-foreign'
        with self.assertRaises(ValueError):self.service.workspace.save_annotations(fields)
    def test_new_import_preserves_previous_runtime_original_assets_and_other_tasks(self):
        old=self.seed();neighbor=self.seed('video-other',b'other',('B2','T2'))
        original=self.root/'assets'/'videos'/'original.mp4';original.parent.mkdir(parents=True);original.write_bytes(b'protected-original')
        job=self.service.create(b'new-original','new.mp4',{'batchId':'B1','taskId':'T1','replaceVideoId':'video-a'})
        self.assertEqual(job['deletedVideoIds'],[]);self.assertEqual(job['cleanupStatus'],'preserved');self.assertTrue(old.exists());self.assertTrue(neighbor.is_dir());self.assertEqual(original.read_bytes(),b'protected-original')
        self.assertEqual(self.service.get('video-a')['id'],'video-a');self.assertTrue((self.service.runtime_root/job['id']/'input'/'new.mp4').is_file())
    def test_busy_replacement_preserves_old_data(self):
        old=self.seed();self.service.jobs['video-a']['status']='running'
        with self.assertRaises(VideoInferenceBusyError):self.service.create(b'new','new.mp4',{'batchId':'B1','taskId':'T1','replaceVideoId':'video-a'})
        self.assertTrue(old.is_dir());self.assertEqual(len(list(self.service.runtime_root.iterdir())),1)
    def test_cross_task_replacement_rejected_before_new_folder_created(self):
        old=self.seed()
        with self.assertRaises(ValueError):self.service.create(b'new','new.mp4',{'batchId':'B2','taskId':'T2','replaceVideoId':'video-a'})
        self.assertTrue(old.is_dir());self.assertEqual(len(list(self.service.runtime_root.iterdir())),1)
    def test_legacy_context_only_bound_after_explicit_selection(self):
        self.seed(context=('',''));self.assertEqual(self.service.workspace.list()[0]['context']['taskId'],'')
        b=self.service.workspace.bind({'videoId':'video-a','batchId':'B1','taskId':'T1'});self.assertEqual(b['manifest']['context']['taskId'],'T1')
        with self.assertRaises(ValueError):self.service.workspace.bind({'videoId':'video-a','batchId':'B2','taskId':'T2'})
    def test_legacy_without_job_manifest_recovers_context_from_owned_reconstruction(self):
        directory=self.seed(context=('',''));self.geometry();(directory/'job.json').unlink()
        restored=VideoInferenceService(self.root)
        try:
            job=restored.get('video-a');self.assertEqual((job['batchId'],job['taskId']),('B1','T1'))
            bundle=restored.workspace.refresh('video-a');self.assertEqual(bundle['manifest']['errors'],[]);self.assertIn('/video-a/',bundle['twin']['modelUrl'])
        finally:restored.pool.shutdown(wait=False)
    def test_identical_refresh_does_not_rewrite_derived_files(self):
        d=self.seed();b=self.service.workspace.refresh('video-a');before=(d/'health'/'assessment.json').stat().st_mtime_ns
        self.assertEqual(b,self.service.workspace.refresh('video-a'));self.assertEqual(before,(d/'health'/'assessment.json').stat().st_mtime_ns)
    def test_invalid_directory_id_never_resolves_neighbor(self):
        self.seed()
        for value in ('../video-a','video-a/../../','',None):
            with self.assertRaises(ValueError):self.service.workspace.directory(value)
    def test_media_api_supports_range_and_folder_list(self):
        self.seed(data=b'0123456789')
        handler=functools.partial(LocalWorkBenchHandler,service=self.service,directory=str(self.root))
        server=ThreadingHTTPServer(('127.0.0.1',0),handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();base=f'http://127.0.0.1:{server.server_port}'
        try:
            with urlopen(Request(base+'/api/video-workspace/media?id=video-a',headers={'Range':'bytes=2-5'})) as r:
                self.assertEqual(r.status,206);self.assertEqual(r.read(),b'2345');self.assertEqual(r.headers['Content-Range'],'bytes 2-5/10')
            with urlopen(base+'/api/video-workspace/list') as r:self.assertEqual(json.load(r)['videos'][0]['videoId'],'video-a')
            with self.assertRaises(HTTPError):urlopen(base+'/api/video-workspace/media?id=../video-a')
        finally:server.shutdown();server.server_close();thread.join(2)

if __name__=='__main__':unittest.main(verbosity=2)
