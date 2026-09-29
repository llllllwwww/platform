"""Orient mesher triangle winding to measured MVS normals, without changing geometry."""
import argparse
import json
from pathlib import Path
import numpy as np
from scipy.spatial import cKDTree
from mesh_io import read_ply

def orient(mesh_path,fused_path,out):
    props,faces=read_ply(mesh_path)
    pts,_=read_ply(fused_path)
    vertices=np.column_stack([props[a] for a in ('x','y','z')])
    cloud=np.column_stack([pts[a] for a in ('x','y','z')])
    normals=np.column_stack([pts[a] for a in ('nx','ny','nz')])
    dist,nearest=cKDTree(cloud).query(vertices)
    tri=vertices[faces]
    face_normals=np.cross(tri[:,1]-tri[:,0],tri[:,2]-tri[:,0])
    measured=normals[nearest[faces]].sum(axis=1)
    flip=np.einsum('ij,ij->i',face_normals,measured)<0
    oriented=faces.copy()
    oriented[flip]=oriented[flip][:,[0,2,1]]
    assert np.array_equal(np.sort(oriented,axis=1),np.sort(faces,axis=1))
    path=Path(out)
    if path.exists():raise FileExistsError(path)
    with path.open('wb') as f:
        f.write(('ply\nformat binary_little_endian 1.0\nelement vertex '+str(len(vertices))+'\nproperty float x\nproperty float y\nproperty float z\nelement face '+str(len(faces))+'\nproperty list uchar int vertex_index\nend_header\n').encode('ascii'))
        f.write(vertices.astype('<f4').tobytes())
        block=np.empty(len(faces),dtype=[('count','u1'),('indices','<i4',(3,))])
        block['count']=3;block['indices']=oriented
        f.write(block.tobytes())
    report={'vertices':len(vertices),'triangles':len(faces),'flipped_triangles':int(flip.sum()),
        'max_vertex_to_fused_point_distance':float(dist.max()),
        'geometry_unchanged':True,'method':'Triangle winding agrees with nearest MVS point normals; no vertex movement or inferred surface filling.'}
    path.with_suffix('.orientation.json').write_text(json.dumps(report,indent=2),'utf-8')
    print(json.dumps(report))
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mesh',required=True);p.add_argument('--fused',required=True);p.add_argument('--out',required=True)
    a=p.parse_args();orient(a.mesh,a.fused,a.out)
