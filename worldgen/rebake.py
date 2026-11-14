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
from worldgen.panos import SNAP, PanoSet, Raycaster, shade, shade_single

WALL_INSET = 0.05  # wall surface sits about half a wall thickness inside the centreline polygon
SHELL_TOL = 0.08  # scan faces this close to (and parallel to) a shell plane are replaced by it
PAGE = 2048
PAD = 4
SINGLE_VIEW_MAX = 600_000  # texels; box faces up to this size are textured from one panorama at a time
UNSEEN_PLANE = 0.05  # a shell plane cameras saw less of than this is painted in its room's colour


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
    # Line-of-sight tests while texturing use `sight` when set: the scan plus stand-in solids for things
    # that were removed from the model (ceiling fans), so their photo is not projected onto what is behind.
    sight: Raycaster | None = None

    @property
    def eyes(self) -> Raycaster:
        return self.sight or self.rays


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
    room: int = -1  # shell planes: index of the room they belong to
    box: int = -1  # kind "box": index of the Box this face belongs to
    seen: float = 0.0  # fraction of texels some camera saw (set by bake_plane)
    unseen: np.ndarray | None = None  # (H, W) texels no camera saw (set by bake_plane)
    shape_mask: np.ndarray | None = None  # (H, W) texels inside the plane's shape (set by bake_plane)


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
    slabs = []
    for ln, t in r.wall_lines:
        (ax, ay), (bx, by) = ln.coords[0], ln.coords[-1]
        d = np.array([bx - ax, by - ay])
        if np.linalg.norm(d) > 1e-6:
            slab = ln.buffer(t / 2 + WALL_INSET + 0.04, cap_style="flat")
            slabs.append((d / np.linalg.norm(d), slab))
    ring = list(inset.exterior.coords)
    out = []
    for p0, p1 in zip(ring[:-1], ring[1:], strict=True):
        seg = LineString([p0, p1])
        length = seg.length
        if length < 0.05:
            continue
        # Only walls that run along this stretch of the outline: the slab of a wall that meets it at a
        # right angle also reaches a few cm along it, which made a 12 cm strip of "wall" in thin air.
        along = (np.asarray(p1) - np.asarray(p0)) / length
        walls = unary_union([slab for d, slab in slabs if abs(float(d @ along)) > 0.8])
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


END_REACH = 0.16  # m; an open wall end looks this far ahead for another wall face to meet


def _open_end(e, t, n, own, surfaces) -> tuple[float, bool]:
    """For a wall face that ends at point e (2D) where the outline goes on without a wall (t = direction
    past the end, n = the face's normal): how far to extend the face so that it meets another room's wall
    face, and whether it met one.

    Two rooms that share an open edge (a room-split line, an invisible edge) each end their wall faces
    WALL_INSET short of that edge, which leaves a strip of nothing in a wall that really runs straight
    on, or at a corner where two walls meet. A face that continues ours in the same line gets half the gap
    (it extends too); a face that crosses our line gets the whole distance."""
    best = None
    for k, (q0, q1, qn) in enumerate(surfaces):
        if k in own:
            continue
        d = q1 - q0
        length = float(np.linalg.norm(d))
        if length < 1e-6:
            continue
        d = d / length
        if abs(float(t @ d)) > 0.95 and float(n @ qn) > 0.9:  # same line, same side: meet half way
            if abs(float((q0 - e) @ n)) > 0.03:
                continue
            ahead = min(float((q0 - e) @ t), float((q1 - e) @ t))
            behind = max(float((q0 - e) @ t), float((q1 - e) @ t))
            if behind < -0.005 or ahead > END_REACH:
                continue
            ext = max(ahead, 0.0) / 2
        elif abs(float(t @ d)) < 0.3:  # crosses our line: go all the way to it
            denom = float(t @ qn)
            if abs(denom) < 0.5:
                continue
            dist = float((q0 - e) @ qn) / denom
            where = float((e + dist * t - q0) @ d)
            if not (-0.03 <= dist <= END_REACH and -0.05 <= where <= length + 0.05):
                continue
            ext = max(dist, 0.0)
        else:
            continue
        best = ext if best is None else min(best, ext)
    return (0.0, False) if best is None else (best, True)


def shell_planes(rooms: list[ShellRoom]) -> list[Plane]:
    """Floors, flat ceilings and wall faces of every room, plus headers over open passages into lower
    rooms and end caps where a wall stops in open space."""
    planes: list[Plane] = []
    ex, ey, ez = np.eye(3)
    runs = [wall_runs(r) for r in rooms]
    # Every wall face of the house in plan, to let open ends find each other: (start, end, normal).
    surfaces, owner = [], {}
    for ri, room_runs in enumerate(runs):
        for si, (p0, p1, length, covered) in enumerate(room_runs):
            u = (p1 - p0) / length
            for ci, (a, b) in enumerate(covered):
                owner[(ri, si, ci)] = len(surfaces)
                surfaces.append((p0 + u * a, p0 + u * b, np.array([-u[1], u[0]])))
    for ri, r in enumerate(rooms):
        first = len(planes)
        minx, miny, maxx, maxy = r.poly.bounds
        flat = Polygon([(x - minx, y - miny) for x, y in r.poly.exterior.coords])
        w, h = maxx - minx, maxy - miny
        planes.append(Plane("floor", np.array([minx, miny, r.floor_z]), ex, ey, ez, w, h, flat))
        if r.flat_ceiling:
            top = np.array([minx, miny, r.floor_z + r.height])
            planes.append(Plane("ceiling", top, ex, ey, -ez, w, h, flat))
        height = room_top(r) - r.floor_z
        for si, (p0, p1, length, covered) in enumerate(runs[ri]):
            u = np.array([p1[0] - p0[0], p1[1] - p0[1], 0.0]) / length
            n = np.array([-u[1], u[0], 0.0])  # left of the segment = into the room
            if covered:
                prev, nxt = runs[ri][si - 1], runs[ri][(si + 1) % len(runs[ri])]
                spans = []
                for ci, (a, b) in enumerate(covered):
                    own = {owner[(ri, si, ci)]}
                    # An end is open when the wall does not go on around the corner (or stops mid-way).
                    open_a = a > 1e-6 or not any(e >= prev[2] - 0.02 for _, e in prev[3])
                    open_b = b < length - 1e-6 or not any(st <= 0.02 for st, _ in nxt[3])
                    for is_open, at, t in ((open_a, a, -u), (open_b, b, u)):
                        ext = 0.0
                        if is_open:
                            e = p0 + u[:2] * at
                            ext, met = _open_end(e, t[:2], n[:2], own, surfaces)
                            if not met:  # the wall ends in open space: close its end, half the thickness
                                o = np.array([e[0], e[1], r.floor_z])
                                end = box(0, 0, WALL_INSET, height)
                                cap = Plane("wall", o, -n, ez, t, WALL_INSET, height, end, carve=False)
                                planes.append(cap)
                        spans.append(ext)
                    a2, b2 = a - spans[-2], b + spans[-1]
                    spans[-2:] = [(a2, b2)]
                lo = min(0.0, min(a for a, _ in spans))
                hi = max(length, max(b for _, b in spans))
                region = unary_union([box(a - lo, 0, b - lo, height) for a, b in spans])
                if region.area >= 0.01:
                    origin = np.array([p0[0] + u[0] * lo, p0[1] + u[1] * lo, r.floor_z])
                    planes.append(Plane("wall", origin, u, ez, n, hi - lo, height, region))
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
        for p in planes[first:]:
            p.room = ri
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
    at = pts + nrm * smooth[mask][:, None]
    if p.kind == "box" and len(at) <= SINGLE_VIEW_MAX:
        rgb, valid = shade_single(at, nrm, panos, scan.eyes)  # one photo per face where possible
    else:
        rgb, valid = shade(at, nrm, panos, scan.eyes, snap=False)
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
    # Outside the plane's shape nothing is drawn: count it as seen so the fill does no work there.
    p.image = np.dstack([_fill(img, ok | ~mask) if ok.any() else np.full_like(img, 200), alpha])
    stats = {"area": p.shape.area, "seen": float(ok.sum()) / max(1, mask.sum()), "carved_m2": carved}
    p.seen = stats["seen"]
    p.unseen = mask & ~ok & (alpha > 0)
    p.shape_mask = mask
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


DETAIL_PATCH = 96  # texels; side of the sample square whose fine texture is repeated over unseen areas
DETAIL_GRAIN = 4.0  # texels; structure broader than this is not copied
DETAIL_MAX_PEAK = 5.0  # a patch whose strongest detail is this many times its typical detail is not grain


