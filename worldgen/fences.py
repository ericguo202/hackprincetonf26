"""Fences of the scanned lot as flat panels, textured from the outdoor panoramas.

The MatterPak scans a board fence as a wavy sheet, each triangle then picks its own cameras, and whatever a
shrub hid is a hole or a smear. This module does for fences what rebake's relief boxes do for kitchen
cabinets:

1. Find fence runs (`find_runs`): straight stretches, in plan, of near-vertical scan surface that is solid
   from the ground up, flat, between FENCE_MIN_H and FENCE_MAX_H tall and not under a roof (a garage wall
   stands under its eaves, a deck railing stops at 1 m, a hedge is not a flat sheet).
2. One flat panel per run (`run_planes`): from just below the ground along it up to the fence's top line (a
   polyline from the scan, with posts, overhanging shrubs and the dips behind trees taken out, so a fence
   that steps or slopes keeps its silhouette). One double-sided panel for a boundary fence; two back to
   back when cameras saw both sides.
3. Texture (`bake_run_plane`): every texel from the panorama that sees it best, which gives each camera one
   coherent area, cross-faded at the seams and sampled where the fence really is. A camera does not count
   where something stands in front of the fence, with a wide margin (the scan of a shrub is full of holes);
   leaf-green and sky colours are never fence; a camera that disagrees with the others loses the texel (a
   plant the scan missed).
4. What no camera saw well gets the same fence from further along the run (`fill_along`): whole stretches of
   boards shifted by a whole number of board widths at the same height below the top, laid over a smooth
   blend of the colours around the hole. Not a blur.
5. The scan faces on the run are dropped from the picture and from collision (`on_runs`); thin boxes
   collide instead (`collision_boxes`).

Called by exterior.add_exterior after the outdoor scan faces are chosen. All geometry is Matterport Z-up plan
coordinates; nodes added to the scene are Y-up (house_mesh.Z_UP_TO_Y_UP)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import shapely
import trimesh
from scipy import ndimage
from shapely.geometry import Polygon

from worldgen import house_mesh as hm
from worldgen import rebake as rb
from worldgen.panos import PanoSet

FENCE_RES = 96.0  # texels per metre (as exterior.PIECE_RES)
FENCE_MIN_H = 1.2  # m above the ground: lower is a railing, a planter, an AC unit
FENCE_MAX_H = 2.6  # m: taller is a wall (a garage the floor plan left out, a shed)
FENCE_MIN_LEN = 1.5  # m: shorter runs stay scan
FENCE_TOL = 0.12  # m: scan surface this close to a run's line belongs to it (the scan waves by several cm)
FENCE_TAKE = 0.15  # m: scan faces this close to a panel are replaced by it
FENCE_SHEET = 0.06  # m: ... or only this close, where something stands against the fence:
OBJECT_REACH = 0.5  # m: scan surface up to this far from the run counts as standing against it
OBJECT_AREA = 0.04  # m2 of such surface per OBJECT_BIN of run
OBJECT_BIN = 0.2  # m
SOLID_BAND = (0.3, 1.3)  # m above the ground: the height range that must be filled for "solid"
SOLID_MIN = 0.7  # fraction of SOLID_BAND with scan surface, per 10 cm of run
GAP_CLOSE = 0.5  # m: holes in a run shorter than this are bridged (boards the scan missed)
PIECE_MAX = 21.0  # m: longer runs are textured in pieces (this fits a page's width at FENCE_RES)
DIP_MAX = 4.0  # m: a dip in the top line shorter than this is the scanner not seeing the top (a tree)
BUMP_MAX = 2.0  # m: a bump in the top line shorter than this is something on or behind the fence
SAMPLE = 0.03  # m: spacing of the sample points on the scan faces
CELL = 0.05  # m: plan grid for the line search
S_BIN = 0.1  # m: bins along a run
Z_BIN = 0.1  # m: height bins
FLAT_MIN = 0.6  # fraction of a run's scan surface within FLAT_TOL of its line: less is a hedge, not boards
FLAT_TOL = 0.04  # m
ROOF_REACH = 1.2  # m: a downward-facing surface this close above the top means a wall under its eaves
ROOF_MAX = 0.5  # fraction of the run's top that may be under such a surface
GROUND_SINK = 0.08  # m: panels start this far below the ground
VIEW_MIN_COS = 0.2  # a camera more than about 78 degrees off the panel's normal does not count
VIEW_MAX_DIST = 14.0  # m
OCCLUDER_GAP = 0.2  # m in front of the panel (measured square to it): further out is an occluder, nearer is
# the fence's own relief (posts, rails, the scan's ripple) or something leaning on it
OCCLUDER_CLOSE = 0.25  # m: side of the window over which an occluder's density is measured
OCCLUDER_DENSE = 0.3  # an occluder covering more than this fraction of that window is dense: fronds with
# air between them are, a few twigs of a bare tree are not
OCCLUDER_MARGIN = 0.4  # m: texels this close to an occluder's outline are not trusted either (the scan
# of a plant misses whole fronds; what leaks through is filled with boards like any unseen texel)
OCCLUDER_NEAR = 1.0  # m: of two cameras that disagree, the one this close to an occluder's outline loses
CONSISTENT = 45.0  # 0-255: a view further than this from the median of three or more is dropped
VIEW_SHARP = 26.0  # m: distance / cosine under which a 2k panorama shows the boards (2 cm per pixel)
VIEW_MAX_PANOS = 16
SHARP_MIN = 0.15  # a panel seen sharply over less than this is textured from its soft views as they are
LEAF_OVER_RED = 6.0  # leaf-green: green above red by this much (0-255) ...
LEAF_OVER_BLUE = 15.0  # ... and above blue by this much
LEAF_MIN_TEXELS = 15  # smaller green specks (after closing over 3 texels) are moss or noise and stay
LEAF_MARGIN = 0.1  # m: a leaf patch is grown by this (its fringe is not green enough to be caught)
SOURCE_CLEAR = 0.6  # m: boards are copied only from texels at least this far from an occluder's outline
SEAM = 6.0  # texels: half-width of the cross-fade between two cameras' areas
SKY_BAND = 0.25  # m below the top line in which sky-coloured texels are cut out (pointed board tops)
PERIOD_RANGE = (0.07, 0.32)  # m: board widths searched
PERIOD_MIN_CORR = 0.15  # weaker repetition than this is not trusted
BOARD_DEFAULT = 0.14  # m: a 5.5 inch picket, used when no period is found
SPAN_MIN = 0.25  # a column missing more than this fraction of its height belongs to a hole to fill as one
FILL_PASSES = 6  # stretches tried per hole before single boards are repeated
DETAIL_SIGMA = 12.0  # texels: detail finer than this is copied, colour broader than this is blended
TWO_SIDED_MIN = 0.3  # both sides seen at least this much: two panels back to back
TWO_SIDED_GAP = 0.04  # m between the two panels
UNSEEN_PANEL = 0.08  # a panel seen less than this borrows the best-seen panel's picture


@dataclass
class Run:
    """One straight stretch of fence. Points on it are a + s * u, 0 <= s <= length."""

    a: np.ndarray  # (2,) plan
    u: np.ndarray  # (2,) unit
    length: float
    s: np.ndarray  # (k,) profile stations along the run, 0 .. length
    top: np.ndarray  # (k,) absolute z of the fence's top at the stations
    ground: np.ndarray  # (k,) absolute z of the ground at the stations
    spread: float = 0.0  # median distance of the scan surface from the line (how wavy the scan is)
    meta: dict = field(default_factory=dict)

    @property
    def b(self) -> np.ndarray:
        return self.a + self.u * self.length

    @property
    def n(self) -> np.ndarray:
        """Left normal of the run in plan."""
        return np.array([-self.u[1], self.u[0]])


# ---------------------------------------------------------------- finding runs


def sample_faces(
    tris: np.ndarray, areas: np.ndarray, spacing: float = SAMPLE
) -> tuple[np.ndarray, np.ndarray]:
    """Points spread evenly over triangles (about one per spacing^2, at least one each), and the triangle
    each came from. Deterministic. The MatterPak covers flat surfaces with triangles of very different
    sizes, so counting faces or centroids says nothing about how much surface there is."""
    rng = np.random.default_rng(7)
    n = np.maximum(1, np.round(areas / spacing**2)).astype(int)
    idx = np.repeat(np.arange(len(tris)), n)
    r1, r2 = np.sqrt(rng.random(len(idx))), rng.random(len(idx))
    bary = np.stack([1 - r1, r1 * (1 - r2), r1 * r2], 1)
    return np.einsum("ij,ijk->ik", bary, tris[idx]), idx


def _hough_peak(xy: np.ndarray, w: np.ndarray, step_deg: float = 0.5) -> tuple[float, float, float]:
    """Strongest straight line through weighted plan points: (theta, rho, score), the line being
    x cos(theta) + y sin(theta) = rho. Deterministic (no random sampling)."""
    best = (0.0, 0.0, 0.0)
    for th in np.radians(np.arange(0.0, 180.0, step_deg)):
        rho = xy[:, 0] * math.cos(th) + xy[:, 1] * math.sin(th)
        lo = rho.min()
        h = np.bincount(((rho - lo) / CELL).astype(int), weights=w)
        h = np.convolve(h, np.ones(3), mode="same")  # the scan waves across two or three cells
        k = int(np.argmax(h))
        if h[k] > best[2]:
            best = (float(th), float(lo + (k + 0.5) * CELL), float(h[k]))
    return best


def _fit_line(xy: np.ndarray, w: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Weighted least-squares line through plan points: (a point on it, unit direction)."""
    c = np.average(xy, axis=0, weights=w)
    d = (xy - c) * np.sqrt(w)[:, None]
    _, _, vt = np.linalg.svd(d, full_matrices=False)
    u = vt[0]
    if u[0] < 0 or (abs(u[0]) < 1e-9 and u[1] < 0):
        u = -u
    return c, u


