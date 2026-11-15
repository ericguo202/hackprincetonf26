"""Deck and porch railings as flat see-through panels cut from the drone splat.

The MatterPak scans a railing (posts, two rails, balusters a hand apart) as crumpled foil: it is thinner than
the scanner resolves. The drone splat (splat.py) has it as a thin, straight sheet of small Gaussians, seen
from every side. So, where a splat exists:

1. Find rails (`find_rails`): in plan, straight thin lines of Gaussians standing RAIL_BAND above the ground
   (the yard's heightfield, which is the deck's top on a deck), at least RAIL_MIN_LEN long, with nothing
   between RAIL_TALL[0] and RAIL_TALL[1] above them (a fence, a wall, a shrub; a porch roof is higher and
   does not count), not on the house's walls.
2. One panel per rail (`rail_plane`): the Gaussians within RAIL_HALF of the rail's vertical plane, flattened
   onto it (splat.Splat.plane_image): colour, and alpha cut at 0.5, so the gaps between the balusters are
   holes. A panel that comes out nearly solid or nearly empty (cover outside RAIL_COVER), or without a top
   rail along RAIL_TOP_FULL of its length, is not a railing (a planter, a parked car's side, a row of
   shrubs) and is dropped: the scan stays. So is a line whose top is outside RAIL_HEIGHT.
3. The scan faces on the rail are dropped from the picture and from collision; a thin box collides instead.

A railing is one painted colour, so the splat's softer texture costs little here; its straight shape is the
gain. Called by exterior.add_exterior next to fences.replace_fences; nodes `fence_r<N>` (alpha MASK, double
sided). All geometry is Matterport Z-up plan coordinates."""

from __future__ import annotations

import math

import numpy as np
import shapely
import trimesh
from scipy import ndimage
from shapely.geometry import Polygon, box

from worldgen import fences
from worldgen import house_mesh as hm
from worldgen import rebake as rb
from worldgen import splat as sp
from worldgen.panos import shade

RAIL_RES = 96.0  # texels per metre (as fences.FENCE_RES)
RAIL_BAND = (0.45, 1.4)  # m above the ground: where a railing has its top rail and balusters
RAIL_HEIGHT = (
    0.7,
    1.2,
)  # m: a railing's top is this far above the ground (the 98th percentile of its Gaussians)
RAIL_TOP = 0.2  # m: the top rail is looked for in the top this much of the panel
RAIL_TOP_FULL = 0.85  # ... and must cover this share of the panel's length
RAIL_TALL = (1.45, 2.05)  # m above the ground: anything here over the line means it is not a railing
RAIL_MIN_LEN = 1.0  # m
RAIL_CELL = 0.05  # m: plan grid for the line search
RAIL_TOL = 0.08  # m: Gaussians this close to the line belong to it
RAIL_THIN = 0.045  # m: largest weighted spread across the line (a shrub or a planter is thicker)
RAIL_FILL = 0.6  # least share of 10 cm bins along a rail that hold Gaussians
RAIL_GAP = 0.3  # m: holes along a rail shorter than this are bridged
RAIL_HALF = 0.12  # m: half-thickness of the slab flattened onto the panel
RAIL_COVER = (
    0.12,
    0.8,
)  # share of the panel (above 0.15 m) the railing covers: less is noise, more is a wall
RAIL_TAKE = 0.15  # m: scan faces this close to a rail are replaced by its panel
RAIL_PAINT = (0.6, 2.0)  # limits of the gain from the splat's brightness to the photos'
RAIL_HUE = 0.1  # photo samples count when each channel's share of r + g + b is this close to the splat's
RAIL_ALPHA = 0.3  # Gaussians more transparent than this are not counted in the search
MAX_RAILS = 16


