"""Build leaf_rgba.png: 2x2 atlas of live-oak-like leaf clusters from Poly Haven island_tree_02 leaf sprites (CC0).

Usage: uv run python scripts/block_trees/atlas.py <polyhaven_src_dir> <out_dir>   (out = out/block_compare/cache/trees)
Generated for worldgen.block_combined (tree models). Inputs: Poly Haven CC0 (see out/block_compare/cache/trees/LICENSE.json).
"""

import sys

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

src, out = sys.argv[1], sys.argv[2]
rng = np.random.default_rng(7)
diff = np.asarray(Image.open(f"{src}/island_tree_02_leaves_diff_1k.jpg").convert("RGB")).astype(np.float32)
alpha = np.asarray(Image.open(f"{src}/island_tree_02_leaves_alpha_1k.jpg").convert("L")).astype(np.float32)
lab, n = ndimage.label(alpha > 128)
sprites = []
for i, sl in enumerate(ndimage.find_objects(lab)):
    m = lab[sl] == i + 1
    if m.sum() < 3000:
        continue
    rgb = diff[sl].copy()
    a = alpha[sl] * m
    # recolour: keep the leaf's luminance detail, push hue to a neutral light green
    L = rgb @ np.array([0.299, 0.587, 0.114], np.float32)
    Lm = L[m].mean()
    rel = np.clip(L / Lm, 0.4, 1.6)
    base = np.array([132, 160, 118], np.float32)
    rec = rel[..., None] * base
    rgb = 0.15 * rgb * (base.mean() / rgb[m].mean()) + 0.85 * rec
    sprites.append(Image.fromarray(np.dstack([np.clip(rgb, 0, 255), a]).astype(np.uint8), "RGBA"))
print("sprites", len(sprites), [s.size for s in sprites])

T = 512


def tile(seed):
    r = np.random.default_rng(seed)
    im = Image.new("RGBA", (T, T), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    c = np.array([T / 2, T / 2])
    # twigs: from bottom centre fanning up/out
    tips = []
    for _k in range(r.integers(5, 8)):
        ang = r.uniform(-1.25, 1.25) - np.pi / 2
        L = r.uniform(0.25, 0.42) * T
        p0 = np.array([T / 2 + r.normal(0, 10), T * 0.92])
        p1 = c + np.array([np.cos(ang), np.sin(ang)]) * L * 0.9 + r.normal(0, 12, 2)
        mid = (p0 + p1) / 2 + r.normal(0, 18, 2)
        pts = [tuple(p0), tuple(mid), tuple(p1)]
        d.line(pts, fill=(92, 80, 62, 255), width=int(r.integers(3, 6)))
        tips.append((p0, mid, p1))
    # leaves: sub-clumps along twigs, back (darker) to front (lighter)
    centres = []
    for p0, mid, p1 in tips:
        for t in np.linspace(0.3, 1.0, 5):
            q = (1 - t) * mid + t * p1 if t > 0.5 else (1 - 2 * t) * p0 + 2 * t * mid
            centres.append(q)
    centres = np.array(centres)
    N = 900
    idx = r.integers(0, len(centres), N)
    pos = centres[idx] + r.normal(0, 38, (N, 2))
    # keep inside a ragged disc
    rr = np.linalg.norm(pos - c, axis=1)
    keep = rr < T * 0.47
    pos = pos[keep]
    depth = r.uniform(0, 1, len(pos))
    order = np.argsort(depth)
    for j in order:
        s = sprites[r.integers(len(sprites))]
        length = r.uniform(22, 34)
        sc = length / s.size[1]
        sp = s.resize(
            (max(2, int(s.size[0] * sc * r.uniform(0.85, 1.1))), max(2, int(s.size[1] * sc))), Image.LANCZOS
        )
        sp = sp.rotate(r.uniform(0, 360), expand=True, resample=Image.BICUBIC)
        a = np.asarray(sp).astype(np.float32)
        shade = 0.62 + 0.45 * depth[j] + r.normal(0, 0.05)
        hue = np.array([1 + r.normal(0, 0.05), 1, 1 + r.normal(0, 0.07)])
        a[..., :3] = np.clip(a[..., :3] * shade * hue, 0, 255)
        sp = Image.fromarray(a.astype(np.uint8), "RGBA")
        x, y = pos[j] - np.array(sp.size) / 2
        im.alpha_composite(sp, (int(x), int(y)))
    return im


atlas = Image.new("RGBA", (2 * T, 2 * T), (0, 0, 0, 0))
for k in range(4):
    atlas.alpha_composite(tile(100 + k), ((k % 2) * T, (k // 2) * T))
a = np.asarray(atlas).astype(np.float32)
A = a[..., 3] > 110
rgb = a[..., :3].copy()
# un-premultiply edge darkening is not needed (alpha_composite is straight); dilate colour into transparent texels
fill = rgb.copy()
m = A.copy()
for _ in range(24):
    w = ndimage.uniform_filter(m.astype(np.float32), 3)
    acc = np.stack([ndimage.uniform_filter(fill[..., c] * m, 3) for c in range(3)], -1)
    new = (~m) & (w > 0)
    fill[new] = acc[new] / w[new, None]
    m = m | new
fill[~m] = rgb[A].mean(0)
outa = np.dstack([fill, A * 255.0]).astype(np.uint8)
Image.fromarray(outa, "RGBA").save(out, optimize=True)
print("coverage", A.mean(), "mean RGB (opaque)", rgb[A].mean(0).round(1))