def _runs_on_line(
    pts: np.ndarray, hag: np.ndarray, c: np.ndarray, u: np.ndarray
) -> list[tuple[float, float, np.ndarray, np.ndarray]]:
    """Fence stretches along the line (c, u): (s0, s1, stations, top z at the stations) for each stretch
    whose scan surface is solid over SOLID_BAND, at least FENCE_MIN_LEN long and FENCE_MIN_H .. FENCE_MAX_H
    tall. `pts` are scan sample points (n, 3), `hag` their height above the ground."""
    nrm = np.array([-u[1], u[0]])
    d = (pts[:, :2] - c) @ nrm
    near = np.abs(d) < FENCE_TOL
    if near.sum() < 50:
        return []
    p, h = pts[near], hag[near]
    s = (p[:, :2] - c) @ u
    s_lo = math.floor(s.min() / S_BIN) * S_BIN
    si = ((s - s_lo) / S_BIN).astype(int)
    nz = int(4.0 / Z_BIN)
    zi = np.clip((h / Z_BIN).astype(int), -1, nz - 1)
    ns = si.max() + 1
    occ = np.zeros((ns, nz), bool)
    ok = zi >= 0
    occ[si[ok], zi[ok]] = True
    b0, b1 = round(SOLID_BAND[0] / Z_BIN), round(SOLID_BAND[1] / Z_BIN)
    solid = occ[:, b0:b1].mean(1) >= SOLID_MIN
    gap = max(1, round(GAP_CLOSE / S_BIN))
    solid = ndimage.binary_closing(np.pad(solid, gap), iterations=gap)[gap:-gap]  # bridge short holes ...
    solid = ndimage.binary_opening(solid, iterations=1)  # ... and drop single posts
    # Top per station: the scan surface goes on upward from the solid band until 20 cm are empty.
    top_bin = np.full(ns, b1, int)
    for i in np.nonzero(solid)[0]:
        k = b1
        while k + 2 < nz and (occ[i, k] or occ[i, k + 1]):
            k += 1
        top_bin[i] = k
    top_z = np.full(ns, np.nan)
    order = np.argsort(si, kind="stable")
    starts = np.searchsorted(si[order], np.arange(ns + 1))
    for i in np.nonzero(solid)[0]:
        j = order[starts[i] : starts[i + 1]]
        j = j[zi[j] <= top_bin[i]]
        if len(j):
            top_z[i] = p[j, 2].max()
    out = []
    lab, n = ndimage.label(solid)
    for k in range(1, n + 1):
        ii = np.nonzero(lab == k)[0]
        if len(ii) * S_BIN < FENCE_MIN_LEN:
            continue
        tz = top_z[ii]
        good = np.isfinite(tz)
        if good.mean() < 0.5:
            continue
        tz = np.interp(ii, ii[good], tz[good])
        height = float(np.median((top_bin[ii] * Z_BIN)[good]))
        if not (FENCE_MIN_H <= height <= FENCE_MAX_H):
            continue
        st = s_lo + (ii + 0.5) * S_BIN
        out.append((float(s_lo + ii[0] * S_BIN), float(s_lo + (ii[-1] + 1) * S_BIN), st, tz))
    return out


