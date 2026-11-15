"""P2: walls. Each facade plane of v43 as the splat sees it: an ortho view along the wall's normal of only the
Gaussians within 0.25 m of the plane. Prints coverage, writes one strip per wall of 3 m or more."""
import sys
sys.path.insert(0, "scripts/splat")
import numpy as np, trimesh
from PIL import Image
import splatlib as sl

M = "y1cVPR1HsnL"; D = sl.OUT / "splat_bake" / M
g = sl.load(M)
s = trimesh.load(sl.OUT / f"cache/glb/{M}_interior_pano_v43.glb")
def zup(v): return np.stack([v[:, 0], -v[:, 2], v[:, 1]], 1)
planes = {}
for n in s.geometry:
    if not n.startswith("facade"): continue
    m = s.geometry[n]; v = zup(np.asarray(m.vertices)); f = np.asarray(m.faces)
    nrm = np.cross(v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]]); ar = np.linalg.norm(nrm, axis=1); ok = ar > 1e-9
    nrm = nrm[ok] / ar[ok, None]; tri = v[f[ok]]
    for k in range(len(nrm)):
        if abs(nrm[k, 2]) > 0.2: continue
        ang = round(np.degrees(np.arctan2(nrm[k, 1], nrm[k, 0])) / 2) * 2 % 360
        off = round(float(tri[k, 0, :2] @ nrm[k, :2]) / 0.1) * 0.1
        planes.setdefault((ang, off), []).append(tri[k])
rows = []
for (ang, off), tris in sorted(planes.items()):
    P = np.concatenate(tris)
    nx, ny = np.cos(np.radians(ang)), np.sin(np.radians(ang))
    t = np.array([-ny, nx, 0.0])  # along the wall, to the left seen from outside... image x runs to the viewer's right
    along = P @ t; lo, hi = along.min(), along.max(); z0, z1 = P[:, 2].min(), P[:, 2].max()
    if hi - lo < 3: continue
    nrm3 = np.array([nx, ny, 0.0])
    # viewer outside looking along -n: image x = -t (so u x v = look = -n with v = down)
    ax_u = t; ax_v = np.array([0, 0, -1.0])
    look = np.cross(ax_u, ax_v)
    if look @ nrm3 > 0: ax_u = -t
    origin = nrm3 * (off + 2.0) + ax_u * ((P @ ax_u).min()) + np.array([0, 0, z1])
    PX = 60
    img, a, dep = sl.ortho(g, origin, ax_u, ax_v, (hi - lo, z1 - z0), PX, near=2.0 - 0.25, far=2.0 + 0.25)
    cov = (a > 0.5).mean()
    print(f"wall normal {ang:3.0f} deg, offset {off:6.1f}, {hi-lo:4.1f} x {z1-z0:.1f} m: splat alpha > 0.5 on {cov*100:3.0f}%, > 0.8 on {(a>0.8).mean()*100:3.0f}%")
    rows.append((ang, off, sl.to_image(img, a)))
Wm = max(r[2].width for r in rows)
sheet = Image.new("RGB", (Wm, sum(r[2].height + 6 for r in rows)), (30, 30, 30))
y = 0
for ang, off, im in rows:
    sheet.paste(im, (0, y)); y += im.height + 6
sheet.save(D / "p2_walls.jpg", quality=90)
print(sheet.size)
