"""Ground for the procedural block: OSM roads as a road surface 0.15 m below the curb, curb faces,
and the remaining land, all on the DSM-derived ground model. Colour comes from the ortho (TEXCOORD_0);
a tiled CC0 normal map on TEXCOORD_1 gives grain at street level.
"""

from __future__ import annotations

import json

import numpy as np
import shapely
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import unary_union

from worldgen import block_data as bd
from worldgen.block_buildings import polys_of, triangulate
from worldgen.block_glb import MeshBuf

CURB = 0.15
WIDTH = {
    "secondary": 8.2,
    "tertiary": 8.6,
    "residential": 8.6,
    "unclassified": 8.0,
    "service": 6.0,
    "living_street": 7.0,
}
TURN_R = 11.5


def load_roads() -> tuple[list[tuple[LineString, str]], list[Point]]:
    osm = json.loads(bd.fetch_osm().read_text())
    nodes = {e["id"]: e for e in osm["elements"] if e["type"] == "node" and "lat" in e}
    ways, circles = [], []
    for e in osm["elements"]:
        t = e.get("tags", {})
        if e["type"] == "way" and t.get("highway") in WIDTH:
            pts = [bd.to_xz(nodes[n]["lat"], nodes[n]["lon"]) for n in e["nodes"] if n in nodes]
            if len(pts) >= 2:
                ways.append((LineString(pts), t["highway"]))
    for n in nodes.values():
        if n.get("tags", {}).get("highway") in ("turning_circle", "turning_loop"):
            circles.append(Point(bd.to_xz(n["lat"], n["lon"])))
    return ways, circles


def calibrate(ln: LineString, kind: str, ortho) -> tuple[float, float]:
    """Left/right half widths of the pavement around an OSM centreline, from the ortho: the edge is
    where pale concrete gives way to non-pavement, voted over perpendicular profiles every 3 m."""
    w0 = WIDTH[kind] / 2
    if ln.length < 12 or ortho is None:
        return w0, w0
    offs = np.arange(0.0, 9.0, 0.25)
    votes = {1: [], -1: []}
    ds = np.arange(2.0, ln.length - 2.0, 3.0)
    P = np.array([ln.interpolate(d).coords[0] for d in ds])
    Q = np.array([ln.interpolate(d + 0.5).coords[0] for d in ds])
    T = (Q - P) / np.maximum(np.linalg.norm(Q - P, axis=1, keepdims=True), 1e-6)
    N = np.c_[-T[:, 1], T[:, 0]]
    for sgn in (1, -1):
        pts = P[:, None, :] + sgn * offs[None, :, None] * N[:, None, :]
        c = ortho.sample(pts[..., 0].ravel(), pts[..., 1].ravel()).reshape(len(ds), len(offs), 3)
        mx, mn = c.max(-1), c.min(-1)
        sat = (mx - mn) / np.maximum(mx, 1)
        pale = ((c.mean(-1) > 110) & (sat < 0.22)).astype(float)
        for row in pale:
            best, bw = -1.0, None
            for i in range(8, len(offs) - 4):
                sc = row[max(0, i - 6) : i].mean() - row[i : i + 4].mean()
                if sc > best:
                    best, bw = sc, offs[i]
            if best > 0.6:
                votes[sgn].append(bw)
    out = []
    for sgn in (1, -1):
        v = votes[sgn]
        out.append(float(np.clip(np.median(v), w0 - 1.2, w0 + 1.2)) if len(v) >= 3 else w0)
    return out[0], out[1]


def road_polygon(ways, circles, half: float, ortho=None):
    parts = []
    for ln, k in ways:
        wl, wr = (
            calibrate(ln, k, ortho) if k in ("residential", "secondary", "tertiary") else (WIDTH[k] / 2,) * 2
        )
        shift = (wl - wr) / 2
        base = ln.offset_curve(shift) if abs(shift) > 0.05 else ln
        if base.is_empty or base.geom_type != "LineString":
            base = ln
        parts.append(base.buffer((wl + wr) / 2, cap_style=2 if k == "service" else 1, join_style=1))
    parts += [c.buffer(TURN_R) for c in circles]
    # fillet the corners where streets meet: grow then shrink
    road = unary_union(parts).buffer(4.0, join_style=1).buffer(-4.0, join_style=1)
    return road.intersection(box(-half, -half, half, half)).buffer(0)


