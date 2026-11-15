"""P1b: roof planes fitted (worldgen.roof.fit_roof, unchanged) to the splat's roof layer instead of the Vexcel
surface model; outline from the splat too; texture = the splat flattened onto the fitted planes."""
import sys
sys.path.insert(0, "scripts/splat")
import numpy as np, trimesh, shapely
from scipy import ndimage as ndi
from PIL import Image
import splatlib as sl, sview
from worldgen import roof as rf

M = "y1cVPR1HsnL"; D = sl.OUT / "splat_bake" / M
GLB = str(sl.OUT / f"cache/glb/{M}_interior_pano_v43.glb")
g = sl.load(M)
s = trimesh.load(GLB)
def zup(v): return np.stack([v[:, 0], -v[:, 2], v[:, 1]], 1)
rv = np.concatenate([zup(np.asarray(s.geometry[n].vertices)) for n in s.geometry if n.startswith("roof")])
x0, y1 = rv[:, 0].min() - 1.5, rv[:, 1].max() + 1.5
CELL = 0.1
W, H = int(round((rv[:, 0].max() + 1.5 - x0) / CELL)), int(round((y1 - rv[:, 1].min() + 1.5) / CELL))
xyz, al = g["xyz"], g["alpha"]
iu = ((xyz[:, 0] - x0) / CELL).astype(int); iv = ((y1 - xyz[:, 1]) / CELL).astype(int)
ZB = 0.05; z0 = 1.9; NZ = int((9 - z0) / ZB)
iz = ((xyz[:, 2] - z0) / ZB).astype(int)
inb = (iu >= 0) & (iu < W) & (iv >= 0) & (iv < H)
ok = inb & (iz >= 0) & (iz < NZ) & (al > 0.2)
hist = np.zeros((H, W, NZ), np.float32)
np.add.at(hist, (iv[ok], iu[ok], iz[ok]), al[ok])
hist = ndi.uniform_filter(hist, size=(3, 3, 3))
peak = hist.max(2)
Z = z0 + (hist.argmax(2) + 0.5) * ZB  # the densest layer: a roof is a sheet, a canopy is spread out
have = peak > 0.15
foot = np.zeros((H, W), bool)
foot[((y1 - rv[:, 1]) / CELL).astype(int), ((rv[:, 0] - x0) / CELL).astype(int)] = True
foot = ndi.binary_fill_holes(ndi.binary_closing(foot, iterations=4))
cand = have & (Z > 2.05) & (Z < 7) & ndi.binary_dilation(foot, iterations=8)
cand = ndi.binary_opening(ndi.binary_closing(cand, iterations=2), iterations=3)
lab, n = ndi.label(cand)
region_mask = lab == (np.bincount(lab.ravel())[1:].argmax() + 1)
region_mask = ndi.binary_fill_holes(region_mask)
print("roof area: v43 %.0f m2, splat outline %.0f m2" % (foot.sum() * CELL**2, region_mask.sum() * CELL**2))
ii, jj = np.nonzero(region_mask[::2, ::2])
region = shapely.unary_union([shapely.box(x0 + j * .2, y1 - (i + 1) * .2, x0 + (j + 1) * .2, y1 - i * .2) for i, j in zip(ii, jj)]).buffer(0.1).buffer(-0.1).simplify(0.08)
if region.geom_type != "Polygon": region = max(region.geoms, key=lambda p: p.area)
Zn = np.where(have, Z, np.nan)
def surface(x, y):
    u = np.clip(((np.asarray(x) - x0) / CELL).astype(int), 0, W - 1); v = np.clip(((y1 - np.asarray(y)) / CELL).astype(int), 0, H - 1)
    return Zn[v, u]
import time; t = time.time()
roof = rf.fit_roof(surface, region, min_z=2.0)
print("fit s", round(time.time() - t, 1), "planes", len(roof.planes) if hasattr(roof, "planes") else roof)
xs = x0 + (np.arange(W) + 0.5) * CELL; ys = y1 - (np.arange(H) + 0.5) * CELL
X, Y = np.meshgrid(xs, ys)
Zp = roof.height(X.ravel(), Y.ravel()).reshape(H, W)
inreg = shapely.contains_xy(region, X.ravel(), Y.ravel()).reshape(H, W)
d = (Z - Zp)[inreg & have]
print("splat roof layer minus fitted planes: median %.3f, within 5 cm %.0f%%, 10 cm %.0f%%, 20 cm %.0f%%" % (np.median(d), *(100 * (abs(d) < t).mean() for t in (.05, .1, .2))))
gz = np.full(len(xyz), np.nan); gz[inb] = Zp[iv[inb], iu[inb]]
PX = 50
img, a, dep = sl.ortho(g, (x0, y1, 30.0), (1, 0, 0), (0, -1, 0), (W * CELL, H * CELL), PX, mask=np.abs(xyz[:, 2] - gz) < 0.2)
big = np.kron(inreg, np.ones((int(PX * CELL),) * 2, bool))
print("roof texels with alpha > 0.8: %.0f%%, > 0.5: %.0f%%" % ((a[big] > .8).mean() * 100, (a[big] > .5).mean() * 100))
rgb = np.clip(img / np.maximum(a, 1e-3)[..., None], 0, 1)
seen = (a > 0.5) & big
ind = ndi.distance_transform_edt(~seen, return_distances=False, return_indices=True)
rgb = rgb[ind[0], ind[1]]
tex = np.dstack([(rgb * 255).astype(np.uint8), np.full(a.shape, 255, np.uint8)])
Image.fromarray(np.where(big[..., None], tex[..., :3], 255)).save(D / "p1b_roof_tex.jpg", quality=92)
Image.fromarray((np.dstack([seen | ~big] * 3) * 255).astype(np.uint8)).save(D / "p1b_roof_seen.png")
verts, faces, uvs = sview.grid_mesh(x0, y1, CELL, Zp, inreg)
VIEWS = ["18,-8,10;3,3,2.5;55", "24,3,1.6;5,4,2.2;75", "-13,13,1.6;3,4,1.8;75", "-9,10,1.7;0,5,2.8;70", "3,25,14;3,4,2;50", "-10,-10,12;3,4,2;50"]
sc = sview.Scene(GLB, hide=("roof",)); sc.add(verts, faces, uvs, tex)
sc.shots(VIEWS, str(D / "p1b_roof_splat.jpg"))