def find_rails(splat: sp.Splat, g, walls: Polygon) -> list[fences.Run]:
    """Straight railings on the lot, as fences.Run (level top: the rail's height above the ground at its
    ends). `g` is exterior.Ground (height(x, y), lot)."""
    x0, y0, x1, y1 = g.lot.buffer(0.5).bounds
    p = splat.xyz
    sel = (splat.alpha > RAIL_ALPHA) & (p[:, 0] > x0) & (p[:, 0] < x1) & (p[:, 1] > y0) & (p[:, 1] < y1)
    sel[sel] &= shapely.contains_xy(g.lot.buffer(0.3), p[sel, 0], p[sel, 1])
    sel[sel] &= ~shapely.contains_xy(walls.buffer(0.25, join_style="mitre"), p[sel, 0], p[sel, 1])
    p, a = p[sel].astype(float), splat.alpha[sel].astype(float)
    h = p[:, 2] - g.height(p[:, 0], p[:, 1])
    ok = np.isfinite(h)
    p, a, h = p[ok], a[ok], h[ok]
    nx, ny = int(math.ceil((x1 - x0) / RAIL_CELL)), int(math.ceil((y1 - y0) / RAIL_CELL))
    i = np.clip(((p[:, 0] - x0) / RAIL_CELL).astype(int), 0, nx - 1)
    j = np.clip(((p[:, 1] - y0) / RAIL_CELL).astype(int), 0, ny - 1)
    band = (h >= RAIL_BAND[0]) & (h <= RAIL_BAND[1])
    tall = (h >= RAIL_TALL[0]) & (h <= RAIL_TALL[1])
    wb = np.zeros((nx, ny))
    np.add.at(wb, (i[band], j[band]), a[band])
    wt = np.zeros((nx, ny))
    np.add.at(wt, (i[tall], j[tall]), a[tall])
    under = ndimage.binary_dilation(wt > 1.0, iterations=3)  # 0.15 m around anything tall
    free = band & ~under[i, j]
    pts, wgt, hh = p[free], a[free], h[free]
    left = np.ones(len(pts), bool)
    runs: list[fences.Run] = []
    for _ in range(3 * MAX_RAILS):
        if len(runs) >= MAX_RAILS or left.sum() < 200:
            break
        xy = pts[left, :2]
        th, rho, score = fences._hough_peak(xy, wgt[left], step_deg=1.0)
        if (
            score < RAIL_MIN_LEN / RAIL_CELL * 2.0
        ):  # about two opaque Gaussians per cell over the shortest rail
            break
        nrm = np.array([math.cos(th), math.sin(th)])
        on = np.abs(xy @ nrm - rho) < RAIL_TOL
        idx = np.flatnonzero(left)[on]
        c, u = fences._fit_line(pts[idx, :2], wgt[idx])
        n = np.array([-u[1], u[0]])
        d = (pts[left, :2] - c) @ n
        s = (pts[left, :2] - c) @ u
        on = np.abs(d) < RAIL_TOL
        idx = np.flatnonzero(left)[on]
        s_on, d_on = s[on], d[on]
        s_lo = s_on.min()
        bins = ((s_on - s_lo) / 0.1).astype(int)
        occ = np.bincount(bins, weights=wgt[idx]) > 1.0
        occ = ndimage.binary_closing(np.pad(occ, 3), iterations=int(RAIL_GAP / 0.1))[3:-3]
        lab, k = ndimage.label(occ)
        used = np.zeros(len(idx), bool)
        for q in range(1, k + 1):
            b = np.flatnonzero(lab == q)
            m = (bins >= b[0]) & (bins <= b[-1])
            length = (b[-1] - b[0] + 1) * 0.1
            if length < RAIL_MIN_LEN or m.sum() < 50:
                continue
            fill = float((np.bincount(bins[m] - b[0], weights=wgt[idx][m], minlength=len(b)) > 1.0).mean())
            thin = float(
                np.sqrt(
                    np.average((d_on[m] - np.average(d_on[m], weights=wgt[idx][m])) ** 2, weights=wgt[idx][m])
                )
            )
            used |= m
            if fill < RAIL_FILL or thin > RAIL_THIN:
                continue
            a2 = c + u * (s_lo + b[0] * 0.1)
            ends = np.array([a2, a2 + u * length])
            ground = g.height(ends[:, 0], ends[:, 1])
            if not np.isfinite(ground).all():
                continue
            top = float(np.percentile(hh[idx][m], 98)) + 0.03
            if not RAIL_HEIGHT[0] <= top <= RAIL_HEIGHT[1]:
                continue  # it goes on above the band (a shrub, a trunk, a low fence) or is a kerb
            runs.append(
                fences.Run(
                    a2,
                    u,
                    float(length),
                    np.array([0.0, length]),
                    ground + top,
                    ground,
                    spread=thin,
                    meta={"height_m": round(top, 2), "fill": round(fill, 2)},
                )
            )
        # Whatever lay on this line is done with, railing or not (else the same peak comes back).
        left[idx] = False
    return runs


def rail_plane(splat: sp.Splat, r: fences.Run, scan: rb.Scan | None = None, panos=None) -> rb.Plane | None:
    """The rail's panel with its picture from the splat, or None when what stands there is not a railing
    (RAIL_COVER, RAIL_TOP_FULL). The panel is level: from 2 cm under the lower end's ground to the top.

    With `scan` and `panos` the splat's colours are scaled to the ground photos' brightness (`paint`): a
    railing is one paint colour, the drone's exposure is darker than the panoramas' (7014: maroon against
    the photos' bright red), and the two sources are a few cm apart, too far to take the photos texel by
    texel on 4 cm balusters. So one gain: the ratio of the median brightness over the inside of the solid
    parts, counting only photo samples within RAIL_HUE of the splat's own hue (the others are the lawn seen
    between the balusters; a gain per channel turned the railing orange), limited to RAIL_PAINT."""
    z0 = float(r.ground.min()) - 0.02
    height = float(r.top.max()) - z0
    u3 = np.array([r.u[0], r.u[1], 0.0])
    n3 = np.array([r.n[0], r.n[1], 0.0])
    p = rb.Plane(
        "fence",
        np.array([*r.a, z0]),
        u3,
        np.array([0.0, 0.0, 1.0]),
        n3,
        r.length,
        height,
        box(0, 0, r.length, height),
        carve=False,
    )
    W, H = max(2, math.ceil(p.w * RAIL_RES)), max(2, math.ceil(p.h * RAIL_RES))
    rgb, al = splat.plane_image(p.origin, p.u, p.v, p.n, W / RAIL_RES, H / RAIL_RES, RAIL_RES, band=RAIL_HALF)
    solid = ndimage.binary_opening(al > 0.5, iterations=1)
    body = solid[: max(1, H - int(0.17 * RAIL_RES))]  # above the deck's edge board
    cover = float(body.mean())
    # The top rail: the fullest texel row in the top RAIL_TOP of the panel.
    rail = float(solid[: max(1, int(RAIL_TOP * RAIL_RES))].mean(1).max())
    r.meta.update(cover=round(cover, 2), top_rail=round(rail, 2))
    if not RAIL_COVER[0] <= cover <= RAIL_COVER[1] or rail < RAIL_TOP_FULL:
        return None
    gain = 1.0
    if scan is not None and panos is not None:
        gain = paint(p, rgb, solid, scan, panos)
        r.meta["paint_gain"] = round(gain, 2)
    img = rb._fill(np.clip(rgb * gain, 0, 255).astype(np.uint8), solid)
    p.image = np.dstack([img, np.where(solid, 255, 0).astype(np.uint8)])
    return p


