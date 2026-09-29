"""Regression checks for observed-geometry safeguards and misleading registration counts."""
from pathlib import Path
import sys
import tempfile
import unittest
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from benchmark_geometry import reconstruction_score
from refine_surface import bounded_smooth
from regularize_tunnel import coordinate_frame,topology
from multiview_evidence import read_depth

class MultiSceneRegression(unittest.TestCase):
    def test_supported_partial_model_beats_unsupported_registered_poses(self):
        unsupported={'registration_fraction':1.,'points3D':364,'point_mean_reprojection_error_px':.33,
                     'observation_support':{'supported_registration_fraction':13/60}}
        measured={'registration_fraction':24/60,'points3D':970,'point_mean_reprojection_error_px':.63,
                  'observation_support':{'supported_registration_fraction':24/60}}
        self.assertGreater(reconstruction_score(measured),reconstruction_score(unsupported))

    def test_observed_mesh_boundaries_and_topology_remain(self):
        n=18;y,x=np.mgrid[:n,:n];z=np.zeros((n,n));z[1:-1,1:-1]=np.random.default_rng(2).normal(0,.025,(n-2,n-2))
        vertices=np.c_[x.ravel()/(n-1),y.ravel()/(n-1),z.ravel()];faces=[]
        for row in range(n-1):
            for col in range(n-1):
                a=row*n+col;faces.extend([[a,a+1,a+n],[a+1,a+n+1,a+n]])
        faces=np.array(faces);refined,limits,boundary=bounded_smooth(vertices,faces)
        np.testing.assert_array_equal(refined[boundary],vertices[boundary])
        self.assertTrue(np.all(np.linalg.norm(refined-vertices,axis=1)<=limits+1e-12))
        self.assertLess(np.std(refined[:,2]),np.std(vertices[:,2]))
        self.assertEqual(topology(refined,faces)['boundary_edges'],topology(vertices,faces)['boundary_edges'])
        self.assertEqual(topology(refined,faces)['degenerate_faces'],0)

    def test_stationary_camera_cannot_create_tunnel_axis(self):
        cameras=[{'center':[0,0,0],'world_to_camera':np.eye(4).tolist()} for _ in range(4)]
        with self.assertRaisesRegex(ValueError,'translation'):coordinate_frame(cameras)

    def test_colmap_depth_layout(self):
        parent=ROOT/'results';parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as folder:
            path=Path(folder)/'depth.bin';path.write_bytes(b'3&2&1&'+np.array([1,2,3,4,5,6],dtype='<f4').tobytes())
            np.testing.assert_array_equal(read_depth(path),np.array([[1,2,3],[4,5,6]]))

if __name__=='__main__':unittest.main()
