"""A roof as a few flat planes fitted to a height raster (Vexcel's surface model, see aerial.py).

A surface model seen from above has the roof's real shape (gables, hips, ridge heights, lower wings) but as a
soft, slightly noisy heightfield: used directly it gives rounded ridges and bumpy slopes. A house roof is a
handful of planes, so the planes are found and the roof is rebuilt from them: straight ridges, flat slopes.

fit_roof:
1. Sample the raster on a STEP grid over the roof's region. Cells within EDGE of the region's edge are not
   used for fitting (the raster mixes roof and ground there).
2. Repeatedly: among up to SEEDS flat, unused cells, each proposes the plane through it with the local
   gradient; the proposal whose connected set of unused cells within TOL is largest wins, is refitted by
   least squares to those cells (twice), and takes them. Stop when the best is under MIN_AREA or after
   MAX_PLANES. Proposals steeper than MAX_SLOPE or lower than `min_z` (ground, when the region is off) are
   discarded with their cells.
3. Every cell of the grid takes the plane of the nearest fitted cell (edges, ridge rounding, vents, tree
   canopy over the roof).

Roof.height(x, y) is the cell's plane, except within REACH of another plane's cells: there the plane the
raster agrees with best is used, which is the lower one at a ridge or hip and the higher one in a valley, so
planes meet in straight lines at their true intersection. Where the raster cannot be trusted (it differs
from the cell's own plane by more than TRUST: the eaves, a tree) the cell's own plane stands. An optional
`floor(x, y)` (the ceilings below, plus a gap) is never undercut.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import shapely
from scipy import ndimage
from shapely.geometry import Polygon

STEP = 0.15  # grid spacing, m (the surface model is about 0.13 m)
EDGE = 0.45  # cells this close to the region's edge are not fitted
TOL = 0.10  # a cell belongs to a plane when its height is within this of it, m
MIN_AREA = 2.0  # smallest roof plane, m2
MAX_PLANES = 16
MAX_SLOPE = 1.2  # rise over run (50 degrees); steeper is a wall or a tree
SEEDS = 300  # plane proposals per round
REACH = 0.45  # near another plane's cells (this close), that plane may be the better one, m
TRUST = 0.30  # ... where the raster is within this of the cell's own plane, m
SNAP_REACH = 1.5  # fit_roof(snap=True): a plane may take cells this far from its fitted cells, m
SNAP_WINDOW = 0.5  # ... agreement with the raster is averaged over this, m
SNAP_TOL = 0.15  # ... and must be under this, m
PAIR_NEAR = 1.0  # ... how two planes meet is read from their fitted cells within this of the cell, m

Height = Callable[[np.ndarray, np.ndarray], np.ndarray]


@dataclass
class Roof:
    x0: float
    y0: float
    step: float
    planes: np.ndarray  # (k, 3): z = a x + b y + c
    label: np.ndarray  # (nx, ny) plane index of each cell
    near: np.ndarray  # (k, nx, ny): plane k has a cell within REACH
    raw: np.ndarray  # (nx, ny) smoothed raster heights
    trusted: np.ndarray  # (nx, ny): the raster agrees with the cell's plane
    region: Polygon
    areas: list[float]  # m2 of fitted cells per plane
    floor: Height | None = None
    # fit_roof(snap=True), (k, nx, ny): how the cell's plane meets plane k near it: +1 ridge or hip, -1 valley
    kind: np.ndarray | None = None

    def _ij(self, x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        i = np.clip(np.rint((x - self.x0) / self.step).astype(int), 0, self.label.shape[0] - 1)
        j = np.clip(np.rint((y - self.y0) / self.step).astype(int), 0, self.label.shape[1] - 1)
        return i, j

    def surface(self, x: np.ndarray, y: np.ndarray) -> np.ndarray:
        """Height from the planes alone (no floor)."""
        x, y = np.asarray(x, float).ravel(), np.asarray(y, float).ravel()
        i, j = self._ij(x, y)
        own = self.label[i, j]
        p = self.planes[own]
        z = p[:, 0] * x + p[:, 1] * y + p[:, 2]
        if self.kind is not None:
            # Snapped facets: within REACH of a neighbour the two planes meet exactly on their line, the
            # lower of the two at a ridge or hip, the higher in a valley. The surface is then continuous
            # across the junction (and along an eave that crosses it), wherever the cell boundary fell.
            for k, (a, b, c) in enumerate(self.planes):
                kind = self.kind[k, i, j]
                m = np.nonzero(self.near[k, i, j] & (kind != 0) & (own != k))[0]
                if not len(m):
                    continue
                zk = a * x[m] + b * y[m] + c
                z[m] = np.where(kind[m] > 0, np.minimum(z[m], zk), np.maximum(z[m], zk))
            return z
        t = self.trusted[i, j]
        if t.any() and len(self.planes) > 1:
            raw = self.raw[i, j]
            best = np.abs(z - raw)
            for k, (a, b, c) in enumerate(self.planes):
                m = np.nonzero(t & self.near[k, i, j] & (own != k))[0]
                if not len(m):
                    continue
                zk = a * x[m] + b * y[m] + c
                better = np.abs(zk - raw[m]) < best[m]
                z[m[better]] = zk[better]
                best[m[better]] = np.abs(zk[better] - raw[m[better]])
        return z

    def height(self, x: np.ndarray, y: np.ndarray) -> np.ndarray:
        z = self.surface(x, y)
        if self.floor is not None:
            f = self.floor(np.asarray(x, float).ravel(), np.asarray(y, float).ravel())
            z = np.where(np.isfinite(f), np.maximum(z, f), z)
        return z

    def normal(self, x: np.ndarray, y: np.ndarray) -> np.ndarray:
        """Upward unit normals, from the cell's own plane (stable at ridges)."""
        i, j = self._ij(np.asarray(x, float).ravel(), np.asarray(y, float).ravel())
        p = self.planes[self.label[i, j]]
        n = np.stack([-p[:, 0], -p[:, 1], np.ones(len(p))], 1)
        return n / np.linalg.norm(n, axis=1)[:, None]

    @property
    def pitch(self) -> float:
        """Area-weighted median slope of the sloped planes, radians (0 when all are flat)."""
        s = np.hypot(self.planes[:, 0], self.planes[:, 1])
        a = np.asarray(self.areas, float)
        sel = s > 0.08
        if not sel.any():
            return 0.0
        o = np.argsort(s[sel])
        k = np.searchsorted(np.cumsum(a[sel][o]), 0.5 * a[sel].sum())
        return float(math.atan(s[sel][o][min(k, sel.sum() - 1)]))

    def lifted(self) -> tuple[float, float]:
        """(fraction of the region's cells the floor lifts by more than 2 cm, largest lift in m): how much
        the ceilings below disagree with the fitted roof. A check on the registration."""
        if self.floor is None:
            return 0.0, 0.0
        nx, ny = self.label.shape
        X, Y = np.meshgrid(
            self.x0 + np.arange(nx) * self.step, self.y0 + np.arange(ny) * self.step, indexing="ij"
        )
        inside = shapely.contains_xy(self.region, X, Y)
        x, y = X[inside], Y[inside]
        d = self.height(x, y) - self.surface(x, y)
        return float((d > 0.02).mean()) if len(d) else 0.0, float(d.max()) if len(d) else 0.0


