"""P3 + P4: far field as geometry from the splat, with trees taken out of the height surface and rebuilt as
stacks of alpha cards (each card = the splat's Gaussians in one slab of the tree, flattened onto its mid-plane)."""
import sys
sys.path.insert(0, "scripts/splat")
import numpy as np, trimesh
from scipy import ndimage as ndi
from PIL import Image
import splatlib as sl, sview

M = "y1cVPR1HsnL"; D = sl.OUT / "splat_bake" / M
GLB = str(sl.OUT / f"cache/glb/{M}_interior_pano_v43.glb")
g = sl.load(M)
xyz, al = g["xyz"], g["alpha"]
s = trimesh.load(GLB)
def zup(v): return np.stack([v[:, 0], -v[:, 2], v[:, 1]], 1)
own = np.concatenate([zup(np.asarray(s.geometry[n].vertices)) for n in s.geometry if n.split("_")[0] in ("yard", "roof")])
CX, CY, R = 4.2, 4.3, 60.0
CELL = 0.25
N = int(2 * R / CELL)
x0, y1 = CX - R, CY + R
img, a, dep = sl.ortho(g, (x0, y1, 40.0), (1, 0, 0), (0, -1, 0), (2 * R, 2 * R), 4 / CELL)
blk = lambda A: A.reshape(N, 4, N, 4).transpose(0, 2, 1, 3).reshape(N, N, 16)
with np.errstate(all="ignore"):
    import warnings; warnings.simplefilter("ignore")
    Ztop = np.nanmedian(blk(40.0 - dep), axis=2)
rgbtop = (img / np.maximum(a, 1e-3)[..., None]).reshape(N, 4, N, 4, 3).mean((1, 3))
valid = np.isfinite(Ztop)
# ground layer: lowest dense layer of opaque Gaussians per cell
iu = ((xyz[:, 0] - x0) / CELL).astype(int); iv = ((y1 - xyz[:, 1]) / CELL).astype(int)
ZB = 0.1; zb0 = -4.0; NZ = int(24 / ZB)
iz = ((xyz[:, 2] - zb0) / ZB).astype(int)
inb = (iu >= 0) & (iu < N) & (iv >= 0) & (iv < N)
ok = inb & (iz >= 0) & (iz < NZ) & (al > 0.3)
hist = np.zeros((N, N, NZ), np.float32)
np.add.at(hist, (iv[ok], iu[ok], iz[ok]), al[ok])
hs = ndi.uniform_filter(hist, size=(3, 3, 1))
dense = hs > 0.25
Zlow = np.where(dense.any(2), zb0 + (dense.argmax(2) + 0.5) * ZB, np.nan)
# ground = smooth lower envelope of Zlow (a roof column has no ground return: fill from around)
gz = np.where(np.isfinite(Zlow), Zlow, 1e3)
ground = ndi.minimum_filter(gz, size=13)
ground = ndi.uniform_filter(np.where(ground > 500, np.nanmedian(Zlow), ground), size=9)
height = np.where(valid, Ztop - ground, 0)
zt = np.where(valid, Ztop, ground)
rough = np.sqrt(np.maximum(ndi.uniform_filter(zt**2, 5) - ndi.uniform_filter(zt, 5) ** 2, 0))
green = (rgbtop[..., 1] > rgbtop[..., 0] * 1.02) & (rgbtop[..., 1] > rgbtop[..., 2] * 1.15)
tree = (height > 2.0) & ((rough > 0.35) | green)
tree = ndi.binary_opening(ndi.binary_closing(tree, iterations=2), iterations=2)
tree = ndi.binary_dilation(tree, iterations=2) & (height > 0.6)
print("tree cells: %.0f m2 of %.0f" % (tree.sum() * CELL**2, valid.sum() * CELL**2))
Image.fromarray((np.dstack([tree * 255, np.clip(height / 8, 0, 1) * 255, np.clip(rough, 0, 1) * 255])).astype(np.uint8)).save(D / "p4_treemask.png")
Z = np.where(tree | ~valid, ground, Ztop)
Z = ndi.median_filter(Z, size=3)
mine = np.zeros((N, N), bool)
mine[((y1 - own[:, 1]) / CELL).astype(int), ((own[:, 0] - x0) / CELL).astype(int)] = True
mine = ndi.binary_erosion(ndi.binary_fill_holes(ndi.binary_closing(mine, iterations=3)), iterations=1)
X, Y = np.meshgrid(x0 + (np.arange(N) + 0.5) * CELL, y1 - (np.arange(N) + 0.5) * CELL)
keep = ~mine & (np.hypot(X - CX, Y - CY) < R - 1)
verts, faces, _ = sview.grid_mesh(x0, y1, CELL, Z, keep)
tri = verts[faces]; cen = tri.mean(1)
nrm = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]); nrm /= np.linalg.norm(nrm, axis=1)[:, None] + 1e-12
nrm[nrm[:, 2] < 0] *= -1
fu = np.clip(((cen[:, 0] - x0) / CELL).astype(int), 0, N - 1); fv = np.clip(((y1 - cen[:, 1]) / CELL).astype(int), 0, N - 1)
f_tree = tree[fv, fu]
# Gaussians of the surface only (trees removed) for the texture views: within 0.6 m above the surface or below it
gsurf = np.full(len(xyz), np.nan); gsurf[inb] = Z[iv[inb], iu[inb]]
gtree = np.zeros(len(xyz), bool); gtree[inb] = tree[iv[inb], iu[inb]]
notree = ~(gtree & (xyz[:, 2] > gsurf + 0.5))
views = [((1, 0, 0), (0, -1, 0))]
EL = np.radians(25)
for k in range(8):
    az = k * np.pi / 4
    look = np.array([-np.cos(az) * np.cos(EL), -np.sin(az) * np.cos(EL), -np.sin(EL)])
    u = np.cross(look, [0, 0, 1.0]); u /= np.linalg.norm(u); v = np.cross(look, u)
    if np.cross(u, v) @ look < 0: u = -u
    views.append((u, v))
