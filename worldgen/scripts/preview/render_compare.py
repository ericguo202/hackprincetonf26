"""Ray-traced, unlit preview of an interior.glb, compared with the real Matterport panoramas.

Run from worldgen/ (uses worldgen.panos.Raycaster, i.e. embree):

  Side-by-side images (left = our glb, right = the real pano face) for some pano labels:
    uv run python scripts/preview/render_compare.py <glb> out/cache/matterport/<id>.json out/raw/<id>/skybox_2k out.jpg 11,13 [1,2,3,4]

  Score (mean abs RGB error, 0-255, over every indoor pano's 4 side faces; lower is better):
    uv run python scripts/preview/render_compare.py --score <glb> out/cache/matterport/<id>.json out/raw/<id>/skybox_2k

Purple (40, 0, 60) = the ray hit nothing (a hole). NO_CEILING=1 skips ceiling nodes.
CAVEAT: the bake used these same panoramas, so the score at pano positions is optimistic. It still catches
regressions in geometry, carving, alignment and holes. For an unbiased number, bake with some panos held out
(see docs/house-pipeline-handoff.md, "Measuring quality").
Uses the cached raw glb (out/cache/glb/<id>_interior_pano.glb) because trimesh cannot read meshopt files."""
import sys, json, numpy as np, trimesh
from PIL import Image
from worldgen.panos import Raycaster, quat_matrix

def load(glb):
    sc = trimesh.load(glb)
    tris, uvs, tid, texs = [], [], [], []
    import os
    for name, g in sc.geometry.items():
        if os.environ.get('NO_CEILING') and name.startswith('ceiling'): continue
        img = g.visual.material.baseColorTexture
        arr = np.asarray(img.convert("RGBA"))
        tris.append(g.vertices[g.faces]); uvs.append(g.visual.uv[g.faces]); tid.append(np.full(len(g.faces), len(texs))); texs.append(arr)
    return np.concatenate(tris), np.concatenate(uvs), np.concatenate(tid), texs

def render(scene, pos, fwd, right, up, W=640, H=640, fov=90):
    tris, uvs, tid, texs, rc = scene
    f = 1 / np.tan(np.radians(fov) / 2)
    xs = (np.arange(W) + 0.5) / W * 2 - 1; ys = (np.arange(H) + 0.5) / H * 2 - 1
    X, Y = np.meshgrid(xs, ys)
    d = fwd[None] * f + X.ravel()[:, None] * right[None] - Y.ravel()[:, None] * up[None]
    d /= np.linalg.norm(d, axis=1)[:, None]
    o = np.tile(pos, (len(d), 1)).astype(float)
    out = np.full((len(d), 3), (40, 0, 60), np.uint8)   # purple = nothing hit
    active = np.arange(len(d))
    for _ in range(6):
        if not len(active): break
        t, prim = rc.cast(o[active], d[active])
        hit = prim >= 0
        a = active[hit]; pr = prim[hit]; p = o[a] + d[a] * t[hit, None]
        T = tris[pr]; v0 = T[:, 1] - T[:, 0]; v1 = T[:, 2] - T[:, 0]; v2 = p - T[:, 0]
        d00 = (v0 * v0).sum(1); d01 = (v0 * v1).sum(1); d11 = (v1 * v1).sum(1); d20 = (v2 * v0).sum(1); d21 = (v2 * v1).sum(1)
        den = d00 * d11 - d01 * d01 + 1e-15; b1 = (d11 * d20 - d01 * d21) / den; b2 = (d00 * d21 - d01 * d20) / den; b0 = 1 - b1 - b2
        uv = b0[:, None] * uvs[pr, 0] + b1[:, None] * uvs[pr, 1] + b2[:, None] * uvs[pr, 2]
        col = np.zeros((len(a), 4), np.uint8)
        for k in np.unique(tid[pr]):
            s = tid[pr] == k; tex = texs[k]; h, w = tex.shape[:2]
            col[s] = tex[np.clip(((1 - uv[s, 1]) * h).astype(int), 0, h - 1), np.clip((uv[s, 0] * w).astype(int), 0, w - 1)]
        opaque = col[:, 3] >= 128
        out[a[opaque]] = col[opaque, :3]
        o[a[~opaque]] = p[~opaque] + d[a[~opaque]] * 1e-3
        active = a[~opaque]
    return out.reshape(H, W, 3)

