"""真实几何与任务边界的轻量验收；不调用 U-Net、不运行耗时 SfM。"""
from pathlib import Path
import json
import sys
import tempfile
import unittest
import numpy as np
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "algorithm")]
from video_reconstruction_job import gray_texture_data_url, localize_candidates, supported_triangles, validate_source
from video_inference_service import VideoInferenceService, VideoInferenceBusyError
from video_reconstruction_service import ReconstructionBusyError, write_atomic

class DeferredPool:
    def __init__(self): self.calls = []
    def submit(self, fn, *args): self.calls.append((fn, args))
    def shutdown(self, **kwargs): pass

class GeometryTests(unittest.TestCase):
    def setUp(self):
        self.camera = {"image_name":"f.png", "width":640, "height":480, "model":"PINHOLE", "params":[500,500,320,240],
            "world_to_camera":np.eye(4).tolist(), "center":[0,0,0]}
        self.xyz = np.array([[-.05,-.05,5],[.25,-.05,5],[-.05,.25,5]])
        self.uv = np.array([[315,235],[345,235],[315,265]])
        self.scene = {"scene_id":"test-scene", "cameras":[self.camera], "scale":{"status":"unscaled"}}
    def test_known_plane_ray_matches_independent_analytic_position(self):
        faces = supported_triangles(self.xyz,self.uv,[.2]*3,[4]*3,self.camera)
        self.assertEqual(len(faces),1)
        d = {"detections":[{"id":"d1","image_name":"f.png","label":"crack","confidence":.9,"pixels":[[320,240],[400,400]]}]}
        result = localize_candidates(self.scene,d,{"f.png":{"vertices":self.xyz,"faces":faces}})
        row = result['defects'][0]
        self.assertEqual(row['status'],'partial')
        np.testing.assert_allclose(row['points'][0],[0,0,5],atol=1e-6)
        self.assertIsNone(row['observations'][1]['point'])
    def test_source_pixels_and_frame_identity_checked_before_geometry(self):
        review={"jobId":"original", "frames":[{"name":"f.png"}]}
        validate_source(review,{"coordinate_space":"original_image_pixels","detections":[{"image_name":"f.png"}]})
        for d in ({"coordinate_space":"resized_pixels","detections":[]},
                  {"coordinate_space":"original_image_pixels","detections":[{"image_name":"other.png"}]},
                  {"coordinate_space":"original_image_pixels","scene_id":"other-job","detections":[]}):
            with self.assertRaises(ValueError): validate_source(review,d)
    def test_unregistered_frame_never_gets_coordinates(self):
        d={"detections":[{"id":"d1","image_name":"unknown.png","label":"crack","confidence":.9,"pixels":[[320,240]]}]}
        row=localize_candidates(self.scene,d,{})['defects'][0]
        self.assertEqual(row['status'],'unregistered_frame');self.assertEqual(row['points'],[])
    def test_missing_surface_keeps_pending(self):
        d={"detections":[{"id":"d1","image_name":"f.png","label":"crack","confidence":.9,"pixels":[[320,240]]}]}
        row=localize_candidates(self.scene,d,{})['defects'][0]
        self.assertEqual(row['status'],'pending_surface');self.assertEqual(row['points'],[])
    def test_depth_discontinuity_does_not_bridge_surfaces(self):
        xyz=self.xyz.copy();xyz[1,2]=10
        self.assertEqual(len(supported_triangles(xyz,self.uv,[.1]*3,[4]*3,self.camera)),0)
    def test_short_tracks_large_edges_and_bad_reprojection_rejected(self):
        for errors,tracks,uv in [([.1]*3,[2]*3,self.uv),([3]*3,[4]*3,self.uv),([.1]*3,[4]*3,self.uv*3)]:
            self.assertEqual(len(supported_triangles(self.xyz,uv,errors,tracks,self.camera)),0)
    def test_regularized_surface_uses_neutral_gray_texture(self):
        self.assertTrue(gray_texture_data_url().startswith('data:image/jpeg;base64,/9j/'))

