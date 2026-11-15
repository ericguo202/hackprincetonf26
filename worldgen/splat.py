"""Matterport's drone splat as an offline source for what the ground scan lacks.

About 230 models in the hackathon portal come with a 3D Gaussian splat built from a drone flight
(`out/raw/splat/<model>/splat/splat.ply`, docs/house-pipeline-handoff.md 3.11 and 3.12). It is in the model's
own frame (Z up, the plan frame of this pipeline; its ground sits within 5 cm of the scanned yard), it sees
the house from above and from every side, and it is fine near the house (Gaussians of about 3 cm within 15 m).
It is softer than the ground panoramas, so it never replaces a photographed surface: it fills what no ground
camera saw. Nothing of it ships: only meshes and textures made from it.

Three uses, all through one operation, `Splat.flatten`: an orthographic render of the Gaussians inside a slab
(exact for an orthographic camera: the 2D covariance is the projected 3D one), front to back, with alpha.
- the roof's shape: `roof_sheet` (per cell, the height where the Gaussians are densest: a roof is a sheet, a
  tree's canopy is spread out) feeds `roof.fit_roof` instead of Vexcel's surface model, and `roof_outline`;
- the roof's shingles: `roof_colour`, the slab around the fitted planes seen from straight above (the tree
  over the roof is outside the slab);
- walls no camera saw, and thin built things (a railing): `plane_image`, the slab around a vertical plane.

`load` returns None when the model has no splat on disk; every caller then falls back to what it did before.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import shapely
from numba import njit
from scipy import ndimage
from shapely.geometry import Polygon
from shapely.ops import unary_union

RADIUS = 60.0  # Gaussians further than this from the house are not read, m
C0 = 0.28209479177387814  # spherical-harmonics band 0 (the file has no higher bands)
SHEET_CELL = 0.1  # roof_sheet: grid, m
SHEET_BIN = 0.05  # ... height bins, m
SHEET_MIN = 0.05  # ... least summed opacity (3x3x3 mean) for a cell to have a sheet
SHEET_ALPHA = 0.2  # ... Gaussians more transparent than this are not counted
ROOF_BAND = 0.2  # roof_colour: Gaussians within this of the fitted roof, m
EDGE_TOL = 0.15  # roof_outline: a cell belongs to the roof when its sheet is within this of the planes, m
EDGE_SMOOTH = 0.35  # ... notches and tongues of the outline narrower than twice this are closed or cut, m
EDGE_STRAIGHT = 0.22  # ... and its cell staircase is straightened by this much (Douglas-Peucker), m
EDGE_REACH = 1.5  # ... and no further than this outside the walls (and Vexcel's outline), m
WALL_BAND = 0.25  # plane_image: Gaussians within this of the plane, m
WALL_MIN = 0.7  # WallFill: least share of a wall's unseen texels the splat must cover to be used on it
SEEN = 0.6  # alpha above which a flattened texel counts as covered
ROOF_SEEN = 0.3  # ... on the roof: the slab holds nothing but roof, so a thin cover is still its colour

Height = Callable[[np.ndarray, np.ndarray], np.ndarray]


@njit(cache=True)
def _raster(u, v, d, a, b, c, rgb, alpha, W, H):  # pragma: no cover (compiled)
    """Gaussians sorted front to back; (u, v) centres in pixels; (a, b, c) the inverse 2D covariance."""
    img = np.zeros((H, W, 3), np.float32)
    T = np.ones((H, W), np.float32)
    dep = np.zeros((H, W), np.float32)
    for i in range(u.shape[0]):
        det = a[i] * c[i] - b[i] * b[i]
        if det <= 0:
            continue
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
                if al < 1.0 / 255.0:
                    continue
                w = t * al
                img[y, x, 0] += w * rgb[i, 0]
                img[y, x, 1] += w * rgb[i, 1]
                img[y, x, 2] += w * rgb[i, 2]
                dep[y, x] += w * d[i]
                T[y, x] = t * (1 - al)
    return img, T, dep


@dataclass
class Splat:
    xyz: np.ndarray  # (n, 3) plan frame, Z up
    cov: np.ndarray  # (n, 3, 3)
    rgb: np.ndarray  # (n, 3) 0..1
    alpha: np.ndarray  # (n,)
    size: np.ndarray  # (n,) largest standard deviation, m
    source: str = ""

    def flatten(
        self,
        origin: np.ndarray,
        u: np.ndarray,
        v: np.ndarray,
        w: float,
        h: float,
        res: float,
        look: np.ndarray,
        near: float,
        far: float,
        keep: np.ndarray | None = None,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Orthographic render. Pixel (i, j) is at origin + (i + 0.5) / res * u + (j + 0.5) / res * v (u, v
        unit, perpendicular: v is down the image); the camera looks along `look` (unit, perpendicular to
        both) and sees Gaussians whose distance along it from `origin` is within [near, far], and `keep`.
        Returns colour (H, W, 3) 0..255 (not premultiplied; garbage where alpha is 0), alpha (H, W) 0..1 and
        the alpha-weighted distance along `look` (NaN where alpha is under 0.05)."""
        u, v, look = (np.asarray(k, np.float64) for k in (u, v, look))
        rel = self.xyz.astype(np.float64) - np.asarray(origin, np.float64)
        d = rel @ look
        W, H = max(1, round(w * res)), max(1, round(h * res))
        pu, pv = rel @ u * res, rel @ v * res
        pad = self.size * 3 * res
        sel = (d >= near) & (d <= far) & (pu > -pad) & (pu < W + pad) & (pv > -pad) & (pv < H + pad)
        if keep is not None:
            sel &= keep
        idx = np.flatnonzero(sel)
        idx = idx[np.argsort(d[idx], kind="stable")]
        P = np.stack([u, v]) * res
        cov2 = np.einsum("ij,njk,lk->nil", P, self.cov[idx].astype(np.float64), P)
        cov2[:, 0, 0] += 0.3  # low-pass of about half a pixel, as splat renderers do
        cov2[:, 1, 1] += 0.3
        det = cov2[:, 0, 0] * cov2[:, 1, 1] - cov2[:, 0, 1] ** 2
        img, T, dep = _raster(
            pu[idx].astype(np.float32),
            pv[idx].astype(np.float32),
            d[idx].astype(np.float32),
            (cov2[:, 1, 1] / det).astype(np.float32),
            (-cov2[:, 0, 1] / det).astype(np.float32),
            (cov2[:, 0, 0] / det).astype(np.float32),
            self.rgb[idx],
            self.alpha[idx],
            W,
            H,
        )
        al = 1 - T
        with np.errstate(invalid="ignore", divide="ignore"):
            dep = np.where(al > 0.05, dep / al, np.nan)
        return np.clip(img / np.maximum(al, 1e-3)[..., None], 0, 1) * 255, al, dep

    # ------------------------------------------------------------------ the roof

    def roof_sheet(self, bounds: tuple[float, float, float, float], z0: float, z1: float) -> Height:
        """(x, y) -> the height between z0 and z1 where the Gaussians over that point are densest, NaN where
        there are too few. A roof is a thin sheet of Gaussians; leaves over it are spread over metres, so
        the densest SHEET_BIN is the roof even under a tree."""
        x0, y0, x1, y1 = bounds
        nx, ny = int(np.ceil((x1 - x0) / SHEET_CELL)), int(np.ceil((y1 - y0) / SHEET_CELL))
        nz = int(np.ceil((z1 - z0) / SHEET_BIN))
        i = np.floor((self.xyz[:, 0] - x0) / SHEET_CELL).astype(int)
        j = np.floor((self.xyz[:, 1] - y0) / SHEET_CELL).astype(int)
        k = np.floor((self.xyz[:, 2] - z0) / SHEET_BIN).astype(int)
        ok = (i >= 0) & (i < nx) & (j >= 0) & (j < ny) & (k >= 0) & (k < nz) & (self.alpha > SHEET_ALPHA)
        hist = np.zeros((nx, ny, nz), np.float32)
        np.add.at(hist, (i[ok], j[ok], k[ok]), self.alpha[ok])
        hist = ndimage.uniform_filter(hist, size=3)
        z = np.where(hist.max(2) > SHEET_MIN, z0 + (hist.argmax(2) + 0.5) * SHEET_BIN, np.nan)

        def sheet(x: np.ndarray, y: np.ndarray) -> np.ndarray:
            a = np.floor((np.asarray(x, float) - x0) / SHEET_CELL).astype(int)
            b = np.floor((np.asarray(y, float) - y0) / SHEET_CELL).astype(int)
            inside = (a >= 0) & (a < nx) & (b >= 0) & (b < ny)
            out = np.full(np.shape(x), np.nan)
            out[inside] = z[a[inside], b[inside]]
            return out

        return sheet

    def roof_outline(self, sheet: Height, height: Height, walls: Polygon, limit: Polygon) -> Polygon:
        """The roof's edge as the splat has it: `walls` plus every cell within `limit` whose sheet lies
        within EDGE_TOL of the fitted roof `height` there, opened by 0.2 m (specks: a branch at roof height)
        and joined to the walls. Eaves, porch roofs and an attached garage the floor plan lacks come out to
        about a cell (0.1 m); Vexcel's outline is a handful of vertices placed to about 0.4 m."""
        x0, y0, x1, y1 = limit.bounds
        nx, ny = int(np.ceil((x1 - x0) / SHEET_CELL)), int(np.ceil((y1 - y0) / SHEET_CELL))
        X, Y = np.meshgrid(
            x0 + (np.arange(nx) + 0.5) * SHEET_CELL, y0 + (np.arange(ny) + 0.5) * SHEET_CELL, indexing="ij"
        )
        on = np.abs(sheet(X.ravel(), Y.ravel()) - height(X.ravel(), Y.ravel())).reshape(X.shape) < EDGE_TOL
        on &= shapely.contains_xy(limit, X, Y)
        inside = shapely.contains_xy(walls, X, Y)
        on = ndimage.binary_opening(on | inside, iterations=2) | inside
        on = ndimage.binary_closing(on, iterations=2)
        lab, _ = ndimage.label(on)
        keep = np.isin(lab, np.unique(lab[inside & (lab > 0)]))
        ii, jj = np.nonzero(ndimage.binary_fill_holes(keep))
        half = SHEET_CELL / 2
        cells = shapely.box(X[ii, jj] - half, Y[ii, jj] - half, X[ii, jj] + half, Y[ii, jj] + half)
        # A roof's edge has no notch or tongue narrower than about 0.7 m: close, then open (mitred).
        r = (
            unary_union(cells)
            .buffer(EDGE_SMOOTH, join_style="mitre")
            .buffer(-2 * EDGE_SMOOTH, join_style="mitre")
        )
        r = r.buffer(EDGE_SMOOTH, join_style="mitre").union(walls)
        r = max(getattr(r, "geoms", [r]), key=lambda q: q.area)
        return Polygon(r.exterior).simplify(EDGE_STRAIGHT)

    def roof_colour(self, height: Height, region: Polygon, res: float) -> Callable:
        """xyz (n, 3) -> (colour (n, 3) 0..255, covered (n,)): the Gaussians within ROOF_BAND of the fitted
        roof, seen from straight above at `res` texels per metre."""
        x0, y0, x1, y1 = region.buffer(0.3).bounds
        gz = height(self.xyz[:, 0], self.xyz[:, 1])
        near = np.abs(self.xyz[:, 2] - gz) < ROOF_BAND
        top = float(np.nanmax(gz[near])) + 1.0 if near.any() else 10.0
        rgb, al, _ = self.flatten(
            (x0, y1, top), (1, 0, 0), (0, -1, 0), x1 - x0, y1 - y0, res, (0, 0, -1), 0.0, 1e3, keep=near
        )
        H, W = al.shape

        def colour(xyz: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
            i = np.clip(((xyz[:, 0] - x0) * res).astype(int), 0, W - 1)
            j = np.clip(((y1 - xyz[:, 1]) * res).astype(int), 0, H - 1)
            return rgb[j, i], al[j, i] > ROOF_SEEN

        colour.covered = float((al > ROOF_SEEN).mean())
        return colour

    # ------------------------------------------------------------------ vertical planes

    def plane_image(
        self,
        origin: np.ndarray,
        u: np.ndarray,
        v: np.ndarray,
        n: np.ndarray,
        w: float,
        h: float,
        res: float,
        band: float = WALL_BAND,
    ) -> tuple[np.ndarray, np.ndarray]:
        """The Gaussians within `band` of the plane (origin, u, v; n = the side it is looked at from), as an
        image on the plane's texel grid as rebake.Plane has it: texel (i, j) at s = (i + 0.5) / res along u,
        t = h - (j + 0.5) / res along v. Returns colour (H, W, 3) 0..255 and alpha (H, W) 0..1."""
        o = np.asarray(origin, float) + np.asarray(v, float) * h
        rgb, al, _ = self.flatten(o, u, -np.asarray(v, float), w, h, res, -np.asarray(n, float), -band, band)
        return rgb, al


class WallFill:
    """rebake.bake_plane's `extra` source: the splat's view of a wall, for the texels no ground camera saw.

    Covered = alpha over SEEN after dropping specks (opened by 3 texels). A wall is filled only when the
    splat covers at least WALL_MIN of what the cameras missed on it: less is a wall the drone saw through
    leaves or in deep shade, dark smears that look worse than the plain colour the wall gets otherwise. The
    splat's exposure is matched to the photos on the same wall: one gain for all channels, the ratio of the
    median brightness of texels both have (at least 500), limited to `gain`; a wall with too few such texels
    takes the median gain of the walls before it. `filled` collects (wall area, share filled) and `walls`
    one record per wall (size, direction, share missed by the cameras, share of that the splat covers, its
    median brightness, used or not) for the report."""

    def __init__(self, splat: Splat, gain: tuple[float, float] = (0.8, 1.25)):
        self.splat, self.limits = splat, gain
        self.gains: list[float] = []
        self.filled: list[tuple[float, float]] = []
        self.walls: list[dict] = []

    def __call__(self, p, res: float, W: int, H: int, mask: np.ndarray, rgb: np.ndarray, valid: np.ndarray):
        o = np.asarray(p.origin, float) + np.asarray(p.v, float) * p.h
        img, al, _ = self.splat.flatten(
            o,
            p.u,
            -np.asarray(p.v, float),
            W / res,
            H / res,
            res,
            -np.asarray(p.n, float),
            -WALL_BAND,
            WALL_BAND,
        )
        ok = ndimage.binary_opening(al > SEEN, iterations=3)
        mine, ok = img[mask], ok[mask]
        both = ok & valid
        missed = ~valid
        share = float((ok & missed).sum()) / max(1, int(missed.sum()))
        bright = float(np.median(mine[ok].mean(1))) if ok.any() else 0.0
        self.walls.append(
            {
                "m2": round(p.w * p.h, 1),
                "normal_deg": round(float(np.degrees(np.arctan2(p.n[1], p.n[0])))),
                "missed": round(float(missed.mean()), 2),
                "covered": round(share, 2),
                "brightness": round(bright),
                "used": share >= WALL_MIN,
            }
        )
        if not self.walls[-1]["used"]:
            return mine, np.zeros(len(mine), bool)
        k = float(np.median(self.gains)) if self.gains else 1.0
        if both.sum() >= 500:
            k = float(
                np.clip(np.median(rgb[both].mean(1)) / max(np.median(mine[both].mean(1)), 1.0), *self.limits)
            )
            self.gains.append(k)
        self.filled.append((p.w * p.h, float((ok & ~valid).sum()) / max(1, mask.sum())))
        return np.clip(mine * k, 0, 255), ok


def load(out: Path, model_id: str, centre: tuple[float, float]) -> Splat | None:
    """The model's splat within RADIUS of `centre` (plan x, y), or None when out/raw/splat/<model> has none.
    The crop is cached as out/cache/splat/<model>.npz (the file is about 420 MB, the crop about 250)."""
    ply = out / "raw" / "splat" / model_id / "splat" / "splat.ply"
    if not ply.exists():
        return None
    cache = out / "cache" / "splat" / f"{model_id}.npz"
    if cache.exists() and cache.stat().st_mtime >= ply.stat().st_mtime:
        d = np.load(cache)
        if np.allclose(d["centre"], centre, atol=0.5):
            return Splat(d["xyz"], d["cov"], d["rgb"], d["alpha"], d["size"], str(ply.relative_to(out)))
    g = _read_ply(ply)
    keep = np.hypot(g["x"] - centre[0], g["y"] - centre[1]) < RADIUS
    xyz = np.stack([g["x"][keep], g["y"][keep], g["z"][keep]], 1).astype(np.float32)
    s = np.exp(np.stack([g[f"scale_{i}"][keep] for i in range(3)], 1)).astype(np.float64)
    q = np.stack([g[f"rot_{i}"][keep] for i in range(4)], 1).astype(np.float64)  # w x y z
    q /= np.linalg.norm(q, axis=1, keepdims=True)
    qw, qx, qy, qz = q.T
    R = np.stack(
        [
            np.stack([1 - 2 * (qy * qy + qz * qz), 2 * (qx * qy - qw * qz), 2 * (qx * qz + qw * qy)], 1),
            np.stack([2 * (qx * qy + qw * qz), 1 - 2 * (qx * qx + qz * qz), 2 * (qy * qz - qw * qx)], 1),
            np.stack([2 * (qx * qz - qw * qy), 2 * (qy * qz + qw * qx), 1 - 2 * (qx * qx + qy * qy)], 1),
        ],
        1,
    )
    M = R * s[:, None, :]
    cov = (M @ M.transpose(0, 2, 1)).astype(np.float32)
    rgb = np.clip(0.5 + C0 * np.stack([g[f"f_dc_{i}"][keep] for i in range(3)], 1), 0, 1).astype(np.float32)
    alpha = (1 / (1 + np.exp(-g["opacity"][keep].astype(np.float64)))).astype(np.float32)
    size = s.max(1).astype(np.float32)
    cache.parent.mkdir(parents=True, exist_ok=True)
    np.savez(cache, xyz=xyz, cov=cov, rgb=rgb, alpha=alpha, size=size, centre=np.asarray(centre, float))
    return Splat(xyz, cov, rgb, alpha, size, str(ply.relative_to(out)))


def _read_ply(path: Path) -> dict[str, np.ndarray]:
    """A binary little-endian PLY of float properties (the 3DGS layout). Header by hand, data by numpy."""
    with open(path, "rb") as f:
        header = b""
        while not header.endswith(b"end_header\n"):
            line = f.readline()
            if not line or len(header) > 10000:
                raise ValueError(f"{path}: bad PLY header")
            header += line
        offset = f.tell()
    lines = header.decode("ascii").splitlines()
    n = next(int(x.split()[2]) for x in lines if x.startswith("element vertex"))
    props = [x.split()[2] for x in lines if x.startswith("property float")]
    if len(props) != sum(x.startswith("property") for x in lines):
        raise ValueError(f"{path}: a property that is not a float")
    data = np.memmap(path, dtype="<f4", mode="r", offset=offset, shape=(n, len(props)))
    return {p: np.asarray(data[:, i]) for i, p in enumerate(props)}