def _lstsq(x: np.ndarray, y: np.ndarray, z: np.ndarray) -> np.ndarray:
    a = np.c_[x, y, np.ones(len(x))]
    return np.linalg.lstsq(a, z, rcond=None)[0]


def fit_roof(
    surface: Height,
    region: Polygon,
    floor: Height | None = None,
    min_z: float | None = None,
    step: float = STEP,
    snap: bool = False,
    min_area: float = MIN_AREA,
    max_planes: int = MAX_PLANES,
) -> Roof | None:
    """Fit roof planes to `surface(x, y)` over `region` (see the module docstring). None when no plane of
    `min_area` (at most `max_planes` of them) is found.

    `snap` is for a sharp raster (the drone splat's roof sheet, splat.py). After the planes are found, every
    cell within SNAP_REACH of a plane's fitted cells takes the plane the raster agrees with best over a
    SNAP_WINDOW neighbourhood (mean absolute difference, under SNAP_TOL), so facets end at their real
    junctions, not half-way between the fitted cells; cells with no raster keep the plane of the nearest
    fitted cell. Roof.surface then joins neighbouring planes on their intersection line (Roof.kind): the
    lower of the two at a ridge or hip, the higher in a valley, a step where they do not meet."""
    x0, y0, x1, y1 = region.bounds
    nx, ny = math.ceil((x1 - x0) / step) + 1, math.ceil((y1 - y0) / step) + 1
    X, Y = np.meshgrid(x0 + np.arange(nx) * step, y0 + np.arange(ny) * step, indexing="ij")
    z = np.asarray(surface(X.ravel(), Y.ravel()), float).reshape(nx, ny)
    finite = np.isfinite(z)
    if not finite.any():
        return None
    core = shapely.contains_xy(region.buffer(-EDGE), X, Y) & finite
    zs = ndimage.gaussian_filter(np.where(finite, z, np.nanmedian(z)), 1.0)
    gx, gy = np.gradient(zs, step)
    curv = np.abs(ndimage.laplace(zs))
    free = core.copy()
    fitted = np.full((nx, ny), -1)
    planes: list[np.ndarray] = []
    areas: list[float] = []
    cell = step * step

    def member(plane: np.ndarray, at: tuple[int, int]) -> np.ndarray | None:
        ok = free & (np.abs(z - (plane[0] * X + plane[1] * Y + plane[2])) < TOL)
        lab, _ = ndimage.label(ok)
        return lab == lab[at] if lab[at] else None

    for _ in range(3 * max_planes):
        if len(planes) >= max_planes:
            break
        cand = ndimage.binary_erosion(free, iterations=2)
        idx = np.argwhere(cand)
        if not len(idx):
            break
        idx = idx[np.argsort(curv[cand], kind="stable")]  # flattest first
        idx = idx[: max(1, len(idx) * 2 // 3)]
        idx = idx[:: max(1, len(idx) // SEEDS)]
        best: tuple[int, tuple[int, int], np.ndarray] | None = None
        for i, j in idx:
            a, b = gx[i, j], gy[i, j]
            comp = member(np.array([a, b, zs[i, j] - a * X[i, j] - b * Y[i, j]]), (i, j))
            if comp is not None and (best is None or comp.sum() > best[0]):
                best = (int(comp.sum()), (int(i), int(j)), comp)
        if best is None:
            break
        _, at, comp = best
        plane = _lstsq(X[comp], Y[comp], z[comp])
        for _ in range(2):  # grow with the fitted plane, fit again
            grown = member(plane, at)
            if grown is None or grown.sum() < comp.sum():
                break
            comp = grown
            plane = _lstsq(X[comp], Y[comp], z[comp])
        if comp.sum() * cell < min_area:
            break
        free &= ~comp
        steep = math.hypot(plane[0], plane[1]) > MAX_SLOPE
        low = min_z is not None and float(np.median(z[comp])) < min_z
        if steep or low:
            continue
        fitted[comp] = len(planes)
        planes.append(plane)
        areas.append(float(comp.sum() * cell))
    if not planes:
        return None
    idx = ndimage.distance_transform_edt(fitted < 0, return_distances=False, return_indices=True)
    label = fitted[tuple(idx)]
    if snap:
        w = max(3, round(SNAP_WINDOW / step) | 1)
        have = ndimage.uniform_filter(finite.astype(float), w)
        grow = max(1, math.ceil(SNAP_REACH / step))
        best = np.full((nx, ny), np.inf)
        snapped = label.copy()
        for k, (a, b, c) in enumerate(planes):
            off = np.where(finite, np.abs(z - (a * X + b * Y + c)), 0.0)
            mean = ndimage.uniform_filter(off, w) / np.maximum(have, 1e-6)
            ok = ndimage.binary_dilation(fitted == k, iterations=grow) & (have > 0.3) & (mean < SNAP_TOL)
            ok &= mean < best
            snapped[ok] = k
            best[ok] = mean[ok]
        label = snapped
        # How the cell's plane meets each other plane near it, from the two planes' fitted cells within
        # PAIR_NEAR of the cell: each below the other on its own cells = a ridge or hip (+1), each above = a
        # valley (-1), else they do not meet there (a porch roof under the main roof: a real step, 0). It is
        # decided per place, not per pair: one long slope meets another in a hip at one end and in a valley
        # at the other.
        k = len(planes)
        kind = np.zeros((k, nx, ny), np.int8)
        w = max(3, round(2 * PAIR_NEAR / step) | 1)
        few = 4.0 / (w * w)  # at least 4 fitted cells of each plane in the window
        zp = [a_ * X + b_ * Y + c_ for a_, b_, c_ in planes]
        for a in range(k):
            for b in range(a + 1, k):
                sa = ndimage.uniform_filter((fitted == a) * np.sign(zp[b] - zp[a]), w)
                sb = ndimage.uniform_filter((fitted == b) * np.sign(zp[a] - zp[b]), w)
                meet = np.where((sa > few) & (sb > few), 1, np.where((sa < -few) & (sb < -few), -1, 0))
                kind[b][label == a] = meet[label == a]
                kind[a][label == b] = meet[label == b]
    reach = max(1, math.ceil(REACH / step))
    near = np.stack([ndimage.binary_dilation(label == k, iterations=reach) for k in range(len(planes))])
    P = np.asarray(planes)
    own = P[label]
    trusted = finite & (np.abs(zs - (own[..., 0] * X + own[..., 1] * Y + own[..., 2])) < TRUST)
    return Roof(
        float(x0), float(y0), step, P, label, near, zs, trusted, region, areas, floor, kind if snap else None
    )
