"""Feasibility experiment (lane-a, 2026-11-15): use Matterport's drone splat OFFLINE as a texture and shape source.

Not part of the package. Reads out/raw/splat/<model>/splat/splat.ply (Matterport Z-up frame = our plan frame).
An orthographic Gaussian rasteriser (exact for ortho: the 2D covariance is the projected 3D one), front to back,
with alpha and depth outputs, so a slab of the splat can be flattened onto a plane (roof, wall, ground, tree card).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from numba import njit

OUT = Path(__file__).resolve().parents[2] / "out"
C0 = 0.28209479177387814


def read_ply(path: Path) -> dict[str, np.ndarray]:
    with open(path, "rb") as f:
        header = b""
        while not header.endswith(b"end_header\n"):
            line = f.readline()
            if not line or len(header) > 10000:
                raise ValueError("bad PLY header")
            header += line
        offset = f.tell()
    lines = header.decode("ascii").splitlines()
    n = next(int(x.split()[2]) for x in lines if x.startswith("element vertex"))
    props = [x.split()[2] for x in lines if x.startswith("property float")]
    data = np.memmap(path, dtype="<f4", mode="r", offset=offset, shape=(n, len(props)))
    return {p: np.asarray(data[:, i]) for i, p in enumerate(props)}


def load(model: str, centre=(4.2, 4.3), radius=75.0) -> dict[str, np.ndarray]:
    """Gaussians within `radius` m (plan frame, Z-up) of `centre`: xyz, cov (3x3), rgb, alpha, size. Cached as npz."""
    cache = OUT / "splat_bake" / model / f"crop_r{int(radius)}.npz"
    if cache.exists():
        return dict(np.load(cache))
    g = read_ply(OUT / "raw" / "splat" / model / "splat" / "splat.ply")
    keep = np.hypot(g["x"] - centre[0], g["y"] - centre[1]) < radius
    xyz = np.stack([g["x"][keep], g["y"][keep], g["z"][keep]], 1).astype(np.float32)
    s = np.exp(np.stack([g[f"scale_{i}"][keep] for i in range(3)], 1)).astype(np.float64)
    q = np.stack([g[f"rot_{i}"][keep] for i in range(4)], 1).astype(np.float64)  # w x y z
    q /= np.linalg.norm(q, axis=1, keepdims=True)
    w, x, y, z = q.T
    R = np.stack(
        [
            np.stack([1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)], 1),
            np.stack([2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)], 1),
            np.stack([2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)], 1),
        ],
        1,
    )
    M = R * s[:, None, :]
    cov = (M @ M.transpose(0, 2, 1)).astype(np.float32)
    rgb = np.clip(0.5 + C0 * np.stack([g[f"f_dc_{i}"][keep] for i in range(3)], 1), 0, 1).astype(np.float32)
    alpha = (1 / (1 + np.exp(-g["opacity"][keep]))).astype(np.float32)
    out = {"xyz": xyz, "cov": cov, "rgb": rgb, "alpha": alpha, "size": s.max(1).astype(np.float32)}
    cache.parent.mkdir(parents=True, exist_ok=True)
    np.savez(cache, **out)
    return out


@njit(cache=True)
def _raster(u, v, d, a, b, c, rgb, alpha, W, H, min_alpha):
    """Gaussians sorted front to back. (u, v) pixel centres, inverse 2D covariance (a, b, c) in pixels."""
    img = np.zeros((H, W, 3), np.float32)
    T = np.ones((H, W), np.float32)
    dep = np.zeros((H, W), np.float32)
    for i in range(u.shape[0]):
        det = a[i] * c[i] - b[i] * b[i]
        if det <= 0:
            continue
        # 3 sigma box from the covariance (inverse of the conic)
        ru = min(3.0 * np.sqrt(c[i] / det), 400.0)
        rv = min(3.0 * np.sqrt(a[i] / det), 400.0)
        x0 = max(int(np.floor(u[i] - ru)), 0)
        x1 = min(int(np.ceil(u[i] + ru)), W - 1)
        y0 = max(int(np.floor(v[i] - rv)), 0)
        y1 = min(int(np.ceil(v[i] + rv)), H - 1)
        for y in range(y0, y1 + 1):
            dy = y + 0.5 - v[i]
            for x in range(x0, x1 + 1):
                t = T[y, x]
                if t < 0.003:
                    continue
                dx = x + 0.5 - u[i]
                p = 0.5 * (a[i] * dx * dx + c[i] * dy * dy) + b[i] * dx * dy
                if p > 4.5:
                    continue
                al = min(0.99, alpha[i] * np.exp(-p))
                if al < min_alpha:
                    continue
                w = t * al
                img[y, x, 0] += w * rgb[i, 0]
                img[y, x, 1] += w * rgb[i, 1]
                img[y, x, 2] += w * rgb[i, 2]
                dep[y, x] += w * d[i]
                T[y, x] = t * (1 - al)
    return img, T, dep


def ortho(g, origin, ax_u, ax_v, size_m, px_per_m, near=-1e9, far=1e9, mask=None, min_alpha=1 / 255):
    """Orthographic render looking along n = ax_u x ax_v... the camera looks along -n (toward decreasing n).

    origin: world point at pixel (0, 0) corner; ax_u, ax_v: unit world axes of image x and y (y down the image).
    Depth = distance along the view direction `look = ax_u x ax_v` from origin's plane; keeps near <= depth <= far.
    Returns rgb (H, W, 3) premultiplied, alpha (H, W), depth (H, W; weighted mean, nan where alpha is 0).
    """
    ax_u = np.asarray(ax_u, np.float64)
    ax_v = np.asarray(ax_v, np.float64)
    look = np.cross(ax_u, ax_v)
    rel = g["xyz"].astype(np.float64) - np.asarray(origin, np.float64)
    d = rel @ look
    keep = (d >= near) & (d <= far)
    if mask is not None:
        keep &= mask
    W, H = int(round(size_m[0] * px_per_m)), int(round(size_m[1] * px_per_m))
    u = rel @ ax_u * px_per_m
    v = rel @ ax_v * px_per_m
    pad = g["size"] * 3 * px_per_m
    keep &= (u > -pad) & (u < W + pad) & (v > -pad) & (v < H + pad)
    idx = np.flatnonzero(keep)
    idx = idx[np.argsort(d[idx], kind="stable")]
    P = np.stack([ax_u, ax_v]) * px_per_m  # 2x3
    cov2 = np.einsum("ij,njk,lk->nil", P, g["cov"][idx].astype(np.float64), P)
    cov2[:, 0, 0] += 0.3  # the usual low-pass, in pixels^2
    cov2[:, 1, 1] += 0.3
    det = cov2[:, 0, 0] * cov2[:, 1, 1] - cov2[:, 0, 1] ** 2
    a = (cov2[:, 1, 1] / det).astype(np.float32)
    b = (-cov2[:, 0, 1] / det).astype(np.float32)
    c = (cov2[:, 0, 0] / det).astype(np.float32)
    img, T, dep = _raster(
        u[idx].astype(np.float32), v[idx].astype(np.float32), d[idx].astype(np.float32), a, b, c,
        g["rgb"][idx], g["alpha"][idx], W, H, min_alpha,
    )
    al = 1 - T
    with np.errstate(invalid="ignore", divide="ignore"):
        dep = np.where(al > 0.05, dep / al, np.nan)
    return img, al, dep


def to_image(img, al, bg=(1.0, 0.0, 1.0)):
    from PIL import Image

    out = img + (1 - al)[..., None] * np.asarray(bg, np.float32)
    return Image.fromarray((np.clip(out, 0, 1) * 255).round().astype(np.uint8))
