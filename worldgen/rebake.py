"""Game-ready interior from a Matterport scan: clean geometry, textured from the original panoramas.

Why: the scan mesh's own texture is Matterport's low-quality "dollhouse" skin and its geometry is lumpy.
Matterport's viewer looks good because it shows the panoramas. So we rebuild:

1. Shell: flat walls and floors (and flat ceilings) from the floor-plan vectors, the same rooms as rooms.json
   and collision.glb. A ceiling that the scan says is not flat (vaulted) is not replaced.
2. Pieces: scan geometry that is not shell (counters, fireplace, bathroom fixtures, door jambs, window
   reveals, vaulted ceilings), kept as triangles.
3. Texture: every texel of both is coloured by projecting the panoramas that see it (panos.shade).
4. Carving: a wall texel is cut out (alpha 0) only on positive evidence: no scanned surface at the wall
   there AND a camera sees through that spot AND there is a room behind it. Doorways and pass-throughs
   appear where they really are; unscanned patches (behind a fridge) stay solid.

Geometry is in Matterport Z-up plan coordinates; house_mesh.Z_UP_TO_Y_UP is applied on export."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import shapely
import trimesh
from PIL import Image
from scipy import ndimage
from shapely.geometry import LineString, Point, Polygon, box
from shapely.geometry.polygon import orient
from shapely.ops import unary_union

from worldgen import house_mesh as hm
from worldgen.panos import SNAP, PanoSet, Raycaster, shade

WALL_INSET = 0.05  # wall surface sits about half a wall thickness inside the centreline polygon
SHELL_TOL = 0.08  # scan faces this close to (and parallel to) a shell plane are replaced by it
PAGE = 2048
PAD = 4


# ---------------------------------------------------------------- scan


@dataclass
class Scan:
    tris: np.ndarray  # (n, 3, 3) Z-up
    uvs: np.ndarray  # (n, 3, 2) OBJ convention (v up)
    tex: np.ndarray  # (n,) texture index
    normals: np.ndarray  # (n, 3)
    areas: np.ndarray  # (n,)
    images: list[np.ndarray]  # RGB uint8
    rays: Raycaster


def load_scan(obj_path: Path) -> Scan:
    """The MatterPak's textured OBJ as a triangle soup plus a ray-caster."""
    loaded = trimesh.load(obj_path, process=False, maintain_order=True)
    geoms = list(loaded.geometry.values()) if isinstance(loaded, trimesh.Scene) else [loaded]
    tris, uvs, tex, images = [], [], [], []
    for i, g in enumerate(geoms):
        img = getattr(g.visual.material, "image", None)
        if img is None:
            raise RuntimeError(f"{obj_path.name}: geometry {i} has no texture (is Pillow installed?)")
        images.append(np.asarray(img.convert("RGB")))
        tris.append(g.vertices[g.faces])
        uvs.append(g.visual.uv[g.faces])
        tex.append(np.full(len(g.faces), i))
    t = np.concatenate(tris)
    cross = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
    norm = np.linalg.norm(cross, axis=1)
    return Scan(
        tris=t,
        uvs=np.concatenate(uvs),
        tex=np.concatenate(tex),
        normals=cross / np.maximum(norm, 1e-12)[:, None],
        areas=norm / 2,
        images=images,
        rays=Raycaster(t),
    )


def scan_colors(scan: Scan, face: np.ndarray, bary: np.ndarray) -> np.ndarray:
    """The scan's own texture colour at barycentric points of its faces (fallback where no pano sees)."""
    uv = np.einsum("ij,ijk->ik", bary, scan.uvs[face])
    out = np.zeros((len(face), 3))
    for t in np.unique(scan.tex[face]):
        s = scan.tex[face] == t
        img = scan.images[t]
        h, w = img.shape[:2]
        px = np.clip((uv[s, 0] * w).astype(int), 0, w - 1)
        py = np.clip(((1 - uv[s, 1]) * h).astype(int), 0, h - 1)
        out[s] = img[py, px]
    return out


# ---------------------------------------------------------------- rooms and shell planes


@dataclass
class ShellRoom:
    poly: Polygon  # centreline polygon (plan)
    floor_z: float
    height: float
    wall_lines: list[tuple[LineString, float]]  # (centreline, thickness) of this room's wall edges
    flat_ceiling: bool = True
    wall_top: float | None = None  # walls reach this z when the ceiling is not flat