best = np.full(len(faces), -1); score = np.full(len(faces), -1.0)
texs, proj = [], []
PX = 20
for k, (u, v) in enumerate(views):
    u = np.asarray(u, float); v = np.asarray(v, float); look = np.cross(u, v)
    c = np.array([CX, CY, 3.0]); ext = R * 1.05
    origin = c - u * ext - v * ext - look * 150.0
    im, alv, dp = sl.ortho(g, origin, u, v, (2 * ext, 2 * ext), PX, mask=notree)
    rgb = np.clip(im / np.maximum(alv, 1e-3)[..., None], 0, 1)
    seen = alv > 0.4
    ind = ndi.distance_transform_edt(~seen, return_distances=False, return_indices=True)
    rgb = rgb[ind[0], ind[1]]
    Hh, Ww = alv.shape
    pu = (cen - origin) @ u * PX; pv = (cen - origin) @ v * PX; pd = (cen - origin) @ look
    iu2 = np.clip(pu.astype(int), 0, Ww - 1); iv2 = np.clip(pv.astype(int), 0, Hh - 1)
    vis = (alv[iv2, iu2] > 0.5) & (np.abs(pd - np.nan_to_num(dp[iv2, iu2], nan=-1e9)) < 0.6)
    sc = np.where(vis, -(nrm @ look), -1.0)
    if k == 0: sc = np.maximum(sc * 0.999, 0.02)
    take = sc > score
    best[take] = k; score[take] = sc[take]
    texs.append(np.dstack([(rgb * 255).astype(np.uint8), np.full(alv.shape, 255, np.uint8)]))
    proj.append((origin, u, v, Ww, Hh))
print("faces per view", np.bincount(best, minlength=9).tolist(), "total", len(faces))