class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.service=VideoInferenceService(self.root)
        self.service.pool.shutdown(wait=False)
        self.service.pool=DeferredPool()
        self.id='seed-completed'
        d=self.service.runtime_root/self.id
        (d/'detect').mkdir(parents=True);(d/'input').mkdir()
        (d/'input'/'tunnel.mp4').write_bytes(b'fixture')
        write_atomic(d/'detect'/'detections.json',{})
        write_atomic(d/'review.json',{'jobId':self.id,'source':{'filename':'tunnel.mp4'},'frames':[],'candidates':[]})
        write_atomic(d/'job.json',{'id':self.id,'status':'completed','filename':'tunnel.mp4','batchId':'B1','taskId':'T1','result':{},'progress':{}})
        self.fields={'jobId':self.id,'batchId':'B1','taskId':'T1','maxFrames':48}
    def tearDown(self): self.temp.cleanup()
    def test_completed_detection_survives_restart(self):
        job=self.service.get(self.id);self.assertEqual(job['status'],'completed');self.assertEqual(job['taskId'],'T1')
        self.assertNotIn('_input',job)
    def test_wrong_task_is_rejected(self):
        with self.assertRaisesRegex(ValueError,'不属于'):
            self.service.reconstruction.start({**self.fields,'taskId':'T2'})
    def test_running_detection_cannot_start_reconstruction(self):
        self.service.get(self.id);self.service.jobs[self.id]['status']='running'
        with self.assertRaisesRegex(ValueError,'先完成'):
            self.service.reconstruction.start(self.fields)
    def test_duplicate_start_does_not_queue_again(self):
        one=self.service.reconstruction.start(self.fields);two=self.service.reconstruction.start(self.fields)
        self.assertEqual(one['runId'],two['runId']);self.assertEqual(len(self.service.pool.calls),1)
    def test_shared_resource_slot_blocks_new_inference(self):
        self.service.reconstruction.start(self.fields)
        with self.assertRaises(VideoInferenceBusyError): self.service.create(b'fixture','x.mp4',{})
    def test_restart_marks_interrupted_reconstruction_retryable(self):
        self.service.reconstruction.start(self.fields)
        restored=VideoInferenceService(self.root)
        try:
            job=restored.reconstruction.get(self.id)
            self.assertEqual(job['status'],'failed');self.assertIn('中断',job['error'])
            self.assertTrue((self.service.runtime_root/self.id/'review.json').is_file())
        finally: restored.pool.shutdown(wait=False)
    def test_completed_reconstruction_survives_service_restart(self):
        job=self.service.reconstruction.start(self.fields)
        directory=self.service.reconstruction.jobs[self.id]['_dir']
        (directory/'web').mkdir(parents=True)
        (directory/'web'/'index.html').write_text('retained scene',encoding='utf-8')
        url=f"/api/video-inference/file/{self.id}/reconstruction/{job['runId']}/web/index.html"
        self.service.reconstruction.update(self.id,status='completed',result={'viewerUrl':url})
        restored=VideoInferenceService(self.root)
        try:
            same=restored.reconstruction.get(self.id)
            self.assertEqual(same['status'],'completed')
            self.assertEqual(same['result']['viewerUrl'],url)
            self.assertTrue(restored.file_path(self.id,f"reconstruction/{job['runId']}/web/index.html").is_file())
        finally: restored.pool.shutdown(wait=False)
    def test_invalid_paths_and_missing_detection_rejected(self):
        self.assertIsNone(self.service.file_path(self.id,'input/tunnel.mp4'))
        self.assertIsNone(self.service.file_path(self.id,'../job.json'))
        self.assertIsNone(self.service.get('../seed-completed'))
        with self.assertRaises(ValueError): self.service.reconstruction.start({**self.fields,'jobId':'missing'})
    def test_bad_frame_budget_rejected(self):
        for n in (0,200,'bad'):
            with self.assertRaises(ValueError): self.service.reconstruction.start({**self.fields,'maxFrames':n})
    def test_existing_inference_blocks_other_reconstruction(self):
        self.service.jobs['busy']={'status':'running'}
        with self.assertRaises(ReconstructionBusyError): self.service.reconstruction.start(self.fields)

if __name__=='__main__': unittest.main(verbosity=2)