@dataclass
class Plane:
    kind: str  # wall | floor | ceiling
    origin: np.ndarray  # (3,)
    u: np.ndarray  # texture x axis (unit)
    v: np.ndarray  # texture y axis (unit)
    n: np.ndarray  # normal, pointing into the room
    w: float
    h: float
    shape: Polygon  # region of the plane to build, in (s, t) metres
    image: np.ndarray | None = None  # RGBA
    rect: tuple[int, int, int, int] | None = None  # x, y, w, h of the padded image in its page
    page: int = 0
    carve: bool = True  # walls only: may be cut out where a camera sees through


def measure_ceiling(scan: Scan, room: ShellRoom) -> None:
    """Decide from the scan whether the room's ceiling is flat at floor_z + height; if not (vaulted, sloped),
    keep the scanned ceiling and let the walls reach its highest point."""
    c = scan.tris.mean(1)
    sel = (scan.normals[:, 2] < -0.5) & (c[:, 2] > room.floor_z + 1.8) & (c[:, 2] < room.floor_z + 6.0)
    sel &= shapely.contains_xy(room.poly.buffer(-0.15), c[:, 0], c[:, 1])
    if scan.areas[sel].sum() < 0.3 * room.poly.area:
        return  # ceiling barely scanned: assume flat
    z, a = c[sel, 2], scan.areas[sel]
    flat = a[np.abs(z - (room.floor_z + room.height)) < 0.10].sum() / a.sum()
    if flat < 0.8:
        room.flat_ceiling = False
        order = np.argsort(z)
        top = z[order][min(len(z) - 1, np.searchsorted(np.cumsum(a[order]), 0.995 * a.sum()))]
        room.wall_top = float(top) + 0.05


def _intervals(seg: LineString, geom) -> list[tuple[float, float]]:
    """Parameter intervals (metres from seg start) of seg covered by geom."""
    inter = seg.intersection(geom)
    parts = [inter] if inter.geom_type == "LineString" else list(getattr(inter, "geoms", []))
    out = []
    for p in parts:
        if p.geom_type != "LineString" or p.length < 0.02:
            continue
        a, b = seg.project(Point(p.coords[0])), seg.project(Point(p.coords[-1]))
        out.append((min(a, b), max(a, b)))
    return sorted(out)


def _inset(poly: Polygon) -> Polygon | None:
    inset = poly.buffer(-WALL_INSET, join_style="mitre")
    if inset.is_empty:
        return None
    if inset.geom_type == "MultiPolygon":
        inset = max(inset.geoms, key=lambda p: p.area)
    return orient(inset, 1.0)  # counter-clockwise: the room is on the left of each segment


def room_top(r: ShellRoom) -> float:
    return r.wall_top if r.wall_top else r.floor_z + r.height


def wall_runs(r: ShellRoom) -> list[tuple[np.ndarray, np.ndarray, float, list[tuple[float, float]]]]:
    """The room's inset outline as (start, end, length, covered) per segment, where covered lists the
    stretches (metres from start) that have a wall. A segment with nothing covered is an open passage
    (invisible edge or a room-split line)."""
    inset = _inset(r.poly)
    if inset is None:
        return []
    walls = unary_union([ln.buffer(t / 2 + WALL_INSET + 0.04, cap_style="flat") for ln, t in r.wall_lines])
    ring = list(inset.exterior.coords)
    out = []
    for p0, p1 in zip(ring[:-1], ring[1:], strict=True):
        seg = LineString([p0, p1])
        length = seg.length
        if length < 0.05:
            continue
        covered = _intervals(seg, walls)
        if covered:
            merged = [list(covered[0])]
            for a, b in covered[1:]:  # close slivers between wall pieces (they show as cracks at corners)
                if a - merged[-1][1] < 0.3:
                    merged[-1][1] = max(merged[-1][1], b)
                else:
                    merged.append([a, b])
            merged[0][0] = 0.0 if merged[0][0] < 0.25 else merged[0][0]
            merged[-1][1] = length if length - merged[-1][1] < 0.25 else merged[-1][1]
            covered = [(a, b) for a, b in merged]
        out.append((np.asarray(p0, float), np.asarray(p1, float), length, covered))
    return out


