"""Compose the v43 build with experiment meshes and render eye-level views (uses scripts/preview/render_compare)."""
import sys
from pathlib import Path
import numpy as np, trimesh
from PIL import Image
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "preview"))
from render_compare import render, yup  # noqa: E402
from worldgen.panos import Raycaster  # noqa: E402


class Scene:
    def __init__(self, glb, hide=()):
        self.tris, self.uvs, self.tid, self.texs = [], [], [], []
        for name, g in trimesh.load(glb).geometry.items():
            if any(name == h or name.split("_")[0] == h for h in hide):
                continue
            arr = np.asarray(g.visual.material.baseColorTexture.convert("RGBA"))
            self.tris.append(np.asarray(g.vertices)[g.faces]); self.uvs.append(np.asarray(g.visual.uv)[g.faces])
            self.tid.append(np.full(len(g.faces), len(self.texs))); self.texs.append(arr)

    def add(self, verts_zup, faces, uv, rgba):
        """uv: per vertex, origin bottom-left (v up), like trimesh. rgba: (H, W, 4) uint8, row 0 = top (v = 1)."""
        v = np.stack([verts_zup[:, 0], verts_zup[:, 2], -verts_zup[:, 1]], 1)
        self.tris.append(v[faces]); self.uvs.append(uv[faces])
        self.tid.append(np.full(len(faces), len(self.texs))); self.texs.append(rgba)

    def shots(self, views, out, W=800, H=600, cols=2):
        tris = np.concatenate(self.tris)
        scene = (tris, np.concatenate(self.uvs), np.concatenate(self.tid), self.texs, Raycaster(tris))
        ims = []
        for v in views:
            pos, look, *rest = v.split(";")
            pos = np.array([float(x) for x in pos.split(",")]); look = np.array([float(x) for x in look.split(",")])
            fov = float(rest[0]) if rest else 80
            f = look - pos; f /= np.linalg.norm(f)
            up0 = np.array([0, 0, 1.0]) if abs(f[2]) < 0.95 else np.array([0, 1.0, 0])
            r = np.cross(f, up0); r /= np.linalg.norm(r); u = np.cross(r, f)
            ims.append(render(scene, yup(pos), yup(f), yup(r), yup(u), W=W, H=H, fov=fov, layers=40))
        rows = [np.hstack(ims[i:i + cols]) for i in range(0, len(ims), cols)]
        if len(ims) % cols:
            rows[-1] = np.hstack([rows[-1]] + [np.zeros_like(ims[0])] * (cols - len(ims) % cols))
        Image.fromarray(np.vstack(rows)).save(out, quality=88)
        return ims


def grid_mesh(x0, y1, cell, Z, valid):
    """Heightfield mesh. Z (H, W) heights at cell corners..., here at cell centres; uv planar over the raster."""
    H, W = Z.shape
    xs = x0 + (np.arange(W) + 0.5) * cell; ys = y1 - (np.arange(H) + 0.5) * cell
    X, Y = np.meshgrid(xs, ys)
    verts = np.stack([X.ravel(), Y.ravel(), Z.ravel()], 1)
    U, V = np.meshgrid((np.arange(W) + 0.5) / W, 1 - (np.arange(H) + 0.5) / H)
    uvs = np.stack([U.ravel(), V.ravel()], 1)
    i = np.arange(H * W).reshape(H, W)
    a, b, c, d = i[:-1, :-1], i[:-1, 1:], i[1:, :-1], i[1:, 1:]
    ok = valid[:-1, :-1] & valid[:-1, 1:] & valid[1:, :-1] & valid[1:, 1:]
    faces = np.concatenate([np.stack([a[ok], c[ok], b[ok]], 1), np.stack([b[ok], c[ok], d[ok]], 1)])
    return verts, faces, uvs
