"""Free views of a base build with some node groups hidden and other glbs laid over it.
uv run python scripts/splat/overlay.py <base.glb> <hide,groups> <extra.glb[,extra2.glb]> <out.jpg> "eye;look;fov" ..."""
import sys
sys.path.insert(0, "scripts/splat")
import numpy as np, trimesh
import sview
base, hide, extra, out, *views = sys.argv[1:]
sc = sview.Scene(base, hide=tuple(h for h in hide.split(",") if h))
for path in [e for e in extra.split(",") if e]:
    for name, g in trimesh.load(path).geometry.items():
        sc.tris.append(np.asarray(g.vertices)[g.faces]); sc.uvs.append(np.asarray(g.visual.uv)[g.faces])
        sc.tid.append(np.full(len(g.faces), len(sc.texs))); sc.texs.append(np.asarray(g.visual.material.baseColorTexture.convert("RGBA")))
sc.shots(views, out)