def shell_planes(rooms: list[ShellRoom]) -> list[Plane]:
    planes: list[Plane] = []
    ex, ey, ez = np.eye(3)
    for r in rooms:
        minx, miny, maxx, maxy = r.poly.bounds
        flat = Polygon([(x - minx, y - miny) for x, y in r.poly.exterior.coords])
        w, h = maxx - minx, maxy - miny
        planes.append(Plane("floor", np.array([minx, miny, r.floor_z]), ex, ey, ez, w, h, flat))
        if r.flat_ceiling:
            top = np.array([minx, miny, r.floor_z + r.height])
            planes.append(Plane("ceiling", top, ex, ey, -ez, w, h, flat))
        height = room_top(r) - r.floor_z
        for p0, p1, length, covered in wall_runs(r):
            u = np.array([p1[0] - p0[0], p1[1] - p0[1], 0.0]) / length
            n = np.array([-u[1], u[0], 0.0])  # left of the segment = into the room
            origin = np.array([p0[0], p0[1], r.floor_z])
            if covered:
                region = unary_union([box(a, 0, b, height) for a, b in covered])
                if region.area >= 0.01:
                    planes.append(Plane("wall", origin, u, ez, n, length, height, region))
            # Open stretches: where the room beyond has a lower ceiling, close the step with a header.
            edges = [0.0, *[x for ab in covered for x in ab], length]
            for a, b in zip(edges[0::2], edges[1::2], strict=True):
                if b - a < 0.3:
                    continue
                mid = p0 + u[:2] * (a + b) / 2 - n[:2] * (2 * WALL_INSET + 0.15)
                beyond = [q for q in rooms if q is not r and q.poly.contains(Point(mid))]
                if not beyond or room_top(r) - room_top(beyond[0]) < 0.1:
                    continue
                low = room_top(beyond[0])
                step = room_top(r) - low
                o = np.array([p0[0] + u[0] * a, p0[1] + u[1] * a, low])
                planes.append(Plane("wall", o, u, ez, n, b - a, step, box(0, 0, b - a, step), carve=False))
    return planes


# ---------------------------------------------------------------- baking planes


def bake_plane(
    p: Plane, scan: Scan, panos: PanoSet, res: float, behind: Polygon | None = None
) -> tuple[dict[str, float], list[Plane]]:
    """Texture a plane from the panoramas. For walls with a room behind them (`behind` = union of room
    polygons), texels with no scanned wall that a camera sees through are cut out (alpha 0); openings are
    squared off and get jamb / lintel / sill planes (returned, not yet baked)."""
    res = min(res, (PAGE - 2 * PAD - 2) / max(p.w, p.h))
    W, H = max(2, math.ceil(p.w * res)), max(2, math.ceil(p.h * res))
    s = (np.arange(W) + 0.5) / res
    t = p.h - (np.arange(H) + 0.5) / res  # image row 0 = top of the plane (t = h)
    S, T = np.meshgrid(s, t)
    mask = shapely.contains_xy(p.shape.buffer(3 / res), S.ravel(), T.ravel()).reshape(H, W)
    pts = p.origin + S[mask][:, None] * p.u + T[mask][:, None] * p.v
    nrm = np.tile(p.n, (len(pts), 1))
    # Sample the photos at the real surface, not at the plan's idea of it. First the plane as a whole: the
    # plan line can sit several cm off the true wall face (outer walls are thick), which makes cameras
    # disagree. `base` is the median distance from the plane to the scan. Then each texel: a clamped local
    # correction smoothed over about 12 cm, so real relief survives and scan noise (blinds, glass) does not.
    reach = 0.3
    t_hit, _ = scan.rays.cast(pts + nrm * reach, -nrm)
    delta = reach - t_hit
    found = t_hit < 2 * reach
    base = float(np.median(delta[found])) if found.mean() > 0.2 else 0.0
    near = found & (np.abs(delta - base) < 0.12)
    off = np.zeros((H, W))
    hit = np.zeros((H, W))
    off[mask] = np.where(near, np.clip(delta - base, -0.06, 0.06), 0.0)
    hit[mask] = near
    k = max(3, int(0.12 * res) | 1)
    smooth = base + ndimage.uniform_filter(off, k) / np.maximum(ndimage.uniform_filter(hit, k), 1e-6)
    rgb, valid = shade(pts + nrm * smooth[mask][:, None], nrm, panos, scan.rays, snap=False)
    img = np.zeros((H, W, 3), np.uint8)
    img[mask] = np.clip(rgb, 0, 255).astype(np.uint8)
    ok = np.zeros((H, W), bool)
    ok[mask] = valid
    alpha = np.full((H, W), 255, np.uint8)
    carved = 0.0
    frames: list[Plane] = []
    if p.kind == "wall" and p.carve and behind is not None:
        no_surface, empty = see_through(pts, nrm, scan.rays, panos)
        ns = np.zeros((H, W), bool)
        ns[mask] = no_surface
        back = pts[:, :2] - p.n[:2] * (2 * WALL_INSET + 0.3)
        empty &= shapely.contains_xy(behind, back[:, 0], back[:, 1])
        e = np.zeros((H, W), bool)
        e[mask] = empty
        e = ndimage.binary_opening(e, iterations=2)  # drop specks: an opening is at least a few cm wide
        e, rects = _square_openings(e, res, ns)
        frames = _frame_planes(p, rects, res)
        alpha[e] = 0
        ok &= ~e
        carved = float(e.sum()) / res / res
    p.image = np.dstack([_fill(img, ok), alpha])
    stats = {"area": p.shape.area, "seen": float(ok.sum()) / max(1, mask.sum()), "carved_m2": carved}
    return stats, frames