def _plain_patches(img: np.ndarray, valid: np.ndarray, size: int) -> list[np.ndarray]:
    """Candidate size x size squares of the image that cameras saw completely and that look like ordinary
    surface (8 texel steps): the square at the lower quartile of "strongest edge inside it", then the one
    with the weakest. A square with a light switch, a ceiling light or a door edge has a strong edge; the
    weakest one is sometimes blurred or blown out, so it is the second choice."""
    H, W = valid.shape
    if H < size or W < size:
        return []
    c = np.pad(valid.astype(np.float64), ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    full = (c[size:, size:] - c[:-size, size:] - c[size:, :-size] + c[:-size, :-size])[::8, ::8]
    full = full >= size * size - 0.5
    lum = ndimage.gaussian_filter(img[..., :3].astype(np.float32).mean(-1), 2)  # edges, not grain
    gy, gx = np.gradient(lum)
    edge = np.hypot(gx, gy)
    h8, w8 = H // 8, W // 8
    blocks = edge[: h8 * 8, : w8 * 8].reshape(h8, 8, w8, 8).max((1, 3))
    k = size // 8
    peak = ndimage.maximum_filter(blocks, size=k, origin=-(k // 2), mode="nearest")  # max over [i, i + k)
    peak = peak[: full.shape[0], : full.shape[1]]
    full = full[: peak.shape[0], : peak.shape[1]]
    if not full.any():
        return []
    values, where = peak[full], np.argwhere(full)
    out = []
    for i in (int(np.argmin(np.abs(values - np.quantile(values, 0.25)))), int(np.argmin(values))):
        r, c0 = int(where[i][0]) * 8, int(where[i][1]) * 8
        out.append(img[r : r + size, c0 : c0 + size, :3].astype(np.float32))
    return out


def _detail_tile(img: np.ndarray, valid: np.ndarray, H: int, W: int) -> np.ndarray | None:
    """Fine texture (H, W, 3, zero mean) to lay over a smooth fill: the grain of an ordinary seen patch of
    `img`, mirrored so that it repeats without seams. None when there is no suitable patch (the fill then
    stays smooth, which is never wrong).

    Only grain is copied: structure broader than DETAIL_GRAIN is removed, and a patch whose detail has
    outliers (a line, a rim, a screw: 99th percentile over DETAIL_MAX_PEAK times the median) is rejected,
    because a feature repeated every 2 x DETAIL_PATCH texels reads as a grid."""
    for size in (DETAIL_PATCH, DETAIL_PATCH // 2, DETAIL_PATCH // 4):
        for patch in _plain_patches(img, valid, size):
            low = [ndimage.gaussian_filter(patch[..., c], DETAIL_GRAIN, mode="reflect") for c in range(3)]
            d = patch - np.stack(low, -1)
            mag = np.abs(d).mean(-1)
            typical = float(np.median(mag))
            if np.quantile(mag, 0.99) > DETAIL_MAX_PEAK * typical + 1.0:
                continue
            limit = 3.0 * typical + 1.0
            d = np.clip(d, -limit, limit)
            d = np.concatenate([d, d[::-1]], 0)
            d = np.concatenate([d, d[:, ::-1]], 1)
            return np.tile(d, (math.ceil(H / (2 * size)), math.ceil(W / (2 * size)), 1))[:H, :W]
    return None


def _fill(img: np.ndarray, valid: np.ndarray, detail: bool = True) -> np.ndarray:
    """Fill texels no camera saw. Colour: a smooth blend of nearby seen texels (normalised convolution,
    finest scale that has data), so they read as plain surface instead of streaks. Texture: in holes more
    than a few texels across, the fine detail of a seen patch of the same image is laid over the blend
    (`_detail_tile`), so a filled patch of popcorn ceiling, carpet or wood grain is not a smooth smear."""
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
    if detail:
        depth = ndimage.distance_transform_edt(~valid)
        if (depth > 8).sum() > 300:
            tile = _detail_tile(img, valid, *valid.shape)
            if tile is not None:
                out += tile * np.clip((depth - 2) / 8, 0, 1)[..., None]  # fades in from the hole's edge
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


# ---------------------------------------------------------------- relief boxes (wall-attached fixtures)

RELIEF_RES = 50.0  # relief map cells per metre (2 cm)
RELIEF_REACH = 1.0  # how far into the room a wall-attached fixture may stick out
RELIEF_MIN = 0.12  # shallower relief is trim, sills and scan noise: left to the wall texture
RELIEF_LEVEL_TOL = 0.05  # cells within this of a depth level belong to it
RELIEF_MIN_AREA = 0.08  # m2 of front face below which a level or rectangle is ignored
RELIEF_MIN_SIDE = 0.2  # m; narrower strips are slanted or round things, left as scan geometry
RELIEF_MAX_TILT = 0.15  # metres of depth per metre along the wall (about 8 degrees): steeper is not a front
RELIEF_MAX_ROUGH = 0.15  # median depth change between neighbouring cells, metres per metre
RELIEF_MAX_SPREAD = 0.013  # m; median absolute deviation of a front's depth
RELIEF_MAX_HOLE = 0.35  # m2; a smaller hole enclosed by a flat front is bridged
BOX_TOL = 0.08  # scan faces this close to a box are replaced by it


@dataclass
class Box:
    """A fixture that sticks out of a wall with a flat front (cabinet, counter, fridge, tub surround, wall
    stub): the wall rectangle s0..s1 x t0..t1 extruded `depth` into the room. `origin` is the wall run's
    start at floor level, u runs along the wall, n points into the room. `attached` is False on an open
    stretch of the room outline (no wall behind it), where the box also needs a back face."""

    origin: np.ndarray
    u: np.ndarray
    n: np.ndarray
    s0: float
    s1: float
    t0: float
    t1: float
    depth: float
    attached: bool = True
    wall_ends: tuple[bool, bool] = (False, False)  # the box touches a wall at its s0 / s1 end
    to_ceiling: bool = False  # the box reaches the ceiling (no top face)
    room: int = -1  # index of the room the box was found in
    back: float = 0.0  # unattached boxes: how far the fixture reaches behind the outline (a half wall)

    def footprint(self, grow: float = 0.0) -> Polygon:
        o, u, n = self.origin[:2], self.u[:2], self.n[:2]
        a, b = self.s0 - grow, self.s1 + grow
        lo, hi = -self.back - grow, self.depth + grow
        return Polygon([o + a * u + lo * n, o + b * u + lo * n, o + b * u + hi * n, o + a * u + hi * n])

    @property
    def z0(self) -> float:
        return float(self.origin[2] + self.t0)

    @property
    def z1(self) -> float:
        return float(self.origin[2] + self.t1)

    def contains(self, pts: np.ndarray, grow: float = 0.0) -> np.ndarray:
        rel = pts - self.origin
        s, d, t = rel @ self.u, rel @ self.n, rel[:, 2]
        return (
            (s > self.s0 - grow)
            & (s < self.s1 + grow)
            & (d > -self.back - grow)
            & (d < self.depth + grow)
            & (t > self.t0 - grow)
            & (t < self.t1 + grow)
        )

    def face_planes(self) -> list[tuple[np.ndarray, float]]:
        """(outward normal, offset along it) of the six faces."""
        up = np.array([0.0, 0.0, 1.0])
        ou, on = float(self.origin @ self.u), float(self.origin @ self.n)
        return [
            (self.n, on + self.depth),
            (-self.n, -(on - self.back)),
            (-self.u, -(ou + self.s0)),
            (self.u, ou + self.s1),
            (up, self.z1),
            (-up, -self.z0),
        ]

    def triangles(self) -> np.ndarray:
        """(12, 3, 3) triangles of the box, for collision."""
        m = trimesh.creation.box(extents=[self.s1 - self.s0, self.depth + self.back, self.t1 - self.t0])
        rot = np.eye(4)
        rot[:3, 0], rot[:3, 1], rot[:3, 2] = self.u, self.n, [0, 0, 1]
        centre = (
            self.origin
            + self.u * (self.s0 + self.s1) / 2
            + self.n * (self.depth - self.back) / 2
            + np.array([0, 0, (self.t0 + self.t1) / 2])
        )
        rot[:3, 3] = centre
        m.apply_transform(rot)
        return np.asarray(m.triangles)


def relief_runs(r: ShellRoom) -> list[tuple[np.ndarray, np.ndarray, float, list[tuple[float, float]]]]:
    """wall_runs with collinear neighbours joined, so a counter that passes a vertex of the room outline
    (where a closet wall meets the other side, say) is measured as one piece."""
    runs = wall_runs(r)
    if not runs:
        return []

    def continues(a, b) -> bool:
        ua, ub = (a[1] - a[0]) / a[2], (b[1] - b[0]) / b[2]
        return float(ua @ ub) > 0.999 and float(np.linalg.norm(b[0] - a[1])) < 0.12

    start = next((i for i in range(len(runs)) if not continues(runs[i - 1], runs[i])), 0)
    runs = runs[start:] + runs[:start]
    out: list[list] = []
    for i, run in enumerate(runs):
        p0, p1, length, covered = run
        if out and i > 0 and continues(runs[i - 1], run):
            prev = out[-1]
            shift = float(np.linalg.norm(p0 - prev[0]))
            prev[3] = prev[3] + [(a + shift, b + shift) for a, b in covered]
            prev[1], prev[2] = p1, float(np.linalg.norm(p1 - prev[0]))
        else:
            out.append([p0, p1, length, list(covered)])
    return [(p0, p1, length, covered) for p0, p1, length, covered in out]


def wall_relief(
    scan: Scan, room: ShellRoom, p0: np.ndarray, p1: np.ndarray, length: float, res: float = RELIEF_RES
) -> np.ndarray:
    """How far the scanned surface stands out from a wall line, as an (H, W) map in metres (row 0 = top of
    the wall, 0 = bare wall). Rays run from inside the room toward the wall and stop at the first surface
    that faces the room, so the map holds what a player in the room sees. A ray never starts outside the
    room (a closet is shallower than RELIEF_REACH)."""
    u = np.array([p1[0] - p0[0], p1[1] - p0[1], 0.0]) / length
    n = np.array([-u[1], u[0], 0.0])
    height = room_top(room) - room.floor_z
    W, H = max(1, math.ceil(length * res)), max(1, math.ceil(height * res))
    s = (np.arange(W) + 0.5) / res
    t = height - (np.arange(H) + 0.5) / res
    # Per column: the largest start distance that stays inside the room.
    steps = np.arange(0.1, RELIEF_REACH + 1e-6, 0.05)
    base = p0[None, :] + s[:, None] * u[None, :2]
    probe = base[:, None, :] + steps[None, :, None] * n[None, None, :2]
    inside = shapely.contains_xy(room.poly, probe[..., 0].ravel(), probe[..., 1].ravel()).reshape(W, -1)
    n_in = np.where(inside.all(1), len(steps), np.argmin(inside, axis=1))
    reach = np.where(n_in > 0, steps[np.maximum(n_in - 1, 0)], 0.0)  # (W,)
    S, T = np.meshgrid(s, t)
    R = np.broadcast_to(reach[None, :], (H, W)).ravel()
    pts = np.array([p0[0], p0[1], room.floor_z]) + S.ravel()[:, None] * u + T.ravel()[:, None] * [0, 0, 1.0]
    depth = np.zeros(len(pts))
    todo = np.nonzero(R > 0.2)[0]
    walked = np.zeros(len(pts))
    for _ in range(4):
        if not len(todo):
            break
        o = pts[todo] + n * (R[todo] - walked[todo])[:, None]
        tt, prim = scan.rays.cast(o, np.tile(-n, (len(todo), 1)))
        hit = prim >= 0
        d = R[todo] - walked[todo] - tt
        facing = np.zeros(len(todo), bool)
        facing[hit] = scan.normals[prim[hit]] @ n > 0.2
        good = hit & facing & (d > 0)
        depth[todo[good]] = d[good]
        again = hit & ~facing & (d > 0)  # a back face (we started inside something): keep going
        walked[todo[again]] += tt[again] + 1e-3
        todo = todo[again]
    return depth.reshape(H, W)


def _largest_rect(mask: np.ndarray) -> tuple[int, int, int, int]:
    """The largest all-True axis-aligned rectangle in a mask, as (c0, c1, r0, r1) with exclusive ends."""
    H, W = mask.shape
    heights = np.zeros(W, int)
    best, best_area = (0, 0, 0, 0), 0
    for r in range(H):
        heights = np.where(mask[r], heights + 1, 0)
        stack: list[int] = []
        for c in range(W + 1):
            h = heights[c] if c < W else 0
            while stack and heights[stack[-1]] >= h:
                top = stack.pop()
                left = stack[-1] + 1 if stack else 0
                area = heights[top] * (c - left)
                if area > best_area:
                    best_area, best = area, (left, c, r - heights[top] + 1, r + 1)
            stack.append(c)
    return best


def _fill_small_holes(mask: np.ndarray, max_cells: int) -> np.ndarray:
    """Fill holes enclosed by the mask when they are smaller than max_cells (an oven window the scanner
    saw through, a dent in a steel fridge), so one appliance stays one rectangle."""
    holes, n = ndimage.label(ndimage.binary_fill_holes(mask) & ~mask)
    if not n:
        return mask
    size = np.bincount(holes.ravel())
    small = size <= max_cells
    small[0] = False
    return mask | small[holes]


def _refine_edge(solid: np.ndarray, lo: int, hi: int, at: int, step: int, rows: bool, reach: int) -> int:
    """Move one edge of a rectangle (found on the coarse grid) to where the fine mask really ends: the
    outermost row (or column) within `reach` cells of `at` that is at least half solid between lo and hi.
    step = -1 for a top or left edge (exclusive end semantics are handled by the caller)."""
    n = solid.shape[0] if rows else solid.shape[1]
    best = at
    for k in range(at + reach * step, at - reach * step - step, -step):  # from outside in
        if not 0 <= k < n:
            continue
        line = solid[k, lo:hi] if rows else solid[lo:hi, k]
        if line.mean() >= 0.5:
            best = k
            break
    else:
        return at
    return best


def relief_rects(depth: np.ndarray, res: float = RELIEF_RES) -> list[tuple[int, int, int, int, float]]:
    """Flat-fronted rectangles in a relief map, as (c0, c1, r0, r1, depth).

    1. Depth levels: repeatedly the most common depth among cells not yet assigned (within
       RELIEF_LEVEL_TOL), while a level still covers RELIEF_MIN_AREA.
    2. Levels are fitted from the one that sticks out furthest to the shallowest. For a level, "solid" is
       every cell at least that far out, so a counter's rectangle runs on behind the stove that stands 6 cm
       proud of it, and dents in a badly scanned front (a steel fridge) are bridged where they are small.
    3. Each connected solid region is cut into rectangles: the largest rectangle that is mostly solid on a
       coarse (8 cm) grid, its edges then moved to where the fine mask ends, repeated on what is left.
    4. Edges close to the floor, the ceiling or the ends of the wall snap to them (toe kicks, cabinets that
       end at a corner).
    Relief with no dominant depth (a slanted fireplace, a toilet) yields nothing and stays scan geometry."""
    H, W = depth.shape
    D = ndimage.median_filter(depth, size=5)
    todo = (D >= RELIEF_MIN) & (D <= RELIEF_REACH - 0.04)
    min_cells = RELIEF_MIN_AREA * res * res
    side = int(RELIEF_MIN_SIDE * res)
    levels: list[float] = []
    left = todo.copy()
    for _ in range(16):
        if left.sum() < min_cells:
            break
        hist = np.bincount(np.round(D[left] / 0.02).astype(int))
        win = np.convolve(hist, np.ones(3), "same")
        if win.max() < min_cells:
            break
        levels.append(float(np.argmax(win)) * 0.02)
        left &= np.abs(D - levels[-1]) > RELIEF_LEVEL_TOL
    out: list[tuple[int, int, int, int, float]] = []
    boxed = np.zeros((H, W), bool)  # cells inside a rectangle already fitted (to a level further out)
    f = 4  # coarse grid factor
    for level in sorted(levels, reverse=True):
        at = todo & (np.abs(D - level) <= RELIEF_LEVEL_TOL)
        solid = D >= level - RELIEF_LEVEL_TOL
        solid = ndimage.binary_opening(ndimage.binary_closing(solid, iterations=3), iterations=2)
        solid = _fill_small_holes(solid, int(RELIEF_MAX_HOLE * res * res))
        Hc, Wc = math.ceil(H / f), math.ceil(W / f)
        pad = np.zeros((Hc * f, Wc * f))
        pad[:H, :W] = solid
        coarse = pad.reshape(Hc, f, Wc, f).mean((1, 3)) >= 0.6
        labels, _ = ndimage.label(coarse)
        for i, sl in enumerate(ndimage.find_objects(labels), 1):
            comp = labels[sl] == i
            for _ in range(6):
                c0, c1, r0, r1 = _largest_rect(comp)
                if (c1 - c0) * (r1 - r0) * f * f < min_cells / 2:
                    break
                comp[r0:r1, c0:c1] = False
                c0, c1 = (c0 + sl[1].start) * f, min((c1 + sl[1].start) * f, W)
                r0, r1 = (r0 + sl[0].start) * f, min((r1 + sl[0].start) * f, H)
                r0 = _refine_edge(solid, c0, c1, r0, -1, True, f)
                r1 = _refine_edge(solid, c0, c1, r1 - 1, 1, True, f) + 1
                c0 = _refine_edge(solid, r0, r1, c0, -1, False, f)
                c1 = _refine_edge(solid, r0, r1, c1 - 1, 1, False, f) + 1
                own = at[r0:r1, c0:c1]
                if c1 - c0 < side or r1 - r0 < side or own.sum() < min_cells / 2:
                    continue
                # Flat front: most of what no further-out rectangle explains must be at this level. A slope
                # fails here (its cells are spread over every depth), so does a rectangle that only repeats
                # a further-out one.
                if own.sum() < 0.6 * max(1, (~boxed[r0:r1, c0:c1]).sum()):
                    continue
                rr, cc = np.nonzero(own)
                fit = np.linalg.lstsq(np.c_[np.ones(len(rr)), rr, cc], D[r0:r1, c0:c1][own], rcond=None)[0]
                if math.hypot(fit[1], fit[2]) * res > RELIEF_MAX_TILT:
                    continue  # a tilted surface cut off by the level tolerance, not a flat front
                # Wavy or crumpled (a shower curtain, a heap of scan noise): rough from cell to cell AND
                # spread in depth. A box there would be a painted block where the real thing is soft.
                vals = D[r0:r1, c0:c1][own]
                gy, gx = np.gradient(D[r0:r1, c0:c1])
                rough = float(np.median(np.hypot(gx, gy)[own])) * res
                spread = float(np.median(np.abs(vals - np.median(vals))))
                if own.mean() < 0.3 or (rough > RELIEF_MAX_ROUGH and spread > RELIEF_MAX_SPREAD):
                    continue
                d = float(np.median(vals))
                r1 = H if H - r1 <= 0.15 * res else r1  # toe kick: the box stands on the floor
                if H - r0 >= 1.5 * res and H - r1 <= 0.4 * res:
                    r1 = H  # tall things (a fridge, a wall bump, a door leaf) stand on the floor too
                r0 = 0 if r0 <= 0.10 * res else r0
                c0 = 0 if c0 <= 0.08 * res else c0
                c1 = W if W - c1 <= 0.08 * res else c1
                out.append((c0, c1, r0, r1, d))
                boxed[r0:r1, c0:c1] = True
                todo[r0:r1, c0:c1] &= D[r0:r1, c0:c1] > level + RELIEF_LEVEL_TOL
    return out


RELIEF_BIG_WIDTH = 0.5  # m; a rectangle at least this wide ...
RELIEF_BIG_AREA = 0.25  # m2; ... and this large is a fixture in its own right, and so is one ...
RELIEF_TALL = 1.5  # m; ... at least this tall (a wall bump, a door folded back against the wall)
RELIEF_SAME_FRONT = 0.07  # m; a small rectangle continues a big one when their depths differ by less
RELIEF_BUILT_IN = 0.15  # m; ... or is built in between two when its depth is within this of theirs


def _drop_lone_small(rects: list, res: float = RELIEF_RES) -> list:
    """Keep the rectangles that are fixtures in their own right (cabinets, appliances, tub fronts, doors)
    and the small ones that belong to such a fixture:
    - continuing it: touching it along a side with the front within RELIEF_SAME_FRONT (the rest of a
      fridge, the stove's control panel), or
    - built in: kept rectangles on two opposite sides of it, fronts within RELIEF_BUILT_IN (a microwave
      between wall cabinets, a stove between counters).
    A small rectangle on its own, or standing in front of a single larger one (a toilet's tank against a
    wall bump, a pedestal sink's column), is the flat part of something round and would turn half of that
    object into a block; it stays scan geometry."""

    def big(r) -> bool:
        w, h = (r[1] - r[0]) / res, (r[3] - r[2]) / res
        return (w >= RELIEF_BIG_WIDTH and w * h >= RELIEF_BIG_AREA) or h >= RELIEF_TALL

    gap = int(0.08 * res)

    def side(r, q) -> str | None:
        """Which side of r the rectangle q lies on (touching, not overlapping r's middle), if any."""
        dx, dy = max(q[0] - r[1], r[0] - q[1]), max(q[2] - r[3], r[2] - q[3])  # gaps; negative = overlap
        cx, cy = (r[0] + r[1]) / 2, (r[2] + r[3]) / 2
        if q[0] <= cx <= q[1] and q[2] <= cy <= q[3]:
            return None  # q covers r's middle: r stands in front of it
        if max(dx, dy) > gap or min(dx, dy) >= 0:
            return None  # apart, or corner to corner
        if dy < 0 and dx >= -gap:
            return "left" if q[1] <= cx else "right"
        if dx < 0 and dy >= -gap:
            return "above" if q[3] <= cy else "below"
        return None

    keep = [big(r) for r in rects]
    grew = True
    while grew:
        grew = False
        for i, r in enumerate(rects):
            if keep[i]:
                continue
            sides = set()
            for j, q in enumerate(rects):
                where = side(r, q) if keep[j] else None
                if where is None:
                    continue
                diff = abs(q[4] - r[4])
                if diff <= RELIEF_SAME_FRONT:
                    sides.add("same")
                if diff <= RELIEF_BUILT_IN:
                    sides.add(where)
            if "same" in sides or {"left", "right"} <= sides or {"above", "below"} <= sides:
                keep[i] = grew = True
    return [r for r, k in zip(rects, keep, strict=True) if k]


def _snap_depths(rects: list[tuple[int, int, int, int, float]], tol: float = 0.035) -> list:
    """Give rectangles on one wall whose depths differ by less than tol the same depth (area-weighted mean),
    so the pieces of one cabinet run or one fridge do not step by a centimetre or two where they meet."""
    order = sorted(range(len(rects)), key=lambda i: rects[i][4])
    out = list(rects)
    group: list[int] = []
    for i in [*order, None]:
        if i is not None and (not group or rects[i][4] - rects[group[-1]][4] < tol):
            group.append(i)
            continue
        if group:
            area = [(rects[k][1] - rects[k][0]) * (rects[k][3] - rects[k][2]) for k in group]
            d = float(np.average([rects[k][4] for k in group], weights=area))
            for k in group:
                out[k] = (*rects[k][:4], d)
        group = [i] if i is not None else []
    return out


def relief_boxes(scan: Scan, rooms: list[ShellRoom], debug: Path | None = None) -> list[Box]:
    """Every flat-fronted fixture standing out from a wall (or from an open stretch of a room's outline,
    like a kitchen peninsula on the line between kitchen and living room), as boxes."""
    boxes: list[Box] = []
    for ri, r in enumerate(rooms):
        height = room_top(r) - r.floor_z
        runs = relief_runs(r)
        for wi, (p0, p1, length, covered) in enumerate(runs):
            if length < RELIEF_MIN_SIDE:
                continue
            # Is there a wall around the corner at each end of this run? Then a box that reaches the end
            # needs no side face there.
            prev, nxt = runs[wi - 1], runs[(wi + 1) % len(runs)]
            wall_before = any(b > prev[2] - 0.3 for _, b in prev[3])
            wall_after = any(a < 0.3 for a, _ in nxt[3])
            D = wall_relief(scan, r, p0, p1, length)
            rects = _snap_depths(_drop_lone_small(relief_rects(D)))
            if debug is not None:
                _relief_debug(D, rects, debug / f"relief_room{ri + 1}_wall{wi}.png")
            u = np.array([p1[0] - p0[0], p1[1] - p0[1], 0.0]) / length
            n = np.array([-u[1], u[0], 0.0])
            origin = np.array([p0[0], p0[1], r.floor_z])
            H = D.shape[0]
            for c0, c1, r0, r1, d in rects:
                s0, s1 = c0 / RELIEF_RES, min(c1 / RELIEF_RES, length)
                t0, t1 = max(height - r1 / RELIEF_RES, 0.0), height - r0 / RELIEF_RES
                if r1 >= H:
                    t0 = 0.0
                on_wall = sum(max(0.0, min(b, s1) - max(a, s0)) for a, b in covered)
                ends = (wall_before and s0 < 0.03, wall_after and s1 > length - 0.03)
                attached = on_wall > 0.5 * (s1 - s0)
                b = Box(origin, u, n, s0, s1, t0, t1, d, attached, ends, to_ceiling=r0 == 0, room=ri)
                if not attached:
                    b.back = _back_extent(scan, b)
                boxes.append(b)
    return boxes


def _back_extent(scan: Scan, b: Box, reach: float = 0.5) -> float:
    """For a box on an open stretch of the room outline: how far the fixture goes on behind the outline
    (a half wall under a breakfast bar stands partly in the next room). Rays from behind look for the
    surface that faces away from the room; the median over the box's rectangle, 0 when most rays miss."""
    s = np.arange(b.s0 + 0.03, b.s1, 0.06)
    t = np.arange(b.t0 + 0.03, b.t1, 0.06)
    S, T = np.meshgrid(s, t)
    pts = b.origin + S.ravel()[:, None] * b.u + T.ravel()[:, None] * [0, 0, 1.0] - b.n * reach
    tt, prim = scan.rays.cast(pts, np.tile(b.n, (len(pts), 1)))
    ok = (prim >= 0) & (tt < reach + b.depth)
    ok[ok] &= scan.normals[prim[ok]] @ b.n < -0.2
    if ok.mean() < 0.5:
        return 0.0
    return float(np.clip(np.median(reach - tt[ok]), 0.0, reach))


LEAF_MAX_BULGE = 0.06  # m; 90% of a leaf's scanned surface lies within this of its plane
LEAF_MIN_AREA = 0.6  # m2 of scanned surface


def leaf_boxes(scan: Scan, faces: np.ndarray, rooms: list[ShellRoom]) -> list[Box]:
    """Thin, flat, upright pieces that stand free in a room (an open door leaf, a shower screen, a wall
    stub that is not in the floor plan) as slabs. They scan as crumpled sheets; a slab textured from the
    photos is a clean door.

    A connected piece of scan qualifies when its best-fit plane is vertical, 90% of its surface lies within
    LEAF_MAX_BULGE of that plane, it starts near the floor, is at least 1.6 m tall and 0.4 to 1.3 m wide,
    and the scan covers at least half of that rectangle. The slab is as thick as the scan says (twice the
    median distance to the plane, at least 4 cm)."""
    if not len(faces):
        return []
    soup = scan.tris[faces].reshape(-1, 3)
    m = trimesh.Trimesh(soup, np.arange(len(soup)).reshape(-1, 3), process=False)
    m.merge_vertices()
    up = np.array([0.0, 0.0, 1.0])
    out = []
    for comp in trimesh.graph.connected_components(m.face_adjacency, nodes=np.arange(len(faces))):
        f = faces[comp]
        area = scan.areas[f]
        if area.sum() < LEAF_MIN_AREA:
            continue
        pts = scan.tris[f].reshape(-1, 3)
        w = np.repeat(area / 3, 3)
        mu = np.average(pts, axis=0, weights=w)
        _, vec = np.linalg.eigh(np.cov((pts - mu).T, aweights=w))
        n = vec[:, 0]
        if abs(n[2]) > 0.15:
            continue  # not upright
        n = np.array([n[0], n[1], 0.0]) / math.hypot(n[0], n[1])
        u = np.cross(n, up)  # (u, n, up) is right-handed, like a wall run and its inward normal
        dist = np.abs((pts - mu) @ n)
        if np.quantile(dist, 0.9) > LEAF_MAX_BULGE:
            continue
        s = (pts - mu) @ u
        s0, s1 = np.quantile(s, [0.02, 0.98])
        z0, z1 = np.quantile(pts[:, 2], [0.02, 0.98])
        ri = next((i for i, r in enumerate(rooms) if r.poly.contains(Point(mu[0], mu[1]))), None)
        floor = None if ri is None else rooms[ri].floor_z
        if floor is None or z1 - floor < 1.6 or z0 - floor > 0.25 or not 0.4 <= s1 - s0 <= 1.3:
            continue
        if area.sum() < 0.5 * (s1 - s0) * (z1 - z0):
            continue  # too little of the rectangle was scanned to trust it
        thick = max(0.04, 2 * float(np.median(dist)))
        origin = mu + u * s0 - n * thick / 2
        origin[2] = floor
        leaf = Box(origin, u, n, 0.0, float(s1 - s0), 0.0, float(z1 - floor), thick, attached=False, room=ri)
        out.append(leaf)
    return out


LEDGE_MIN_AREA = 0.25  # m2 of flat top
LEDGE_Z = (0.5, 1.6)  # m above the floor: bars, shelves, window seats (lower is a hearth or a step)


def ledge_boxes(scan: Scan, faces: np.ndarray, rooms: list[ShellRoom]) -> list[Box]:
    """Flat, level slabs that stand free of the walls' fronts (a breakfast bar's ledge, a deep shelf) as
    boxes. No wall "sees" them in its relief map: from the side a ledge is a strip a few cm high.

    A connected piece of the left-over scan qualifies when its upward-facing triangles have at least
    LEDGE_MIN_AREA within 4 cm of one height (and that is 60% of all its upward-facing area), the height is
    within LEDGE_Z above the floor, and those triangles fill 60% of their bounding rectangle, taken along
    the direction of the nearest room's walls that makes it smallest. The slab is as thick as the scan says
    (down to the piece's downward-facing triangles, 3 to 15 cm; 4 cm when it has none)."""
    if not len(faces):
        return []
    soup = scan.tris[faces].reshape(-1, 3)
    m = trimesh.Trimesh(soup, np.arange(len(soup)).reshape(-1, 3), process=False)
    m.merge_vertices()
    out = []
    for comp in trimesh.graph.connected_components(m.face_adjacency, nodes=np.arange(len(faces))):
        f = faces[comp]
        up = f[scan.normals[f, 2] > 0.7]
        if scan.areas[up].sum() < LEDGE_MIN_AREA:
            continue
        z = scan.tris[up].mean(1)[:, 2]
        order = np.argsort(z)
        top = float(z[order][np.searchsorted(np.cumsum(scan.areas[up][order]), 0.5 * scan.areas[up].sum())])
        flat = up[np.abs(z - top) < 0.04]
        area = float(scan.areas[flat].sum())
        if area < LEDGE_MIN_AREA or area < 0.6 * scan.areas[up].sum():
            continue
        pts = scan.tris[flat].reshape(-1, 3)
        mid = pts[:, :2].mean(0)
        ri = next((i for i, r in enumerate(rooms) if r.poly.contains(Point(mid))), None)
        if ri is None or not LEDGE_Z[0] <= top - rooms[ri].floor_z <= LEDGE_Z[1]:
            continue
        best = None
        for p0, p1, length, _ in wall_runs(rooms[ri]):
            u = (p1 - p0) / length
            n = np.array([-u[1], u[0]])
            s, d = (pts[:, :2] - mid) @ u, (pts[:, :2] - mid) @ n
            (s0, s1), (d0, d1) = np.quantile(s, [0.02, 0.98]), np.quantile(d, [0.02, 0.98])
            if best is None or (s1 - s0) * (d1 - d0) < best[0]:
                best = ((s1 - s0) * (d1 - d0), u, n, s0, s1, d0, d1)
        if best is None:
            continue
        rect, u, n, s0, s1, d0, d1 = best
        if area < 0.6 * rect or min(s1 - s0, d1 - d0) < 0.12:
            continue
        down = f[scan.normals[f, 2] < -0.7]
        under = scan.tris[down].mean(1)[:, 2] if len(down) else np.zeros(0)
        under = under[(under < top - 0.03) & (under > top - 0.15)]
        bottom = float(np.median(under)) if len(under) else top - 0.04
        floor = rooms[ri].floor_z
        origin = np.array([*(mid + u * s0 + n * d0), floor])
        u3, n3 = np.array([u[0], u[1], 0.0]), np.array([n[0], n[1], 0.0])
        z0, z1 = bottom - floor, top - floor
        out.append(Box(origin, u3, n3, 0.0, float(s1 - s0), z0, z1, float(d1 - d0), False, room=ri))
    return out


def _corners(b: Box) -> np.ndarray:
    ring = np.asarray(b.footprint().exterior.coords)[:-1]
    return np.array([[x, y, z] for x, y in ring for z in (b.z0, b.z1)])


def drop_contained(boxes: list[Box], grow: float = 0.04) -> list[Box]:
    """Remove boxes that lie inside another box (all eight corners within `grow` of it): the same pantry
    measured from two walls, the upper part of a cabinet found again at a second depth level. Of two that
    contain each other the earlier one stays."""
    gone: set[int] = set()
    for i, b in enumerate(boxes):
        for j, other in enumerate(boxes):
            if i == j or j in gone or not other.contains(_corners(b), grow).all():
                continue
            if b.contains(_corners(other), grow).all() and i < j:
                continue  # twins: the later one goes
            gone.add(i)
            break
    return [b for i, b in enumerate(boxes) if i not in gone]


RISER_MAX_GAP = 0.45  # m between a box's top and the ledge above it


def ledge_risers(scan: Scan, boxes: list[Box], ledges: list[Box]) -> list[Box]:
    """The piece of half wall between a free-standing box and the ledge above it. A breakfast bar is a half
    wall with a counter on one side and a ledge on top; the counter's box ends at counter height, the
    ledge floats 10 to 40 cm higher, and the wall between them is thin, seen edge-on by the relief maps,
    and so was left as crumpled scan with gaps.

    For every ledge and every box under it that is not attached to a wall (gap at most RISER_MAX_GAP,
    footprints overlapping by 0.05 m2): a box from the lower box's back face to the wall's other face
    (found by rays from the lower box's side between the two heights; 12 cm when they find nothing),
    under the ledge only."""
    up = np.array([0.0, 0.0, 1.0])
    out = []
    for ledge in ledges:
        for b in boxes:
            gap = ledge.z0 - b.z1
            if b.attached or any(b is x for x in ledges) or not 0.03 <= gap <= RISER_MAX_GAP:
                continue
            both = b.footprint().intersection(ledge.footprint())
            if both.is_empty or both.area < 0.05:
                continue
            ring = np.asarray(both.exterior.coords)[:-1] - b.origin[:2]
            s, d = ring @ b.u[:2], ring @ b.n[:2]
            s0, s1, lo, hi = float(s.min()), float(s.max()), max(float(d.min()), -b.back), float(d.max())
            # Where is the wall's face on the box's side? Rays from 0.5 m out, toward the back.
            ss = np.arange(s0 + 0.05, s1, 0.06)
            zz = np.arange(b.z1 + 0.04, ledge.z0 - 0.02, 0.04)
            S, Z = np.meshgrid(ss, zz)
            start = lo + 0.6
            pts = b.origin + S.ravel()[:, None] * b.u + start * b.n + (Z.ravel() - b.origin[2])[:, None] * up
            t, prim = scan.rays.cast(pts, np.tile(-b.n, (len(pts), 1)))
            ok = (prim >= 0) & (t < 0.6 + 0.02)
            ok[ok] &= scan.normals[prim[ok]] @ b.n > 0.3
            face = float(np.median(start - t[ok])) if len(pts) and ok.mean() > 0.4 else lo + 0.12
            face = float(np.clip(face, lo + 0.06, max(hi, lo + 0.06)))
            origin = b.origin + b.n * lo
            origin[2] = b.origin[2]
            riser = Box(origin, b.u, b.n, s0, s1, b.t1, ledge.z0 - b.origin[2], face - lo, False, room=b.room)
            out.append(riser)
    return out


def _write_box_list(boxes: list[Box], dest: Path) -> None:
    """The fitted boxes as JSON (plan coordinates, metres), for finding the one to drop by hand."""
    import json

    rows = []
    for b in boxes:
        c = b.origin + b.u * (b.s0 + b.s1) / 2 + b.n * (b.depth - b.back) / 2
        rows.append(
            {
                "centre": [round(float(c[0]), 2), round(float(c[1]), 2), round((b.z0 + b.z1) / 2, 2)],
                "width": round(b.s1 - b.s0, 2),
                "depth": round(b.depth + b.back, 2),
                "z": [round(b.z0, 2), round(b.z1, 2)],
                "attached_to_wall": b.attached,
            }
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(rows, indent=1) + "\n", encoding="utf-8")


def _relief_debug(D: np.ndarray, rects: list, dest: Path) -> None:
    """Relief map (brighter = further out of the wall) with the fitted rectangles outlined in red."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    g = (np.clip(D / RELIEF_REACH, 0, 1) * 255).astype(np.uint8)
    img = np.kron(np.dstack([g, g, g]), np.ones((4, 4, 1), np.uint8))
    for c0, c1, r0, r1, _ in rects:
        a, b, c, d = c0 * 4, c1 * 4 - 1, r0 * 4, r1 * 4 - 1
        img[c : d + 1, [a, a + 1, b - 1, b]] = (255, 0, 0)
        img[[c, c + 1, d - 1, d], a : b + 1] = (255, 0, 0)
    Image.fromarray(img).save(dest)


def _hidden(shape: Polygon, face_origin, fu, fv, fn, fw: float, own: Box, others: list[Box], idx: int):
    """The part of a box face (shape, in the face's own (s, t) metres) not buried in another box. A face
    that lies in the same plane as a face of another box, looking the same way (two cabinet fronts at one
    depth), is a tie: the earlier box keeps the overlap and the later one loses it.

    A tie is judged at the middle of the face (distance to the other face's plane under 1.5 cm), not by
    comparing plane offsets: boxes fitted from different walls can be turned by half a degree against each
    other, and then each face looked buried in the other box and both were removed."""
    grow = 0.006
    centre = face_origin + fu * fw / 2 + fv * shape.bounds[3] / 2
    for j, other in enumerate(others):
        if other is own:
            continue
        tie = any(float(fn @ m) > 0.99 and abs(float(m @ centre) - o) < 0.015 for m, o in other.face_planes())
        if tie and j > idx:
            continue
        ring = np.asarray(other.footprint(0.0 if tie else grow).exterior.coords)[:-1] - face_origin[:2]
        if abs(fn[2]) > 0.5:  # top or bottom: buried where the other box spans this height
            z = face_origin[2]
            if not (other.z0 - grow < z < other.z1 + grow):
                continue
            cut = Polygon(np.c_[ring @ fu[:2], ring @ fv[:2]])
        else:  # vertical face
            g = 0.0 if tie else grow  # a tie is cut exactly: any margin leaves a slit between the faces
            lo, hi = other.z0 - g - face_origin[2], other.z1 + g - face_origin[2]
            if tie:  # the other box's face covers ours along its whole width
                along = ring @ fu[:2]
                cuts = [box(float(along.min()), lo, float(along.max()), hi)]
            else:  # buried along the stretch of its line that lies inside the other footprint
                a = face_origin[:2]
                line = LineString([a, a + fu[:2] * fw])
                cuts = [box(s0, lo, s1, hi) for s0, s1 in _intervals(line, other.footprint(grow))]
            if not cuts:
                continue
            cut = unary_union(cuts)
        shape = shape.difference(cut)
        if shape.is_empty:
            return None
    return shape


def box_planes(boxes: list[Box]) -> list[Plane]:
    """Textured faces of the boxes (kind "box"): front, the two sides, top and bottom, and a back where no
    wall stands behind the box. Faces that touch the floor, the room's own walls or lie inside another box
    are left out."""
    up = np.array([0.0, 0.0, 1.0])
    planes: list[Plane] = []
    for i, b in enumerate(boxes):
        w, h, deep = b.s1 - b.s0, b.t1 - b.t0, b.depth + b.back
        corner = b.origin + b.s0 * b.u + b.t0 * up - b.back * b.n
        faces = [(corner + deep * b.n, b.u, up, b.n, w, h)]  # front
        if not b.wall_ends[0]:
            faces.append((corner, b.n, up, -b.u, deep, h))
        if not b.wall_ends[1]:
            faces.append((corner + w * b.u, b.n, up, b.u, deep, h))
        if not b.to_ceiling:
            faces.append((corner + h * up, b.u, b.n, up, w, deep))  # top
        if b.t0 > 0.03:
            faces.append((corner, b.u, b.n, -up, w, deep))
        if not b.attached:
            faces.append((corner, b.u, up, -b.n, w, h))
        for origin, fu, fv, fn, fw, fh in faces:
            shape = _hidden(box(0, 0, fw, fh), origin, fu, fv, fn, fw, b, boxes, i)
            if shape is None or shape.area < 0.004:
                continue
            p = Plane("box", origin, fu, fv, fn, fw, fh, shape, carve=False)
            p.box = i
            planes.append(p)
    return planes


def _paint(p: Plane, colour: np.ndarray, source: Plane | None) -> None:
    """Give the unseen texels of p a colour plus, when `source` (a well-seen plane of the same material)
    has an ordinary patch, that patch's fine texture."""
    H, W = p.unseen.shape
    paint = np.tile(colour.astype(float), (H, W, 1))
    if source is not None:
        tile = _detail_tile(source.image[..., :3], source.shape_mask & ~source.unseen, H, W)
        if tile is not None:
            paint += tile
    p.image[..., :3][p.unseen] = np.clip(paint, 0, 255).astype(np.uint8)[p.unseen]


def _paint_unseen_planes(planes: list[Plane]) -> None:
    """A wall, floor or ceiling that no camera saw (the wall behind a shower curtain, a closet nobody
    scanned) takes the median colour, and the fine texture, of what cameras did see of the same kind of
    surface in its room, or in the whole house when its room has none. Without this it is a flat grey that
    belongs to no room."""

    def colour_of(group: list[Plane]) -> np.ndarray | None:
        px = [p.image[..., :3][p.shape_mask & ~p.unseen][::17] for p in group if p.seen >= 0.3]
        px = [x for x in px if len(x)]
        return np.median(np.concatenate(px), axis=0).astype(np.uint8) if px else None

    def best_of(group: list[Plane]) -> Plane | None:
        group = [p for p in group if p.seen >= 0.3]
        return max(group, key=lambda p: p.seen * p.shape.area) if group else None

    for kind in ("wall", "floor", "ceiling"):
        of_kind = [p for p in planes if p.kind == kind]
        for p in of_kind:
            if p.seen >= UNSEEN_PLANE:
                continue
            group = [q for q in of_kind if q.room == p.room and q.room >= 0]
            if colour_of(group) is None:
                group = of_kind
            colour = colour_of(group)
            if colour is not None:
                _paint(p, colour, best_of(group))


BOX_MIN_SEEN = 0.35  # a box whose front the cameras see less of than this is not trusted


def seen_boxes(boxes: list[Box], scan: Scan, panos: PanoSet) -> tuple[list[Box], int]:
    """Drop boxes whose front no camera really sees: the relief found a surface there, but the photos
    look straight through it (scan debris, the far side of a curtain). Their scan triangles stay as they
    were. The check bakes each front at low resolution. Returns (kept boxes, number dropped)."""
    if not boxes:
        return boxes, 0
    fronts: dict[int, Plane] = {}
    for p in box_planes(boxes):
        fronts.setdefault(p.box, p)  # box_planes lists the front first; it is missing when fully hidden
    keep = []
    for i, b in enumerate(boxes):
        p = fronts.get(i)
        if p is not None and float(p.n @ b.n) > 0.9:
            bake_plane(p, scan, panos, 32.0)
            if p.seen < BOX_MIN_SEEN:
                continue
        keep.append(b)
    return keep, len(boxes) - len(keep)


def _paint_unseen_faces(faces: list[Plane]) -> None:
    """Box faces that cameras saw almost nothing of (the top of a tall cabinet, the side of a fridge in a
    corner) take the median colour and the fine texture of the best-seen face of their box where nothing
    was seen: the side of a wooden cabinet becomes plain wood, not a blur of the few texels that were."""
    by_box: dict[int, list[Plane]] = {}
    for p in faces:
        by_box.setdefault(p.box, []).append(p)
    for group in by_box.values():
        best = max(group, key=lambda p: p.seen * p.shape.area)
        if best.seen < 0.2:
            continue
        seen = best.shape_mask & ~best.unseen
        colour = np.median(best.image[..., :3][seen].reshape(-1, 3), axis=0)
        for p in group:
            if p.seen < 0.15:
                _paint(p, colour, best)


def in_boxes(pts: np.ndarray, boxes: list[Box], grow: float = BOX_TOL) -> np.ndarray:
    """Which points lie in (or within `grow` of) any box."""
    out = np.zeros(len(pts), bool)
    for b in boxes:
        out |= b.contains(pts, grow)
    return out


# ---------------------------------------------------------------- pieces (scan geometry)


def _under_a_ceiling(scan: Scan, faces: np.ndarray, reach: float = 0.8) -> np.ndarray:
    """Which faces have a ceiling (a downward-facing scan surface) within `reach` above them. True for a
    fan blade or a lamp on a short rod; False for the ceiling itself and for a beam, which are the top
    layer. The ray starts just behind the face, so a beam's side starts inside the beam and finds nothing.
    `reach` is short on purpose: scans often hold loose surfaces a metre above a ceiling (roof, attic)."""
    out = np.zeros(len(faces), bool)
    if not len(faces):
        return out
    o = scan.tris[faces].mean(1) - scan.normals[faces] * 0.02
    up = np.tile([0.0, 0.0, 1.0], (len(faces), 1))
    todo = np.arange(len(faces))
    walked = np.zeros(len(faces))
    for _ in range(4):
        if not len(todo):
            break
        t, prim = scan.rays.cast(o[todo] + up[todo] * walked[todo, None], up[todo])
        hit = (prim >= 0) & (walked[todo] + t < reach)
        ceiling = np.zeros(len(todo), bool)
        ceiling[hit] = scan.normals[prim[hit], 2] < -0.3
        ceiling &= walked[todo] + t > 0.05  # not the face itself
        out[todo[ceiling]] = True
        again = hit & ~ceiling
        walked[todo[again]] += t[again] + 1e-3
        todo = todo[again]
    return out


HANG_MAX_FACE = 0.3  # m2; a larger triangle is a piece of ceiling, not of a fan
HANG_MAX_SPAN = 2.2  # m; a hanging object (and its stand-in solid) is at most this long
HANG_MIN_Z = 1.75  # m above the floor; a stand-in solid never reaches below this


def _clumps(scan: Scan, faces: np.ndarray, grow: float = 0.1) -> list[tuple[Polygon, np.ndarray]]:
    """Groups of faces that touch when seen from above (each outline grown by `grow`) and lie in the same
    height layer (layers are separated by 0.4 m with no face in it), as (outline, positions into `faces`)."""
    if not len(faces):
        return []
    centres = scan.tris[faces].mean(1)
    order = np.argsort(centres[:, 2])
    layer = np.zeros(len(faces), int)
    layer[order] = np.cumsum(np.r_[0, np.diff(centres[order, 2]) > 0.4])
    out = []
    for k in range(layer.max() + 1):
        members = np.nonzero(layer == k)[0]
        tri = scan.tris[faces[members]]
        polys = [Polygon(t[:, :2]).buffer(grow) for t in tri if Polygon(t[:, :2]).area > 1e-6]
        blobs = unary_union(polys) if polys else None
        if blobs is None or blobs.is_empty:
            continue
        for blob in [blobs] if blobs.geom_type == "Polygon" else list(blobs.geoms):
            inside = members[shapely.contains_xy(blob, centres[members, 0], centres[members, 1])]
            if len(inside):
                out.append((blob, inside))
    return out


def _span(blob: Polygon) -> float:
    minx, miny, maxx, maxy = blob.bounds
    return float(math.hypot(maxx - minx, maxy - miny))


def _in_compact_clumps(scan: Scan, faces: np.ndarray) -> np.ndarray:
    """Which faces belong to a clump no longer than HANG_MAX_SPAN (a fan, a lamp), as opposed to a long
    strip (the side of a beam) or a wide patch (a lower part of the ceiling)."""
    out = np.zeros(len(faces), bool)
    for blob, inside in _clumps(scan, faces):
        if _span(blob) <= HANG_MAX_SPAN:
            out[inside] = True
    return out


def piece_faces(scan: Scan, rooms: list[ShellRoom]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Scan faces to keep as geometry, as (fixture indices, scanned-ceiling indices), plus the indices of
    hanging things that were dropped.

    A face inside a room is dropped only when a shell plane replaces it: it lies within SHELL_TOL of the
    floor, a flat ceiling or a wall AND is roughly parallel to it. Faces across the wall thickness (door
    jambs, window reveals) and everything else stay. Things hanging from the ceiling away from the walls
    (fans, pendant lamps: they scan as crumpled sheets) are dropped: in a flat-ceiling room anything above
    1.9 m, in a vaulted room anything above 1.9 m that has ceiling above it."""
    c = scan.tris.mean(1)
    nrm = scan.normals
    keep = np.zeros(len(c), bool)
    ceil = np.zeros(len(c), bool)
    hang = np.zeros(len(c), bool)
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
        high = k & (z > 1.9) & (wall_d > 0.35)  # hanging fans and lamps
        if not r.flat_ceiling:
            # Part of a vaulted ceiling can be lower than another part: only what has ceiling above it,
            # is made of small triangles and forms a compact clump is a hanging object.
            high &= scan.areas[idx] < HANG_MAX_FACE
            high[high] = _under_a_ceiling(scan, idx[high])
            high[high] = _in_compact_clumps(scan, idx[high])
        hang[idx[high]] = True
        k &= ~high
        if not r.flat_ceiling:
            is_ceiling = (z > 1.8) & (nrm[idx, 2] < -0.3)
            ceil[idx[k & is_ceiling]] = True
            k &= ~is_ceiling
            # Ceiling triangles that reach into this room but whose centre lies in the next one: that
            # room's rules would drop them (its flat ceiling plane stops at the line between the rooms),
            # which left triangular holes along the edge of a vaulted ceiling.
            v = scan.tris[:, :, :2].reshape(-1, 2)
            touches = shapely.contains_xy(r.poly, v[:, 0], v[:, 1]).reshape(-1, 3).any(1)
            touches[idx] = False
            zc = c[:, 2] - r.floor_z
            ceil |= touches & (nrm[:, 2] < -0.3) & (zc > 1.8) & (zc < top + 0.3)
        keep[idx[k]] = True
    keep &= ~ceil
    fixtures, ceilings = _drop_crumbs(scan, np.nonzero(keep)[0]), _drop_crumbs(scan, np.nonzero(ceil)[0])
    return fixtures, ceilings, np.nonzero(hang)[0]


def hanging_solids(scan: Scan, faces: np.ndarray, rooms: list[ShellRoom]) -> list[np.ndarray]:
    """Stand-in solids (one (n, 3, 3) triangle array each) for dropped hanging things: per clump, the
    outline of its faces seen from above, grown a little, extruded over its height. A fan scans as a few
    crumpled blades, so the scan alone lets most of its photo through onto the ceiling; the solid blocks
    it.

    A solid hides whatever is behind it from the cameras, so it is only made for a clump that really is a
    hanging object: no longer than HANG_MAX_SPAN and entirely above HANG_MIN_Z over the floor (cameras
    stand lower than that, so none can end up inside one). Other dropped faces (the top of an open door, a
    tall cabinet's upper edge) get no solid."""
    out = []
    for blob, inside in _clumps(scan, faces):
        if scan.areas[faces[inside]].sum() < 0.05 or _span(blob) > HANG_MAX_SPAN:
            continue  # a stray crumb, or too big to be one object
        z = scan.tris[faces[inside]][..., 2]
        centre = blob.representative_point()
        floor = next((r.floor_z for r in rooms if r.poly.contains(centre)), None)
        if floor is None or z.min() - floor < HANG_MIN_Z:
            continue
        z0, z1 = z.min() - 0.03, z.max() + 0.03
        # Fan blades scan badly: the real fan is wider than what is left of it in the mesh.
        solid = trimesh.creation.extrude_polygon(blob.convex_hull.buffer(0.2), height=float(z1 - z0))
        solid.apply_translation([0, 0, z0])
        out.append(np.asarray(solid.triangles))
    return out


def _shade_local(
    pts: np.ndarray, normals: np.ndarray, panos: PanoSet, rays: Raycaster, cell: float = 3.0
) -> tuple[np.ndarray, np.ndarray]:
    """`shade` for points scattered over the whole house, called once per 3 m grid cell.

    `shade` considers the 12 panoramas nearest to the centre of the points it is given. That suits one
    wall; given every fixture triangle of a house at once, the centre is the middle of the house and a
    bathroom at the far end has no candidate camera: its toilet counted as unseen and was dropped, and its
    shower curtain was coloured from the MatterPak texture instead of the photos."""
    rgb = np.zeros((len(pts), 3))
    valid = np.zeros(len(pts), dtype=bool)
    if not len(pts):
        return rgb, valid
    key = np.floor(pts[:, :2] / cell).astype(np.int64)
    key = key[:, 0] * 100003 + key[:, 1]
    for k in np.unique(key):
        sel = np.nonzero(key == k)[0]
        rgb[sel], valid[sel] = shade(pts[sel], normals[sel], panos, rays, snap=False)
    return rgb, valid


SEEN_PIECE = 0.25  # a connected piece of scan that cameras see at least this much of is kept whole


def _pieces(scan: Scan, faces: np.ndarray) -> list[np.ndarray]:
    """Connected pieces of a set of scan faces, as positions into `faces`."""
    if not len(faces):
        return []
    soup = scan.tris[faces].reshape(-1, 3)
    m = trimesh.Trimesh(soup, np.arange(len(soup)).reshape(-1, 3), process=False)
    m.merge_vertices()
    return list(trimesh.graph.connected_components(m.face_adjacency, nodes=np.arange(len(faces))))


def _seen(scan: Scan, faces: np.ndarray, panos: PanoSet) -> np.ndarray:
    """The faces to keep, judged by what the panoramas see (a face counts when its centre is seen from its
    front side): a connected piece that is seen over at least SEEN_PIECE of its area is kept whole; of a
    piece seen less, only the seen faces stay (the roof above a vaulted ceiling, junk inside walls).

    Whole pieces, because a small bathroom has one scan position: the camera saw one side of the toilet,
    Matterport's mesh has all of it, and keeping only the seen faces left half a toilet. Faces kept
    without being seen take the scan's own texture (bake_triangles)."""
    if not len(faces):
        return faces
    # Tested against the scan alone, not the stand-in solids: the ceiling above a removed fan stays (its
    # texels fall back to the scan's own texture) instead of leaving a hole.
    _, valid = _shade_local(scan.tris[faces].mean(1), scan.normals[faces], panos, scan.rays)
    keep = valid.copy()
    area = scan.areas[faces]
    for comp in _pieces(scan, faces):
        if area[comp][valid[comp]].sum() >= SEEN_PIECE * area[comp].sum():
            keep[comp] = True
    return faces[keep]


def _drop_crumbs(scan: Scan, faces: np.ndarray, min_area: float = 0.05) -> np.ndarray:
    """Remove connected pieces smaller than min_area m2 (scan noise)."""
    out = [faces[comp] for comp in _pieces(scan, faces) if scan.areas[faces[comp]].sum() >= min_area]
    return np.sort(np.concatenate(out)) if out else np.zeros(0, int)


def removal_solids(scan: Scan, faces: np.ndarray) -> list[np.ndarray]:
    """A stand-in solid for scan faces removed by hand (clutter left in the house: a bin, a ladder): the
    convex outline of the faces seen from above over their height, used for line of sight only, so the
    object's photo is not printed on the wall and floor behind it."""
    if len(faces) < 3:
        return []
    v = scan.tris[faces].reshape(-1, 3)
    hull = shapely.MultiPoint(v[:, :2]).convex_hull.buffer(0.05)
    if hull.area < 1e-4:
        return []
    z0, z1 = float(v[:, 2].min()), float(v[:, 2].max()) + 0.03
    solid = trimesh.creation.extrude_polygon(hull, height=z1 - z0)
    solid.apply_translation([0, 0, z0])
    return [np.asarray(solid.triangles)]


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
        rgb, valid = _shade_local(pts.reshape(-1, 3), nrm.reshape(-1, 3), panos, scan.eyes)
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
    debug: Path | None = None,
    fit_boxes: bool = True,
    drop_boxes: list[tuple[float, float, float]] | None = None,
    only: set[int] | None = None,
    remove: list[tuple[float, float, float, float]] | None = None,
) -> tuple[dict, np.ndarray]:
    """interior.glb: shell nodes `floor_N`, `walls_N`, `ceiling_N`, box nodes `fixtures_box_N` and
    scan-geometry nodes `fixtures_N`, `ceiling_scan_N`, all textured from the panoramas. Returns (stats for
    the build report, the fixture triangles (n, 3, 3) in Z-up coordinates, for collision.glb).

    `only` (room indices) bakes just those rooms, with the whole house still deciding what cameras see:
    a quick partial glb for looking at a change (scripts/preview/bake_rooms.py), never a pack."""
    # Indoor cameras only: outdoor sweeps see doors open that indoor ones saw closed, and see the roof.
    indoors = unary_union([r.poly for r in rooms]).buffer(0.1)
    panos = panos.subset(
        [i for i, q in enumerate(panos.panos) if indoors.contains(Point(q.pos[0], q.pos[1]))]
    )
    for r in rooms:
        measure_ceiling(scan, r)
    fixtures, ceilings, hanging = piece_faces(scan, rooms)
    solids = hanging_solids(scan, hanging, rooms)
    # Hand removals (overrides/<model>.json "remove": clutter that was left in the house when scanned).
    for x, y, _, radius in remove or []:
        c = scan.tris[fixtures].mean(1)
        hit = np.hypot(c[:, 0] - x, c[:, 1] - y) < radius
        solids += removal_solids(scan, fixtures[hit])
        fixtures = fixtures[~hit]
    if solids:
        scan.sight = Raycaster(np.concatenate([scan.tris, *solids]))
    # Wall-attached fixtures with flat fronts (cabinets, counters, fridges, tub surrounds) become boxes.
    boxes = relief_boxes(scan, rooms, debug) if fit_boxes else []
    leaves: list[Box] = []
    ledges: list[Box] = []
    if fit_boxes:
        # Free-standing door leaves, from what the wall boxes left over (plus the dropped tops of doors).
        loose = np.concatenate([fixtures[~in_boxes(scan.tris[fixtures].mean(1), boxes)], hanging])
        leaves = leaf_boxes(scan, loose, rooms)
        ledges = ledge_boxes(scan, loose[~in_boxes(scan.tris[loose].mean(1), leaves)], rooms)
        boxes += leaves + ledges
    boxes = drop_contained(boxes)
    boxes, phantom = seen_boxes(boxes, scan, panos)
    # Hand fixes (overrides/<model>.json "drop_boxes"): a box that contains one of these points is not a
    # real fixture (the scan bulges in front of a mirror, say) and goes back to being scan triangles.
    if drop_boxes:
        pts = np.asarray(drop_boxes, float).reshape(-1, 3)
        boxes = [b for b in boxes if not b.contains(pts).any()]
    ledges = [b for b in ledges if any(b is x for x in boxes)]
    risers = ledge_risers(scan, boxes, ledges)
    boxes += risers
    if debug is not None:
        _write_box_list(boxes, debug / "boxes.json")
    if boxes:
        # From here on, what a camera sees is decided by the clean model: the scan with every triangle a
        # box replaces taken out and the boxes put in. The crumpled scan of a fridge otherwise decides,
        # texel by texel, which camera sees the fridge's flat face (holes and smears on the box), and
        # throws a ragged shadow on the wall beside it.
        inside = in_boxes(scan.tris.mean(1), boxes)
        scan.sight = Raycaster(np.concatenate([scan.tris[~inside], *[b.triangles() for b in boxes], *solids]))
    planes = shell_planes(rooms)
    if only is not None:
        planes = [p for p in planes if p.room in only]
        boxes_baked = [b for b in boxes if b.room in only]
        region = unary_union([rooms[i].poly for i in only])
        fixtures = fixtures[shapely.contains_xy(region, *scan.tris[fixtures].mean(1)[:, :2].T)]
        ceilings = ceilings[shapely.contains_xy(region, *scan.tris[ceilings].mean(1)[:, :2].T)]
    else:
        boxes_baked = boxes
    behind = unary_union([r.poly for r in rooms]).buffer(-0.02)
    stats, frames = [], []
    for p in planes:
        st, fr = bake_plane(p, scan, panos, res, behind)
        stats.append(st)
        for f in fr:
            f.room = p.room
        frames += fr
    for p in frames:
        bake_plane(p, scan, panos, res)
    faces = box_planes(boxes_baked)
    for p in faces:
        bake_plane(p, scan, panos, res)
    _paint_unseen_faces(faces)
    planes += frames
    _paint_unseen_planes(planes)
    planes += faces
    pages = pack(planes)
    scene = trimesh.Scene()
    mats = [_material(pg, f"shell_{i}") for i, pg in enumerate(pages)]
    nodes = (("floor", "floor"), ("wall", "walls"), ("ceiling", "ceiling"), ("box", "fixtures_box"))
    for kind, node in nodes:
        for pi in range(len(pages)):
            parts = [plane_mesh(p) for p in planes if p.page == pi and p.kind == kind]
            parts = [m for m in parts if m is not None]
            if not parts:
                continue
            m = trimesh.util.concatenate(parts)
            m.visual = trimesh.visual.TextureVisuals(uv=m.visual.uv, material=mats[pi])
            m.apply_transform(hm.Z_UP_TO_Y_UP)
            scene.add_geometry(m, node_name=f"{node}_{pi}", geom_name=f"{node}_{pi}")

    fixtures = fixtures[~in_boxes(scan.tris[fixtures].mean(1), boxes)]
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
        "planes": {k: sum(p.kind == k for p in planes) for k in ("wall", "floor", "ceiling", "box")},
        "relief_boxes": len(boxes),
        "door_leaf_slabs": len(leaves),
        "ledge_slabs": len(ledges),
        "ledge_risers": len(risers),
        "removed_by_hand": len(remove or []),
        "relief_boxes_unseen_dropped": phantom,
        "hanging_objects_removed": len(solids),
        "vaulted_rooms": sum(not r.flat_ceiling for r in rooms),
        "texture_pages": n_pages,
        "fixture_faces": int(len(fixtures)),
        "scan_ceiling_faces": int(len(ceilings)),
        "shell_seen_by_panos": round(sum(s["seen"] * s["area"] for s in stats) / max(area, 1e-9), 3),
        "carved_wall_m2": round(sum(s["carved_m2"] for s in stats), 2),
        "framed_openings": len(frames),
    }
    report["panoramas_indoor"] = len(panos)
    solid = [scan.tris[fixtures], *[b.triangles() for b in boxes]]
    return report, np.concatenate(solid)
