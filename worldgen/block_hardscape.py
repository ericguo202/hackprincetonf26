"""Hard surfaces for the combined block as vector geometry: roads with one fitted width per street,
continuous curbs and gutters, sidewalks of constant width and setback, driveways from each garage door
to the street with a curb cut, front walks and stoops. The ortho decides where sidewalks exist and how
wide driveways are; edges are always crisp polygons, the ortho only tints the concrete.

Block frame: +X east, +Y up, +Z south, meters. Used by worldgen.block_combined.
"""

from __future__ import annotations

import json
import math

import numpy as np
import shapely
import shapely.ops
from PIL import Image
from scipy import ndimage
from shapely import contains_xy
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import unary_union

from worldgen import block_data as bd
from worldgen.block_buildings import polys_of, triangulate
from worldgen.block_glb import MeshBuf
from worldgen.block_ground import TURN_R, WIDTH, calibrate

SIDEWALK_H = 0.06  # sidewalk top above the lawn
DRIVE_H = 0.03
STOOP_H = 0.15
GUTTER_W = 0.45
JOINT_SIDEWALK = 1.5  # m between sidewalk joints
JOINT_DRIVE = 3.0


# ---------------------------------------------------------------- roads


def load_ways() -> tuple[list[tuple[LineString, str, str | None]], list[Point]]:
    """OSM streets (line, highway kind, name) and turning circles, block frame."""
    osm = json.loads(bd.fetch_osm().read_text())
    nodes = {e["id"]: e for e in osm["elements"] if e["type"] == "node" and "lat" in e}
    ways, circles = [], []
    for e in osm["elements"]:
        t = e.get("tags", {})
        if e["type"] == "way" and t.get("highway") in WIDTH:
            pts = [bd.to_xz(nodes[n]["lat"], nodes[n]["lon"]) for n in e["nodes"] if n in nodes]
            if len(pts) >= 2:
                ways.append((LineString(pts), t["highway"], t.get("name")))
    for n in nodes.values():
        if n.get("tags", {}).get("highway") in ("turning_circle", "turning_loop"):
            circles.append(Point(bd.to_xz(n["lat"], n["lon"])))
    return ways, circles