def _square_openings(
    e: np.ndarray, res: float, no_surface: np.ndarray | None = None
) -> tuple[np.ndarray, list[tuple[int, int, int, int]]]:
    """Clean up a carve mask: drop openings under 0.15 m2, and replace each roughly rectangular one by its
    rectangle (straight door and pass-through edges). A door-width opening that stops above the floor is
    extended down to it when the scan has no wall surface there either (a doorway no camera looked through
    low down). Returns (mask, [(col0, col1, row0, row1)])."""
    H, W = e.shape
    labels, n = ndimage.label(e)
    out = np.zeros_like(e)
    rects = []
    snap_px = int(0.12 * res)
    for i, sl in enumerate(ndimage.find_objects(labels), 1):
        sub = labels[sl] == i
        if sub.sum() / res / res < 0.15:
            continue
        cols = np.nonzero(sub.mean(0) >= 0.5)[0]
        rows = np.nonzero(sub.mean(1) >= 0.5)[0]
        if len(cols) and len(rows):
            r0, r1 = sl[0].start + rows[0], sl[0].start + rows[-1] + 1
            c0, c1 = sl[1].start + cols[0], sl[1].start + cols[-1] + 1
            if sub[rows[0] : rows[-1] + 1, cols[0] : cols[-1] + 1].mean() >= 0.75:
                r1 = H if r1 >= H - snap_px else r1  # a door reaches the floor
                width = (c1 - c0) / res
                if r1 < H and no_surface is not None and 0.5 <= width <= 2.5:
                    if no_surface[r1:H, c0:c1].mean() > 0.6:
                        r1 = H
                r0 = 0 if r0 <= snap_px else r0
                out[r0:r1, c0:c1] = True
                rects.append((c0, c1, r0, r1))
                continue
        out[sl] |= sub  # irregular opening: keep its real outline, no frame
    return out, rects


def _frame_planes(p: Plane, rects: list[tuple[int, int, int, int]], res: float) -> list[Plane]:
    """Jambs, lintel and sill for each squared opening: thin planes across half the wall thickness (the room
    on the other side adds the other half), to be textured from the panoramas like any other plane."""
    H = max(2, math.ceil(p.h * res))
    depth = WALL_INSET
    up = np.array([0.0, 0.0, 1.0])
    out = []
    for c0, c1, r0, r1 in rects:
        s0, s1 = c0 / res, c1 / res
        t_top, t_bot = p.h - r0 * p.h / H, p.h - r1 * p.h / H
        if (s1 - s0) * (t_top - t_bot) < 0.3:
            continue
        side = box(0, 0, depth, t_top - t_bot)
        base = p.origin + t_bot * p.v
        out.append(Plane("wall", base + s0 * p.u, -p.n, p.v, p.u, depth, t_top - t_bot, side))
        out.append(Plane("wall", base + s1 * p.u, -p.n, p.v, -p.u, depth, t_top - t_bot, side))
        across = box(0, 0, s1 - s0, depth)
        if t_top < p.h - 0.03:
            out.append(
                Plane("wall", p.origin + s0 * p.u + t_top * p.v, p.u, -p.n, -up, s1 - s0, depth, across)
            )
        if t_bot > 0.03:
            out.append(Plane("wall", base + s0 * p.u, p.u, -p.n, up, s1 - s0, depth, across))
    return out