# trees as card stacks
lab, n = ndi.label(tree)
cards = []  # (corners 4x3, rgba)
PXC = 28; T = 1.0
ntree = 0
for i in range(1, n + 1):
    m = lab == i
    if m.sum() * CELL**2 < 2.0: continue
    rr, cc = np.nonzero(m)
    xa, xb = x0 + cc.min() * CELL - 0.3, x0 + (cc.max() + 1) * CELL + 0.3
    ya, yb = y1 - (rr.max() + 1) * CELL - 0.3, y1 - rr.min() * CELL + 0.3
    if np.hypot((xa + xb) / 2 - CX, (ya + yb) / 2 - CY) > 48: continue
    gm = np.zeros(len(xyz), bool); gm[inb] = m[iv[inb], iu[inb]]
    gm &= (xyz[:, 2] > gsurf + 0.25) & (al > 0.05)
    if gm.sum() < 500: continue
    za, zb = float(np.nanmin(ground[m])) , float(np.percentile(xyz[gm, 2], 99.5)) + 0.2
    ntree += 1
    def card(origin, u, v, size, lo, hi):
        im, ac, _ = sl.ortho(g, origin, u, v, size, PXC, near=lo, far=hi, mask=gm)
        if (ac > 0.4).mean() < 0.01: return
        rgb = np.clip(im / np.maximum(ac, 1e-3)[..., None], 0, 1)
        seen = ac > 0.4
        ind = ndi.distance_transform_edt(~seen, return_distances=False, return_indices=True)
        rgb = rgb[ind[0], ind[1]]
        rgba = np.dstack([(rgb * 255).astype(np.uint8), np.where(seen, 255, 0).astype(np.uint8)])
        o = np.asarray(origin, float); u = np.asarray(u, float); v = np.asarray(v, float)
        cards.append((np.array([o, o + u * size[0], o + u * size[0] + v * size[1], o + v * size[1]]), rgba))
    for xk in np.arange(xa + T / 2, xb, T):
        card((xk, yb, zb), (0, -1, 0), (0, 0, -1), (yb - ya, zb - za), -T / 2, T / 2)
    for yk in np.arange(ya + T / 2, yb, T):
        card((xa, yk, zb), (1, 0, 0), (0, 0, -1), (xb - xa, zb - za), -T / 2, T / 2)
    crown0 = za + 1.8
    for zk in np.arange(crown0 + 0.6, zb, 1.2):
        card((xa, yb, zk), (1, 0, 0), (0, -1, 0), (xb - xa, yb - ya), -0.6, 0.6)
mp = sum(c[1].shape[0] * c[1].shape[1] for c in cards) / 1e6
print("trees", ntree, "cards", len(cards), "card texture megapixels %.1f" % mp)

VIEWS = ["-13.8,14.2,1.7;3,4,2.5;80", "-9.4,11.4,1.7;-16,18,2;80", "-13,13,1.6;-30,2,2;80", "12.7,11.9,1.5;30,5,2;80",
         "24,3,1.6;5,4,2.2;75", "14,4,1.6;40,-6,2;75", "18,-8,10;3,3,2.5;55", "-25,-20,25;3,4,0;50"]
sky = np.array([150, 185, 225], np.uint8)
def save(ims, path):
    out = [np.where((im == (40, 0, 60)).all(2, keepdims=True), sky, im) for im in ims]
    rows = [np.hstack(out[i:i + 2]) for i in range(0, len(out), 2)]
    Image.fromarray(np.vstack(rows)).save(path, quality=88)
sc = sview.Scene(GLB, hide=("backdrop", "trees", "plants"))
for k, (origin, u, v, Ww, Hh) in enumerate(proj):
    fk = faces[best == k]
    if not len(fk): continue
    used = np.unique(fk); remap = np.full(len(verts), -1); remap[used] = np.arange(len(used))
    vv = verts[used]
    sc.add(vv, remap[fk], np.stack([(vv - origin) @ u * PX / Ww, 1 - (vv - origin) @ v * PX / Hh], 1), texs[k])
quv = np.array([[0, 1], [1, 1], [1, 0], [0, 0.0]])
for corners, rgba in cards:
    sc.add(corners, np.array([[0, 1, 2], [0, 2, 3]]), quv, rgba)
save(sc.shots(VIEWS, str(D / "_tmp.jpg")), D / "p4_splat_all.jpg")
