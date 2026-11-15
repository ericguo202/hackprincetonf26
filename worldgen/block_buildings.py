"""Buildings for the procedural block: footprint -> union of rectangles -> walls + hip/gable roofs
fitted to the Vexcel DSM.

Vexcel footprints trace the roof outline (eave edge), so walls are inset by the overhang and the
roof edge sits on the footprint. A rectilinear footprint is covered by a few overlapping maximal
rectangles; each gets its own hip (or gable) roof with one shared pitch and eave height, and their
union (what the depth buffer shows) is a roof with real valleys and lower wings.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import mapbox_earcut as earcut
import numpy as np
from shapely import contains_xy
from shapely.geometry import Polygon

OVERHANG = 0.45
FASCIA = 0.18


@dataclass
class Rect:
    u0: float
    u1: float
    v0: float
    v1: float

    @property
    def w(self) -> float:
        return self.u1 - self.u0

    @property
    def d(self) -> float:
        return self.v1 - self.v0


@dataclass
class Building:
    poly: Polygon  # block frame (x, z), roof outline
    primary: bool
    theta: float = 0.0
    origin: tuple[float, float] = (0.0, 0.0)
    rects: list[Rect] = field(default_factory=list)
    kind: str = "hip"  # hip | gable | flat
    eave: float = 2.8  # height of the roof edge above ground (at the outline)
    pitch: float = 0.45  # tan(slope)
    flat_h: float = 3.0
    ground: float = 0.0
    fit_rms: float | None = None
    name: str = ""
    tag: str | None = None

    def to_local(self, x: np.ndarray, z: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        c, s = math.cos(self.theta), math.sin(self.theta)
        dx, dz = np.asarray(x) - self.origin[0], np.asarray(z) - self.origin[1]
        return c * dx + s * dz, -s * dx + c * dz

    def to_world(self, u: np.ndarray, v: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        c, s = math.cos(self.theta), math.sin(self.theta)
        u, v = np.asarray(u), np.asarray(v)
        return self.origin[0] + c * u - s * v, self.origin[1] + s * u + c * v

    def ridge_h(self) -> float:
        if self.kind == "flat" or not self.rects:
            return self.flat_h
        return self.eave + self.pitch * max(min(r.w, r.d) for r in self.rects) / 2


def dominant_angle(poly: Polygon) -> float:
    xy = np.asarray(poly.exterior.coords)
    e = np.diff(xy, axis=0)
    ln = np.hypot(e[:, 0], e[:, 1])
    a = np.arctan2(e[:, 1], e[:, 0])
    return 0.25 * math.atan2((ln * np.sin(4 * a)).sum(), (ln * np.cos(4 * a)).sum())


def _cluster(vals: np.ndarray, tol: float) -> list[float]:
    vals = np.sort(vals)
    out: list[list[float]] = []
    for v in vals:
        if out and v - out[-1][-1] < tol:
            out[-1].append(v)
        else:
            out.append([v])
    return [float(np.mean(g)) for g in out]


def decompose(b: Building) -> float:
    """Fill b.rects with a small cover of maximal rectangles; return IoU of the cover vs footprint."""
    b.theta = dominant_angle(b.poly)
    c = b.poly.centroid
    b.origin = (c.x, c.y)
    xy = np.asarray(b.poly.exterior.coords)
    u, v = b.to_local(xy[:, 0], xy[:, 1])
    lp = Polygon(np.c_[u, v]).buffer(0)
    us, vs = _cluster(u, 0.8), _cluster(v, 0.8)
    if len(us) < 2 or len(vs) < 2 or len(us) > 14 or len(vs) > 14:
        return 0.0
    nu, nv = len(us) - 1, len(vs) - 1
    cu = (np.array(us[:-1]) + np.array(us[1:])) / 2
    cv = (np.array(vs[:-1]) + np.array(vs[1:])) / 2
    CU, CV = np.meshgrid(cu, cv, indexing="ij")
    inside = contains_xy(lp, CU, CV)
    if not inside.any():
        return 0.0
    cand = []
    for i0 in range(nu):
        for i1 in range(i0, nu):
            for j0 in range(nv):
                for j1 in range(j0, nv):
                    if inside[i0 : i1 + 1, j0 : j1 + 1].all():
                        cand.append((i0, i1, j0, j1))
    maximal = [
        r
        for r in cand
        if not any(o != r and o[0] <= r[0] and o[1] >= r[1] and o[2] <= r[2] and o[3] >= r[3] for o in cand)
    ]
    covered = np.zeros_like(inside)
    chosen = []
    while (inside & ~covered).any():
        best, gain = None, 0
        for r in maximal:
            g = (inside[r[0] : r[1] + 1, r[2] : r[3] + 1] & ~covered[r[0] : r[1] + 1, r[2] : r[3] + 1]).sum()
            area = (us[r[1] + 1] - us[r[0]]) * (vs[r[3] + 1] - vs[r[2]])
            score = g + 1e-3 * area
            if g > 0 and score > gain:
                best, gain = r, score
        if best is None:
            break
        chosen.append(best)
        covered[best[0] : best[1] + 1, best[2] : best[3] + 1] = True
    b.rects = [Rect(us[r[0]], us[r[1] + 1], vs[r[2]], vs[r[3] + 1]) for r in chosen]
    # drop slivers that a neighbour almost covers
    b.rects = [r for r in b.rects if min(r.w, r.d) >= 1.0] or b.rects
    from shapely.geometry import box
    from shapely.ops import unary_union

    cover = unary_union([box(r.u0, r.v0, r.u1, r.v1) for r in b.rects])
    return cover.intersection(lp).area / max(cover.union(lp).area, 1e-6)


def roof_dist(b: Building, u: np.ndarray, v: np.ndarray, kind: str) -> np.ndarray:
    d = np.full(u.shape, -np.inf)
    for r in b.rects:
        if kind == "gable":
            dd = np.minimum(v - r.v0, r.v1 - v) if r.w >= r.d else np.minimum(u - r.u0, r.u1 - u)
        else:
            dd = np.minimum.reduce([u - r.u0, r.u1 - u, v - r.v0, r.v1 - v])
        ins = (u >= r.u0) & (u <= r.u1) & (v >= r.v0) & (v <= r.v1)
        d = np.where(ins, np.maximum(d, dd), d)
    return d


def fit_roof(b: Building, X: np.ndarray, Z: np.ndarray, ND: np.ndarray, shape_hint: str | None) -> None:
    """X, Z, ND: flat arrays of nDSM samples (height above ground) inside the footprint."""
    if len(ND) < 20:
        b.kind, b.flat_h = "flat", 3.0
        return
    if b.kind == "flat":
        b.flat_h = float(np.clip(np.percentile(ND, 60), 2.5, 25))
        return
    u, v = b.to_local(X, Z)
    best = None
    kinds = ["hip", "gable"]
    for kind in kinds:
        d = roof_dist(b, u, v, kind)
        ok = np.isfinite(d) & (d > 0.25)
        dd, hh = d[ok], ND[ok]
        if len(dd) < 15:
            continue
        keep = np.ones(len(dd), bool)
        for _ in range(4):
            A = np.c_[np.ones(keep.sum()), dd[keep]]
            (e, t), *_ = np.linalg.lstsq(A, hh[keep], rcond=None)
            res = hh - (e + t * dd)
            keep = np.abs(res) < max(0.5, 2.0 * np.median(np.abs(res[keep])))
        rms = float(np.sqrt(np.mean(res[keep] ** 2)))
        if best is None or rms < best[0]:
            best = (rms, kind, e, t)
    if best is None:
        b.kind, b.flat_h = "flat", float(np.clip(np.percentile(ND, 60), 2.5, 25))
        return
    rms, kind, e, t = best
    if shape_hint in ("hip", "gable"):
        # Vexcel's roof-shape classification wins unless the DSM strongly disagrees
        for r_ in [k for k in kinds if k == shape_hint]:
            kind = r_
    half = max(min(r.w, r.d) for r in b.rects) / 2
    # trees over a roof push high percentiles up; cap the ridge with the robust plane fit
    ridge = float(min(np.percentile(ND, 99), e + t * half + 0.4))
    t = float(np.clip(t, 0.25, 0.75))
    # storey prior: ridge from the DSM is reliable, the eave is not (canopy, blur); Houston tract
    # homes have ~2.7 m (one storey) or ~5.4 m (two storeys) to the roof edge
    # two storeys only if most of the roof is high (a few tall samples are trees, often bare in winter)
    two = ridge >= 6.8 and float((ND > 5.8).mean()) > 0.4
    if not two:
        ridge = min(ridge, 6.0)
    lo, hi = (5.2, 6.0) if two else (2.65, 3.0)
    e = float(np.clip(ridge - t * half, lo, hi))
    t = float(np.clip((ridge - e) / max(half, 1.0), 0.22, 0.75))
    b.kind, b.eave, b.pitch, b.fit_rms = kind, e, t, rms


# ---------------- meshes ----------------


def triangulate(poly: Polygon) -> tuple[np.ndarray, np.ndarray]:
    """Polygon (with holes) -> (N, 2) vertices, (M, 3) triangles."""
    rings = [np.asarray(poly.exterior.coords)[:-1]] + [np.asarray(i.coords)[:-1] for i in poly.interiors]
    verts = np.concatenate(rings)
    ends = np.cumsum([len(r) for r in rings]).astype(np.uint32)
    tri = earcut.triangulate_float64(verts.astype(np.float64), ends)
    return verts, np.asarray(tri, np.int64).reshape(-1, 3)


def polys_of(g) -> list[Polygon]:
    if g.is_empty:
        return []
    if g.geom_type == "Polygon":
        return [g]
    return [p for p in getattr(g, "geoms", []) if p.geom_type == "Polygon"]


def wall_ring_mesh(
    buf, ring: np.ndarray, y0: float, y1: float, outward_ccw: bool, tex_m: float, color=None
) -> None:
    """Vertical walls along a closed ring (x, z) from y0 to y1. UV in meters / tex_m."""
    pts = ring[:-1] if np.allclose(ring[0], ring[-1]) else ring
    n = len(pts)
    s = 0.0
    for i in range(n):
        a, b2 = pts[i], pts[(i + 1) % n]
        L = float(np.hypot(*(b2 - a)))
        if L < 1e-3:
            continue
        # outward normal in the xz plane
        t = (b2 - a) / L
        nrm = np.array([t[1], 0.0, -t[0]]) if outward_ccw else np.array([-t[1], 0.0, t[0]])
        uv = np.array([[s, y0], [s + L, y0], [s + L, y1], [s, y1]]) / tex_m
        uv[:, 1] = -uv[:, 1]
        buf.quad(
            [a[0], y0, a[1]],
            [b2[0], y0, b2[1]],
            [b2[0], y1, b2[1]],
            [a[0], y1, a[1]],
            uv=uv,
            color=color,
            facing=nrm,
        )
        s += L


def ring_is_ccw_xz(ring: np.ndarray) -> bool:
    """Signed area in the (x, z) plane; 'ccw' here means positive shoelace in x-z coordinates."""
    x, z = ring[:, 0], ring[:, 1]
    return float(np.sum(x[:-1] * z[1:] - x[1:] * z[:-1])) > 0


def outer_walls(buf, poly: Polygon, y0: float, y1: float, tex_m: float, color=None) -> None:
    ext = np.asarray(poly.exterior.coords)
    # For a ring with positive x-z shoelace, the outward normal of edge t is (t_z, -t_x) ... check:
    # positive shoelace in (x, z) means counter-clockwise when x right and z up; outward = (t_z, -t_x).
    wall_ring_mesh(buf, ext, y0, y1, ring_is_ccw_xz(ext), tex_m, color)


def roof_mesh(buf, b: Building, uvf, y_base: float) -> None:
    """Hip/gable roof per rectangle, plus gable-end triangles (returned in buf as roof too)."""
    for r in b.rects:
        e = y_base + b.eave
        if b.kind == "gable":
            along_u = r.w >= r.d
            half = (r.d if along_u else r.w) / 2
            hr = e + b.pitch * half
            if along_u:
                vm = (r.v0 + r.v1) / 2
                P = [
                    (r.u0, r.v0, e),
                    (r.u1, r.v0, e),
                    (r.u1, vm, hr),
                    (r.u0, vm, hr),
                    (r.u0, r.v1, e),
                    (r.u1, r.v1, e),
                ]
                faces = [[0, 1, 2, 3], [4, 5, 2, 3]]
            else:
                um = (r.u0 + r.u1) / 2
                P = [
                    (r.u0, r.v0, e),
                    (r.u0, r.v1, e),
                    (um, r.v1, hr),
                    (um, r.v0, hr),
                    (r.u1, r.v0, e),
                    (r.u1, r.v1, e),
                ]
                faces = [[0, 1, 2, 3], [4, 5, 2, 3]]
        else:
            if r.w >= r.d:
                half = r.d / 2
                hr = e + b.pitch * half
                vm = (r.v0 + r.v1) / 2
                P = [
                    (r.u0, r.v0, e),
                    (r.u1, r.v0, e),
                    (r.u1, r.v1, e),
                    (r.u0, r.v1, e),
                    (r.u0 + half, vm, hr),
                    (r.u1 - half, vm, hr),
                ]
                faces = [[0, 1, 5, 4], [2, 3, 4, 5], [3, 0, 4], [1, 2, 5]]
            else:
                half = r.w / 2
                hr = e + b.pitch * half
                um = (r.u0 + r.u1) / 2
                P = [
                    (r.u0, r.v0, e),
                    (r.u1, r.v0, e),
                    (r.u1, r.v1, e),
                    (r.u0, r.v1, e),
                    (um, r.v0 + half, hr),
                    (um, r.v1 - half, hr),
                ]
                faces = [[0, 1, 4], [1, 2, 5, 4], [2, 3, 5], [3, 0, 4, 5]]
        P = np.array(P)
        x, z = b.to_world(P[:, 0], P[:, 1])
        W3 = np.c_[x, P[:, 2], z]
        for f in faces:
            pts = W3[f]
            tri = [[0, 1, 2]] if len(f) == 3 else [[0, 1, 2], [0, 2, 3]]
            n = np.cross(pts[1] - pts[0], pts[2] - pts[0])
            if n[1] < 0:
                n = -n
            n = n / max(np.linalg.norm(n), 1e-9)
            buf.add(pts, tri, uv=uvf(pts[:, 0], pts[:, 2]), normals=n, facing=n)


def gable_ends(buf, b: Building, y_base: float, tex_m: float, color=None) -> None:
    if b.kind != "gable":
        return
    inset = OVERHANG * 0.6
    for r in b.rects:
        e = y_base + b.eave
        along_u = r.w >= r.d
        if along_u:
            half = r.d / 2
            hr = e + b.pitch * (half - inset)
            vm = (r.v0 + r.v1) / 2
            for uu, sgn in ((r.u0 + inset, -1), (r.u1 - inset, 1)):
                P = np.array([(uu, r.v0 + inset, e - FASCIA), (uu, r.v1 - inset, e - FASCIA), (uu, vm, hr)])
                x, z = b.to_world(P[:, 0], P[:, 1])
                nx, nz = b.to_world(np.array([sgn]), np.array([0.0]))
                nrm = np.array([nx[0] - b.origin[0], 0, nz[0] - b.origin[1]])
                pts = np.c_[x, P[:, 2], z]
                uv = np.c_[P[:, 1] / tex_m, -P[:, 2] / tex_m]
                buf.add(pts, [[0, 1, 2]], uv=uv, normals=nrm, facing=nrm, color=color)
        else:
            half = r.w / 2
            hr = e + b.pitch * (half - inset)
            um = (r.u0 + r.u1) / 2
            for vv, sgn in ((r.v0 + inset, -1), (r.v1 - inset, 1)):
                P = np.array([(r.u0 + inset, vv, e - FASCIA), (r.u1 - inset, vv, e - FASCIA), (um, vv, hr)])
                x, z = b.to_world(P[:, 0], P[:, 1])
                nx, nz = b.to_world(np.array([0.0]), np.array([sgn]))
                nrm = np.array([nx[0] - b.origin[0], 0, nz[0] - b.origin[1]])
                pts = np.c_[x, P[:, 2], z]
                uv = np.c_[P[:, 0] / tex_m, -P[:, 2] / tex_m]
                buf.add(pts, [[0, 1, 2]], uv=uv, normals=nrm, facing=nrm, color=color)


def flat_roof(buf, poly: Polygon, y: float, uvf) -> None:
    v, t = triangulate(poly)
    pts = np.c_[v[:, 0], np.full(len(v), y), v[:, 1]]
    buf.add(pts, t, uv=uvf(v[:, 0], v[:, 1]), normals=np.array([0, 1, 0]), facing=np.array([0, 1, 0]))


def horizontal(buf, poly: Polygon, y: float, up: bool, uv_scale: float | None = None, color=None) -> None:
    for p in polys_of(poly):
        v, t = triangulate(p)
        pts = np.c_[v[:, 0], np.full(len(v), y), v[:, 1]]
        n = np.array([0, 1 if up else -1, 0])
        uv = v / uv_scale if uv_scale else None
        buf.add(pts, t, uv=uv, normals=n, facing=n, color=color)
