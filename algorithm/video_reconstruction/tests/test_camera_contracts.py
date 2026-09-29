"""Cross-library camera ray checks and original-pixel preservation for quality keyframes."""
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
import cv2
import numpy as np
import pycolmap
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from keyframes import prepare_quality_video

class CameraContracts(unittest.TestCase):
    def test_fisheye_pixel_centers_match_opencv(self):
        fx,fy,cx,cy=191.,190.,255.2,256.1;k=np.array([.0034,.0007,-.002,.0002])
        intrinsic=np.array([[fx,0,cx],[0,fy,cy],[0,0,1.]])
        rays=np.array([[-1.,-.4],[0,0],[.5,.2],[2.,.7],[-2.5,.2]])
        pixels=cv2.fisheye.distortPoints(rays[:,None,:],intrinsic,k).reshape(-1,2)
        camera=pycolmap.Camera(model='OPENCV_FISHEYE',width=512,height=512,params=[fx,fy,cx+.5,cy+.5,*k])
        recovered=camera.cam_from_img(pixels+.5)
        np.testing.assert_allclose(recovered,rays,rtol=1e-8,atol=1e-8)
        np.testing.assert_allclose(camera.img_from_cam(np.c_[rays,np.ones(len(rays))]),pixels+.5,atol=1e-8)

    def test_quality_selection_preserves_pixels_and_avoids_blur(self):
        parent=ROOT/'results';parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as directory:
            base=Path(directory);video=base/'source.avi';out=base/'selected'
            writer=cv2.VideoWriter(str(video),cv2.VideoWriter_fourcc(*'FFV1'),20,(256,192))
            self.assertTrue(writer.isOpened())
            texture=np.random.default_rng(18).integers(20,235,(192,256,3),dtype=np.uint8);original=[]
            for i in range(40):
                frame=np.roll(texture,2*i,axis=1)
                if i%10==0:frame=cv2.GaussianBlur(frame,(11,11),3)
                original.append(frame.copy());writer.write(frame)
            writer.release()
            prepare_quality_video(types.SimpleNamespace(video=str(video),out=str(out),sample_hz=2,max_frames=4))
            report=json.loads((out/'frames.json').read_text('utf-8'));selected=[r for r in report['frames'] if r['accepted']]
            self.assertEqual(len(selected),4)
            for entry in selected:
                self.assertNotEqual(entry['frame_index']%10,0)
                actual=cv2.imdecode(np.fromfile(out/'images'/entry['image_name'],dtype=np.uint8),cv2.IMREAD_COLOR)
                np.testing.assert_array_equal(actual,original[entry['frame_index']])
            self.assertLessEqual(report['max_accepted_gap_s'],1.)

if __name__=='__main__':unittest.main()