def gridded(
    buf: MeshBuf, region, ground, half: float, step: float, dy: float, uvf, detail_m: float, colorf=None
) -> None:
    """Triangulate `region` cut into step x step cells, heights from the ground model.
    `colorf(x, z)` (optional) gives per-vertex RGB(A) colours."""
    n = int(round(2 * half / step))
    cs = -half + np.arange(n) * step
    X0, Z0 = np.meshgrid(cs, cs)
    cells = shapely.box(X0.ravel(), Z0.ravel(), X0.ravel() + step, Z0.ravel() + step)
    shapely.prepare(region)
    inside = shapely.contains(region, cells)
    touch = shapely.intersects(region, cells) & ~inside
    allp, allt, cnt = [], [], 0
    # full cells: two triangles each
    full = np.flatnonzero(inside)
    if len(full):
        x0, z0 = X0.ravel()[full], Z0.ravel()[full]
        corners = np.stack(
            [np.c_[x0, z0], np.c_[x0 + step, z0], np.c_[x0 + step, z0 + step], np.c_[x0, z0 + step]], 1
        ).reshape(-1, 2)
        base = np.arange(len(full)) * 4
        tris = np.r_[np.c_[base, base + 1, base + 2], np.c_[base, base + 2, base + 3]]
        allp.append(corners)
        allt.append(tris)
        cnt = len(corners)
    pieces = shapely.intersection(cells[touch], region)
    for g in pieces:
        for p in polys_of(g):
            if p.area < 1e-4:
                continue
            v, t = triangulate(p)
            allp.append(v)
            allt.append(t + cnt)
            cnt += len(v)
    if not allp:
        return
    V = np.concatenate(allp)
    T = np.concatenate(allt)
    Y = ground(V[:, 0], V[:, 1]) + dy
    P = np.c_[V[:, 0], Y, V[:, 1]]
    buf.add(
        P,
        T,
        uv=uvf(V[:, 0], V[:, 1]),
        normals=np.array([0, 1, 0]),
        facing=np.array([0, 1, 0]),
        uv2=V / detail_m,
        color=None if colorf is None else colorf(V[:, 0], V[:, 1]),
    )


def curbs(
    buf: MeshBuf,
    road,
    ground,
    half: float,
    uvf,
    top_buf: MeshBuf | None = None,
    skip=None,
    top_w: float = 0.15,
) -> None:
    """Vertical curb faces along the road outline (facing into the road), and a curb top band
    `top_w` wide. Segments whose middle lies in `skip` (driveway curb cuts) get neither."""
    lim = half - 0.01
    for p in polys_of(road):
        for ring in [p.exterior, *p.interiors]:
            xy = np.asarray(ring.coords)
            # dense resample so the face follows the ground
            ln = LineString(xy)
            n = max(2, int(ln.length / 0.5))
            pts = np.array([ln.interpolate(i / n, normalized=True).coords[0] for i in range(n + 1)])
            g = ground(pts[:, 0], pts[:, 1])
            s = 0.0
            for i in range(len(pts) - 1):
                a, b = pts[i], pts[i + 1]
                L = float(np.hypot(*(b - a)))
                if L < 1e-3 or max(abs(a).max(), abs(b).max()) >= lim:
                    s += L
                    continue
                mid = (a + b) / 2
                if skip is not None and skip.contains(Point(mid)):
                    s += L
                    continue
                t = (b - a) / L
                nrm = np.array([t[1], 0, -t[0]])
                probe = mid + 0.3 * nrm[[0, 2]]
                if not road.contains(Point(probe)):
                    nrm = -nrm
                P = [
                    [a[0], g[i] - CURB - 0.05, a[1]],
                    [b[0], g[i + 1] - CURB - 0.05, b[1]],
                    [b[0], g[i + 1] + 0.04, b[1]],
                    [a[0], g[i] + 0.04, a[1]],
                ]
                buf.quad(*P, uv=np.array([[s, 0], [s + L, 0], [s + L, CURB], [s, CURB]]) / 1.8, facing=nrm)
                # curb top: a 15 cm band hides the seam between the curb face and the land mesh
                back = -top_w * nrm
                T = [
                    P[3],
                    P[2],
                    [b[0] + back[0], g[i + 1] + 0.04, b[1] + back[2]],
                    [a[0] + back[0], g[i] + 0.04, a[1] + back[2]],
                ]
                ta = uvf(np.array([a[0] + back[0]]), np.array([a[1] + back[2]]))[0]
                tb = uvf(np.array([b[0] + back[0]]), np.array([b[1] + back[2]]))[0]
                (top_buf or buf).quad(*T, uv=np.array([ta, tb, tb, ta]), facing=np.array([0, 1, 0]))
                s += L