def see_through(
    pts: np.ndarray, nrm: np.ndarray, rays: Raycaster, panos: PanoSet
) -> tuple[np.ndarray, np.ndarray]:
    """(no_surface, through): no_surface is True where there is no scanned surface within SNAP of the point
    along its normal; through is True where, in addition, some camera on the room side sees past the point
    by more than 0.35 m (positive evidence of empty space)."""
    t, _ = rays.cast(pts + nrm * SNAP, -nrm)
    no_surface = t >= 2 * SNAP
    todo = np.nonzero(no_surface)[0]
    out = np.zeros(len(pts), bool)
    if not len(todo):
        return no_surface, out
    centre = pts[todo].mean(0)
    order = np.argsort(np.linalg.norm(panos.positions - centre, axis=1))
    for j in order[:12]:
        if not len(todo):
            break
        c = panos.positions[j]
        v = pts[todo] - c
        dist = np.linalg.norm(v, axis=1)
        front = (np.einsum("ij,ij->i", -v, nrm[todo]) / np.maximum(dist, 1e-6) > 0.12) & (dist < 10)
        idx = todo[front]
        if not len(idx):
            continue
        tt, _ = rays.cast(np.tile(c, (len(idx), 1)), v[front] / dist[front, None])
        through = tt > dist[front] + 0.35
        out[idx[through]] = True
        todo = todo[~np.isin(todo, idx[through])]
    return no_surface, out


