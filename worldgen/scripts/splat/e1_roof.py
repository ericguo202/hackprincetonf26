"""E1: the roof. (a) how far is our Vexcel-fitted roof from the splat's roof surface, (b) the roof's texture
from the splat: a top-down ortho of only the Gaussians near the roof surface (the tree over the roof is cut)."""
import sys
sys.path.insert(0, "scripts/splat")
import numpy as np, trimesh
from PIL import Image
import splatlib as sl

M = "y1cVPR1HsnL"
D = sl.OUT / "splat_bake" / M
g = sl.load(M)
s = trimesh.load(sl.OUT / f"cache/glb/{M}_interior_pano_v43.glb")
def zup(v): return np.stack([v[:, 0], -v[:, 2], v[:, 1]], 1)
top = s.geometry["roof_5"] if "roof_5" in s.geometry else None
names = [n for n in s.geometry if n.startswith("roof")]
print(names)
meshes = []
for n in names:
    m = s.geometry[n]
    v = zup(np.asarray(m.vertices)); f = np.asarray(m.faces)
    nz = np.cross(v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]])[:, 2]
    print(n, len(f), "up faces", (nz > 0).mean().round(2), "z", v[:, 2].min().round(2), v[:, 2].max().round(2))
    meshes.append(trimesh.Trimesh(v, f, process=False))
roof = trimesh.util.concatenate(meshes)
# roof height raster: highest hit of a ray going down
x0, x1, y0, y1 = roof.bounds[0, 0] - 1, roof.bounds[1, 0] + 1, roof.bounds[0, 1] - 1, roof.bounds[1, 1] + 1
PX = 50
W, H = int(round((x1 - x0) * PX)), int(round((y1 - y0) * PX))
xs = x0 + (np.arange(W) + 0.5) / PX; ys = y1 - (np.arange(H) + 0.5) / PX
X, Y = np.meshgrid(xs, ys)
o = np.stack([X.ravel(), Y.ravel(), np.full(X.size, 30.0)], 1)
ray = trimesh.ray.ray_pyembree.RayMeshIntersector(roof)
loc, ri, _ = ray.intersects_location(o, np.tile([0, 0, -1.0], (len(o), 1)), multiple_hits=False)
rz = np.full(X.size, np.nan); rz[ri] = loc[:, 2]; rz = rz.reshape(H, W)
print("roof raster", W, H, "covered", np.isfinite(rz).mean().round(2))
# splat surface from above (first surface) and the splat near our roof
img_all, al_all, dep_all = sl.ortho(g, (x0, y1, 30.0), (1, 0, 0), (0, -1, 0), (x1 - x0, y1 - y0), PX)
z_top = 30.0 - dep_all
d = (z_top - rz)[np.isfinite(rz) & np.isfinite(z_top)]
print("splat top surface minus our roof: median %.2f, p10 %.2f, p90 %.2f; within 0.15 m: %.0f%%, 0.3 m: %.0f%%" % (np.median(d), *np.percentile(d, [10, 90]), (abs(d) < .15).mean() * 100, (abs(d) < .3).mean() * 100))
# Gaussians near our roof surface only: sample roof height at each Gaussian
xyz = g["xyz"]
iu = ((xyz[:, 0] - x0) * PX).astype(int); iv = ((y1 - xyz[:, 1]) * PX).astype(int)
ok = (iu >= 0) & (iu < W) & (iv >= 0) & (iv < H)
gz = np.full(len(xyz), np.nan); gz[ok] = rz[iv[ok], iu[ok]]
dz = xyz[:, 2] - gz
for band in (0.25, 0.45):
    m = np.abs(dz) < band
    img, al, dep = sl.ortho(g, (x0, y1, 30.0), (1, 0, 0), (0, -1, 0), (x1 - x0, y1 - y0), PX, mask=m)
    on = np.isfinite(rz)
    print(f"band {band}: gaussians {m.sum()}, roof texels with alpha > 0.8: {(al[on] > .8).mean()*100:.0f}%, > 0.5: {(al[on] > .5).mean()*100:.0f}%")
    sl.to_image(img, al).save(D / f"roof_slab_{band}.jpg", quality=92)
    np.savez_compressed(D / f"roof_slab_{band}.npz", img=img, al=al, x0=x0, y1=y1, px=PX)
sl.to_image(img_all, al_all).save(D / "roof_all.jpg", quality=92)
# error map: splat roof (slab depth) minus our roof
zs = 30.0 - dep
err = np.where(np.isfinite(rz), zs - rz, np.nan)
e = np.clip((err + 0.4) / 0.8, 0, 1)
vis = np.stack([e, 1 - np.abs(e - .5) * 2, 1 - e], -1); vis[~np.isfinite(err)] = 0.2
Image.fromarray((vis * 255).astype(np.uint8)).save(D / "roof_err.png")