FENCE_H = 1.83


def fence_lines(parcels: list[dict], buildings, road, streets=None) -> list[LineString]:
    """Backyard privacy fences: side and rear lot lines behind the middle of the house, plus the two
    short returns from the house to the side lot lines. Shared lot lines are merged."""
    from shapely import set_precision
    from shapely.ops import linemerge

    road_zone = road.buffer(7.0)
    homes = [b for b in buildings if b.primary and b.kind != "flat"]
    lines = []
    seen: list = []
    for pc in parcels:
        par = pc["poly"]
        if any(par.intersection(q).area > 0.6 * min(par.area, q.area) for q in seen):
            continue  # the same lot from an older collection
        seen.append(par)
        if par.is_empty or par.area > 2500 or par.area < 150:
            continue
        inside = [b for b in homes if par.contains(b.poly.centroid)]
        if not inside:
            continue
        b = max(inside, key=lambda h: h.poly.area)
        c = b.poly.centroid
        if streets:
            ln = min(streets, key=lambda s_: s_.distance(c))
            near = ln.interpolate(ln.project(c))
        else:
            near = road.boundary.interpolate(road.boundary.project(c))
        d = np.array([near.x - c.x, near.y - c.y])
        if np.linalg.norm(d) < 1e-3:
            continue
        d /= np.linalg.norm(d)
        cut = float(np.array([c.x, c.y]) @ d)
        # half plane behind the house middle (away from the street)
        far = 400.0
        perp = np.array([-d[1], d[0]])
        p0 = np.array([c.x, c.y])
        back = Polygon(
            [p0 + perp * far, p0 - perp * far, p0 - perp * far - d * far, p0 + perp * far - d * far]
        )
        for poly in polys_of(par):
            edge = LineString(np.asarray(poly.exterior.coords))
            keep = edge.intersection(back).difference(road_zone)
            lines.append(keep)
            cross = LineString([p0 + perp * 60, p0 - perp * 60]).intersection(poly)
            ret = cross.difference(b.poly.buffer(0.2))
            lines.append(ret)
        _ = cut
    merged = unary_union([set_precision(g, 0.05) for g in lines if not g.is_empty])
    merged = linemerge(merged) if merged.geom_type in ("MultiLineString", "LineString") else merged
    out = []
    for g in getattr(merged, "geoms", [merged]):
        if g.geom_type == "LineString" and g.length > 1.0:
            out.append(g)
    return out


def fence_mesh(buf: MeshBuf, lines: list[LineString], ground, half: float) -> None:
    lim = half - 0.5
    for ln in lines:
        pts = np.asarray(ln.coords)
        for i in range(len(pts) - 1):
            a, b = pts[i], pts[i + 1]
            L = float(np.hypot(*(b - a)))
            if L < 0.05 or max(abs(a).max(), abs(b).max()) > lim:
                continue
            n_seg = max(1, int(L / 2.5))
            for k in range(n_seg):
                p, q = a + (b - a) * k / n_seg, a + (b - a) * (k + 1) / n_seg
                gp, gq = ground(np.array([p[0], q[0]]), np.array([p[1], q[1]]))
                t = (q - p) / max(np.hypot(*(q - p)), 1e-6)
                nrm = np.array([t[1], 0, -t[0]])
                l0 = L * k / n_seg
                l1 = L * (k + 1) / n_seg
                uv = np.array([[l0, FENCE_H], [l1, FENCE_H], [l1, 0], [l0, 0]]) / 1.8
                P = [
                    [p[0], gp - 0.05, p[1]],
                    [q[0], gq - 0.05, q[1]],
                    [q[0], gq + FENCE_H, q[1]],
                    [p[0], gp + FENCE_H, p[1]],
                ]
                buf.quad(*P, uv=uv, facing=nrm)
                buf.quad(P[1], P[0], P[3], P[2], uv=uv[[1, 0, 3, 2]], facing=-nrm)