def _fill(img: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """Fill texels no camera saw with a smooth blend of nearby seen texels (normalised convolution, finest
    scale that has data), so they read as plain surface instead of streaks."""
    if valid.all():
        return img
    if not valid.any():
        return np.full_like(img, 200)
    out = img.astype(float)
    todo = ~valid
    m = valid.astype(float)
    src = img.astype(float) * m[..., None]
    for sigma in (3, 8, 20, 50, 120):
        wsum = ndimage.gaussian_filter(m, sigma)
        csum = np.stack([ndimage.gaussian_filter(src[..., c], sigma) for c in range(3)], -1)
        ok = todo & (wsum > 0.05)
        out[ok] = csum[ok] / wsum[ok, None]
        todo &= ~ok
        if not todo.any():
            break
    if todo.any():
        out[todo] = img[valid].mean(0)
    return np.clip(out, 0, 255).astype(np.uint8)


def pack(planes: list[Plane]) -> list[np.ndarray]:
    """Shelf-pack plane textures (with edge padding) into square RGBA pages."""
    pages: list[np.ndarray] = []
    order = sorted(range(len(planes)), key=lambda i: -planes[i].image.shape[0])
    x = y = shelf = 0
    for i in order:
        img = np.pad(planes[i].image, ((PAD, PAD), (PAD, PAD), (0, 0)), mode="edge")
        h, w = img.shape[:2]
        if not pages or x + w > PAGE:
            x, y, shelf = 0, y + shelf, 0
        if not pages or y + h > PAGE:
            pages.append(np.zeros((PAGE, PAGE, 4), np.uint8))
            pages[-1][..., :3] = 128
            pages[-1][..., 3] = 255
            x = y = shelf = 0
        pages[-1][y : y + h, x : x + w] = img
        planes[i].page, planes[i].rect = len(pages) - 1, (x, y, w, h)
        x += w
        shelf = max(shelf, h)
    return pages


def plane_mesh(p: Plane) -> trimesh.Trimesh | None:
    polys = [p.shape] if p.shape.geom_type == "Polygon" else list(p.shape.geoms)
    meshes = []
    x, y, w, h = p.rect
    for poly in polys:
        if poly.area < 1e-4:
            continue
        v2, f = trimesh.creation.triangulate_polygon(poly)
        pts = p.origin + np.outer(v2[:, 0], p.u) + np.outer(v2[:, 1], p.v)
        # (s, t) -> atlas pixel -> OBJ-style UV (v up); image row 0 = top of the plane.
        px = x + PAD + v2[:, 0] * (w - 2 * PAD) / p.w
        py = y + PAD + (p.h - v2[:, 1]) * (h - 2 * PAD) / p.h
        uv = np.c_[px / PAGE, 1 - py / PAGE]
        tri_n = np.cross(pts[f[:, 1]] - pts[f[:, 0]], pts[f[:, 2]] - pts[f[:, 0]])
        flip = tri_n @ p.n < 0  # face the room
        f[flip] = f[flip][:, ::-1]
        m = trimesh.Trimesh(pts, f, process=False)
        m.visual = trimesh.visual.TextureVisuals(uv=uv)
        meshes.append(m)
    return trimesh.util.concatenate(meshes) if meshes else None


# ---------------------------------------------------------------- pieces (scan geometry)


def piece_faces(scan: Scan, rooms: list[ShellRoom]) -> tuple[np.ndarray, np.ndarray]:
    """Scan faces to keep as geometry, as (fixture indices, scanned-ceiling indices).

    A face inside a room is dropped only when a shell plane replaces it: it lies within SHELL_TOL of the
    floor, a flat ceiling or a wall AND is roughly parallel to it. Faces across the wall thickness (door
    jambs, window reveals) and everything else stay. In rooms with a flat ceiling, things hanging from the
    ceiling away from the walls (fans, lamps: they scan as crumpled sheets) are dropped."""
    c = scan.tris.mean(1)
    nrm = scan.normals
    keep = np.zeros(len(c), bool)
    ceil = np.zeros(len(c), bool)
    for r in rooms:
        idx = np.nonzero(shapely.contains_xy(r.poly, c[:, 0], c[:, 1]))[0]
        inset = _inset(r.poly)
        if not len(idx) or inset is None:
            continue
        z = c[idx, 2] - r.floor_z
        top = room_top(r) - r.floor_z
        runs = [
            (p0 + (p1 - p0) * (ca / length), p0 + (p1 - p0) * (cb / length))
            for p0, p1, length, covered in wall_runs(r)
            for ca, cb in covered
        ]
        if not runs:
            continue
        a, b = np.array([x[0] for x in runs]), np.array([x[1] for x in runs])
        ab = b - a
        seg_len = np.maximum(np.linalg.norm(ab, axis=1), 1e-9)
        wall_d = np.full(len(idx), np.inf)
        for k in range(len(a)):  # distance to the nearest stretch of real wall
            ap = c[idx, :2] - a[k]
            tpar = np.clip(ap @ ab[k] / seg_len[k] ** 2, 0, 1)
            wall_d = np.minimum(wall_d, np.linalg.norm(ap - tpar[:, None] * ab[k], axis=1))
        flat = np.abs(nrm[idx, 2]) > 0.5
        on_floor = (z < SHELL_TOL) & flat
        on_ceiling = r.flat_ceiling & (np.abs(z - r.height) < SHELL_TOL) & flat
        on_wall = wall_d < SHELL_TOL  # everything in the wall slab: openings get clean jamb planes instead
        outside = (z < -0.3) | (z > top + 0.3)
        k = ~(on_floor | on_ceiling | on_wall | outside)
        if r.flat_ceiling:
            k &= ~((z > 1.9) & (wall_d > 0.35))  # hanging fans and lamps
        else:
            is_ceiling = (z > 1.8) & (nrm[idx, 2] < -0.3)
            ceil[idx[k & is_ceiling]] = True
            k &= ~is_ceiling
        keep[idx[k]] = True
    return _drop_crumbs(scan, np.nonzero(keep)[0]), _drop_crumbs(scan, np.nonzero(ceil)[0])


def _seen(scan: Scan, faces: np.ndarray, panos: PanoSet) -> np.ndarray:
    """The faces whose centre at least one panorama sees from its front side."""
    if not len(faces):
        return faces
    _, valid = shade(scan.tris[faces].mean(1), scan.normals[faces], panos, scan.rays, snap=False)
    return faces[valid]


def _drop_crumbs(scan: Scan, faces: np.ndarray, min_area: float = 0.05) -> np.ndarray:
    """Remove connected pieces smaller than min_area m2 (scan noise)."""
    if not len(faces):
        return faces
    soup = scan.tris[faces].reshape(-1, 3)
    m = trimesh.Trimesh(soup, np.arange(len(soup)).reshape(-1, 3), process=False)
    m.merge_vertices()
    out = []
    for comp in trimesh.graph.connected_components(m.face_adjacency, nodes=np.arange(len(faces))):
        if scan.areas[faces[comp]].sum() >= min_area:
            out.append(faces[comp])
    return np.sort(np.concatenate(out)) if out else np.zeros(0, int)


def bake_triangles(
    scan: Scan, faces: np.ndarray, panos: PanoSet, res: float
) -> tuple[list[np.ndarray], np.ndarray, np.ndarray]:
    """Give each kept scan triangle its own small chart and colour it from the panoramas (the scan's own
    texture where no camera sees it). Returns (RGB pages, uv (n, 3, 2), page index (n,))."""
    tri = scan.tris[faces]
    # Put the longest edge on the chart's x axis so charts are flat rectangles.
    e = np.linalg.norm(tri[:, [1, 2, 0]] - tri, axis=2)  # |AB|, |BC|, |CA|
    order = (np.arange(3)[None, :] + np.argmax(e, axis=1)[:, None]) % 3
    tri = np.take_along_axis(tri, order[:, :, None], axis=1)
    unroll = np.argsort(order, axis=1)  # rolled vertex order -> the scan's own order
    A, B, C = tri[:, 0], tri[:, 1], tri[:, 2]
    L = np.maximum(np.linalg.norm(B - A, axis=1), 1e-6)
    ex = (B - A) / L[:, None]
    cx = np.einsum("ij,ij->i", C - A, ex)
    perp = (C - A) - cx[:, None] * ex
    cy = np.maximum(np.linalg.norm(perp, axis=1), 1e-6)
    ey = perp / cy[:, None]
    r = np.minimum(res, 1000.0 / np.maximum(L, cy))  # per-triangle texel density (huge triangles capped)
    pad = 2
    wpx = np.ceil(L * r).astype(int) + 2 * pad + 1
    hpx = np.ceil(cy * r).astype(int) + 2 * pad + 1

    # Shelf packing, tallest first.
    n = len(faces)
    px0, py0, pg = np.zeros(n, int), np.zeros(n, int), np.zeros(n, int)
    x = y = shelf = page = 0
    for i in np.argsort(-hpx, kind="stable"):
        if x + wpx[i] > PAGE:
            x, y, shelf = 0, y + shelf, 0
        if y + hpx[i] > PAGE:
            page, x, y, shelf = page + 1, 0, 0, 0
        px0[i], py0[i], pg[i] = x, y, page
        x += wpx[i]
        shelf = max(shelf, hpx[i])
    pages = [np.zeros((PAGE, PAGE, 3), np.uint8) for _ in range(page + 1)]

    # All texels of all charts, grouped by chart size so the grids vectorise.
    size_key = wpx.astype(np.int64) * 100000 + hpx
    for key in np.unique(size_key):
        grp = np.nonzero(size_key == key)[0]
        w, h = int(wpx[grp[0]]), int(hpx[grp[0]])
        gi, gj = np.meshgrid(np.arange(w), np.arange(h))
        gi, gj = gi.ravel(), gj.ravel()
        x2 = (gi[None, :] + 0.5 - pad) / r[grp, None]  # (g, w*h) metres along ex
        y2 = (gj[None, :] + 0.5 - pad) / r[grp, None]
        pts = A[grp, None, :] + x2[..., None] * ex[grp, None, :] + y2[..., None] * ey[grp, None, :]
        nrm = np.broadcast_to(scan.normals[faces[grp], None, :], pts.shape)
        rgb, valid = shade(pts.reshape(-1, 3), nrm.reshape(-1, 3), panos, scan.rays, snap=False)
        miss = ~valid
        if miss.any():
            lc = y2 / cy[grp, None]
            lb = (x2 - lc * cx[grp, None]) / L[grp, None]
            bary = np.clip(np.stack([1 - lb - lc, lb, lc], -1), 0, 1)
            bary /= np.maximum(bary.sum(-1, keepdims=True), 1e-9)
            bary = np.take_along_axis(bary, unroll[grp][:, None, :], axis=2).reshape(-1, 3)
            fidx = np.repeat(faces[grp], w * h)
            rgb[miss] = scan_colors(scan, fidx[miss], bary[miss])
        rgb = np.clip(rgb, 0, 255).astype(np.uint8).reshape(len(grp), h, w, 3)
        for k, f in enumerate(grp):
            pages[pg[f]][py0[f] : py0[f] + h, px0[f] : px0[f] + w] = rgb[k]

    # Vertex UVs (rolled order -> back to the scan's vertex order).
    vx = np.stack([np.full(n, float(pad)), pad + L * r, pad + cx * r], 1) + px0[:, None]
    vy = np.stack([np.full(n, float(pad)), np.full(n, float(pad)), pad + cy * r], 1) + py0[:, None]
    uv_rolled = np.stack([vx / PAGE, 1 - vy / PAGE], -1)
    uv = np.take_along_axis(uv_rolled, unroll[:, :, None], axis=1)
    return pages, uv, pg


# ---------------------------------------------------------------- export


def _material(img: np.ndarray, name: str) -> trimesh.visual.material.PBRMaterial:
    cut = img.shape[2] == 4 and bool((img[..., 3] < 255).any())
    return trimesh.visual.material.PBRMaterial(
        name=name,
        baseColorTexture=Image.fromarray(img if cut else np.ascontiguousarray(img[..., :3])),
        metallicFactor=0.0,
        roughnessFactor=1.0,
        doubleSided=True,
        alphaMode="MASK" if cut else "OPAQUE",
        alphaCutoff=0.5 if cut else None,
    )


def _add_soup(scene: trimesh.Scene, scan: Scan, faces: np.ndarray, pages, uv, pg, name: str) -> None:
    for pi in range(len(pages)):
        sel = pg == pi
        if not sel.any():
            continue
        verts = scan.tris[faces[sel]].reshape(-1, 3)
        m = trimesh.Trimesh(verts, np.arange(len(verts)).reshape(-1, 3), process=False)
        mat = _material(pages[pi], f"{name}_{pi}")
        m.visual = trimesh.visual.TextureVisuals(uv=uv[sel].reshape(-1, 2), material=mat)
        m.apply_transform(hm.Z_UP_TO_Y_UP)
        scene.add_geometry(m, node_name=f"{name}_{pi}", geom_name=f"{name}_{pi}")


def write_rebaked_interior_glb(
    scan: Scan,
    rooms: list[ShellRoom],
    panos: PanoSet,
    dest: Path,
    res: float = 256.0,
    piece_res: float = 160.0,
) -> tuple[dict, np.ndarray]:
    """interior.glb: shell nodes `floor_N`, `walls_N`, `ceiling_N` and scan-geometry nodes `fixtures_N`,
    `ceiling_scan_N`, all textured from the panoramas. Returns (stats for the build report, the fixture
    triangles (n, 3, 3) in Z-up coordinates, for collision.glb)."""
    # Indoor cameras only: outdoor sweeps see doors open that indoor ones saw closed, and see the roof.
    indoors = unary_union([r.poly for r in rooms]).buffer(0.1)
    panos = panos.subset(
        [i for i, q in enumerate(panos.panos) if indoors.contains(Point(q.pos[0], q.pos[1]))]
    )
    for r in rooms:
        measure_ceiling(scan, r)
    planes = shell_planes(rooms)
    behind = unary_union([r.poly for r in rooms]).buffer(-0.02)
    stats, frames = [], []
    for p in planes:
        st, fr = bake_plane(p, scan, panos, res, behind)
        stats.append(st)
        frames += fr
    for p in frames:
        bake_plane(p, scan, panos, res)
    planes += frames
    pages = pack(planes)
    scene = trimesh.Scene()
    mats = [_material(pg, f"shell_{i}") for i, pg in enumerate(pages)]
    for kind, node in (("floor", "floor"), ("wall", "walls"), ("ceiling", "ceiling")):
        for pi in range(len(pages)):
            parts = [plane_mesh(p) for p in planes if p.page == pi and p.kind == kind]
            parts = [m for m in parts if m is not None]
            if not parts:
                continue
            m = trimesh.util.concatenate(parts)
            m.visual = trimesh.visual.TextureVisuals(uv=m.visual.uv, material=mats[pi])
            m.apply_transform(hm.Z_UP_TO_Y_UP)
            scene.add_geometry(m, node_name=f"{node}_{pi}", geom_name=f"{node}_{pi}")

    fixtures, ceilings = piece_faces(scan, rooms)
    # Keep only pieces some indoor camera sees (drops the roof above a vaulted ceiling, junk inside walls).
    fixtures = _drop_crumbs(scan, _seen(scan, fixtures, panos))
    ceilings = _seen(scan, ceilings, panos)
    n_pages = len(pages)
    for faces, name in ((fixtures, "fixtures"), (ceilings, "ceiling_scan")):
        if len(faces):
            pg_imgs, uv, pg = bake_triangles(scan, faces, panos, piece_res)
            _add_soup(scene, scan, faces, pg_imgs, uv, pg, name)
            n_pages += len(pg_imgs)

    dest.parent.mkdir(parents=True, exist_ok=True)
    scene.export(dest)
    area = sum(s["area"] for s in stats)
    report = {
        "planes": {k: sum(p.kind == k for p in planes) for k in ("wall", "floor", "ceiling")},
        "vaulted_rooms": sum(not r.flat_ceiling for r in rooms),
        "texture_pages": n_pages,
        "fixture_faces": int(len(fixtures)),
        "scan_ceiling_faces": int(len(ceilings)),
        "shell_seen_by_panos": round(sum(s["seen"] * s["area"] for s in stats) / max(area, 1e-9), 3),
        "carved_wall_m2": round(sum(s["carved_m2"] for s in stats), 2),
        "framed_openings": len(frames),
    }
    report["panoramas_indoor"] = len(panos)
    return report, scan.tris[fixtures]