def _smooth_top(st: np.ndarray, tz: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The fence's top line from noisy per-station tops (stations S_BIN apart): a 0.7 m median (leaves and
    scan spikes go); bumps shorter than BUMP_MAX removed (posts that stand above the boards, a neighbour's
    shrub hanging over them; at the run's ends, bumps up to half that); dips shorter than DIP_MAX filled
    (the scanner did not see the top behind a tree; real fences do not dip and come back); then the fewest
    points that stay within 3 cm. Steps and slopes survive: opening and closing leave a monotone line
    unchanged. Bumps go first: closing first would fill the fence up to its posts."""
    k = max(3, int(0.7 / S_BIN) | 1)
    top = ndimage.median_filter(tz, size=k, mode="nearest")
    ko = max(3, min(round(BUMP_MAX / S_BIN), len(top) // 2))  # a short run keeps half of itself
    low = float(top.min()) - 10.0  # beyond its ends the run counts as low, so a bump at an end can go
    top = ndimage.grey_opening(np.pad(top, ko, constant_values=low), size=ko)[ko:-ko]
    top = ndimage.grey_closing(top, size=max(3, round(DIP_MAX / S_BIN)), mode="nearest")
    line = shapely.LineString(np.c_[st, top]).simplify(0.03)
    xy = np.asarray(line.coords)
    return xy[:, 0], xy[:, 1]


def find_runs(
    scan: rb.Scan, g, walls: Polygon, max_lines: int = 40, rejected: list | None = None
) -> list[Run]:
    """Fence runs in the lot. Looks at every scan face there, seen by a camera or not: the far end of a
    fence is the same fence. `rejected` collects (run, reason) for stretches that looked like a fence in
    plan and failed a test (for debugging)."""
    c = scan.tris.mean(1)
    region = g.lot.buffer(1.5).difference(walls.buffer(0.3))
    x0, y0, x1, y1 = region.bounds
    sel = (c[:, 0] > x0) & (c[:, 0] < x1) & (c[:, 1] > y0) & (c[:, 1] < y1)
    sel = np.nonzero(sel & (np.abs(scan.normals[:, 2]) < 0.5))[0]
    sel = sel[shapely.contains_xy(region, c[sel, 0], c[sel, 1])]
    if not len(sel):
        return []
    pts, _ = sample_faces(scan.tris[sel], scan.areas[sel])
    hag = pts[:, 2] - g.height(pts[:, 0], pts[:, 1])
    keep = (hag > -0.3) & (hag < 4.0)
    pts, hag = pts[keep], hag[keep]
    # Plan cells weighted by how much of SOLID_BAND they hold.
    ix = ((pts[:, 0] - x0) / CELL).astype(int)
    iy = ((pts[:, 1] - y0) / CELL).astype(int)
    band = (hag >= SOLID_BAND[0]) & (hag < SOLID_BAND[1])
    zi = ((hag[band] - SOLID_BAND[0]) / Z_BIN).astype(int)
    nb = round((SOLID_BAND[1] - SOLID_BAND[0]) / Z_BIN)
    occ = np.zeros((ix.max() + 1, iy.max() + 1, nb), bool)
    occ[ix[band], iy[band], np.clip(zi, 0, nb - 1)] = True
    weight = occ.sum(2).astype(float)
    cx, cy = np.nonzero(weight >= 3)
    cells = np.c_[x0 + (cx + 0.5) * CELL, y0 + (cy + 0.5) * CELL]
    cw = weight[cx, cy]
    alive = np.ones(len(cells), bool)
    min_score = FENCE_MIN_LEN / CELL * nb * SOLID_MIN * 0.5
    runs: list[Run] = []
    for _ in range(max_lines):
        if alive.sum() < 20:
            break
        th, rho, score = _hough_peak(cells[alive], cw[alive])
        if score < min_score:
            break
        nrm = np.array([math.cos(th), math.sin(th)])
        on = alive & (np.abs(cells @ nrm - rho) < FENCE_TOL)
        p0, u = _fit_line(cells[on], cw[on])
        on = alive & (np.abs((cells - p0) @ np.array([-u[1], u[0]])) < FENCE_TOL)
        if on.sum() >= 10:
            p0, u = _fit_line(cells[on], cw[on])
        nrm = np.array([-u[1], u[0]])
        d = (cells - p0) @ nrm
        s_cells = (cells - p0) @ u
        dp, sp = (pts[:, :2] - p0) @ nrm, (pts[:, :2] - p0) @ u
        for s0, s1, st, tz in _runs_on_line(pts, hag, p0, u):
            alive &= ~((np.abs(d) < 2 * FENCE_TOL) & (s_cells > s0 - 0.3) & (s_cells < s1 + 0.3))
            m = (np.abs(dp) < FENCE_TOL) & (sp > s0) & (sp < s1)
            # The stretch's own line (a 20 m line through several stretches is a few cm off each).
            q0, v = _fit_line(pts[m, :2], np.ones(int(m.sum())))
            if v @ u < 0:
                v = -v
            vn = np.array([-v[1], v[0]])
            dist = np.abs((pts[m, :2] - q0) @ vn)
            a = q0 + v * ((p0 + u * s0 - q0) @ v)
            ss, tt = _smooth_top(st - s0, tz)
            ss = np.clip(ss, 0.0, s1 - s0)
            ss[0], ss[-1] = 0.0, s1 - s0
            q = a + np.outer(ss, v)
            beside = np.stack([g.height(*(q + vn * k).T) for k in (-0.3, 0.0, 0.3)])
            r = Run(a, v, s1 - s0, ss, tt, beside.min(0), float(np.median(dist)))
            r.meta["flat"] = round(float((dist < FLAT_TOL).mean()), 2)
            r.meta["height"] = round(float(np.median(tt - beside.max(0))), 2)
            r.meta["under_roof"] = round(_under_a_roof(scan, r), 2)
            why = None
            if r.meta["flat"] < FLAT_MIN:
                why = "not flat (a hedge)"
            elif r.meta["height"] < FENCE_MIN_H:
                why = "too low above the higher ground (a railing)"
            elif r.meta["under_roof"] > ROOF_MAX:
                why = "under a roof (a wall)"
            if why is None:
                runs.append(r)
            elif rejected is not None:
                rejected.append((r, why))
        alive &= ~(np.abs(d) < FENCE_TOL)  # whatever else lies on this line is not a fence
    _join_corners(runs)
    return [r for r in runs if r.length >= FENCE_MIN_LEN]


def _under_a_roof(scan: rb.Scan, r: Run) -> float:
    """Largest fraction, over the two sides, of points beside the run that have a downward-facing scan
    surface above them: a wall under its eaves or a garage door under its header, not a fence. The rays
    start 0.2 m beside the run and 0.3 m below its top (the top of a wall is where it meets the soffit) and
    go ROOF_REACH above the top."""
    st = np.arange(0.15, r.length, 0.25)
    if not len(st):
        return 0.0
    top = np.interp(st, r.s, r.top)
    best = 0.0
    for side in (1.0, -1.0):
        xy = r.a + np.outer(st, r.u) + r.n * (0.2 * side)
        o = np.c_[xy, top - 0.3]
        t, prim = scan.rays.cast(o, np.tile([0.0, 0.0, 1.0], (len(o), 1)))
        hit = (prim >= 0) & (t < ROOF_REACH + 0.3)
        hit[hit] &= scan.normals[prim[hit], 2] < -0.3
        best = max(best, float(hit.mean()))
    return best


def _join_corners(runs: list[Run], reach: float = 0.4) -> None:
    """Move run ends onto the line of another run that passes within `reach` of them (in place), so fences
    meet at corners instead of leaving a slit or crossing."""
    for i, r in enumerate(runs):
        for end in (0, 1):
            p = r.a if end == 0 else r.b
            for j, o in enumerate(runs):
                den = r.u[0] * o.u[1] - r.u[1] * o.u[0]
                if i == j or abs(den) < 0.3:  # itself, or nearly parallel
                    continue
                w = o.a - r.a
                t = (w[0] * o.u[1] - w[1] * o.u[0]) / den  # where the lines cross, along r ...
                v = (w[0] * r.u[1] - w[1] * r.u[0]) / den  # ... and along o
                x = r.a + r.u * t
                if np.linalg.norm(x - p) > reach or not (-reach <= v <= o.length + reach):
                    continue
                if end == 0:
                    r.a, r.length, r.s = x, r.length - t, r.s - t
                    r.s[0] = 0.0
                else:
                    r.length = float(t)
                    r.s[-1] = r.length
                break


# ---------------------------------------------------------------- panels


def _profile(r: Run, s0: float, s1: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Stations of the run between s0 and s1 (both included) with the top and ground heights there."""
    inner = r.s[(r.s > s0 + 1e-6) & (r.s < s1 - 1e-6)]
    extra = np.arange(s0, s1, 0.5)[1:]  # the ground is not a straight line between top stations
    st = np.unique(np.r_[s0, inner, extra, s1])
    return st, np.interp(st, r.s, r.top), np.interp(st, r.s, r.ground)


def run_planes(r: Run, sides: tuple[float, ...] = (1.0,), gap: float = 0.0) -> list[rb.Plane]:
    """The run as flat panels: pieces of at most PIECE_MAX, each a rebake.Plane whose shape runs from
    GROUND_SINK below the ground to the fence's top line. One plane per side in `sides` (+1 = the left of
    the run's direction, -1 = the right), `gap` apart. A single plane is drawn double-sided, so the far side
    of a boundary fence shows the mirrored near side."""
    out = []
    n_pieces = max(1, math.ceil(r.length / PIECE_MAX))
    edges = np.linspace(0.0, r.length, n_pieces + 1)
    ez = np.array([0.0, 0.0, 1.0])
    u3 = np.array([r.u[0], r.u[1], 0.0])
    for s0, s1 in zip(edges[:-1], edges[1:], strict=True):
        st, top, ground = _profile(r, s0, s1)
        z0 = float(ground.min()) - GROUND_SINK - 0.02
        lower = np.c_[st - s0, ground - GROUND_SINK - z0]
        upper = np.c_[st - s0, np.maximum(top, ground + 0.3) - z0]
        shape = Polygon(np.r_[lower, upper[::-1]])
        if not shape.is_valid or shape.area < 0.05:
            continue
        for side in sides:
            n3 = np.array([r.n[0], r.n[1], 0.0]) * side
            origin = np.array([*(r.a + r.u * s0), z0]) + n3 * (gap / 2)
            p = rb.Plane(
                "fence", origin, u3, ez, n3, float(s1 - s0), float(upper[:, 1].max()), shape, carve=False
            )
            p.top = (st - s0, upper[:, 1])  # the top line in the plane's (s, t)
            out.append(p)
    return out


def _sky(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(blue sky, cloud) colours, the two halves of exterior._sky."""
    r, g, b = (rgb[..., k].astype(float) for k in range(3))
    mx, mn = np.maximum(np.maximum(r, g), b), np.minimum(np.minimum(r, g), b)
    return (b > r + 20) & (b >= g) & (mx > 110), (mn > 185) & (mx - mn < 40)


def _leafy(rgb: np.ndarray) -> np.ndarray:
    """Leaf-green colours: green clearly above red and blue. Weathered boards are grey, new ones yellow
    (red above green), mossy ones only faintly green."""
    r, g, b = (rgb[..., k].astype(float) for k in range(3))
    return (g > r + LEAF_OVER_RED) & (g > b + LEAF_OVER_BLUE)


def _grid(p: rb.Plane, res: float):
    W, H = max(2, math.ceil(p.w * res)), max(2, math.ceil(p.h * res))
    s = (np.arange(W) + 0.5) / res
    t = p.h - (np.arange(H) + 0.5) / res  # image row 0 = top of the plane
    S, T = np.meshgrid(s, t)
    mask = shapely.contains_xy(p.shape.buffer(2 / res), S.ravel(), T.ravel()).reshape(H, W)
    return S, T, mask


def _surface(p: rb.Plane, scan: rb.Scan, S: np.ndarray, T: np.ndarray, mask: np.ndarray):
    """Where the scanned fence really is relative to the flat panel: (offset per texel column along the
    panel's normal, has-surface (H, W) on a 4 texel grid spread to every texel).

    The offset is the median over column blocks of 0.25 m, smoothed over about a metre: photos are sampled
    at the real surface, so cameras agree, but only the slow bend of the fence is followed, not the scan's
    ripple. has-surface says whether the scan has anything within 0.2 m of the panel there."""
    step = 4
    H, W = mask.shape
    rows, cols = np.meshgrid(np.arange(0, H, step), np.arange(0, W, step), indexing="ij")
    pts = p.origin + S[rows, cols].ravel()[:, None] * p.u + T[rows, cols].ravel()[:, None] * p.v
    reach = 0.35
    t, _ = scan.rays.cast(pts + p.n * reach, np.tile(-p.n, (len(pts), 1)))
    delta = (reach - t).reshape(rows.shape)
    ok = np.abs(delta) < 0.2
    has = np.repeat(np.repeat(ok, step, 0), step, 1)[:H, :W]
    inside = ok & mask[rows, cols]
    block = max(1, round(0.25 * W / max(p.w, 1e-6) / step))  # coarse columns per 0.25 m
    nb = math.ceil(ok.shape[1] / block)
    off = np.full(nb, np.nan)
    for k in range(nb):
        m = inside[:, k * block : (k + 1) * block]
        if m.sum() >= 8:
            off[k] = np.median(delta[:, k * block : (k + 1) * block][m])
    if not np.isfinite(off).any():
        return np.zeros(W), has
    good = np.isfinite(off)
    off = np.interp(np.arange(nb), np.nonzero(good)[0], off[good])
    off = ndimage.gaussian_filter1d(off, 2.0, mode="nearest")
    return np.interp(np.arange(W), (np.arange(nb) + 0.5) * block * step, off), has


@dataclass
class View:
    pano: int
    vis: np.ndarray  # (H, W) texels this panorama sees
    sharp: np.ndarray  # (H, W) ... and sees well enough to show the boards
    geo: np.ndarray  # (H, W) float32, cos / distance^2
    col: np.ndarray  # (H, W, 3) uint8
    clear: np.ndarray  # (H, W) float32, texels from the nearest occluder's outline (large = far from any)


def _views(p: rb.Plane, scan: rb.Scan, panos: PanoSet, at: np.ndarray, mask: np.ndarray, res: float) -> list:
    """What each nearby panorama sees of the panel's texels `at` ((n, 3), the texels in `mask`).

    A panorama sees a texel when the texel faces it (cosine over VIEW_MIN_COS), is 0.25 to VIEW_MAX_DIST
    away and nothing in the scan is in front of it. It sees it sharply when distance / cosine is under
    VIEW_SHARP (one photo pixel then covers at most about 2 cm of fence). Where something other than the
    ground stands more than OCCLUDER_GAP in front of the panel (a shrub, a trunk, a deck post), the texels
    it hides do not count. Where it is dense (it hides more than OCCLUDER_DENSE of an OCCLUDER_CLOSE
    window: foliage, a palm's fronds with air between them), nothing within OCCLUDER_MARGIN counts either:
    the scan of a plant is full of holes and misses whole fronds, and through them its photo would be
    printed on the boards. A few twigs get no margin: behind a bare tree the photo of the fence is good.
    Leaf-green patches are thrown out too."""
    H, W = mask.shape
    centre = at.mean(0)
    reach = float(np.linalg.norm(at - centre, axis=1).max()) + VIEW_MAX_DIST
    d = np.linalg.norm(panos.positions - centre, axis=1)
    order = [int(j) for j in np.argsort(d)[:VIEW_MAX_PANOS] if d[j] <= reach]
    close, margin = max(3, round(OCCLUDER_CLOSE * res)), max(1, round(OCCLUDER_MARGIN * res))
    out = []
    for j in order:
        c = panos.positions[j]
        v = c - at
        dist = np.linalg.norm(v, axis=1)
        cos = (v @ p.n) / np.maximum(dist, 1e-6)
        elig = (cos > VIEW_MIN_COS) & (dist < VIEW_MAX_DIST) & (dist > 0.25)
        if elig.sum() < 20:
            continue
        idx = np.nonzero(elig)[0]
        origin, toward = np.tile(c, (len(idx), 1)), -v[idx] / dist[idx, None]
        t, prim = scan.rays.cast(origin, toward)
        # Line of sight is judged against scan.eyes (the scan plus whatever the caller generated, such as
        # a roof); what is in the way is looked up in the scan itself, whose face indices we know.
        t_eye = t if scan.eyes is scan.rays else scan.eyes.cast(origin, toward)[0]
        vis = np.zeros(len(at), bool)
        vis[idx] = t_eye >= dist[idx] - (0.10 + 0.02 / cos[idx])
        # The ground in front of the fence's foot hides it too, but it has no holes: no margin for it.
        blocked = np.zeros(len(at), bool)
        ahead = (dist[idx] - t) * cos[idx]  # how far in front of the panel the ray was stopped
        blocked[idx] = (ahead > OCCLUDER_GAP) & (scan.normals[prim, 2] <= 0.6)
        b2 = np.zeros((H, W), bool)
        b2[mask] = blocked
        if b2.any():
            # Dense occluders (foliage, a trunk, a post) get the margin; a few twigs only hide themselves.
            dense = ndimage.uniform_filter(b2.astype(np.float32), size=close) > OCCLUDER_DENSE
            b2 = ndimage.binary_dilation(b2, iterations=2)
            if dense.any():
                b2 |= ndimage.binary_dilation(dense, iterations=margin)
        v2 = np.zeros((H, W), bool)
        v2[mask] = vis
        v2 &= ~b2
        if v2.sum() < 20:
            continue
        col = np.zeros((H, W, 3), np.uint8)
        rgb, _ = panos.sample(j, at[v2[mask]])
        col[v2] = np.clip(rgb, 0, 255).astype(np.uint8)
        leaf = _leafy(col) & v2
        if leaf.any():
            # Only patches count (a few green texels are moss or noise), grown a little for the fringe.
            lab, n = ndimage.label(ndimage.binary_closing(leaf, iterations=3))
            sizes = np.asarray(ndimage.sum(leaf, lab, range(1, n + 1)))
            big = np.isin(lab, 1 + np.nonzero(sizes >= LEAF_MIN_TEXELS)[0])
            if big.any():
                grown = ndimage.binary_dilation(big, iterations=max(1, round(LEAF_MARGIN * res)))
                v2 &= ~grown
                b2 |= grown
        geo = np.zeros((H, W), np.float32)
        geo[mask] = np.where(elig, cos / np.maximum(dist, 0.25) ** 2, 0.0)
        sharp = np.zeros((H, W), bool)
        sharp[mask] = dist / np.maximum(cos, 1e-3) < VIEW_SHARP
        clear = (
            ndimage.distance_transform_edt(~b2).astype(np.float32)
            if b2.any()
            else np.full((H, W), 1e6, np.float32)
        )
        out.append(View(j, v2, sharp & v2, geo, col, clear))
    return out


def _drop_disagreeing(views: list, cols: list[np.ndarray], res: float) -> None:
    """In place: where panoramas disagree about a texel's colour, the ones that are wrong lose it.

    The fence is flat, so cameras agree on it; a plant in front of it that the scan missed (or half missed)
    is somewhere else for every camera. Colours are compared after a 2 texel blur, so board edges a texel
    apart do not count.
    - Three or more views: a view further than CONSISTENT from the per-channel median is dropped.
    - Two views more than 1.5 x CONSISTENT apart: the one nearer to an occluder's outline is dropped when
      that is within OCCLUDER_NEAR. Far from any occluder both stay: there is no telling which is right.

    Tried and dropped: a second, coarse comparison (10 texel blur, limit 22) against lens flare, with "the
    brighter of two views loses". Panoramas differ by that much everywhere (exposure, white balance, the
    sheen of a new board): it threw out 45% of the best camera's texels on 7014's west fence and left a
    patchwork of cameras, grey smudges on yellow boards. Flare stays (6915's east fence)."""
    if len(views) < 2:
        return
    vis = np.stack([v.vis for v in views])
    clear = np.stack([v.clear for v in views])
    soft = np.stack([_blur(c, vis[k], 2.0) for k, c in enumerate(cols)])
    cnt = vis.sum(0)
    rr, cc = np.nonzero(cnt >= 3)
    if len(rr):
        sm = vis[:, rr, cc]  # (K, n)
        val = soft[:, rr, cc]  # (K, n, 3)
        srt = np.sort(np.where(sm[..., None], val, np.inf), axis=0)
        n = sm.sum(0)
        pick = np.arange(len(rr))
        med = (srt[(n - 1) // 2, pick] + srt[n // 2, pick]) / 2
        bad = (np.abs(val - med[None]).max(-1) > CONSISTENT) & sm
        bad[:, bad.sum(0) == n] = False  # never drop every view
        vis[:, rr, cc] = sm & ~bad
    rr, cc = np.nonzero(cnt == 2)
    if len(rr):
        k2 = np.argsort(~vis[:, rr, cc], axis=0, kind="stable")[:2]  # the two views of each texel
        a, b = soft[k2[0], rr, cc], soft[k2[1], rr, cc]
        ca, cb = clear[k2[0], rr, cc], clear[k2[1], rr, cc]
        differ = np.abs(a - b).max(-1) > 1.5 * CONSISTENT
        differ &= np.minimum(ca, cb) < OCCLUDER_NEAR * res
        drop_a, drop_b = differ & (ca < cb), differ & (ca >= cb)
        vis[k2[0][drop_a], rr[drop_a], cc[drop_a]] = False
        vis[k2[1][drop_b], rr[drop_b], cc[drop_b]] = False
    for k, v in enumerate(views):
        v.vis = vis[k]
        v.sharp &= vis[k]


def _composite(views: list, mask: np.ndarray, res: float) -> tuple[np.ndarray, np.ndarray, np.ndarray, int]:
    """One picture from the views. Every texel goes to the panorama that sees it best: a sharp view before
    a soft one, then the largest geometric weight (cos / distance^2 is smooth over the panel, so each
    camera gets one coherent area: no patchwork). Before that, later panoramas are scaled to the exposure
    of the earlier ones where they overlap, and views that disagree are dropped (`_drop_disagreeing`).
    Areas are cross-faded over SEAM texels. Returns (rgb (H, W, 3) uint8, seen (H, W), seen sharply (H, W),
    clean (H, W): seen sharply and at least SOURCE_CLEAR from any occluder's outline in the view used,
    panoramas used)."""
    H, W = mask.shape
    if not views:
        none = np.zeros((H, W), bool)
        return np.zeros((H, W, 3), np.uint8), none, none.copy(), none.copy(), 0

    def labels():
        vis = np.stack([v.vis for v in views])
        score = np.stack([v.geo + 10.0 * v.sharp for v in views])
        return np.argmax(np.where(vis, score, -1.0), axis=0), vis

    label, vis = labels()
    area = [int(((label == k) & vis[k]).sum()) for k in range(len(views))]
    order = [int(k) for k in np.argsort(area)[::-1] if area[k] > 0]
    cols = [v.col.astype(np.float32) for v in views]
    have = np.zeros((H, W), bool)
    acc = np.zeros((H, W, 3), np.float32)
    for k in order:
        both = have & vis[k]
        if both.sum() >= 200:
            gain = (np.median(acc[both], axis=0) + 1.0) / (np.median(cols[k][both], axis=0) + 1.0)
            cols[k] = cols[k] * np.clip(gain, 0.7, 1.4)
        new = vis[k] & ~have
        acc[new] = cols[k][new]
        have |= vis[k]
    _drop_disagreeing(views, cols, res)
    label, vis = labels()
    seen = vis.any(0)
    sharp = np.stack([v.sharp for v in views]).any(0)
    num = np.zeros((H, W, 3), np.float32)
    den = np.zeros((H, W), np.float32)
    used = 0
    for k in range(len(views)):
        mine = (label == k) & seen
        if not mine.any():
            continue
        used += 1
        f = ndimage.gaussian_filter(mine.astype(np.float32), SEAM) * vis[k]
        num += cols[k] * f[..., None]
        den += f
    rgb = num / np.maximum(den, 1e-4)[..., None]
    clear = np.take_along_axis(np.stack([v.clear for v in views]), label[None], 0)[0]
    sharp &= seen
    clean = sharp & (clear > SOURCE_CLEAR * res)
    return np.clip(rgb, 0, 255).astype(np.uint8), seen & (den > 1e-4), sharp, clean, used


def _blend(img: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """The picture with every texel that is not `valid` replaced by a smooth blend of the valid texels
    around it (normalised convolution at the finest scale that reaches some), float32. Like rebake._fill
    without the grain, and without counting texels outside the panel as black data."""
    out = img.astype(np.float32).copy()
    todo = ~valid
    m = valid.astype(np.float32)
    for sigma in (3, 8, 20, 50, 120, 400):
        if not todo.any():
            break
        w = ndimage.gaussian_filter(m, sigma)
        ok = todo & (w > 0.02)
        if not ok.any():
            continue
        for c in range(3):
            num = ndimage.gaussian_filter(img[..., c].astype(np.float32) * m, sigma)
            out[..., c][ok] = (num / np.maximum(w, 1e-6))[ok]
        todo &= ~ok
    if todo.any() and valid.any():
        out[todo] = img[valid].mean(0)
    return out


def _blur(img: np.ndarray, valid: np.ndarray, sigma: float) -> np.ndarray:
    """Normalised gaussian blur of the valid texels (float32); zero where nothing valid is near."""
    m = valid.astype(np.float32)
    w = ndimage.gaussian_filter(m, sigma)
    out = np.stack([ndimage.gaussian_filter(img[..., c].astype(np.float32) * m, sigma) for c in range(3)], -1)
    return out / np.maximum(w, 1e-3)[..., None]


def board_period(img: np.ndarray, valid: np.ndarray, res: float) -> float:
    """Width of one board in texels: the lag, between PERIOD_RANGE, at which the picture's fine detail
    along the fence repeats best. BOARD_DEFAULT when nothing repeats clearly (a smooth panel fence)."""
    lum = img[..., :3].astype(np.float32).mean(-1)
    d = lum - ndimage.gaussian_filter1d(lum, 0.15 * res, axis=1, mode="nearest")
    d = np.where(valid, d, 0.0)
    n_valid = int(valid.sum())
    lo, hi = round(PERIOD_RANGE[0] * res), round(PERIOD_RANGE[1] * res)
    if n_valid < 2000 or valid.shape[1] <= hi + 8:
        return BOARD_DEFAULT * res
    var = float((d * d).sum()) / n_valid
    lags, corr = [], []
    for lag in range(lo, hi + 1):
        both = valid[:, lag:] & valid[:, :-lag]
        n = int(both.sum())
        if n < 500 or var <= 0:
            continue
        lags.append(lag)
        corr.append(float((d[:, lag:] * d[:, :-lag] * both).sum()) / n / var)
    if not lags or max(corr) <= PERIOD_MIN_CORR:
        return BOARD_DEFAULT * res
    # Two boards repeat as well as one: take the shortest lag that is nearly as good as the best.
    good = [lag for lag, c in zip(lags, corr, strict=True) if c >= 0.8 * max(corr)]
    return float(good[0])


def _spans(need_cols: np.ndarray, join: int) -> list[tuple[int, int]]:
    """[a, b) column ranges where need_cols is set, ranges less than `join` columns apart merged."""
    idx = np.nonzero(need_cols)[0]
    if not len(idx):
        return []
    cuts = np.nonzero(np.diff(idx) > join)[0]
    starts = np.r_[idx[0], idx[cuts + 1]]
    ends = np.r_[idx[cuts], idx[-1]] + 1
    return list(zip(starts.tolist(), ends.tolist(), strict=True))


def fill_along(
    img: np.ndarray,
    sharp: np.ndarray,
    soft: np.ndarray,
    mask: np.ndarray,
    top_row: np.ndarray,
    period: float,
    source: np.ndarray | None = None,
) -> np.ndarray:
    """Give every texel of `mask` that was not seen sharply the boards of the same fence from elsewhere
    along the run. `sharp`: texels with a good photo (kept); `soft`: texels with only a distant or grazing
    photo (their broad colour is kept, their detail replaced); the rest of `mask` was not seen at all.
    `source` (default: sharp) are the texels boards may be copied from.

    Rows are first lined up at the fence's top (`top_row[i]` = image row of the top in column i): boards
    hang from the top, so rails and board ends then sit in the same row everywhere. A texel takes its broad
    colour (blur over DETAIL_SIGMA) from its own soft photo, or from a smooth blend of the seen texels
    around it, so light and shade along the fence stay continuous. Its detail (picture minus that blur,
    clipped to 3 times the typical detail so that a shadow or a leaked leaf is not stamped along the fence)
    is copied from the same row further along:
    - each stretch of columns that needs filling is shifted as a whole, by the whole number of board widths
      (at least its own length) for which most of it lands on source texels: a stretch of real fence with
      its own boards, the same shift in every row, so boards stay vertical; what that stretch cannot give
      comes from the next best shift, up to FILL_PASSES times;
    - what is left takes the nearest source texel a whole number of board widths away in its row (this
      repeats single boards, so it comes last);
    - a row with no source texel at all takes the nearest detail from any row."""
    H, W = mask.shape
    need = mask & ~sharp
    if not need.any():
        return img
    if not (sharp | soft).any():
        return np.full_like(img, 128)
    source = sharp if source is None else source & sharp
    shift = np.round(top_row - top_row.min()).astype(int)
    top = int(shift.max())
    rows = np.arange(H)[:, None] - shift[None, :] + top  # aligned row of each texel
    cols = np.broadcast_to(np.arange(W), (H, W))
    hs = H + top
    A = np.zeros((hs, W, 3), np.float32)
    V, Sf, N, Src = (np.zeros((hs, W), bool) for _ in range(4))
    A[rows, cols] = img[..., :3]
    V[rows, cols] = sharp
    Sf[rows, cols] = soft & ~sharp
    N[rows, cols] = need
    Src[rows, cols] = source
    seen = V | Sf
    low = _blur(A, seen, DETAIL_SIGMA)
    detail = np.where(Src[..., None], A - low, 0.0)
    typical = float(np.median(np.abs(detail[Src]))) if Src.any() else 0.0
    limit = 3.0 * typical + 2.0
    detail = np.clip(detail, -limit, limit)
    base = np.where(Sf[..., None], low, _blend(A, seen))
    src = np.zeros((hs, W, 3), np.float32)
    todo = N.copy()
    step = max(period, 1.0)
    if Src.any():
        # Stretches = columns where a good part of the fence's height is missing. A thin unseen strip along
        # the ground or the top (in every column) is not a stretch: it is left to the single-board copy.
        height = np.maximum((V | Sf | N).sum(0), 1)
        for a, b in _spans(N.sum(0) > SPAN_MIN * height, join=round(step)):
            m0 = max(1, math.ceil((b - a) / step))
            for _ in range(FILL_PASSES):  # what one stretch cannot give, the next best one does
                want = todo[:, a:b]
                total = int(want.sum())
                if not total:
                    break
                best_cover, best_d = 0.0, 0
                for m in range(m0, m0 + int(W / step) + 1):
                    d = round(m * step)
                    for sgn in (-1, 1):
                        lo, hi = a + sgn * d, b + sgn * d
                        if lo < 0 or hi > W:
                            continue
                        cover = float((want & Src[:, lo:hi]).sum()) / total
                        if cover > best_cover:
                            best_cover, best_d = cover, sgn * d
                    if best_cover > 0.9 or (a - d < 0 and b + d > W):
                        break
                if best_cover < 0.02:
                    break
                take = want & Src[:, a + best_d : b + best_d]
                src[:, a:b][take] = detail[:, a + best_d : b + best_d][take]
                todo[:, a:b] &= ~take
        for m in range(1, int(W / step) + 2):
            k = round(m * step)
            if k >= W or not todo.any():
                break
            for sgn in (-1, 1):
                cand = np.zeros_like(V)
                dcand = np.zeros_like(src)
                if sgn < 0:
                    cand[:, k:] = Src[:, :-k]
                    dcand[:, k:] = detail[:, :-k]
                else:
                    cand[:, :-k] = Src[:, k:]
                    dcand[:, :-k] = detail[:, k:]
                take = todo & cand
                src[take] = dcand[take]
                todo &= ~take
        if todo.any():  # rows without any source texel: the nearest detail from another row
            idx = ndimage.distance_transform_edt(~Src, return_distances=False, return_indices=True)
            src[todo] = detail[idx[0][todo], idx[1][todo]]
    out = base + src
    res = img.copy()
    res[..., :3][need] = np.clip(out[rows, cols], 0, 255).astype(np.uint8)[need]
    return res


def bake_run_plane(p: rb.Plane, scan: rb.Scan, panos: PanoSet, res: float = FENCE_RES) -> dict:
    """Texture one fence panel (sets p.image, RGBA, and p.seen, p.sharp, p.unseen, p.shape_mask, p.res).
    Returns its numbers."""
    res = min(res, (rb.PAGE - 2 * rb.PAD - 2) / max(p.w, p.h))
    S, T, mask = _grid(p, res)
    H, W = mask.shape
    off, has = _surface(p, scan, S, T, mask)
    at = p.origin + S[mask][:, None] * p.u + T[mask][:, None] * p.v + off[np.nonzero(mask)[1]][:, None] * p.n
    rgb, seen, sharp, clean, used = _composite(_views(p, scan, panos, at, mask, res), mask, res)
    seen &= mask
    sharp &= mask
    top_t = np.interp((np.arange(W) + 0.5) / res, p.top[0], p.top[1])
    top_row = (p.h - top_t) * res
    # Sky is not fence, but colour alone cannot say so: a sunlit pale board is as bright as a cloud, and
    # boards in shade or under lens flare turn blue. So only where the scan has no surface either. Near the
    # top line such texels are cut out (alpha): the photo shows sky between the pointed board tops, and the
    # boards keep their points. Anywhere else (the scanner did not see the fence's top behind a tree, and
    # the photo shows sky through the leaves) they count as unseen and get boards.
    blue, cloud = _sky(rgb)
    r = np.arange(H)[:, None]
    band = (r >= top_row[None, :] - 2) & (r < top_row[None, :] + SKY_BAND * res)
    not_fence = seen & (blue | cloud) & ~has
    sky = ndimage.binary_opening(band & not_fence, iterations=1)
    alpha = np.full((H, W), 255, np.uint8)
    alpha[sky] = 0
    lost = ndimage.binary_dilation(ndimage.binary_opening(not_fence & ~sky, iterations=1), iterations=3)
    seen &= ~lost | sky
    sharp &= seen
    clean &= seen
    if sharp.sum() < SHARP_MIN * mask.sum():
        sharp = clean = seen.copy()  # nothing sharp to copy boards from: the soft photo is the picture
    if clean.sum() < SHARP_MIN * mask.sum():
        clean = sharp  # occluders everywhere: copy from whatever is sharp
    period = board_period(rgb, sharp & ~sky, res)
    img = fill_along(rgb, sharp | sky, seen | sky, mask, top_row, period, source=clean & ~sky)
    p.image = np.dstack([img, alpha])
    p.res = res
    p.seen = float((seen & ~sky).sum()) / max(1, int(mask.sum()))
    p.sharp = float((sharp & ~sky).sum()) / max(1, int(mask.sum()))
    p.unseen = mask & ~seen & (alpha > 0)
    p.kinds = (sharp & mask, seen & mask, clean & mask)  # sharp, seen, board source: for debug pictures
    p.shape_mask = mask
    return {
        "seen": p.seen,
        "sharp": p.sharp,
        "panos": used,
        "period_m": period / res,
        "mtx": H * W / 1e6,
    }


def _side_seen(p: rb.Plane, scan: rb.Scan, panos: PanoSet) -> float:
    """Fraction of a panel some camera sees from the side its normal points to (coarse, no colours)."""
    S, T, mask = _grid(p, 8.0)
    at = p.origin + S[mask][:, None] * p.u + T[mask][:, None] * p.v
    seen = np.zeros(len(at), bool)
    for c in panos.positions:
        v = c - at
        dist = np.linalg.norm(v, axis=1)
        cos = (v @ p.n) / np.maximum(dist, 1e-6)
        idx = np.nonzero((cos > VIEW_MIN_COS) & (dist < VIEW_MAX_DIST) & (dist > 0.25) & ~seen)[0]
        if not len(idx):
            continue
        t, _ = scan.eyes.cast(np.tile(c, (len(idx), 1)), -v[idx] / dist[idx, None])
        seen[idx] = t >= dist[idx] - 0.25
    return float(seen.mean()) if len(seen) else 0.0


def _borrow(p: rb.Plane, donor: rb.Plane) -> None:
    """Texture a panel no camera saw with the best-seen panel's picture, lined up at the top and mirrored
    back and forth along the run."""
    H, W = p.shape_mask.shape
    dh, dw = donor.image.shape[:2]
    d_top = (donor.h - np.interp((np.arange(dw) + 0.5) / donor.res, donor.top[0], donor.top[1])) * donor.res
    p_top = (p.h - np.interp((np.arange(W) + 0.5) / p.res, p.top[0], p.top[1])) * p.res
    idx = np.arange(W) % (2 * dw)
    idx = np.where(idx >= dw, 2 * dw - 1 - idx, idx)
    r = (
        np.arange(H)[:, None]
        - np.round(p_top)[None, :].astype(int)
        + np.round(d_top[idx])[None, :].astype(int)
    )
    img = donor.image[np.clip(r, 0, dh - 1), idx[None, :], :3]
    p.image = np.dstack([img, np.full((H, W), 255, np.uint8)])


def collision_boxes(runs: list[Run]) -> np.ndarray:
    """(n, 3, 3) triangles: one thin box per straight stretch between top stations, ground to top."""
    out = []
    for r in runs:
        for s0, s1 in zip(r.s[:-1], r.s[1:], strict=True):
            z0 = float(min(np.interp([s0, s1], r.s, r.ground))) - GROUND_SINK
            z1 = float(max(np.interp([s0, s1], r.s, r.top)))
            seg = hm.WallSegment(tuple(r.a + r.u * s0), tuple(r.a + r.u * s1), 0.08, z0, z1 - z0)
            m = hm.wall_box(seg)
            if m is not None:
                out.append(m.vertices[m.faces])
    return np.concatenate(out) if out else np.zeros((0, 3, 3))


def on_runs(pts: np.ndarray, runs: list[Run], tol: float = FENCE_TAKE) -> np.ndarray:
    """Which points (n, 3) lie on a run: within `tol` of its line, between its ends (plus tol) and not
    more than 0.15 m above its top."""
    hit = np.zeros(len(pts), bool)
    for r in runs:
        w = pts[:, :2] - r.a
        s = w @ r.u
        near = (np.abs(w @ r.n) < tol) & (s > -tol) & (s < r.length + tol)
        near[near] &= pts[near, 2] < np.interp(s[near], r.s, r.top) + 0.15
        hit |= near
    return hit


def taken_faces(scan: rb.Scan, faces: np.ndarray, runs: list[Run]) -> np.ndarray:
    """Which of the scan faces `faces` the panels replace: those within FENCE_TAKE of a run (`on_runs`).
    Where something stands or leans against the fence (per OBJECT_BIN of run: more than OBJECT_AREA of scan
    surface between FENCE_TAKE and OBJECT_REACH from it, below its top), only the fence's own sheet goes
    (within FENCE_SHEET), so a pallet leaning on the boards is not cut in half."""
    c = scan.tris[faces].mean(1)
    area = scan.areas[faces]
    hit = np.zeros(len(faces), bool)
    for r in runs:
        w = c[:, :2] - r.a
        s, d = w @ r.u, np.abs(w @ r.n)
        inside = (s > -FENCE_TAKE) & (s < r.length + FENCE_TAKE)
        inside[inside] &= c[inside, 2] < np.interp(s[inside], r.s, r.top) + 0.15
        near = inside & (d < FENCE_TAKE)
        beyond = inside & (d >= FENCE_TAKE) & (d < OBJECT_REACH)
        b = np.clip((s / OBJECT_BIN).astype(int), 0, int(r.length / OBJECT_BIN) + 1)
        crowded = np.bincount(b[beyond], weights=area[beyond], minlength=b.max() + 1)[b] > OBJECT_AREA
        hit |= near & (~crowded | (d < FENCE_SHEET))
    return hit


def replace_fences(
    scene: trimesh.Scene,
    scan: rb.Scan,
    g,  # exterior.Ground: height(x, y), normal(x, y), lot
    faces: np.ndarray,
    tree: np.ndarray,
    avail: np.ndarray,
    panos: PanoSet,
    walls: Polygon,
    report: dict,
    debug: list | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Replace scanned fence runs by flat textured panels (nodes `fence_N`).

    `faces`: indices into scan.tris of the outdoor scan faces an outdoor camera sees; `tree`: which of them
    are more than exterior.TREE_MIN_Z above the ground and away from the roof (canopies); `avail`: which are
    not yet replaced by an earlier step; `panos`: the outdoor panoramas; `walls`: the facade outline.
    Returns (taken, solids): a bool mask over `faces` of the scan faces this replaces (the caller then
    neither renders nor collides them) and (n, 3, 3) Z-up collision triangles for what was built. Adds its
    nodes to `scene` and its numbers to `report`. `debug`, when given, receives the runs and the panels."""
    taken = np.zeros(len(faces), bool)
    runs = find_runs(scan, g, walls)
    if not runs:
        report["fences"] = {"runs": 0}
        return taken, np.zeros((0, 3, 3))
    planes: list[rb.Plane] = []
    stats = []
    for r in runs:
        # Which side do the cameras see? Both for a fence inside the lot (two panels back to back); one
        # for a boundary fence (a single double-sided panel: the far side mirrors the near side).
        probe = {sd: run_planes(r, (sd,)) for sd in (1.0, -1.0)}
        seen = {
            sd: float(np.mean([_side_seen(p, scan, panos) for p in ps])) if ps else 0.0
            for sd, ps in probe.items()
        }
        best = max(seen, key=seen.get)
        both = min(seen.values()) >= TWO_SIDED_MIN
        ps = run_planes(r, (1.0, -1.0), gap=TWO_SIDED_GAP) if both else run_planes(r, (best,))
        r.meta.update(sides=2 if both else 1, side_seen={int(k): round(v, 2) for k, v in seen.items()})
        for p in ps:
            stats.append(bake_run_plane(p, scan, panos))
        planes += ps
    # Panels no camera saw take the picture of the best-seen one.
    if planes:
        donor = max(planes, key=lambda p: p.sharp * p.shape.area)
        for p in planes:
            if p.seen < UNSEEN_PANEL and donor.sharp >= 0.3 and p is not donor:
                _borrow(p, donor)
    if debug is not None:
        debug += [runs, planes]
    pages = rb.pack(planes) if planes else []
    for pi, page in enumerate(pages):
        parts = [m for m in (rb.plane_mesh(p) for p in planes if p.page == pi) if m is not None]
        if not parts:
            continue
        m = trimesh.util.concatenate(parts)
        # Fences rarely fill a page: keep only the top half or quarter of it when that holds them all.
        used = max(p.rect[1] + p.rect[3] for p in planes if p.page == pi)
        rows = next(h for h in (rb.PAGE // 4, rb.PAGE // 2, rb.PAGE) if used <= h)
        uv = np.asarray(m.visual.uv, float).copy()
        uv[:, 1] = 1.0 - (1.0 - uv[:, 1]) * rb.PAGE / rows
        m.visual = trimesh.visual.TextureVisuals(uv=uv, material=rb._material(page[:rows], f"fence_{pi}"))
        m.apply_transform(hm.Z_UP_TO_Y_UP)
        scene.add_geometry(m, node_name=f"fence_{pi}", geom_name=f"fence_{pi}")
    if len(faces):
        taken = taken_faces(scan, faces, runs) & avail
    solids = collision_boxes(runs)
    area = sum(p.shape.area for p in planes)
    report["fences"] = {
        "runs": len(runs),
        "length_m": round(sum(r.length for r in runs), 1),
        "two_sided": sum(r.meta.get("sides") == 2 for r in runs),
        "panels": len(planes),
        "faces_replaced": int(taken.sum()),
        "collision_triangles": int(len(solids)),
        "megatexels": round(sum(s["mtx"] for s in stats), 2),
        "pages": len(pages),
        "seen": round(sum(p.seen * p.shape.area for p in planes) / max(area, 1e-9), 3),
        "seen_sharp": round(sum(p.sharp * p.shape.area for p in planes) / max(area, 1e-9), 3),
        "board_period_m": round(float(np.median([s["period_m"] for s in stats])), 3) if stats else None,
    }
    return taken, solids
