"""Re-derive the panorama orientation convention by brute force (how panos.py's convention was found).

Run from worldgen/ after a build (needs out/cache/matterport/<id>.json, out/raw/<id>/matterpak, skybox_2k):
    uv run python scripts/preview/calib_panos.py <model_id>
Casts rays from a few pano centres into the MatterPak mesh, reads the mesh's own texture colour at each hit,
and scores every candidate mapping (forward axis, handedness, rotation vs inverse) by mean abs colour error
against the skybox faces. On 7014 Navidad the winner scored ~22, every other candidate 35+. Re-run this if
panoramas from a different capture device ever look rotated or mirrored.
NOTE: written as a scratch script; it imports rebake.load_scan and uses trimesh's embree intersector."""
import json, itertools, numpy as np, pathlib, trimesh
from PIL import Image
from worldgen import rebake as rb
from trimesh.ray.ray_pyembree import RayMeshIntersector
import sys
MID = sys.argv[1] if len(sys.argv) > 1 else 'y1cVPR1HsnL'
m=json.load(open(f'out/cache/matterport/{MID}.json'))
scan=rb.load_scan(sorted(pathlib.Path(f'out/raw/{MID}/matterpak').glob('*.obj'))[0])
V=scan.tris.reshape(-1,3); F=np.arange(len(V)).reshape(-1,3)
mesh=trimesh.Trimesh(V,F,process=False); rmi=RayMeshIntersector(mesh)
def quat_R(q):
    x,y,z,w=q['x'],q['y'],q['z'],q['w']
    return np.array([[1-2*(y*y+z*z),2*(x*y-z*w),2*(x*z+y*w)],[2*(x*y+z*w),1-2*(x*x+z*z),2*(y*z-x*w)],[2*(x*z-y*w),2*(y*z+x*w),1-2*(x*x+y*y)]])
def mesh_colors(tri, loc):
    T=scan.tris[tri]; v0=T[:,1]-T[:,0]; v1=T[:,2]-T[:,0]; v2=loc-T[:,0]
    d00=(v0*v0).sum(1); d01=(v0*v1).sum(1); d11=(v1*v1).sum(1); d20=(v2*v0).sum(1); d21=(v2*v1).sum(1)
    den=d00*d11-d01*d01+1e-12; b1=(d11*d20-d01*d21)/den; b2=(d00*d21-d01*d20)/den; b0=1-b1-b2
    uv=b0[:,None]*scan.uvs[tri,0]+b1[:,None]*scan.uvs[tri,1]+b2[:,None]*scan.uvs[tri,2]
    out=np.zeros((len(tri),3))
    for t in np.unique(scan.tex[tri]):
        s=scan.tex[tri]==t; img=scan.images[t]; h,w=img.shape[:2]
        out[s]=img[np.clip(((1-uv[s,1])*h).astype(int),0,h-1),np.clip((uv[s,0]*w).astype(int),0,w-1)]
    return out
rng=np.random.default_rng(0)
res=[]
panos=[p for p in m['panoLocations'] if -1<p['position']['x']<9 and 0<p['position']['y']<10][:6]
data=[]
for p in panos:
    c=np.array([p['position'][k] for k in 'xyz'])
    d=rng.normal(size=(6000,3)); d/=np.linalg.norm(d,axis=1)[:,None]; d=d[abs(d[:,2])<0.6]
    loc,ray,tri=rmi.intersects_location(np.tile(c,(len(d),1)),d,multiple_hits=False)
    faces=[np.asarray(Image.open(f'out/raw/{MID}/skybox_2k/{p["id"]}_{i}.jpg').convert('RGB')) for i in range(6)]
    data.append((quat_R(p['rotation']),d[ray],mesh_colors(tri,loc),faces))
X=np.array([1.,0,0]);Y=np.array([0,1.,0]);Z=np.array([0,0,1.])
def sample_ring(faces,dc,f,r,u):
    out=np.zeros((len(dc),3)); ok=np.zeros(len(dc),bool)
    df,dr,du=dc@f,dc@r,dc@u
    for fi,(L,Rt) in {1:(df,dr),2:(dr,-df),3:(-df,-dr),4:(-dr,df)}.items():
        s=(L>0)&(abs(Rt)<=L)&(abs(du)<=L)
        if not s.any(): continue
        img=faces[fi]; H,W=img.shape[:2]
        x=np.clip(((Rt[s]/L[s]+1)/2*W).astype(int),0,W-1); y=np.clip(((-du[s]/L[s]+1)/2*H).astype(int),0,H-1)
        out[s]=img[y,x]; ok|=s
    return out,ok
for inv in (False,True):
    for fn,f in (('+X',X),('+Y',Y),('-X',-X),('-Y',-Y)):
        for hand in (1,-1):
            r=np.cross(f,Z)*hand; errs=[]
            for R,d,col,faces in data:
                dc=d@R if not inv else d@R.T   # d@R = R^T d (world->cam)
                pc,ok=sample_ring(faces,dc,f,r,Z)
                errs.append(np.abs(pc[ok]-col[ok]).mean())
            res.append((np.mean(errs),inv,fn,hand))
for e in sorted(res)[:5]: print(e)
print('worst',sorted(res)[-1])
print('--- top/bottom')
f=X; r=np.cross(f,Z)
data2=[]
for p in panos:
    c=np.array([p['position'][k] for k in 'xyz'])
    d=rng.normal(size=(20000,3)); d/=np.linalg.norm(d,axis=1)[:,None]; d=d[abs(d[:,2])>0.8]
    loc,ray,tri=rmi.intersects_location(np.tile(c,(len(d),1)),d,multiple_hits=False)
    faces=[np.asarray(Image.open(f'out/raw/{MID}/skybox_2k/{p["id"]}_{i}.jpg').convert('RGB')) for i in range(6)]
    data2.append((quat_R(p['rotation']),d[ray],mesh_colors(tri,loc),faces))
opts={'U=-f,R=r':(-f,r),'U=r,R=f':(r,f),'U=f,R=-r':(f,-r),'U=-r,R=-f':(-r,-f),'U=-f,R=-r':(-f,-r),'U=r,R=-f':(r,-f),'U=f,R=r':(f,r),'U=-r,R=f':(-r,f)}
for fi,sign in ((0,1),(5,-1)):
    out=[]
    for name,(U,Rt) in opts.items():
        errs=[]
        for R,d,col,faces in data2:
            dc=d@R; L=sign*(dc@Z); s=(L>0)&(abs(dc@Rt)<=L)&(abs(dc@U)<=L)
            if fi==5:  # skip tripod blur
                s&=(np.hypot(dc@Rt,dc@U)/np.maximum(L,1e-9))>0.45
            img=faces[fi]; H,W=img.shape[:2]
            x=np.clip((((dc@Rt)[s]/L[s]+1)/2*W).astype(int),0,W-1); y=np.clip(((-(dc@U)[s]/L[s]+1)/2*H).astype(int),0,H-1)
            errs.append(np.abs(img[y,x]-col[s]).mean())
        out.append((np.mean(errs),name))
    print('face',fi,sorted(out)[:3])