def paint(p: rb.Plane, rgb: np.ndarray, solid: np.ndarray, scan: rb.Scan, panos) -> float:
    """The gain taking the splat's brightness of a railing to the ground photos' (see rail_plane)."""
    core = ndimage.binary_erosion(solid, iterations=2)
    jj, ii = np.nonzero(core)
    if len(ii) < 200:
        return 1.0
    pts = p.origin + ((ii + 0.5) / RAIL_RES)[:, None] * p.u + (p.h - (jj + 0.5) / RAIL_RES)[:, None] * p.v
    mine = rgb[jj, ii]
    photo, own = [], []
    for side in (1.0, -1.0):  # a railing is seen from both sides
        col, ok = shade(pts, np.tile(p.n * side, (len(pts), 1)), panos, scan.eyes, max_dist=15.0)
        photo.append(col[ok])
        own.append(mine[ok])
    photo, own = np.concatenate(photo), np.concatenate(own)
    # Only photo samples of the railing's own hue count: the rest is the lawn seen between the balusters.
    hue = np.median(own / np.maximum(own.sum(1, keepdims=True), 1.0), axis=0)
    same = np.abs(photo / np.maximum(photo.sum(1, keepdims=True), 1.0) - hue).max(1) < RAIL_HUE
    if same.sum() < 200:
        return 1.0
    k = np.median(photo[same].sum(1)) / max(float(np.median(own[same].sum(1))), 1.0)
    return float(np.clip(k, *RAIL_PAINT))


def replace_railings(
    scene: trimesh.Scene,
    scan: rb.Scan,
    g,
    faces: np.ndarray,
    tree: np.ndarray,
    avail: np.ndarray,
    panos,
    walls: Polygon,
    report: dict,
    splat: sp.Splat | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Replace scanned railings by panels from the splat (nodes `fence_r<N>`); the same contract as
    fences.replace_fences. Without a splat nothing is replaced."""
    taken = np.zeros(len(faces), bool)
    if splat is None:
        return taken, np.zeros((0, 3, 3))
    runs, planes = [], []
    found = find_rails(splat, g, walls)
    for r in found:
        p = rail_plane(splat, r, scan, panos)
        if p is not None:
            runs.append(r)
            planes.append(p)
    pages = rb.pack(planes) if planes else []
    for pi, page in enumerate(pages):
        parts = [m for m in (rb.plane_mesh(p) for p in planes if p.page == pi) if m is not None]
        if not parts:
            continue
        m = trimesh.util.concatenate(parts)
        used = max(p.rect[1] + p.rect[3] for p in planes if p.page == pi)
        rows = next(h for h in (rb.PAGE // 8, rb.PAGE // 4, rb.PAGE // 2, rb.PAGE) if used <= h)
        uv = np.asarray(m.visual.uv, float).copy()
        uv[:, 1] = 1.0 - (1.0 - uv[:, 1]) * rb.PAGE / rows
        m.visual = trimesh.visual.TextureVisuals(uv=uv, material=rb._material(page[:rows], f"fence_r{pi}"))
        m.apply_transform(hm.Z_UP_TO_Y_UP)
        scene.add_geometry(m, node_name=f"fence_r{pi}", geom_name=f"fence_r{pi}")
    if len(faces) and runs:
        c = scan.tris[faces].mean(1)
        hit = fences.on_runs(c, runs, tol=RAIL_TAKE)
        hit &= c[:, 2] > g.height(c[:, 0], c[:, 1]) + 0.1  # the deck's own boards stay
        taken = hit & avail
    report["railings"] = {
        "found": len(found),
        "built": len(runs),
        "length_m": round(sum(r.length for r in runs), 1),
        "rails": [
            {"at": [round(float(v), 2) for v in r.a], "length_m": round(r.length, 2), **r.meta} for r in found
        ],
        "faces_replaced": int(taken.sum()),
    }
    return taken, fences.collision_boxes(runs)
