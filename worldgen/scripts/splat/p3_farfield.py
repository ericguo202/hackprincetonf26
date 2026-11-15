"""P3: the far field as real geometry from the splat instead of the photo disk and dome.

A 2.5D surface (top-down depth of the splat, 0.25 m cells) outside the lot, each face textured from whichever of
9 orthographic splat renders (straight down + 8 obliques at 30 degrees) sees it most squarely (depth-tested)."""
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
own = np.concatenate([zup(np.asarray(s.geometry[n].vertices)) for n in s.geometry if n.split("_")[0] in ("yard", "roof")])
CX, CY, R = 4.2, 4.3, 60.0
CELL = 0.25
N = int(2 * R / CELL)
x0, y1 = CX - R, CY + R
img, a, dep = sl.ortho(g, (x0, y1, 40.0), (1, 0, 0), (0, -1, 0), (2 * R, 2 * R), 1 / CELL * 4)  # 6.25 cm
PXT = 4 / CELL
Zfull = 40.0 - dep
Z = np.nanmedian(Zfull.reshape(N, 4, N, 4).transpose(0, 2, 1, 3).reshape(N, N, 16), axis=2)
valid = np.isfinite(Z)
Z = np.where(valid, Z, 0.0)
Z = ndi.median_filter(Z, size=3)
mine = np.zeros((N, N), bool)
iu = ((own[:, 0] - x0) / CELL).astype(int); iv = ((y1 - own[:, 1]) / CELL).astype(int)
mine[iv, iu] = True
mine = ndi.binary_erosion(ndi.binary_fill_holes(ndi.binary_closing(mine, iterations=3)), iterations=1)
X, Y = np.meshgrid(x0 + (np.arange(N) + 0.5) * CELL, y1 - (np.arange(N) + 0.5) * CELL)
keep = valid & ~mine & (np.hypot(X - CX, Y - CY) < R - 1)
verts, faces, _ = sview.grid_mesh(x0, y1, CELL, Z, keep)
print("far-field mesh", len(faces), "faces; own lot cells", mine.sum())
tri = verts[faces]; cen = tri.mean(1)
nrm = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]); nrm /= np.linalg.norm(nrm, axis=1)[:, None] + 1e-12
nrm[nrm[:, 2] < 0] *= -1
# views
views = [((1, 0, 0), (0, -1, 0))]
EL = np.radians(30)
for k in range(8):
    az = k * np.pi / 4
    look = np.array([-np.cos(az) * np.cos(EL), -np.sin(az) * np.cos(EL), -np.sin(EL)])  # camera at azimuth az looking in
    u = np.cross(look, [0, 0, 1.0]); u /= np.linalg.norm(u); v = np.cross(look, u)  # v points down the image
    if np.cross(u, v) @ look < 0: u = -u
    views.append((u, v))
best = np.full(len(faces), -1); score = np.full(len(faces), -1.0)
texs, proj = [], []
PX = 16
for k, (u, v) in enumerate(views):
    u = np.asarray(u, float); v = np.asarray(v, float); look = np.cross(u, v)
    c = np.array([CX, CY, 3.0])
    ext = R * 1.05
    origin = c - u * ext - v * ext - look * 150.0
    im, al, dp = sl.ortho(g, origin, u, v, (2 * ext, 2 * ext), PX)
    rgb = np.clip(im / np.maximum(al, 1e-3)[..., None], 0, 1)
    Hh, Ww = al.shape
    pu = (cen - origin) @ u * PX; pv = (cen - origin) @ v * PX; pd = (cen - origin) @ look
    iu2 = np.clip(pu.astype(int), 0, Ww - 1); iv2 = np.clip(pv.astype(int), 0, Hh - 1)
    vis = (al[iv2, iu2] > 0.5) & (pd <= np.nan_to_num(dp[iv2, iu2], nan=-1e9) + 0.6)
    sc = np.where(vis, -(nrm @ look), -1.0)
    if k == 0: sc = np.where(al[iv2, iu2] > 0.3, np.maximum(sc * 0.999, 0.02), 0.01)  # straight down: the fallback
    take = sc > score
    best[take] = k; score[take] = sc[take]
    texs.append(np.dstack([(rgb * 255).astype(np.uint8), np.full(al.shape, 255, np.uint8)]))
    proj.append((origin, u, v, Ww, Hh))
    sl.to_image(im, al).resize((Ww // 3, Hh // 3)).save(D / f"p3_view{k}.jpg", quality=85)
    print("view", k, "faces won so far", np.bincount(best[best >= 0], minlength=k + 1).tolist())
VIEWS = ["-13.8,14.2,1.7;-30,30,2;80", "-9.4,11.4,1.7;-16,18,2;80", "-13,13,1.6;3,4,1.8;75", "12.7,11.9,1.5;30,5,2;80",
         "24,3,1.6;5,4,2.2;75", "14,4,1.6;40,-6,2;75", "18,-8,10;3,3,2.5;55", "3,25,14;3,4,2;50"]
base = sview.Scene(GLB)
base.shots(VIEWS, str(D / "p3_v43.jpg"))
sc = sview.Scene(GLB, hide=("backdrop",))
for k, (origin, u, v, Ww, Hh) in enumerate(proj):
    fk = faces[best == k]
    if not len(fk): continue
    used = np.unique(fk); remap = np.full(len(verts), -1); remap[used] = np.arange(len(used))
    vv = verts[used]
    uv = np.stack([(vv - origin) @ u * PX / Ww, 1 - (vv - origin) @ v * PX / Hh], 1)
    sc.add(vv, remap[fk], uv, texs[k])
ims = sc.shots(VIEWS, str(D / "p3_splatmesh.jpg"))