def yup(v): return np.array([v[0], v[2], -v[1]])

def score(glb, model_json, sky, size=192):
    from shapely.geometry import Point
    from shapely.ops import unary_union
    tris, uvs, tid, texs = load(glb)
    scene = (tris, uvs, tid, texs, Raycaster(tris))
    m = json.load(open(model_json))
    # indoor = inside the union of room polygons (rooms.json next to interior.glb in the pack is house-local;
    # here we use the footprint of the glb itself: a pano is indoor if a ray straight up hits geometry)
    errs, holes, used = [], [], 0
    for p in m["panoLocations"]:
        R = quat_matrix(p["rotation"]); pos = np.array([p["position"][k] for k in "xyz"])
        t, prim = scene[4].cast(yup(pos)[None], np.array([[0.0, 1.0, 0.0]]))
        if prim[0] < 0 or t[0] > 6:
            continue  # outdoors: nothing overhead
        used += 1
        f, r, u = R @ [1, 0, 0], R @ [0, -1, 0], R @ [0, 0, 1]
        for L, Rt, U, fi in ((f, r, u, 1), (r, -f, u, 2), (-f, -r, u, 3), (-r, f, u, 4)):
            img = render(scene, yup(pos), yup(L), yup(Rt), yup(U), W=size, H=size).astype(float)
            real = np.asarray(Image.open(f"{sky}/{p['id']}_{fi}.jpg").convert("RGB").resize((size, size))).astype(float)
            hole = (img == [40, 0, 60]).all(-1)
            holes.append(hole.mean())
            errs.append(np.abs(img[~hole] - real[~hole]).mean() if (~hole).any() else 255.0)
    print(json.dumps({"indoor_panos": used, "mean_abs_err": round(float(np.mean(errs)), 2),
                      "hole_fraction": round(float(np.mean(holes)), 4)}))


if __name__ == "__main__":
    if sys.argv[1] == "--score":
        score(*sys.argv[2:5])
        sys.exit(0)
    glb, model_json, sky, out = sys.argv[1:5]
    labels = sys.argv[5].split(",")
    faces_wanted = [int(x) for x in sys.argv[6].split(",")] if len(sys.argv) > 6 else [1, 2, 3, 4]
    tris, uvs, tid, texs = load(glb)
    scene = (tris, uvs, tid, texs, Raycaster(tris))
    m = json.load(open(model_json))
    rows = []
    for lab in labels:
        p = next(q for q in m["panoLocations"] if q["label"] == lab)
        R = quat_matrix(p["rotation"]); pos = np.array([p["position"][k] for k in "xyz"])
        f, r, u = R @ [1, 0, 0], R @ [0, -1, 0], R @ [0, 0, 1]
        dirs = {1: (f, r, u), 2: (r, -f, u), 3: (-f, -r, u), 4: (-r, f, u), 0: (u, r, -f), 5: (-u, r, f)}
        for fi in faces_wanted:
            L, Rt, U = dirs[fi]
            img = render(scene, yup(pos), yup(L), yup(Rt), yup(U))
            real = np.asarray(Image.open(f"{sky}/{p['id']}_{fi}.jpg").convert("RGB").resize((640, 640)))
            rows.append(np.hstack([img, real]))
    grid = [np.hstack(rows[i:i + 2]) for i in range(0, len(rows), 2)]
    if len(rows) % 2: grid[-1] = np.hstack([grid[-1], np.zeros_like(rows[0])])
    Image.fromarray(np.vstack(grid)).save(out, quality=88)