def fit_roads(ways, circles, half: float, ortho, refit_names=None) -> tuple[Polygon, list[dict]]:
    """Road polygon with one pavement width per named street (median of the ortho-calibrated widths of
    all its ways); each way keeps its own centre shift (OSM centreline offset). Streets in
    `refit_names` are measured station by station instead (refit_street): one mode width per street
    and a re-centred line. Returns the polygon and per-way records {line, width, name, kind}."""
    cal = []
    refit: dict = {}
    for name in sorted({n for _, _, n in ways if n in (refit_names or ())}):
        mine = [(i, ln) for i, (ln, k, n) in enumerate(ways) if n == name and k != "service"]
        W, lines = refit_street(
            [ln for _, ln in mine], ortho, WIDTH.get(mine[0] and ways[mine[0][0]][1], 8.6) / 2
        )
        for (i, _), nl in zip(mine, lines, strict=True):
            refit[i] = (nl, W)
    for i, (ln, k, name) in enumerate(ways):
        if i in refit:
            nl, W = refit[i]
            cal.append((nl, k, name, W / 2, W / 2))
            continue
        if k in ("residential", "secondary", "tertiary"):
            wl, wr = calibrate(ln, k, ortho)
        else:
            wl = wr = WIDTH[k] / 2
        cal.append((ln, k, name, wl, wr))
    by_name: dict[str, list[float]] = {}
    for ln, k, name, wl, wr in cal:
        if name and k != "service":
            by_name.setdefault(name, []).extend([wl + wr] * max(1, int(ln.length // 20)))
    width = {n: float(np.median(v)) for n, v in by_name.items()}
    recs, parts = [], []
    for ln, k, name, wl, wr in cal:
        W = width.get(name, wl + wr) if k != "service" else wl + wr
        shift = (wl - wr) / 2
        base = ln.offset_curve(shift) if abs(shift) > 0.05 else ln
        if base.is_empty or base.geom_type != "LineString":
            base = ln
        recs.append({"line": base, "width": W, "name": name, "kind": k})
        parts.append(base.buffer(W / 2, cap_style=2 if k == "service" else 1, join_style=1))
    parts += [c.buffer(TURN_R) for c in circles]
    road = unary_union(parts).buffer(4.0, join_style=1).buffer(-4.0, join_style=1)
    return road.intersection(box(-half, -half, half, half)).buffer(0), recs


def gutter_band(road, width: float = GUTTER_W):
    """The concrete gutter: a band along the inside of every curb."""
    return road.difference(road.buffer(-width, join_style=2)).buffer(0)


# ---------------------------------------------------------------- sidewalks


def _stations(line: LineString, step: float = 1.0):
    n = int(line.length // step)
    if n < 4:
        return None, None
    P = np.array([line.interpolate(i * step).coords[0] for i in range(n + 1)])
    T = np.gradient(P, axis=0)
    T /= np.maximum(np.linalg.norm(T, axis=1, keepdims=True), 1e-9)
    N = np.c_[-T[:, 1], T[:, 0]]  # left normal (same convention as offset_curve > 0)
    return P, N


def find_sidewalks(
    recs, road, lab_vis, half: float, res: float, zone, names
) -> tuple[list[dict], list[dict]]:
    """Per named street and side: fit one setback (from the curb) and width from the ortho classes
    (concrete band with non-concrete behind it), then mark where it exists (visible evidence over
    +-4 m), bridge gaps hidden by crowns/shadows and short gaps between present stretches, drop runs
    under 8 m. Returns strips [{poly, line, side}] and a report per street side."""
    conc = lab_vis == 1
    vis = lab_vis >= 0
    d = np.arange(0.1, 6.0, 0.2)  # offsets beyond the curb

    def sample(m, Q):
        c = np.clip(((Q[..., 0] + half) / res).astype(int), 0, m.shape[1] - 1)
        r = np.clip(((Q[..., 1] + half) / res).astype(int), 0, m.shape[0] - 1)
        return m[r, c]

    groups: dict[tuple[str, int], list] = {}
    for rec in recs:
        if rec["name"] not in names:
            continue
        P, N = _stations(rec["line"])
        if P is None:
            continue
        for side in (1, -1):
            Q = P[:, None, :] + side * (rec["width"] / 2 + d)[None, :, None] * N[:, None, :]
            C = sample(conc, Q).astype(float)
            V = sample(vis, Q).astype(float)
            inzone = contains_xy(
                zone,
                P[:, 0] + side * N[:, 0] * (rec["width"] / 2 + 2),
                P[:, 1] + side * N[:, 1] * (rec["width"] / 2 + 2),
            )
            groups.setdefault((rec["name"], id(rec), side), []).append((rec, P, N, C, V, inzone))
    strips, report = [], []
    k = np.ones(7)
    for (name, _wid, side), items in groups.items():
        # fit (setback, width) over the whole street side
        best = None
        for i0 in range(2, 20):  # setback 0.4 .. 3.8 m (a band right at the curb is the gutter pan)
            for w in range(5, 9):  # width 1.0 .. 1.6 m
                if i0 + w + 3 >= len(d):
                    continue
                score = 0.0
                for _rec, _P, _N, C, V, inz in items:
                    cs = ndimage.convolve1d(C, k, axis=0, mode="nearest")
                    vs = ndimage.convolve1d(V, k, axis=0, mode="nearest")
                    f = cs / np.maximum(vs, 1e-6)
                    band = f[:, i0 : i0 + w].mean(1)
                    behind = f[:, i0 + w : i0 + w + 3].mean(1)
                    front = f[:, max(0, i0 - 3) : i0].mean(1) if i0 >= 2 else np.zeros(len(band))
                    ok = (band > 0.6) & (behind < 0.35) & (front < 0.5) & inz
                    score += float(ok.sum())
                if best is None or score > best[0]:
                    best = (score, i0, w)
        total = sum(int(it[5].sum()) for it in items)
        score, i0, w = best if best else (0, 0, 6)
        setback, width = float(d[i0] - 0.1), float(w * 0.2)
        present_m = 0.0
        if total == 0 or score < 0.12 * total:
            report.append(
                {
                    "street": name,
                    "side": "left" if side > 0 else "right",
                    "length_m": total,
                    "sidewalk_m": 0,
                    "setback_m": None,
                    "width_m": None,
                    "note": "no consistent concrete band in the ortho",
                }
            )
            continue
        for rec, P, N, C, V, inz in items:
            band_c = C[:, i0 : i0 + w].sum(1)
            band_v = V[:, i0 : i0 + w].sum(1)
            kk = np.ones(9)
            fc = ndimage.convolve1d(band_c, kk, mode="nearest")
            fv = ndimage.convolve1d(band_v, kk, mode="nearest")
            frac = fc / np.maximum(fv, 1e-6)
            hidden = fv < 0.3 * 9 * w
            state = np.where(hidden, 0, np.where(frac > 0.45, 1, -1))  # 1 present, -1 absent, 0 unknown
            # stations whose band runs into another street (intersections): absent
            o1 = rec["width"] / 2 + setback
            mid = P + side * N * (o1 + width / 2)
            state[contains_xy(road, mid[:, 0], mid[:, 1])] = -1
            state[~inz] = -1
            # the median of a divided road: another carriageway within 12 m on this side
            far = P + side * N * (rec["width"] / 2 + 12.0)
            state[contains_xy(road, far[:, 0], far[:, 1])] = -1
            pres = state == 1
            # unknown gaps bridged when present on both sides; short absent gaps (<= 10 m) too
            lab_, nr = ndimage.label(state != 1)
            for j in range(1, nr + 1):
                idx = np.flatnonzero(lab_ == j)
                a, b = idx[0] - 1, idx[-1] + 1
                if a < 0 or b >= len(state) or not (pres[a] and pres[b]):
                    continue
                gap = state[idx]
                if (gap == 0).all() or (len(idx) <= 10 and not (gap == -1).sum() > 6):
                    pres[idx] = ~contains_xy(road, mid[idx, 0], mid[idx, 1])
            runs, nr = ndimage.label(pres)
            for j in range(1, nr + 1):
                idx = np.flatnonzero(runs == j)
                if len(idx) < 8:
                    continue
                a = P[idx] + side * N[idx] * o1
                b = P[idx] + side * N[idx] * (o1 + width)
                poly = Polygon(np.r_[a, b[::-1]]).buffer(0)
                if poly.is_empty:
                    continue
                strips.append({"poly": poly, "line": rec["line"], "side": side})
                present_m += len(idx) - 1
        report.append(
            {
                "street": name,
                "side": "left" if side > 0 else "right",
                "length_m": total,
                "sidewalk_m": round(present_m),
                "setback_m": round(setback, 1),
                "width_m": round(width, 1),
            }
        )
    return strips, report


# ---------------------------------------------------------------- driveways, walks, stoops


def driveway(garage: dict, road, lab_vis, half: float, res: float, max_len: float = 40.0) -> dict | None:
    """Straight concrete driveway from the garage door to the street, width from the ortho (contiguous
    concrete across the driveway 2-6 m out), at least the door width."""
    p, n, w0 = np.asarray(garage["p"], float), np.asarray(garage["n"], float), float(garage["w"])
    ray = LineString([p + n * 0.05, p + n * max_len])
    hit = ray.intersection(road)
    if hit.is_empty:
        return None
    L = float(min(Point(p).distance(g) for g in getattr(hit, "geoms", [hit])))
    if L < 2.0:
        return None
    t = np.array([-n[1], n[0]])
    us = np.arange(2.0, max(2.5, min(L - 1.0, 6.0)), 0.5)
    vs = np.arange(-6.0, 6.01, 0.2)
    U, Vv = np.meshgrid(us, vs, indexing="ij")
    Q = p + U[..., None] * n + Vv[..., None] * t
    c = np.clip(((Q[..., 0] + half) / res).astype(int), 0, lab_vis.shape[1] - 1)
    r = np.clip(((Q[..., 1] + half) / res).astype(int), 0, lab_vis.shape[0] - 1)
    L_ = lab_vis[r, c]
    conc = ((L_ == 1) | (L_ == -2)).mean(0) > 0.5  # concrete (or hidden) across the depth
    j0 = len(vs) // 2
    if not conc[j0]:
        lo = hi = 0.0
    else:
        a = j0
        while a > 0 and conc[a - 1]:
            a -= 1
        b = j0
        while b < len(vs) - 1 and conc[b + 1]:
            b += 1
        lo, hi = vs[a], vs[b]
    lo, hi = min(lo, -w0 / 2 - 0.15), max(hi, w0 / 2 + 0.15)
    lo, hi = max(lo, -4.6), min(hi, 4.6)  # at most a 9.2 m double driveway
    return {
        "p": p,
        "n": n,
        "t": t,
        "L": L,
        "v0": float(lo),
        "v1": float(hi),
        "poly": _rect(p, n, t, 0.0, L, lo, hi),
    }


def _rect(p, n, t, u0, u1, v0, v1) -> Polygon:
    return Polygon([p + n * u0 + t * v0, p + n * u1 + t * v0, p + n * u1 + t * v1, p + n * u0 + t * v1])


def stoop(door: dict) -> dict:
    p, n = np.asarray(door["p"], float), np.asarray(door["n"], float)
    t = np.array([-n[1], n[0]])
    return {"p": p, "n": n, "t": t, "poly": _rect(p, n, t, 0.0, 1.2, -0.8, 0.8)}


def front_walk(door: dict, drive: dict | None, sidewalks, road, lab_vis, half, res) -> LineString | None:
    """Path from the stoop to the driveway (default) or straight to the sidewalk/curb when the ortho
    shows a walk there (or there is no driveway)."""
    p, n = np.asarray(door["p"], float), np.asarray(door["n"], float)
    s = p + n * 1.2
    straight = None
    ray = LineString([s, s + n * 30.0])
    target = unary_union([sidewalks, road]) if not sidewalks.is_empty else road
    hit = ray.intersection(target)
    if not hit.is_empty:
        dmin = min(Point(s).distance(g) for g in getattr(hit, "geoms", [hit]))
        e = s + n * dmin
        q = np.linspace(0.3, 1.0, 12)[:, None] * (e - s) + s
        c = np.clip(((q[:, 0] + half) / res).astype(int), 0, lab_vis.shape[1] - 1)
        r = np.clip(((q[:, 1] + half) / res).astype(int), 0, lab_vis.shape[0] - 1)
        frac = ((lab_vis[r, c] == 1) | (lab_vis[r, c] == 4)).mean()
        straight = (LineString([s, e]), frac)
    if drive is not None and (straight is None or straight[1] < 0.55):
        # out 1 m from the stoop, then the shortest way to the driveway (Houston walks often curve
        # across the lawn to the drive)
        q = s + n * 1.0
        e = np.array(shapely.ops.nearest_points(drive["poly"], Point(q))[0].coords[0])
        if np.linalg.norm(e - q) < 14.0:
            return LineString([s, q, e]) if np.linalg.norm(e - q) > 0.3 else LineString([s, q])
    if straight is not None and (drive is None or straight[1] >= 0.55):
        return straight[0]
    return None


# ---------------------------------------------------------------- meshes


def strip_mesh(top: MeshBuf, edge: MeshBuf, poly, ground, h: float, uvf, colorf, edge_shade: float = 0.72):
    """Flat slab `h` above the ground on `poly` (segmentised so it follows the terrain), with vertical
    edge faces down to just below the ground."""
    for p in polys_of(poly):
        p = p.segmentize(1.0)
        v, tri = triangulate(p)
        if not len(tri):
            continue
        y = ground(v[:, 0], v[:, 1]) + h
        col = colorf(v[:, 0], v[:, 1])
        top.add(
            np.c_[v[:, 0], y, v[:, 1]],
            tri,
            uv=uvf(v[:, 0], v[:, 1]),
            normals=np.array([0, 1, 0]),
            facing=np.array([0, 1, 0]),
            color=col,
        )
        if h <= 0.005:
            continue
        for ring in [p.exterior, *p.interiors]:
            xy = np.asarray(ring.coords)
            g = ground(xy[:, 0], xy[:, 1])
            ec = colorf(xy[:, 0], xy[:, 1]) * edge_shade
            for i in range(len(xy) - 1):
                a, b = xy[i], xy[i + 1]
                L = float(np.hypot(*(b - a)))
                if L < 1e-3:
                    continue
                tt = (b - a) / L
                nrm = np.array([tt[1], 0, -tt[0]])
                mid = (a + b) / 2
                if p.contains(Point(mid + 0.02 * nrm[[0, 2]])):
                    nrm = -nrm
                P = [
                    [a[0], g[i] - 0.03, a[1]],
                    [b[0], g[i + 1] - 0.03, b[1]],
                    [b[0], g[i + 1] + h, b[1]],
                    [a[0], g[i] + h, a[1]],
                ]
                uv = np.array([[0, 0], [L, 0], [L, h], [0, h]])
                cc = np.array([ec[i], ec[i + 1], ec[i + 1], ec[i]])
                edge.quad(*P, uv=uv, color=cc, facing=nrm)


def driveway_mesh(top: MeshBuf, edge: MeshBuf, d: dict, ground, colorf, curb: float, cut=None):
    """Driveway slab on a (u along, v across) grid: DRIVE_H above the lawn, ramping down over the last
    0.9 m to the road surface (the curb cut). Joint texture: u, v in metres / JOINT_DRIVE."""
    p, n, t, L = d["p"], d["n"], d["t"], d["L"]
    us = np.unique(np.r_[np.arange(0.0, L - 0.9, 1.0), L - 0.9, L + 0.05])
    vs = np.linspace(d["v0"], d["v1"], max(2, int((d["v1"] - d["v0"]) / 1.0) + 1))
    U, V = np.meshgrid(us, vs, indexing="ij")
    XZ = p + U[..., None] * n + V[..., None] * t
    g = ground(XZ[..., 0].ravel(), XZ[..., 1].ravel()).reshape(U.shape)
    ramp = np.clip((U - (L - 0.9)) / 0.9, 0, 1)
    Y = g + DRIVE_H - ramp * (DRIVE_H + curb)
    P = np.c_[XZ[..., 0].ravel(), Y.ravel(), XZ[..., 1].ravel()]
    nu, nv = U.shape
    idx = np.arange(nu * nv).reshape(nu, nv)
    a, b, c, e = idx[:-1, :-1], idx[1:, :-1], idx[1:, 1:], idx[:-1, 1:]
    tri = np.r_[np.c_[a.ravel(), b.ravel(), c.ravel()], np.c_[a.ravel(), c.ravel(), e.ravel()]]
    uv = np.c_[U.ravel(), V.ravel()] / JOINT_DRIVE
    col = colorf(P[:, 0], P[:, 2])
    top.add(P, tri, uv=uv, normals=np.array([0, 1, 0]), facing=np.array([0, 1, 0]), color=col)
    # side edges (the slab stands DRIVE_H proud of the lawn)
    for j, sgn in ((0, -1), (nv - 1, 1)):
        nrm = np.r_[t[0] * sgn, 0, t[1] * sgn]
        for i in range(nu - 1):
            a3, b3 = P[idx[i, j]], P[idx[i + 1, j]]
            ga, gb = g[i, j], g[i + 1, j]
            Q = [[a3[0], ga - 0.03, a3[2]], [b3[0], gb - 0.03, b3[2]], b3, a3]
            cc = np.repeat(col[[idx[i, j]]] * 0.72, 4, axis=0)
            edge.quad(*Q, uv=np.zeros((4, 2)), color=cc, facing=nrm)


def joint_texture(
    src, tile_px: int = 1024, grid: bool = False, line_px: int = 5, dark: float = 0.5
) -> Image.Image:
    """Concrete with saw-cut expansion joints at the tile edge (u = 0; also v = 0 if grid)."""
    img = src.convert("RGB").resize((tile_px, tile_px))
    a = np.asarray(img, np.float32)
    m = np.ones(a.shape[:2], np.float32)
    m[:, :line_px] *= dark
    m[:, line_px : line_px + 2] *= 1.08  # a lit lip next to the cut
    if grid:
        m[:line_px, :] *= dark
        m[line_px : line_px + 2, :] *= 1.08
    out = Image.fromarray(np.clip(a * m[..., None], 0, 255).astype(np.uint8))
    return out


def line_uv(line: LineString, tile: float):
    """UV for a sidewalk strip: u = distance along its street centreline / tile (so the joints run
    across the walk), v = distance from the centreline / tile."""

    def f(x, z):
        pts = shapely.points(np.asarray(x, float), np.asarray(z, float))
        return np.c_[shapely.line_locate_point(line, pts), shapely.distance(line, pts)] / tile

    return f


__all__ = [
    "fit_roads",
    "find_sidewalks",
    "driveway",
    "front_walk",
    "gutter_band",
    "joint_texture",
    "line_uv",
    "load_ways",
    "math",
    "stoop",
    "strip_mesh",
    "driveway_mesh",
]


def calibrate_curbs(ln: LineString, ortho, w0: float) -> tuple[float, float, int, int]:
    """Half widths left/right of a centreline from the ortho: per station (every 1 m) the offset where
    pale pavement ends for at least 0.8 m; per side the mode of those offsets (0.1 m bins, smoothed).
    The curb is the one offset many stations agree on; driveways (pavement continues) and tree shadows
    (pavement ends early) scatter around it. Returns (left, right, votes_left, votes_right)."""
    if ln.length < 12:
        return w0, w0, 0, 0
    P, N = _stations(ln, 1.0)
    offs = np.arange(1.5, 8.0, 0.1)
    out, votes = [], []
    for sgn in (1, -1):
        Q = P[:, None, :] + sgn * offs[None, :, None] * N[:, None, :]
        c = ortho.sample(Q[..., 0].ravel(), Q[..., 1].ravel()).reshape(len(P), len(offs), 3)
        mx, mn = c.max(-1), c.min(-1)
        sat = (mx - mn) / np.maximum(mx, 1)
        pale = (c.mean(-1) > 105) & (sat < 0.2)
        notpale = ndimage.uniform_filter1d((~pale).astype(float), 8, axis=1, origin=-3) > 0.85
        inside = ndimage.uniform_filter1d(pale.astype(float), 10, axis=1, origin=4) > 0.8
        edge = notpale & inside
        first = np.where(edge.any(1), edge.argmax(1), -1)
        e = offs[first[first >= 0]]
        if len(e) < 8:
            out.append(w0)
            votes.append(0)
            continue
        h, b = np.histogram(e, bins=np.arange(1.5, 8.05, 0.1))
        hs = ndimage.uniform_filter1d(h.astype(float), 3)
        k = int(np.argmax(hs))
        out.append(float((b[k] + b[k + 1]) / 2))
        votes.append(int(h[max(0, k - 1) : k + 2].sum()))
    return out[0], out[1], votes[0], votes[1]


def curb_edges(ln: LineString, ortho, step: float = 1.0):
    """Per station along the centreline: stations, normals and the offsets (left, right; NaN when
    unclear) where pale pavement ends for at least 0.8 m after at least 1 m of pavement."""
    P, N = _stations(ln, step)
    if P is None:
        return None
    offs = np.arange(1.0, 8.0, 0.1)
    E = []
    for sgn in (1, -1):
        Q = P[:, None, :] + sgn * offs[None, :, None] * N[:, None, :]
        c = ortho.sample(Q[..., 0].ravel(), Q[..., 1].ravel()).reshape(len(P), len(offs), 3)
        mx, mn = c.max(-1), c.min(-1)
        sat = (mx - mn) / np.maximum(mx, 1)
        pale = (c.mean(-1) > 105) & (sat < 0.2)
        notpale = ndimage.uniform_filter1d((~pale).astype(float), 8, axis=1, origin=-3) > 0.85
        inside = ndimage.uniform_filter1d(pale.astype(float), 10, axis=1, origin=4) > 0.8
        edge = notpale & inside
        first = np.where(edge.any(1), edge.argmax(1), -1)
        E.append(np.where(first >= 0, offs[np.maximum(first, 0)], np.nan))
    return P, N, E[0], E[1]


def refit_street(ways_of_street, ortho, w0: float) -> tuple[float, list[LineString]]:
    """One pavement width for a whole street (mode of left+right edge sums where both edges are clean)
    and a re-centred line per way (rolling median of the measured centre offset, +-15 m): OSM
    centrelines drift a metre or two from the real pavement."""
    data = [curb_edges(ln, ortho) for ln in ways_of_street]
    sums = np.concatenate([d[2] + d[3] for d in data if d is not None]) if any(data) else np.array([])
    sums = sums[np.isfinite(sums)]
    if len(sums) >= 20:
        h, b = np.histogram(sums, bins=np.arange(4.0, 16.05, 0.1))
        k = int(np.argmax(ndimage.uniform_filter1d(h.astype(float), 3)))
        W = float((b[k] + b[k + 1]) / 2)
    else:
        W = 2 * w0
    lines = []
    for ln, d in zip(ways_of_street, data, strict=True):
        if d is None:
            lines.append(ln)
            continue
        P, N, el, er = d
        ok = np.isfinite(el) & np.isfinite(er) & (np.abs(el + er - W) < 0.5)
        cen = np.where(ok, (el - er) / 2, np.nan)
        if ok.sum() < 5:
            lines.append(ln)
            continue
        sm = np.array(
            [
                np.nanmedian(cen[max(0, i - 15) : i + 16])
                if np.isfinite(cen[max(0, i - 15) : i + 16]).any()
                else np.nan
                for i in range(len(cen))
            ]
        )
        good = np.isfinite(sm)
        idx = np.arange(len(sm))
        sm = np.interp(idx, idx[good], sm[good])
        sm = ndimage.uniform_filter1d(sm, 9, mode="nearest")
        lines.append(LineString(P + sm[:, None] * N))
    return W, lines


def driveways_from_ortho(
    lab, recs, road, built, lots, half: float, res: float, names, zone=None
) -> list[dict]:
    """Driveways where the ortho shows them: concrete components on house lots that touch the curb,
    regularised to rectangles in the street frame (perpendicular to the street, from the curb to the
    end of the concrete or the house wall). Side-by-side driveways of neighbours are split at gaps."""
    X0 = -half
    conc = lab  # boolean: pale pavement (bright, unsaturated) in the cars-removed ortho
    H, W_ = lab.shape
    xs = X0 + (np.arange(W_) + 0.5) * res
    zs = X0 + (np.arange(H) + 0.5) * res
    lots_m = np.zeros_like(conc)
    area_ = zone.difference(road) if zone is not None else lots  # lots + the right of way (walks)
    b = area_.bounds
    c0, c1 = max(0, int((b[0] - X0) / res)), min(W_, int((b[2] - X0) / res) + 1)
    r0, r1 = max(0, int((b[1] - X0) / res)), min(H, int((b[3] - X0) / res) + 1)
    XX, ZZ = np.meshgrid(xs[c0:c1], zs[r0:r1])
    lots_m[r0:r1, c0:c1] = contains_xy(area_, XX, ZZ)
    comp, n = ndimage.label(conc & lots_m, structure=np.ones((3, 3)))
    streets = [r for r in recs if r["name"] in names and r["kind"] != "service"]
    out = []
    for k, sl in enumerate(ndimage.find_objects(comp), start=1):
        if sl is None:
            continue
        m = comp[sl] == k
        if m.sum() * res * res < 10:
            continue
        rr, cc = np.nonzero(m)
        qx, qz = xs[sl[1]][cc], zs[sl[0]][rr]
        cen = Point(qx.mean(), qz.mean())
        st = min(streets, key=lambda r: r["line"].distance(cen), default=None)
        if st is None or st["line"].distance(cen) > 45:
            continue
        line, Wd = st["line"], st["width"]
        s0 = line.project(cen)
        a, b2 = (
            np.array(line.interpolate(max(0, s0 - 1)).coords[0]),
            np.array(line.interpolate(s0 + 1).coords[0]),
        )
        t = (b2 - a) / max(np.linalg.norm(b2 - a), 1e-9)
        foot = np.array(line.interpolate(s0).coords[0])
        nn = np.array([-t[1], t[0]])
        if (np.array([cen.x, cen.y]) - foot) @ nn < 0:
            nn = -nn
        Q = np.c_[qx, qz] - foot
        u, v = Q @ nn, Q @ t
        curb = Wd / 2
        if (u < curb + 2.2).sum() * res * res < 1.0:
            continue  # not connected to the street: a patio or a side pad
        band = (u > curb + 3.6) & (u < curb + 8.0)  # beyond any sidewalk
        if band.sum() < 20:
            continue
        bins = np.arange(v[band].min() - 0.2, v[band].max() + 0.4, 0.2)
        h, _ = np.histogram(v[band], bins=bins)
        depth = max(1, int(np.ptp(u[band]) / res))
        occ = ndimage.uniform_filter1d(h / depth, 5) > 0.5
        lab1, nr = ndimage.label(occ)
        for j in range(1, nr + 1):
            idx = np.flatnonzero(lab1 == j)
            v0, v1 = bins[idx[0]], bins[idx[-1] + 1]
            if v1 - v0 < 2.4:
                continue
            if v1 - v0 > 9.6:  # two drives merged without a visible gap: split evenly
                parts = int(np.ceil((v1 - v0) / 6.0))
                edges = np.linspace(v0, v1, parts + 1)
                spans = [(edges[i] + 0.15, edges[i + 1] - 0.15) for i in range(parts)]
            else:
                spans = [(v0, v1)]
            for va, vb in spans:
                mid = foot + nn * (curb + 5.0) + t * (va + vb) / 2
                if not lots.contains(Point(mid)):
                    continue  # not on a house lot
                sel = (v >= va) & (v <= vb)
                u_end = float(np.percentile(u[sel], 97)) if sel.sum() > 10 else curb + 6
                u_end = min(u_end, curb + 30)
                # stop at the house
                for uu in np.arange(curb + 1.0, u_end, 0.25):
                    pts = foot + nn * uu + t * np.array([[va + 0.3], [(va + vb) / 2], [vb - 0.3]])
                    if contains_xy(built, pts[:, 0], pts[:, 1]).any():
                        u_end = uu - 0.05
                        break
                if u_end - curb < 2.0:
                    continue
                p = foot + nn * u_end  # garage end, the driveway runs back toward the street
                nd = -nn
                td = np.array([-nd[1], nd[0]])
                # v in the driveway frame: td = -t, so v_d = -v
                d0, d1 = -vb, -va
                L = u_end - curb
                out.append(
                    {
                        "p": p,
                        "n": nd,
                        "t": td,
                        "L": L,
                        "v0": d0,
                        "v1": d1,
                        "poly": _rect(p, nd, td, 0, L, d0, d1),
                        "source": "ortho",
                    }
                )
    return out
