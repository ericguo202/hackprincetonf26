"""P1: roof shape and texture from the splat alone, next to v43's Vexcel-fitted plain roof."""
import sys
sys.path.insert(0, "scripts/splat")
import numpy as np, trimesh
from scipy import ndimage as ndi
from PIL import Image
import splatlib as sl, sview

M = "y1cVPR1HsnL"; D = sl.OUT / "splat_bake" / M
GLB = str(sl.OUT / f"cache/glb/{M}_interior_pano_v43.glb")
g = sl.load(M)
s = trimesh.load(GLB)
def zup(v): return np.stack([v[:, 0], -v[:, 2], v[:, 1]], 1)
rv = np.concatenate([zup(np.asarray(s.geometry[n].vertices)) for n in s.geometry if n.startswith("roof")])
yard = np.concatenate([zup(np.asarray(s.geometry[n].vertices)) for n in s.geometry if n.startswith("yard")])
x0, x1, y0, y1 = rv[:, 0].min() - 1.2, rv[:, 0].max() + 1.2, rv[:, 1].min() - 1.2, rv[:, 1].max() + 1.2
CELL = 0.1
W, H = int(round((x1 - x0) / CELL)), int(round((y1 - y0) / CELL))
x1, y0 = x0 + W * CELL, y1 - H * CELL
xyz, al = g["xyz"], g["alpha"]
# vertical registration on the yard: splat ground (lowest dense layer) against our yard heightfield vertices
from scipy.spatial import cKDTree
m = (al > 0.5) & (xyz[:, 2] < 1.0) & (g["size"] < 0.1)
t = cKDTree(xyz[m][:, :2])
sub = yard[::25]
dist, idx = t.query(sub[:, :2], k=8, distance_upper_bound=0.1)
okq = np.isfinite(dist).all(1)
dz = np.median(xyz[m][idx[okq], 2], axis=1) - sub[okq, 2]
print("yard: splat ground minus our ground: median %.3f m, MAD %.3f m (n=%d)" % (np.median(dz), np.median(abs(dz - np.median(dz))), okq.sum()))
# roof layer: per cell, the lowest strong mode of opaque Gaussians between 1.9 and 9 m
iu = ((xyz[:, 0] - x0) / CELL).astype(int); iv = ((y1 - xyz[:, 1]) / CELL).astype(int)
ZB = 0.05; z0 = 1.9; NZ = int((9 - z0) / ZB)
iz = ((xyz[:, 2] - z0) / ZB).astype(int)
ok = (iu >= 0) & (iu < W) & (iv >= 0) & (iv < H) & (iz >= 0) & (iz < NZ) & (al > 0.2)
hist = np.zeros((H, W, NZ), np.float32)
np.add.at(hist, (iv[ok], iu[ok], iz[ok]), al[ok])
hist = ndi.uniform_filter(hist, size=(3, 3, 3))
peak = hist.max(2)
strong = hist >= 0.5 * peak[..., None]
low = strong.argmax(2)
Z = z0 + (low + 0.5) * ZB
have = peak > 0.15
Zf = ndi.median_filter(np.where(have, Z, np.nan), size=5)
Zf = np.where(np.isfinite(Zf), Zf, Z)
top = z0 + (hist.shape[2] - 1 - (hist[..., ::-1] > 0.3 * peak[..., None]).argmax(2) + 0.5) * ZB
print("cells under something higher than the roof layer by 1 m (tree over roof): %.0f%%" % ((top - Zf > 1.0)[have].mean() * 100))
# region: where v43 has a roof (dilated 0.3 m) - the outline question is separate
foot = np.zeros((H, W), bool)
ru = ((rv[:, 0] - x0) / CELL).astype(int); rw = ((y1 - rv[:, 1]) / CELL).astype(int)
foot[rw, ru] = True
foot = ndi.binary_fill_holes(ndi.binary_closing(foot, iterations=4))
region = ndi.binary_dilation(foot, iterations=3) & have
# texture: Gaussians within 0.2 m of the roof layer, from straight above
gz = np.full(len(xyz), np.nan); inb = (iu >= 0) & (iu < W) & (iv >= 0) & (iv < H)
gz[inb] = Zf[iv[inb], iu[inb]]
PX = 50
img, a, dep = sl.ortho(g, (x0, y1, 30.0), (1, 0, 0), (0, -1, 0), (x1 - x0, y1 - y0), PX, mask=np.abs(xyz[:, 2] - gz) < 0.2)
big = np.kron(region, np.ones((int(PX * CELL),) * 2, bool))
print("roof texels with alpha > 0.8: %.0f%%, > 0.5: %.0f%%" % ((a[big] > .8).mean() * 100, (a[big] > .5).mean() * 100))
rgb = np.clip(img / np.maximum(a, 1e-3)[..., None], 0, 1)
# fill what the drone did not see with the nearest seen texel (prototype; the pipeline has _fill)
seen = a > 0.5
ind = ndi.distance_transform_edt(~seen, return_distances=False, return_indices=True)
rgb = rgb[ind[0], ind[1]]
tex = np.dstack([(rgb * 255).astype(np.uint8), np.full(a.shape, 255, np.uint8)])
Image.fromarray(tex[..., :3]).save(D / "p1_roof_tex.jpg", quality=92)
np.save(D / "p1_roof_z.npy", Zf)
verts, faces, uvs = sview.grid_mesh(x0, y1, CELL, Zf, region)
print("roof mesh", len(faces), "faces")
VIEWS = ["18,-8,10;3,3,2.5;55", "24,3,1.6;5,4,2.2;75", "-13,13,1.6;3,4,1.8;75", "-9,10,1.7;0,5,2.8;70", "3,25,14;3,4,2;50", "-14.8,12.6,1.7;0,5,2.5;80"]
sview.Scene(GLB).shots(VIEWS, str(D / "p1_roof_v43.jpg"))
sc = sview.Scene(GLB, hide=("roof",)); sc.add(verts, faces, uvs, tex)
sc.shots(VIEWS, str(D / "p1_roof_splat.jpg"))
