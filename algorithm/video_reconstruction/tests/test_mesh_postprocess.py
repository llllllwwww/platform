"""Regression checks for camera-corridor point filtering and hole filling."""
from pathlib import Path
import sys
import unittest
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from mesh_postprocess import boundary_loops,fill_small_holes,velocity_drift_indicator


def grid_mesh_with_hole(n=14,hole=None):
    """A flat triangulated grid; removing one interior vertex opens a hexagonal hole."""
    y,x=np.mgrid[:n,:n]
    vertices=np.c_[x.ravel()/(n-1),y.ravel()/(n-1),np.zeros(n*n)]
    faces=[]
    for row in range(n-1):
        for col in range(n-1):
            a=row*n+col;faces.extend([[a,a+1,a+n],[a+1,a+n+1,a+n]])
    faces=np.array(faces,dtype=np.int32)
    if hole:
        keep=~np.isin(faces[:,0],list(hole))&~np.isin(faces[:,1],list(hole))&~np.isin(faces[:,2],list(hole))
        faces=faces[keep]
    return vertices,faces


class MeshPostProcessRegression(unittest.TestCase):
    def test_boundary_loop_walker_closes_simple_hole(self):
        vertices,faces=grid_mesh_with_hole(hole={75})
        loops,stats=boundary_loops(vertices,faces)
        # 6 hole edges + 52 outer grid edges; walker must close both cycles.
        self.assertEqual(stats['boundary_edges_before_fill'],58)
        self.assertEqual(sorted(len(l) for l in loops),[6,52])

    def test_fan_fill_restores_manifold_topology_without_moving_observed_vertices(self):
        vertices,faces=grid_mesh_with_hole(hole={75})
        original=vertices.copy()
        vertices2,faces2,mask,report=fill_small_holes(vertices,faces)
        self.assertEqual(report['holes_filled'],1)
        self.assertEqual(report['triangles_added'],6)
        edges=np.sort(np.concatenate([faces2[:,[0,1]],faces2[:,[1,2]],faces2[:,[2,0]]]),axis=1)
        unique,cnt=np.unique(edges,axis=0,return_counts=True)
        self.assertEqual(int((cnt>2).sum()),0,'fan filling must not create non-manifold edges')
        np.testing.assert_array_equal(vertices2[:len(original)],original)
        self.assertEqual(len(mask),len(vertices2),'mask must align with returned vertices')
        self.assertTrue(mask[len(original):].all(),'new centroid vertices must be marked inferred')
        self.assertFalse(mask[:len(original)].any())

    def test_large_loops_are_rejected(self):
        vertices,faces=grid_mesh_with_hole(hole={75})
        _,_,_,report=fill_small_holes(vertices,faces,max_edges=3)
        self.assertEqual(report['holes_filled'],0)
        self.assertEqual(report['rejected_long_loops'],2,'hexagon and outer grid both exceed max_edges')

    def test_velocity_drift_indicator_detects_uniform_compression(self):
        axis=np.array([0.,0.,1.])
        compressed=np.array([[0,0,s] for s in np.cumsum(np.linspace(.6,.2,12))])
        indicator=velocity_drift_indicator(compressed,axis)
        self.assertIsNotNone(indicator)
        self.assertLess(indicator['drift_indicator'],0,'shrinking spacing must yield a negative indicator')
        uniform=np.array([[0,0,i] for i in np.linspace(0,5,12)])
        flat=velocity_drift_indicator(uniform,axis)
        self.assertLess(abs(flat['drift_indicator']),0.2)


if __name__=='__main__':unittest.main()