def overlay_meshes(ortho, ground, road, built, half: float, step: float = 1.0) -> tuple[MeshBuf, MeshBuf]:
    """Semi-transparent detail layers 3 cm above the ground: a tiled grass grain over lawns and a
    concrete grain over pavement. Vertex colour = local ortho colour (1 m), vertex alpha = how much
    the ortho looks like grass / pavement there. Keeps the photo's colours from above and gives
    street level real texture grain instead of magnified photo pixels."""
    from scipy import ndimage

    n = int(round(2 * half / step))
    xs = -half + np.arange(n + 1) * step
    X, Z = np.meshgrid(xs, xs)
    # colour: average the ortho over each vertex's 1 m neighbourhood
    sub = np.linspace(-0.4, 0.4, 3)
    acc = np.zeros(X.shape + (3,))
    for dx in sub:
        for dz in sub:
            acc += ortho.sample((X + dx).ravel(), (Z + dz).ravel()).reshape(X.shape + (3,))
    col = acc / 9 / 255.0
    mx, mn = col.max(-1), col.min(-1)
    sat = (mx - mn) / np.maximum(mx, 1e-3)
    lum = col.mean(-1)
    green = np.clip((col[..., 1] - np.maximum(col[..., 0], col[..., 2]) + 0.005) / 0.05, 0, 1)
    pale = np.clip((lum - 0.40) / 0.12, 0, 1) * np.clip((0.22 - sat) / 0.08, 0, 1)
    in_road = shapely.contains_xy(road, X, Z)
    in_bld = shapely.contains_xy(built, X, Z)
    green = ndimage.gaussian_filter(green, 1.5)
    pale = ndimage.gaussian_filter(pale, 1.5)
    Y = ground(X.ravel(), Z.ravel()).reshape(X.shape) + 0.03 - np.where(in_road, CURB, 0.0)
    idx = np.arange((n + 1) ** 2).reshape(n + 1, n + 1)
    a, b, c, d = idx[:-1, :-1], idx[:-1, 1:], idx[1:, 1:], idx[1:, :-1]
    quads = np.c_[a.ravel(), b.ravel(), c.ravel(), d.ravel()]
    rflat, bflat = in_road.ravel(), in_bld.ravel()
    same = (rflat[quads].min(1) == rflat[quads].max(1)) & ~bflat[quads].any(1)
    P = np.c_[X.ravel(), Y.ravel(), Z.ravel()]
    rgb = np.clip(col.reshape(-1, 3) / 0.85, 0, 1)
    out = []
    for w, amax in ((green, 0.38), (pale, 0.35)):
        alpha = (w * amax).ravel()
        keep = same & (alpha[quads].max(1) > 0.03)
        q = quads[keep]
        used = np.unique(q)
        remap = np.full(len(P), -1)
        remap[used] = np.arange(len(used))
        tris = np.r_[np.c_[q[:, 0], q[:, 1], q[:, 2]], np.c_[q[:, 0], q[:, 2], q[:, 3]]]
        buf = MeshBuf()
        buf.add(
            P[used],
            remap[tris],
            uv=P[used][:, [0, 2]] / (2.0 if amax > 0.36 else 1.8),
            normals=np.array([0, 1, 0]),
            facing=np.array([0, 1, 0]),
            color=np.c_[rgb[used], alpha[used]],
        )
        out.append(buf)
    return out[0], out[1]
