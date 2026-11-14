"""Render an interior.glb from arbitrary eye positions (Matterport plan coordinates, Z up).

Run from worldgen/:
    uv run python scripts/preview/freeview.py <raw glb> out.jpg "x,y,z;lookx,looky,lookz[;fov]" ...
Eye height 1.65 m is a standing player. NO_CEILING=1 with a camera high above gives a top-down plan.
Example (7014 living room): "0.6,5.6,1.65;4.5,3.0,1.2"   top-down: "3.6,4.6,17;3.6,4.61,0;50" """
import sys, numpy as np
from PIL import Image
sys.path.insert(0, sys.path[0])
from render_compare import load, render, yup
from worldgen.panos import Raycaster
glb, out = sys.argv[1], sys.argv[2]
views = [v.split(";") for v in sys.argv[3:]]
tris, uvs, tid, texs = load(glb)
scene = (tris, uvs, tid, texs, Raycaster(tris))
ims = []
for pos, look, *rest in views:
    pos = np.array([float(x) for x in pos.split(",")]); look = np.array([float(x) for x in look.split(",")])
    fov = float(rest[0]) if rest else 80
    f = look - pos; f /= np.linalg.norm(f)
    up0 = np.array([0, 0, 1.0]) if abs(f[2]) < 0.95 else np.array([0, 1.0, 0])
    r = np.cross(f, up0); r /= np.linalg.norm(r); u = np.cross(r, f)
    ims.append(render(scene, yup(pos), yup(f), yup(r), yup(u), W=800, H=600, fov=fov))
rows = [np.hstack(ims[i:i + 2]) for i in range(0, len(ims), 2)]
if len(ims) % 2: rows[-1] = np.hstack([rows[-1], np.zeros_like(ims[0])])
Image.fromarray(np.vstack(rows)).save(out, quality=88)
