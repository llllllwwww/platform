"""Diagnose tunnel cross sections and topology before imposing a geometric prior."""
import json
from pathlib import Path
import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT=Path(__file__).resolve().parent
RUN=ROOT/'results/dvp_tunnel'
OUT=RUN/'regularization';OUT.mkdir(exist_ok=True)
s=json.loads((RUN/'scene.json').read_text('utf-8'))
c=np.array([x['center'] for x in s['cameras']]); origin=np.median(c,axis=0)
_,_,vt=np.linalg.svd(c-c.mean(0),full_matrices=False)
t=vt[0];t*=np.sign((c[-1]-c[0])@t)
up=-np.mean([np.array(x['world_to_camera'])[1,:3] for x in s['cameras']],axis=0)
up-=t*(up@t);up/=np.linalg.norm(up)
u=np.cross(up,t);u/=np.linalg.norm(u);v=np.cross(t,u)
basis=np.array([u,v,t]); cc=(c-origin)@basis.T
with np.load(RUN/'surface.npz') as m:vertices=m['vertices'];faces=m['faces']
p=(vertices-origin)@basis.T
edges=np.sort(np.concatenate([faces[:,[0,1]],faces[:,[1,2]],faces[:,[2,0]]]),axis=1)
ue,counts=np.unique(edges,axis=0,return_counts=True)
g=coo_matrix((np.ones(len(ue)),(ue[:,0],ue[:,1])),shape=(len(p),len(p))).tocsr()
n_components,labels=connected_components(g,directed=False)
ends=np.linspace(*np.quantile(p[:,2],[.01,.99]),33)
rows=[]
for lo,hi in zip(ends[:-1],ends[1:]):
    q=p[(p[:,2]>=lo)&(p[:,2]<hi)];a=np.arctan2(q[:,1],q[:,0]);r=np.linalg.norm(q[:,:2],axis=1)
    occupied=np.unique(np.floor((a+np.pi)/(2*np.pi)*72).astype(int))
    rows.append({'s':float((lo+hi)/2),'count':len(q),'angular_coverage':len(occupied)/72,
                 'radius_p10_p50_p90':np.quantile(r,[.1,.5,.9]).tolist() if len(q) else None})
fig,axs=plt.subplots(2,3,figsize=(15,9))
rng=np.random.default_rng(0);idx=rng.choice(len(p),min(30000,len(p)),replace=False)
axs[0,0].scatter(p[idx,2],p[idx,0],s=.4,alpha=.3);axs[0,0].plot(cc[:,2],cc[:,0],c='r');axs[0,0].set(xlabel='axis s',ylabel='transverse x',title='Plan view')
axs[0,1].scatter(p[idx,2],p[idx,1],s=.4,alpha=.3);axs[0,1].plot(cc[:,2],cc[:,1],c='r');axs[0,1].set(xlabel='axis s',ylabel='up y',title='Profile')
axs[0,2].plot([r['s'] for r in rows],[r['angular_coverage'] for r in rows]);axs[0,2].set(xlabel='axis s',ylabel='fraction occupied (72 angular bins)',title='Observed cross-section coverage')
for ax,station in zip(axs[1],np.quantile(cc[:,2],[.1,.4,.7])):
    q=p[np.abs(p[:,2]-station)<.25];ax.scatter(q[:,0],q[:,1],s=1,alpha=.3);ax.scatter([0],[0],c='r');ax.set(xlabel='transverse x',ylabel='up y',title=f'Cross section near s={station:.2f}',aspect='equal')
fig.tight_layout();fig.savefig(OUT/'diagnostics.png',dpi=160);plt.close(fig)
report={'basis_rows':basis.tolist(),'origin':origin.tolist(),'camera_local':cc.tolist(),
        'vertices':len(p),'triangles':len(faces),'boundary_edges':int((counts==1).sum()),'nonmanifold_edges':int((counts>2).sum()),'components':int(n_components),
        'largest_component_vertices':int(np.bincount(labels).max()),'xyz_quantiles':np.quantile(p,[.01,.05,.5,.95,.99],axis=0).tolist(),'sections':rows}
(OUT/'diagnostics.json').write_text(json.dumps(report,indent=2),'utf-8')
print('Boundary edges:',report['boundary_edges'],'components:',n_components)
