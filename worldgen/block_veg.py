"""Trees for the procedural block: crowns segmented from the DSM-minus-DTM canopy, each built from
leaf cards that fill the real crown shape and take their colour from the ortho, plus a trunk and a
few limbs. The leaf and facade textures are drawn here (procedural, no external assets).
"""

from __future__ import annotations

import math

import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from scipy import ndimage

from worldgen.block_glb import MeshBuf


def leaf_texture(size: int = 1024, seed: int = 3) -> Image.Image:
    """2 x 2 atlas of irregular leaf clumps, near-white with alpha; the vertex colour (from the
    ortho) tints it. Each clump is a few overlapping sub-clusters so card silhouettes are ragged."""
    rng = np.random.default_rng(seed)
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    q = size // 2
    for cell in range(4):
        ox, oy = (cell % 2) * q, (cell // 2) * q
        c = q / 2
        for _ in range(7):
            a = rng.uniform(0, 2 * math.pi)
            r = rng.uniform(0.2, 0.42) * q
            d.line(
                [(ox + c, oy + c), (ox + c + r * math.cos(a), oy + c + r * math.sin(a))],
                fill=(85, 75, 65, 255),
                width=3,
            )
        subs = [
            (c + rng.uniform(-0.24, 0.24) * q, c + rng.uniform(-0.24, 0.24) * q, rng.uniform(0.1, 0.2) * q)
            for _ in range(rng.integers(9, 14))
        ]
        for _ in range(7000):
            sx, sy, sr = subs[rng.integers(len(subs))]
            rr = sr * math.sqrt(rng.uniform(0, 1))
            a = rng.uniform(0, 2 * math.pi)
            x, y = ox + sx + rr * math.cos(a), oy + sy + rr * math.sin(a)
            L = rng.uniform(5, 9) * size / 1024
            W = L * rng.uniform(0.38, 0.55)
            ang = rng.uniform(0, math.pi)
            g = rng.uniform(0.55, 1.0)
            col = (int(205 * g), int(235 * g), int(190 * g), 255)
            pts = []
            for k in range(8):
                t = 2 * math.pi * k / 8
                px, py = L * math.cos(t), W * math.sin(t)
                pts.append(
                    (x + px * math.cos(ang) - py * math.sin(ang), y + px * math.sin(ang) + py * math.cos(ang))
                )
            d.polygon(pts, fill=col)
    return img.filter(ImageFilter.SMOOTH)


def segment_crowns(nd: np.ndarray, res: float, building_mask: np.ndarray, min_h: float = 2.5):
    """Return label image and list of (label, peak_row, peak_col, height)."""
    sm = ndimage.gaussian_filter(nd, 1.2)
    mask = (sm > min_h) & ~building_mask
    mask = ndimage.binary_opening(mask, iterations=2)
    win = max(3, int(round(3.5 / res)))
    peaks = (sm == ndimage.maximum_filter(sm, size=win)) & mask & (sm > 3.0)
    markers, n = ndimage.label(peaks)
    inv = np.clip(40 - sm, 0, 40)
    img = (inv / 40 * 254).astype(np.uint8)
    mk = markers.astype(np.int32)
    mk[~mask] = -1  # background marker... watershed_ift needs non-negative for labels
    mk2 = np.where(mask, markers, n + 1).astype(np.int32)
    lab = ndimage.watershed_ift(img, mk2)
    lab[~mask] = 0
    lab[lab == n + 1] = 0
    return lab, sm


def build_trees(
    nd_sm: np.ndarray,
    lab: np.ndarray,
    X: np.ndarray,
    Z: np.ndarray,
    ground: np.ndarray,
    ortho,
    res: float,
    rng: np.random.Generator,
) -> tuple[MeshBuf, MeshBuf, list[dict]]:
    leaves, bark = MeshBuf(), MeshBuf()
    info = []
    objs = ndimage.find_objects(lab)
    for li, sl in enumerate(objs, start=1):
        if sl is None:
            continue
        m = lab[sl] == li
        area = m.sum() * res * res
        if area < 2.0:
            continue
        if area < 7.0 and nd_sm[sl][m].max() > 6.0:
            continue  # tall and thin: a utility pole or light mast, not a tree
        hh = nd_sm[sl][m]
        H = float(hh.max())
        xs, zs = X[sl][m], Z[sl][m]
        gy = ground[sl][m]
        top = hh + gy
        g0 = float(np.median(gy))
        cx, cz = float(xs.mean()), float(zs.mean())
        R = math.sqrt(area / math.pi)
        base = max(1.6, min(H * 0.45, H - 1.5))
        info.append({"x": cx, "z": cz, "h": H, "r": R})
        # leaf cards
        k = int(np.clip(area * 4.0, 20, 1800))
        pick = rng.choice(len(xs), size=k, replace=len(xs) < k)
        cols = ortho.sample(xs[pick], zs[pick]) / 255.0
        for j, p in enumerate(pick):
            px, pz = xs[p] + rng.uniform(-0.3, 0.3), zs[p] + rng.uniform(-0.3, 0.3)
            # radial position: edge cards hang lower, centre cards stay near the top
            rr = math.hypot(px - cx, pz - cz) / max(R, 0.5)
            t = top[p]
            # fill the crown volume, denser near the top; edge cards reach lower (rounded crown)
            lo_y = g0 + base + (t - g0 - base) * 0.45 * (1.0 - min(rr, 1.0))
            y = lo_y + (t - 0.3 - lo_y) * math.sqrt(rng.uniform(0, 1))
            y = max(y, g0 + base)
            s = rng.uniform(0.8, 1.25) * (1.0 if H > 5 else 0.8)
            nrm = rng.normal(size=3)
            nrm[1] = abs(nrm[1]) * 1.2 + 0.3
            nrm /= np.linalg.norm(nrm)
            a = np.cross(nrm, [0.3, 0.1, 1.0])
            a /= np.linalg.norm(a)
            bvec = np.cross(nrm, a)
            ctr = np.array([px, y, pz])
            P = np.array(
                [
                    ctr - a * s / 2 - bvec * s / 2,
                    ctr + a * s / 2 - bvec * s / 2,
                    ctr + a * s / 2 + bvec * s / 2,
                    ctr - a * s / 2 + bvec * s / 2,
                ]
            )
            shade = 0.72 + 0.45 * (y - (g0 + base)) / max(t - (g0 + base), 0.5)
            oc = cols[j]
            lum = float(oc @ [0.3, 0.5, 0.2])
            oc = oc * (0.30 / max(lum, 0.04)) ** 0.65  # lift the photo's shadowed canopy
            oc = 0.7 * oc + 0.3 * np.array([0.24, 0.31, 0.15])
            col = np.clip(oc * min(shade, 1.05) * 0.95, 0, 1)
            cu, cv = (rng.integers(2) * 0.5, rng.integers(2) * 0.5)
            leaves.add(
                P,
                [[0, 1, 2], [0, 2, 3]],
                uv=np.array([[0, 0.5], [0.5, 0.5], [0.5, 0], [0, 0]]) + [cu, cv],
                normals=np.array([0, 1, 0]),
                color=np.r_[col, 1.0],
            )
        # trunk and limbs
        r_t = float(np.clip(0.06 + 0.022 * H + 0.012 * R, 0.08, 0.45))
        tip = np.array([cx, g0 + base + 0.6, cz])
        _cyl(bark, np.array([cx, g0 - 0.1, cz]), tip, r_t, r_t * 0.7)
        nl = 3 if R > 2.5 else 2
        ang = rng.uniform(0, 2 * math.pi)
        for q in range(nl):
            ang = ang if q == 0 else ang + 2 * math.pi / nl + rng.uniform(-0.4, 0.4)
            end = np.array(
                [cx + math.cos(ang) * R * 0.4, g0 + base + (H - base) * 0.4, cz + math.sin(ang) * R * 0.4]
            )
            _cyl(bark, tip - np.array([0, 0.5, 0]), end, r_t * 0.62, r_t * 0.25)
    return leaves, bark, info


def _cyl(buf: MeshBuf, a: np.ndarray, b: np.ndarray, r0: float, r1: float, n: int = 7) -> None:
    ax = b - a
    L = np.linalg.norm(ax)
    if L < 1e-3:
        return
    ax = ax / L
    u = np.cross(ax, [0, 0, 1] if abs(ax[2]) < 0.9 else [1, 0, 0])
    u /= np.linalg.norm(u)
    v = np.cross(ax, u)
    ang = np.linspace(0, 2 * math.pi, n + 1)
    ring = np.cos(ang)[:, None] * u + np.sin(ang)[:, None] * v
    P = np.concatenate([a + ring * r0, b + ring * r1])
    N = np.concatenate([ring, ring])
    uv = np.c_[
        np.r_[ang, ang] / (2 * math.pi) * max(1, round(r0 * 6)),
        np.r_[np.zeros(n + 1), np.full(n + 1, L / 1.0)],
    ]
    tris = []
    for i in range(n):
        tris += [[i, i + 1, n + 1 + i + 1], [i, n + 1 + i + 1, n + 1 + i]]
    tris = np.array(tris)
    # orient outward
    c = (P[tris[:, 0]] + P[tris[:, 1]] + P[tris[:, 2]]) / 3
    mid = (a + b) / 2
    out = c - mid - ((c - mid) @ ax)[:, None] * ax
    buf.add(P, tris, uv=uv, normals=N, facing=out)


def build_shrubs(
    nd_sm: np.ndarray,
    X: np.ndarray,
    Z: np.ndarray,
    ground: np.ndarray,
    ortho,
    res: float,
    exclude: np.ndarray,
    green: np.ndarray,
    rng: np.random.Generator,
    buf: MeshBuf,
) -> int:
    """Foundation shrubs and hedges: green blobs 0.6-2.6 m tall in the nDSM, as small leaf cards."""
    mask = (nd_sm > 0.6) & (nd_sm < 2.6) & ~exclude & green
    mask = ndimage.binary_opening(mask, iterations=1)
    lab, n = ndimage.label(mask)
    count = 0
    for li, sl in enumerate(ndimage.find_objects(lab), start=1):
        if sl is None:
            continue
        m = lab[sl] == li
        area = m.sum() * res * res
        if area < 1.5 or area > 120:
            continue
        xs, zs = X[sl][m], Z[sl][m]
        hh = nd_sm[sl][m]
        gy = ground[sl][m]
        k = int(np.clip(area * 14, 16, 900))
        pick = rng.choice(len(xs), size=k, replace=len(xs) < k)
        cols = ortho.sample(xs[pick], zs[pick]) / 255.0
        for j, p in enumerate(pick):
            top = gy[p] + hh[p]
            y = gy[p] + hh[p] * rng.uniform(0.25, 0.9)
            s = rng.uniform(0.45, 0.8)
            nrm = rng.normal(size=3)
            nrm[1] = abs(nrm[1]) + 0.2
            nrm /= np.linalg.norm(nrm)
            a = np.cross(nrm, [0.3, 0.1, 1.0])
            a /= np.linalg.norm(a)
            bvec = np.cross(nrm, a)
            ctr = np.array(
                [xs[p] + rng.uniform(-0.15, 0.15), min(y, top - 0.2), zs[p] + rng.uniform(-0.15, 0.15)]
            )
            P = np.array(
                [
                    ctr - a * s / 2 - bvec * s / 2,
                    ctr + a * s / 2 - bvec * s / 2,
                    ctr + a * s / 2 + bvec * s / 2,
                    ctr - a * s / 2 + bvec * s / 2,
                ]
            )
            oc = cols[j]
            lum = float(oc @ [0.3, 0.5, 0.2])
            oc = oc * (0.27 / max(lum, 0.04)) ** 0.6
            oc = 0.7 * oc + 0.3 * np.array([0.22, 0.30, 0.13])
            shade = 0.75 + 0.3 * (y - gy[p]) / max(hh[p], 0.3)
            cu, cv = (rng.integers(2) * 0.5, rng.integers(2) * 0.5)
            buf.add(
                P,
                [[0, 1, 2], [0, 2, 3]],
                uv=np.array([[0, 0.5], [0.5, 0.5], [0.5, 0], [0, 0]]) + [cu, cv],
                normals=np.array([0, 1, 0]),
                color=np.r_[np.clip(oc * shade, 0, 1), 1.0],
            )
        count += 1
    return count
