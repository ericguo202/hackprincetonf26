"""The house's scanned lot as playable outdoor space, textured from the outdoor panoramas.

Matterport scans of houses usually include outdoor sweeps (front yard, back yard, driveway). The MatterPak
mesh covers the whole lot, about 15 m around the house, with good ground, fence, deck and facade geometry.
This module rebuilds that outdoor part the same way rebake.py rebuilds the interior: clean geometry where
the scan is noisy, scan triangles where it is not, and every texel coloured from the original panoramas.

Parts (all Matterport Z-up plan coordinates until export):

1. Ground: a heightfield over the lot (the scan's lowest upward-facing surface per 0.15 m cell, median
   filtered so grass noise goes and steps such as a deck edge or a curb stay), clipped exactly to the lot
   outline and the house outline.
2. Facades: one flat plane per segment of the house outline, moved onto the real brick or siding face
   measured from the scan, from just below the ground up to the soffit.
3. Roof: the scan's roof when it has one (some scans include it). Otherwise a generated hip roof over the
   outline plus an overhang (eave height measured at the facades, pitch measured where the scan saw the
   roof, else 5/12), with a flat soffit and a fascia band.
4. Pieces: every other scan triangle outside the house that an outdoor camera sees (fences, deck railings,
   porch posts and roof, the AC unit, shrubs, trees). Tree canopies get an alpha cutout where the photo
   shows sky through the leaves.
5. Backdrop: past the scan's edge there is no geometry, but the photos show the street, the neighbours'
   houses and the sky. A ground disk plus a dome around the lot are textured from every outdoor panorama
   that sees each texel unobstructed (the occlusion test against the scan does the choosing). This is a
   baked, ground-anchored panorama: right near the capture points, with some parallax far from them.

Nodes added to interior.glb: `yard_N`, `facade_N`, `roof_N`, `outdoor_N`, `backdrop_N`. Collision adds
`ground`, `outdoor` (pieces players bump into) and `bounds` (an invisible fence at the edge of the lot)."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
import shapely
import trimesh
from scipy import ndimage
from shapely import affinity
from shapely.geometry import LineString, Point, Polygon, box
from shapely.geometry.polygon import orient
from shapely.ops import unary_union

from worldgen import house_mesh as hm
from worldgen import rebake as rb
from worldgen.panos import PanoSet, shade

CELL = 0.15  # ground heightfield sample spacing (m)
GROUND_RES = 96.0  # texels per metre on the yard (about 1 cm)
FACADE_RES = 128.0  # texels per metre on the facades (seen up close, like interior walls)
ROOF_RES = 64.0  # the roof is seen from below and far away
PIECE_RES = 96.0  # outdoor scan pieces
TREE_RES = 40.0  # tree canopies: seen from below, noisy geometry; their own (alpha-cut) texture pages
DOME_RES = 16.0  # backdrop texels per degree (the 2k skybox faces have about 23)
DISK_RES = 32.0  # backdrop ground texels per metre
OVERHANG = 0.45  # generated roof: eave overhang past the facade (m)
DEFAULT_PITCH = math.atan(5 / 12)
TREE_MIN_Z = 2.0  # pieces this far above the ground (and away from the roof) may be cut out where sky shows


# ---------------------------------------------------------------- helpers


def polygons(geom) -> list[Polygon]:
    """The polygons of a Polygon or MultiPolygon (or collection)."""
    if geom.is_empty:
        return []
    if geom.geom_type == "Polygon":
        return [geom]
    return [q for g in getattr(geom, "geoms", []) for q in polygons(g)]


def filled(geom):
    """geom without holes."""
    return unary_union([Polygon(q.exterior) for q in polygons(geom)])


def house_footprint(scan: rb.Scan, rooms: list[rb.ShellRoom], voids: list[Polygon] = ()) -> Polygon:
    """The house outline (house_outline) plus enclosed spaces Matterport's plan left out: a notch of the
    outline under 1.5 m wide (closed with mitred corners, so it stays rectangular) with no scanned ground in
    it (a closet, a chimney chase) is inside the house, not yard. A notch with ground (an open entry alcove)
    stays outside."""
    outline = house_outline(rooms, voids)
    floor_z = min(r.floor_z for r in rooms)
    g = ground_grid(scan, outline, floor_z)
    closed = outline.buffer(0.75, join_style="mitre").buffer(-0.75, join_style="mitre")
    gaps = closed.difference(outline).difference(g.lot.buffer(0.15, join_style="mitre"))
    enclosed = [q for q in polygons(gaps) if q.area > 0.1 and q.distance(outline) < 0.05]
    if not enclosed:
        return outline
    u = unary_union([outline, *enclosed]).buffer(0.05, join_style="mitre").buffer(-0.05, join_style="mitre")
    return Polygon(max(polygons(u), key=lambda q: q.area).exterior)


def outdoor_panos(panos: PanoSet, outline: Polygon) -> PanoSet:
    """The panoramas taken outside the house (outside the union of room polygons)."""
    inside = outline.buffer(0.1)
    return panos.subset([i for i, q in enumerate(panos.panos) if not inside.contains(Point(*q.pos[:2]))])


def house_outline(rooms: list[rb.ShellRoom], voids: list[Polygon] = ()) -> Polygon:
    """Union of the room centreline polygons with notches under 0.6 m closed, as one polygon. `voids` are
    dropped Matterport "Void" rooms (closets, chases): inside the house, so not yard."""
    u = (
        unary_union([r.poly for r in rooms] + list(voids))
        .buffer(0.3, join_style="mitre")
        .buffer(-0.3, join_style="mitre")
    )
    if u.geom_type == "MultiPolygon":
        u = max(u.geoms, key=lambda p: p.area)
    return Polygon(u.exterior)  # courtyards are not handled


def _cells(poly: Polygon, x0: float, y0: float, nx: int, ny: int, step: float):
    """Grid cells (i, j) of a regular grid fully inside poly, and the clipped pieces of those on its edge."""
    xs = x0 + np.arange(nx) * step
    ys = y0 + np.arange(ny) * step
    X, Y = np.meshgrid(xs, ys, indexing="ij")
    boxes = shapely.box(X.ravel(), Y.ravel(), X.ravel() + step, Y.ravel() + step)
    shapely.prepare(poly)
    inside = shapely.contains(poly, boxes)
    edge = ~inside & shapely.intersects(poly, boxes)
    clipped = shapely.intersection(boxes[edge], poly)
    return X.ravel()[inside], Y.ravel()[inside], clipped


def clipped_grid(poly: Polygon, step: float) -> tuple[np.ndarray, np.ndarray]:
    """Triangulate poly as a regular grid (cells inside: two triangles; cells on the outline: the exact
    clipped piece, ear-cut), so outlines stay straight while the inside can follow a height function.
    Returns (vertices (n, 2), faces (m, 3))."""
    x0, y0, x1, y1 = poly.bounds
    nx, ny = max(1, math.ceil((x1 - x0) / step)), max(1, math.ceil((y1 - y0) / step))
    cx, cy, clipped = _cells(poly, x0, y0, nx, ny, step)
    verts = [
        np.stack([cx, cy], 1),
        np.stack([cx + step, cy], 1),
        np.stack([cx + step, cy + step], 1),
        np.stack([cx, cy + step], 1),
    ]
    n = len(cx)
    quad = np.stack(verts, 1).reshape(-1, 2)
    base = np.arange(n) * 4
    faces = [np.stack([base, base + 1, base + 2], 1), np.stack([base, base + 2, base + 3], 1)]
    vs = [quad]
    off = len(quad)
    for g in clipped:
        parts = (
            [g]
            if g.geom_type == "Polygon"
            else [p for p in getattr(g, "geoms", []) if p.geom_type == "Polygon"]
        )
        for p in parts:
            if p.area < 1e-6:
                continue
            v2, f = trimesh.creation.triangulate_polygon(p)
            vs.append(np.asarray(v2, float))
            faces.append(np.asarray(f) + off)
            off += len(v2)
    return np.concatenate(vs), np.concatenate(faces)


# ---------------------------------------------------------------- patches (parametric textured surfaces)


@dataclass
class Patch:
    """A textured surface given by a function of (s, t) in [0, w] x [0, h] (metres or degrees), with an
    optional clip polygon in (s, t). Texel (i, j) is at s = (i + 0.5) / res, t = h - (j + 0.5) / res,
    as rebake.Plane. `image`, `page` and `rect` make it packable by rebake.pack."""

    kind: str  # node group: yard | facade | roof | backdrop
    w: float
    h: float
    res: float
    pos: Callable[[np.ndarray, np.ndarray], np.ndarray]  # (s, t) -> (n, 3)
    normal: Callable[[np.ndarray, np.ndarray, np.ndarray], np.ndarray]  # (s, t, xyz) -> (n, 3) for shading
    clip: Polygon | None = None
    step: float = 0.5  # mesh cell size in (s, t) units
    snap: bool = False
    no_sky: bool = False  # sky-coloured samples count as unseen (a generated roof the cameras looked past)
    image: np.ndarray | None = None
    rect: tuple[int, int, int, int] | None = None
    page: int = 0
    meta: dict = field(default_factory=dict)


def bake_patch(p: Patch, panos: PanoSet, scan: rb.Scan, max_dist: float) -> float:
    """Colour the patch's texels from the panoramas; returns the fraction some camera saw."""
    res = min(p.res, (rb.PAGE - 2 * rb.PAD - 2) / max(p.w, p.h))
    p.res = res
    W, H = max(2, math.ceil(p.w * res)), max(2, math.ceil(p.h * res))
    s = (np.arange(W) + 0.5) / res
    t = p.h - (np.arange(H) + 0.5) / res
    S, T = np.meshgrid(s, t)
    if p.clip is not None:
        mask = shapely.contains_xy(p.clip.buffer(3 / res), S.ravel(), T.ravel()).reshape(H, W)
    else:
        mask = np.ones((H, W), bool)
    xyz = p.pos(S[mask], T[mask])
    nrm = p.normal(S[mask], T[mask], xyz)
    rgb, valid = shade(xyz, nrm, panos, scan.rays, snap=p.snap, max_dist=max_dist)
    if p.no_sky:
        valid &= ~_sky(np.clip(rgb, 0, 255).astype(np.uint8)[None])[0]
    img = np.zeros((H, W, 3), np.uint8)
    img[mask] = np.clip(rgb, 0, 255).astype(np.uint8)
    ok = np.zeros((H, W), bool)
    ok[mask] = valid
    if p.kind == "roof" and p.no_sky:
        # Most of a generated roof is out of every camera's view. Blurring the seen texels into it pulls in
        # fascia and wall colours (it came out beige); use the shingles' own colour: the median of the dark
        # seen texels, else asphalt grey.
        seen = img[ok].astype(float)
        dark = seen[seen.mean(1) < 140] if len(seen) else seen
        img[~ok] = np.median(dark, axis=0).astype(np.uint8) if len(dark) > 50 else (92, 88, 84)
        ok[:] = True
    p.image = np.dstack([rb._fill(img, ok), np.full((H, W), 255, np.uint8)])
    return float(valid.sum()) / max(1, mask.sum())


def patch_mesh(p: Patch) -> trimesh.Trimesh | None:
    clip = p.clip if p.clip is not None else box(0, 0, p.w, p.h)
    v2, f = clipped_grid(clip, p.step)
    if not len(f):
        return None
    pts = p.pos(v2[:, 0], v2[:, 1])
    x, y, w, h = p.rect
    px = x + rb.PAD + v2[:, 0] * (w - 2 * rb.PAD) / p.w
    py = y + rb.PAD + (p.h - v2[:, 1]) * (h - 2 * rb.PAD) / p.h
    uv = np.c_[px / rb.PAGE, 1 - py / rb.PAGE]
    m = trimesh.Trimesh(pts, f, process=False)
    m.visual = trimesh.visual.TextureVisuals(uv=uv)
    return m


# ---------------------------------------------------------------- ground


@dataclass
class Ground:
    x0: float
    y0: float
    z: np.ndarray  # (nx, ny) heights at grid points x0 + i * CELL, y0 + j * CELL (NaN = no ground)
    lot: Polygon  # the scanned ground outside the house
    edge_z: float  # median ground height along the lot's outer edge

    def height(self, x: np.ndarray, y: np.ndarray) -> np.ndarray:
        """Bilinear ground height (NaNs filled from the nearest sample)."""
        fx = np.clip((np.asarray(x) - self.x0) / CELL, 0, self.z.shape[0] - 1.001)
        fy = np.clip((np.asarray(y) - self.y0) / CELL, 0, self.z.shape[1] - 1.001)
        i, j = fx.astype(int), fy.astype(int)
        a, b = fx - i, fy - j
        z = self.filled
        return (
            z[i, j] * (1 - a) * (1 - b)
            + z[i + 1, j] * a * (1 - b)
            + z[i, j + 1] * (1 - a) * b
            + z[i + 1, j + 1] * a * b
        )

    @property
    def filled(self) -> np.ndarray:
        if not hasattr(self, "_filled"):
            bad = np.isnan(self.z)
            idx = ndimage.distance_transform_edt(bad, return_distances=False, return_indices=True)
            self._filled = self.z[tuple(idx)]
        return self._filled

    def normal(self, x: np.ndarray, y: np.ndarray) -> np.ndarray:
        e = CELL
        dzdx = (self.height(x + e, y) - self.height(x - e, y)) / (2 * e)
        dzdy = (self.height(x, y + e) - self.height(x, y - e)) / (2 * e)
        n = np.stack([-dzdx, -dzdy, np.ones_like(dzdx)], 1)
        return n / np.linalg.norm(n, axis=1)[:, None]


def ground_grid(scan: rb.Scan, outline: Polygon, floor_z: float) -> Ground:
    """The scan's lowest upward-facing surface per cell outside the house, median filtered, as a heightfield,
    and the lot outline (cells with ground, closed, pieces over 4 m2, minus the house).

    Measured with rays cast upward from below the scan (4 per cell): the first upward-facing surface hit is
    the ground, whatever is above it (trees, eaves, a parked car). Binning triangles by centroid does not
    work: the MatterPak covers flat concrete with a few triangles several metres long."""
    c = scan.tris.mean(1)
    near = (c[:, 2] < floor_z + 1.2) & (c[:, 2] > floor_z - 3.0)
    v = scan.tris[near].reshape(-1, 3)
    x0, y0 = v[:, 0].min() - CELL, v[:, 1].min() - CELL
    nx = int((v[:, 0].max() + CELL - x0) / CELL) + 2
    ny = int((v[:, 1].max() + CELL - y0) / CELL) + 2
    gi, gj = np.meshgrid(np.arange(nx), np.arange(ny), indexing="ij")
    zs = np.full((4, nx * ny), np.nan)
    for k, (dx, dy) in enumerate(((-0.25, -0.25), (0.25, -0.25), (-0.25, 0.25), (0.25, 0.25))):
        o = np.c_[
            x0 + (gi.ravel() + dx) * CELL, y0 + (gj.ravel() + dy) * CELL, np.full(nx * ny, floor_z - 3.5)
        ]
        todo = np.arange(len(o))
        for _ in range(4):  # step past downward- or sideways-facing hits (fence bottoms, undersides)
            if not len(todo):
                break
            t, prim = scan.rays.cast(o[todo], np.tile([0.0, 0.0, 1.0], (len(todo), 1)))
            hit = (prim >= 0) & (t < 4.7)
            todo, t, prim = todo[hit], t[hit], prim[hit]
            ok = scan.normals[prim, 2] > 0.6
            zs[k, todo[ok]] = o[todo[ok], 2] + t[ok]
            todo, t = todo[~ok], t[~ok]
            o[todo, 2] += t + 1e-3
    with np.errstate(all="ignore"):
        cnt = np.isfinite(zs).sum(0)
        z = np.where(cnt >= 2, np.nanmedian(zs, axis=0), np.nan).reshape(nx, ny)
    inside = shapely.contains_xy(outline.buffer(-0.05), x0 + gi * CELL, y0 + gj * CELL)
    z[inside] = np.nan
    have = ~np.isnan(z)
    lot_mask = ndimage.binary_fill_holes(ndimage.binary_closing(have, iterations=3))
    lot_mask = ndimage.binary_opening(lot_mask, iterations=2)
    # Keep every piece over 4 m2: the house usually splits the scan into a front and a back yard.
    lab, n = ndimage.label(lot_mask)
    if n > 1:
        sizes = np.asarray(ndimage.sum(lot_mask, lab, range(1, n + 1))) * CELL * CELL
        lot_mask = np.isin(lab, 1 + np.nonzero(sizes >= 4.0)[0])
    # Fill holes (under cars, shrubs) from the neighbours, then median filter: grass noise goes, steps stay.
    idx = ndimage.distance_transform_edt(~have, return_distances=False, return_indices=True)
    zf = z[tuple(idx)]
    zf = ndimage.median_filter(zf, size=5)
    zf[~lot_mask] = np.nan
    # Lot outline from the mask (cell squares, simplified so the edge is not a staircase).
    ii, jj = np.nonzero(lot_mask)
    sq = shapely.box(
        x0 + (ii - 0.5) * CELL, y0 + (jj - 0.5) * CELL, x0 + (ii + 0.5) * CELL, y0 + (jj + 0.5) * CELL
    )
    lot = filled(unary_union(sq).simplify(CELL * 0.75)).difference(outline.buffer(0.1, join_style="mitre"))
    lot = unary_union([q for q in polygons(lot) if q.area >= 1.0])
    ring = np.concatenate([np.asarray(q.exterior.coords) for q in polygons(lot)])
    g = Ground(x0, y0, zf, lot, 0.0)
    g.edge_z = float(np.median(g.height(ring[:, 0], ring[:, 1])))
    return g


def plan_tiles(
    geom,
    tile: float,
    kind: str,
    res: float,
    z: Callable[[np.ndarray, np.ndarray], np.ndarray],
    normal: Callable[[np.ndarray, np.ndarray, np.ndarray], np.ndarray],
    step: float,
    snap: bool = False,
    min_area: float = 0.05,
) -> list[Patch]:
    """A surface over a plan region (height z(x, y)) as patches: the region is cut into square tiles and
    each connected piece gets its own patch cropped to its bounding box, so no texture is spent outside it.
    `normal(x, y, xyz)` is in world coordinates."""
    x0, y0, x1, y1 = geom.bounds
    out = []
    for tx in np.arange(x0, x1, tile):
        for ty in np.arange(y0, y1, tile):
            for piece in polygons(geom.intersection(box(tx, ty, tx + tile, ty + tile))):
                if piece.area < min_area:
                    continue
                bx, by, bx1, by1 = piece.bounds
                local = affinity.translate(piece, -bx, -by)

                def pos(s, t, bx=bx, by=by):
                    return np.stack([s + bx, t + by, z(s + bx, t + by)], 1)

                def nrm(s, t, xyz, bx=bx, by=by):
                    return normal(s + bx, t + by, xyz)

                out.append(Patch(kind, bx1 - bx, by1 - by, res, pos, nrm, local, step=step, snap=snap))
    return out


def ground_patches(g: Ground, tile: float = 10.0) -> list[Patch]:
    """The lot as tiles of heightfield (each fits one texture page at GROUND_RES)."""
    return plan_tiles(g.lot, tile, "yard", GROUND_RES, g.height, lambda x, y, _: g.normal(x, y), CELL, True)


# ---------------------------------------------------------------- facades


def facade_ring(outline: Polygon, scan: rb.Scan, floor_z: float) -> tuple[np.ndarray, np.ndarray]:
    """The outline's corners moved out onto the real outer wall faces. Each segment is moved along its
    outward normal by the median distance at which rays from outside hit the scan (between 0.5 and 2 m up),
    and the corners are rebuilt by intersecting neighbouring segments. Returns (corners (n, 2) clockwise
    from above, so the left of each segment is outside; per-segment outward normals (n, 2))."""
    ring = np.asarray(orient(outline.simplify(0.05), -1.0).exterior.coords)[:-1]
    n = len(ring)
    off = np.zeros(n)
    normals = np.zeros((n, 2))
    lengths = np.linalg.norm(np.roll(ring, -1, axis=0) - ring, axis=1)
    for k in range(n):
        a, b = ring[k], ring[(k + 1) % n]
        u = (b - a) / max(np.linalg.norm(b - a), 1e-9)
        nm = np.array([-u[1], u[0]])  # left = outside for a clockwise ring
        normals[k] = nm
        L = np.linalg.norm(b - a)
        ts = np.linspace(0.15, max(0.16, L - 0.15), max(2, int(L / 0.1)))
        zs = floor_z + np.array([0.5, 0.9, 1.3, 1.7, 2.0])
        T, Z = np.meshgrid(ts, zs)
        pts = np.c_[a[0] + u[0] * T.ravel(), a[1] + u[1] * T.ravel(), Z.ravel()]
        d3 = np.array([nm[0], nm[1], 0.0])
        reach = 0.6
        t, _ = scan.rays.cast(pts + d3 * reach, np.tile(-d3, (len(pts), 1)))
        d = reach - t[np.isfinite(t)]
        # Only hits outside the centreline count: where the outer surface was not scanned the rays reach
        # inside surfaces (a recessed fireplace, a closet), and a facade there covers them from the room.
        d = d[(d > 0.02) & (d < 0.45)]
        off[k] = float(np.median(d)) if len(d) > 0.2 * len(pts) else np.nan
    good = np.isfinite(off) & (lengths >= 0.6)
    uniform = float(np.average(off[good], weights=lengths[good])) if good.any() else 0.1
    for k in np.nonzero(~good)[0]:  # short or unmeasured segments take their neighbours' offset
        nb = [off[(k + s) % n] for s in (-1, 1) if good[(k + s) % n]]
        off[k] = float(np.mean(nb)) if nb else uniform
    corners = np.zeros((n, 2))
    for k in range(n):
        p, q = k - 1, k
        a1 = ring[p] + normals[p] * off[p]
        a2 = ring[q] + normals[q] * off[q]
        d1 = ring[q] - ring[p]
        d2 = ring[(q + 1) % n] - ring[q]
        den = d1[0] * d2[1] - d1[1] * d2[0]
        if abs(den) < 0.05 * np.linalg.norm(d1) * np.linalg.norm(d2):
            corners[k] = (a1 + d1 + a2) / 2  # (nearly) collinear: average
        else:
            s = ((a2[0] - a1[0]) * d2[1] - (a2[1] - a1[1]) * d2[0]) / den
            corners[k] = a1 + s * d1
    poly = Polygon(corners)
    if not poly.is_valid or np.linalg.norm(corners - ring, axis=1).max() > 0.7:
        # Fall back to one offset for the whole house.
        poly = orient(outline.simplify(0.05).buffer(uniform, join_style="mitre"), -1.0)
        corners = np.asarray(poly.exterior.coords)[:-1]
        d = np.roll(corners, -1, axis=0) - corners
        d /= np.maximum(np.linalg.norm(d, axis=1), 1e-9)[:, None]
        normals = np.stack([-d[:, 1], d[:, 0]], 1)
    return corners, normals


def eave_height(corners: np.ndarray, normals: np.ndarray, scan: rb.Scan, floor_z: float, top: float) -> float:
    """Where the outer wall ends (the underside of the soffit), measured per facade segment: horizontal rays
    from 0.8 m outside, every 0.25 m along the segment and every 5 cm up. The wall is there at a height while
    most rays hit the scan within 12 cm of the facade plane, and gone once it stays missing for 30 cm (glass
    is not scanned, so window heads dip for a few bins). Above the soffit the rays hit the fascia, about half
    a metre further out, or nothing. Segments whose lower wall the scan does not cover (side walls no camera
    saw) are skipped. Result: the length-weighted median over segments (gable ends do not lift it), clamped
    to [floor + 2.1, top + 1.0] (`top` = highest room top); top + 0.2 when nothing was measured. Rooms taller
    than this (a garage, a vaulted room) are handled by facade_planes and the roof. Tried first: the 97th
    percentile height of facade faces; a tree trunk on the facade line gave 12.6 m, gables 3.6 m."""
    zs = floor_z + np.arange(1.6, 4.6, 0.05)
    reach = 0.8
    found, weights = [], []
    for k in range(len(corners)):
        a, b = corners[k], corners[(k + 1) % len(corners)]
        L = float(np.linalg.norm(b - a))
        if L < 1.0:
            continue
        u = (b - a) / L
        ts = np.arange(0.3, L - 0.3, 0.25)
        if not len(ts):
            continue
        T, Z = np.meshgrid(ts, zs)
        d3 = np.array([normals[k][0], normals[k][1], 0.0])
        o = np.c_[a[0] + u[0] * T.ravel(), a[1] + u[1] * T.ravel(), Z.ravel()] + d3 * reach
        t, _ = scan.rays.cast(o, np.tile(-d3, (len(o), 1)))
        on = (np.abs(reach - t) < 0.12).reshape(len(zs), len(ts)).mean(1)
        low = zs < floor_z + 2.0
        if on[low].mean() < 0.5:
            continue
        # The wall must stay missing for 30 cm: glass is not scanned, so window heads dip for a few bins.
        gone = on < 0.35
        run = np.array([gone[i : i + 6].all() and i + 6 <= len(gone) for i in range(len(gone))])
        above = np.nonzero((zs >= floor_z + 2.0) & run)[0]
        if len(above):
            found.append(float(zs[above[0]]))
            weights.append(L)
    if not found:
        return top + 0.2
    o = np.argsort(found)
    f, w = np.asarray(found)[o], np.asarray(weights)[o]
    med = float(f[min(len(f) - 1, np.searchsorted(np.cumsum(w), 0.5 * w.sum()))])
    return float(np.clip(med, floor_z + 2.1, top + 1.0))


Door = tuple[tuple[float, float], tuple[float, float], float, float]  # centreline (a, b), z0, z1


def _on_segment(a2: np.ndarray, u2: np.ndarray, nm: np.ndarray, L: float, door: Door, reach: float):
    """(s0, s1, depth) of a door along a facade segment (start a2, direction u2, outward normal nm) when the
    door lies on it: parallel, within `reach` behind it, and overlapping it. Else None."""
    da, db = np.asarray(door[0], float), np.asarray(door[1], float)
    if abs((db - da) @ nm) > 0.05 * np.linalg.norm(db - da):
        return None
    depth = float(-((da - a2) @ nm))  # how far the centreline sits behind the facade
    if not (-0.05 < depth < reach):
        return None
    s0, s1 = sorted(((da - a2) @ u2, (db - a2) @ u2))
    if s1 < 0.05 or s0 > L - 0.05:
        return None
    return float(max(s0, 0.0)), float(min(s1, L)), depth


def facade_planes(
    corners: np.ndarray,
    normals: np.ndarray,
    g: Ground,
    eave_z: float,
    doors: list[Door] = (),
    rooms: list[rb.ShellRoom] = (),
) -> list[rb.Plane]:
    """One plane per outline segment, from just below the ground to the soffit (or, next to a room taller
    than the soffit such as a garage, to that room's top + 0.1 m, where the generated roof starts), with the
    open doors cut out."""
    ez = np.array([0.0, 0.0, 1.0])
    out = []
    n = len(corners)
    for k in range(n):
        a, b = corners[k], corners[(k + 1) % n]
        L = float(np.linalg.norm(b - a))
        if L < 0.05:
            continue
        u2 = (b - a) / L
        ts = np.linspace(0, L, max(2, int(L / 0.2)))
        out_pts = a + np.outer(ts, u2) + normals[k] * 0.3
        bottom = float(np.nanmin(g.height(out_pts[:, 0], out_pts[:, 1]))) - 0.15
        seg = LineString([a, b])
        tops = [rb.room_top(r) + 0.1 for r in rooms if r.poly.distance(seg) < 0.6]
        hgt = max([eave_z, *tops]) - bottom
        u = np.array([u2[0], u2[1], 0.0])
        nm = np.array([normals[k][0], normals[k][1], 0.0])
        shape = box(0, 0, L, hgt)
        for d in doors:
            hit = _on_segment(a, u2, normals[k], L, d, 0.7)
            if hit:
                shape = shape.difference(box(hit[0], -0.01, hit[1], d[3] - bottom))
        out.append(rb.Plane("wall", np.array([a[0], a[1], bottom]), u, ez, nm, L, hgt, shape, carve=False))
    return out


def door_frames(
    corners: np.ndarray, normals: np.ndarray, doors: list[Door], floor_z: float
) -> list[rb.Plane]:
    """Jambs, lintel and threshold through the whole wall for each opened door: from the room's wall surface
    (WALL_INSET inside the centreline) out to the facade, so the doorway is a clean tunnel."""
    ez = np.array([0.0, 0.0, 1.0])
    out = []
    n = len(corners)
    for d in doors:
        for k in range(n):
            a, b = corners[k], corners[(k + 1) % n]
            L = float(np.linalg.norm(b - a))
            if L < 0.3:
                continue
            u2 = (b - a) / L
            hit = _on_segment(a, u2, normals[k], L, d, 0.7)
            if not hit:
                continue
            s0, s1, depth = hit
            nm = np.r_[normals[k], 0.0]
            u = np.r_[u2, 0.0]
            inner = depth + rb.WALL_INSET  # facade -> room wall surface
            z1 = d[3]
            hgt = z1 - floor_z
            base = np.r_[a, floor_z] - nm * inner  # at s = 0, on the room's wall surface
            jamb = box(0, 0, inner, hgt)
            out.append(rb.Plane("wall", base + u * s0, nm, ez, u, inner, hgt, jamb, carve=False))
            out.append(rb.Plane("wall", base + u * s1, nm, ez, -u, inner, hgt, jamb, carve=False))
            across = box(0, 0, s1 - s0, inner)
            out.append(
                rb.Plane("wall", base + u * s0 + ez * hgt, u, nm, -ez, s1 - s0, inner, across, carve=False)
            )
            sill = box(0, 0, s1 - s0, depth)  # from the centreline (where the room's floor ends) out
            out.append(
                rb.Plane(
                    "floor", base + u * s0 + nm * rb.WALL_INSET, u, nm, ez, s1 - s0, depth, sill, carve=False
                )
            )
            break
    return out


# ---------------------------------------------------------------- roof


def roof_coverage(scan: rb.Scan, outline: Polygon, top: float) -> float:
    """Fraction of the house outline (0.5 m cells) under scanned, upward-facing roof surface."""
    c = scan.tris.mean(1)
    sel = shapely.contains_xy(outline.buffer(0.3), c[:, 0], c[:, 1]) & (c[:, 2] > top + 0.15)
    sel &= (scan.normals[:, 2] > 0.2) & (c[:, 2] < top + 4.0)  # tree canopies are higher
    g = 0.5
    x0, y0, x1, y1 = outline.bounds
    X, Y = np.meshgrid(np.arange(x0, x1, g) + g / 2, np.arange(y0, y1, g) + g / 2)
    inside = shapely.contains_xy(outline, X.ravel(), Y.ravel())
    hit = set(zip(((c[sel, 0] - x0) // g).astype(int), ((c[sel, 1] - y0) // g).astype(int), strict=True))
    cells = zip(((X.ravel() - x0) // g).astype(int), ((Y.ravel() - y0) // g).astype(int), strict=True)
    cov = np.array([k in hit for k in cells])
    return float(cov[inside].mean()) if inside.any() else 0.0


def roof_patches(
    eaves: Polygon, wall_poly: Polygon, eave_z: float, pitch: float, vaulted: list[tuple[Polygon, float]] = ()
) -> list[Patch]:
    """Generated hip roof: height grows with the distance to the eave line (exact for convex outlines; a
    concave corner gets a rounded valley), plus a flat soffit under the overhang and a fascia band."""
    tan = math.tan(pitch)
    eave_line = eaves.exterior
    x0, y0, x1, y1 = eaves.bounds

    def z_roof(x, y):
        z = eave_z + tan * shapely.distance(eave_line, shapely.points(x, y))
        for poly, z_min in vaulted:  # never below a room's ceiling (tall garages, vaulted rooms)
            inside = shapely.contains_xy(poly, x, y)
            z[inside] = np.maximum(z[inside], z_min)
        return z

    def pos(s, t):
        return np.stack([s + x0, t + y0, z_roof(s + x0, t + y0)], 1)

    def nrm(s, t, xyz):
        e = 0.05
        x, y = s + x0, t + y0
        dzdx = (z_roof(x + e, y) - z_roof(x - e, y)) / (2 * e)
        dzdy = (z_roof(x, y + e) - z_roof(x, y - e)) / (2 * e)
        v = np.stack([-dzdx, -dzdy, np.ones_like(dzdx)], 1)
        return v / np.linalg.norm(v, axis=1)[:, None]

    local = affinity.translate(eaves, -x0, -y0)
    roof = Patch("roof", x1 - x0, y1 - y0, ROOF_RES, pos, nrm, local, step=0.25, no_sky=True)
    soffit_clip = affinity.translate(eaves.difference(wall_poly.buffer(-0.02)), -x0, -y0)

    def spos(s, t):
        return np.stack([s + x0, t + y0, np.full(len(s), eave_z)], 1)

    def snrm(s, t, xyz):
        return np.tile([0.0, 0.0, -1.0], (len(s), 1))

    soffit = Patch("roof", x1 - x0, y1 - y0, ROOF_RES, spos, snrm, soffit_clip, step=0.5)
    return [roof, soffit]


def fascia_planes(eaves: Polygon, eave_z: float, depth: float = 0.2) -> list[rb.Plane]:
    ring = np.asarray(orient(eaves, -1.0).exterior.coords)
    out = []
    ez = np.array([0.0, 0.0, 1.0])
    for a, b in zip(ring[:-1], ring[1:], strict=True):
        L = float(np.linalg.norm(b - a))
        if L < 0.05:
            continue
        u2 = (b - a) / L
        nm = np.array([-u2[1], u2[0], 0.0])
        o = np.array([a[0], a[1], eave_z - depth])
        out.append(rb.Plane("wall", o, np.r_[u2, 0.0], ez, nm, L, depth, box(0, 0, L, depth), carve=False))
    return out


def measure_pitch(scan: rb.Scan, eaves: Polygon, eave_z: float) -> float:
    """Median slope of sloped, upward-facing scan faces over the roof (near the eaves, below the canopy)."""
    c = scan.tris.mean(1)
    nz = scan.normals[:, 2]
    sel = shapely.contains_xy(eaves, c[:, 0], c[:, 1]) & (c[:, 2] > eave_z) & (c[:, 2] < eave_z + 1.5)
    sel &= (nz > 0.75) & (nz < 0.97)
    if scan.areas[sel].sum() < 2.0:
        return DEFAULT_PITCH
    ang = np.arccos(nz[sel])
    o = np.argsort(ang)
    a = ang[o][np.searchsorted(np.cumsum(scan.areas[sel][o]), 0.5 * scan.areas[sel].sum())]
    return float(np.clip(a, math.radians(14), math.radians(40)))


# ---------------------------------------------------------------- backdrop


def backdrop_patches(
    g: Ground, centre: np.ndarray, radius: float, house: Polygon, floor_z: float
) -> list[Patch]:
    """A ground disk (outside the lot) and a dome of `radius` around `centre` (x, y), textured as the photos
    see them from the middle of the lot. Shading normals point at an eye in the middle of the lot, so every
    outdoor camera counts as a frontal view and the occlusion test picks the cameras that see past the house
    and trees."""
    cx, cy = float(centre[0]), float(centre[1])
    z0 = g.edge_z - 0.03
    eye = np.array([cx, cy, z0 + 1.5])

    def toward_eye(s, t, xyz):
        v = eye - xyz
        return v / np.linalg.norm(v, axis=1)[:, None]

    # Ground: follows the lot's edge height near the lot, flattens to z0 farther out.
    far = g.filled.copy()
    far = ndimage.gaussian_filter(far, 1.5 / CELL)

    def zdisk(x, y):
        fx = np.clip((x - g.x0) / CELL, 0, far.shape[0] - 1.001)
        fy = np.clip((y - g.y0) / CELL, 0, far.shape[1] - 1.001)
        zn = far[fx.astype(int), fy.astype(int)] - 0.04
        d = np.hypot(x - cx, y - cy)
        k = np.clip((d - radius * 0.5) / (radius * 0.3), 0, 1)
        z = zn * (1 - k) + z0 * k
        near = shapely.dwithin(house, shapely.points(x, y), 1.5)
        z[near] = np.minimum(z[near], floor_z - 0.08)  # never above the house floor next to the house
        return z

    # The disk stops 0.3 m inside the lot (the yard overlaps it, 4 cm higher) and at the house wall, and
    # stays below the floor near the house: a lot that does not wrap around the house (an unscanned side
    # yard) leaves no hole there, and the blurred yard height near a deck is above the floor.
    hole = filled(g.lot).buffer(-0.3).union(house.buffer(-0.02, join_style="mitre"))
    disk = Point(cx, cy).buffer(radius, quad_segs=32).difference(hole)
    out = plan_tiles(disk, 2 * radius / 3, "backdrop", DISK_RES, zdisk, toward_eye, 1.0, min_area=0.5)

    # Dome: four 90 degree tiles from the horizon to 60 degrees up, plus a cap.
    for a0 in (0.0, 90.0, 180.0, 270.0):

        def dpos(s, t, a0=a0):
            az, el = np.radians(s + a0), np.radians(t)
            return np.stack(
                [
                    cx + radius * np.cos(el) * np.cos(az),
                    cy + radius * np.cos(el) * np.sin(az),
                    z0 + radius * np.sin(el),
                ],
                1,
            )

        out.append(Patch("backdrop", 90.0, 60.0, DOME_RES, dpos, toward_eye, None, step=3.0))
    rc = radius * math.cos(math.radians(60))

    def cpos(s, t):
        x, y = s - rc, t - rc
        return np.stack([cx + x, cy + y, z0 + np.sqrt(np.maximum(radius**2 - x * x - y * y, 0))], 1)

    cap = Point(rc, rc).buffer(rc * 1.01, quad_segs=32)
    out.append(Patch("backdrop", 2 * rc, 2 * rc, 512 / (2 * rc), cpos, toward_eye, cap, step=rc / 8))
    return out


# ---------------------------------------------------------------- outdoor scan pieces


def outdoor_faces(
    scan: rb.Scan,
    outline: Polygon,
    walls: Polygon,
    g: Ground,
    top: float,
    eaves: Polygon | None,
    eave_z: float,
) -> np.ndarray:
    """Scan faces to keep outdoors: outside the house volume, not on the ground heightfield, not on the
    facade planes, not under a generated roof, and inside the lot."""
    c = scan.tris.mean(1)
    nz = scan.normals[:, 2]
    in_lot = shapely.contains_xy(g.lot.buffer(1.0).union(walls.buffer(1.0)), c[:, 0], c[:, 1])
    in_house = shapely.contains_xy(walls.buffer(-0.02), c[:, 0], c[:, 1]) & (c[:, 2] < top + 0.1)
    near_wall = shapely.dwithin(walls.exterior, shapely.points(c[:, 0], c[:, 1]), 0.1)
    on_facade = near_wall & (np.abs(nz) < 0.5) & (c[:, 2] < eave_z + 0.05)
    in_wall_slab = shapely.contains_xy(walls, c[:, 0], c[:, 1]) & ~shapely.contains_xy(
        outline.buffer(-0.02), c[:, 0], c[:, 1]
    )
    gz = g.height(c[:, 0], c[:, 1])
    on_ground = (c[:, 2] - gz < 0.1) & (nz > 0.6) & shapely.contains_xy(g.lot, c[:, 0], c[:, 1])
    below = c[:, 2] < gz - 0.25
    keep = in_lot & ~in_house & ~on_facade & ~in_wall_slab & ~on_ground & ~below
    if eaves is not None:
        keep &= ~(shapely.contains_xy(eaves.buffer(0.05), c[:, 0], c[:, 1]) & (c[:, 2] > eave_z - 0.25))
    return np.nonzero(keep)[0]


def _seen_faces(scan: rb.Scan, faces: np.ndarray, panos: PanoSet) -> np.ndarray:
    """The faces whose centre an outdoor camera sees from the front, judged face by face. Not rb._seen:
    indoors it keeps a piece whole once a quarter of it is seen (half a toilet looked wrong), but outdoors a
    tree canopy or a fence run is one huge piece, and keeping them whole took 7014's interior.glb from 22 to
    33 MB (255k tree triangles instead of 140k) for surfaces no photo shows."""
    if not len(faces):
        return faces
    _, valid = shade(scan.tris[faces].mean(1), scan.normals[faces], panos, scan.rays, snap=False)
    return faces[valid]


def _sky(rgb: np.ndarray) -> np.ndarray:
    """Pixels that look like sky or cloud (for cutting tree canopies out)."""
    r, g, b = (rgb[..., k].astype(float) for k in range(3))
    mx, mn = np.maximum(np.maximum(r, g), b), np.minimum(np.minimum(r, g), b)
    blue = (b > r + 20) & (b >= g) & (mx > 110)
    cloud = (mn > 185) & (mx - mn < 40)
    return blue | cloud


def cut_sky(pages: list[np.ndarray], uv: np.ndarray, pg: np.ndarray, which: np.ndarray) -> list[np.ndarray]:
    """RGBA pages where the charts of faces `which` are transparent wherever they show sky."""
    out = [np.dstack([p, np.full(p.shape[:2], 255, np.uint8)]) for p in pages]
    for f in np.nonzero(which)[0]:
        px = uv[f, :, 0] * rb.PAGE
        py = (1 - uv[f, :, 1]) * rb.PAGE
        x0, x1 = max(0, int(px.min()) - 2), min(rb.PAGE, int(math.ceil(px.max())) + 3)
        y0, y1 = max(0, int(py.min()) - 2), min(rb.PAGE, int(math.ceil(py.max())) + 3)
        sub = out[pg[f]][y0:y1, x0:x1]
        sky = _sky(sub[..., :3])
        sub[..., 3][sky] = 0
    return out


def unsky(pages: list[np.ndarray], uv: np.ndarray, pg: np.ndarray, which: np.ndarray) -> None:
    """In place: sky-coloured texels in the charts of faces `which` (a scanned roof: the cameras looked past
    its edge) become the mean of the chart's other texels, or the median roof colour."""
    boxes = []
    for f in np.nonzero(which)[0]:
        px = uv[f, :, 0] * rb.PAGE
        py = (1 - uv[f, :, 1]) * rb.PAGE
        boxes.append(
            (
                pg[f],
                max(0, int(px.min()) - 2),
                min(rb.PAGE, int(math.ceil(px.max())) + 3),
                max(0, int(py.min()) - 2),
                min(rb.PAGE, int(math.ceil(py.max())) + 3),
            )
        )
    good = [pages[k][y0:y1, x0:x1][~_sky(pages[k][y0:y1, x0:x1])] for k, x0, x1, y0, y1 in boxes]
    rest = np.concatenate([g for g in good if len(g)] or [np.full((1, 3), 90, np.uint8)])
    median = np.median(rest, axis=0)
    for (k, x0, x1, y0, y1), g in zip(boxes, good, strict=True):
        sub = pages[k][y0:y1, x0:x1]
        sky = _sky(sub)
        if sky.any():
            sub[sky] = g.mean(0) if len(g) > 4 else median


# ---------------------------------------------------------------- export


def _soup(scan_tris: np.ndarray, uv: np.ndarray, pages: list[np.ndarray], pg: np.ndarray, name: str):
    out = []
    for pi in range(len(pages)):
        sel = pg == pi
        if not sel.any():
            continue
        verts = scan_tris[sel].reshape(-1, 3)
        m = trimesh.Trimesh(verts, np.arange(len(verts)).reshape(-1, 3), process=False)
        m.visual = trimesh.visual.TextureVisuals(
            uv=uv[sel].reshape(-1, 2), material=rb._material(pages[pi], f"{name}_{pi}")
        )
        m.apply_transform(hm.Z_UP_TO_Y_UP)
        out.append((f"{name}_{pi}", m))
    return out


@dataclass
class Exterior:
    report: dict
    ground_tris: np.ndarray  # (n, 3, 3) Z-up, for collision
    piece_tris: np.ndarray  # outdoor scan pieces players bump into
    bounds: Polygon  # lot + house without holes; its outline is the invisible fence
    outline: Polygon  # the facade outline (outer wall faces)


def add_exterior(
    scene: trimesh.Scene,
    scan: rb.Scan,
    rooms: list[rb.ShellRoom],
    panos: PanoSet,
    doors: list[Door] = (),
    outline: Polygon | None = None,
    backdrop_radius: float = 45.0,
) -> Exterior:
    """Bake the lot, facades, roof, outdoor pieces and backdrop into `scene` (Y-up nodes). `doors` are the
    outside doors opened for play (also cut from the rooms' walls via ShellRoom.cuts, and from collision)."""
    outline = outline if outline is not None else house_footprint(scan, rooms)
    floor_z = min(r.floor_z for r in rooms)
    top = max(rb.room_top(r) for r in rooms)
    out_panos = outdoor_panos(panos, outline)
    report: dict = {"panoramas_outdoor": len(out_panos)}
    if len(out_panos) == 0:
        raise ValueError("no outdoor panoramas: this scan has no yard")
    g = ground_grid(scan, outline, floor_z)
    corners, normals = facade_ring(outline, scan, floor_z)
    walls = Polygon(corners)
    eave_z = eave_height(corners, normals, scan, floor_z, top)
    report.update(lot_m2=round(g.lot.area, 1), eave_z=round(eave_z, 2), ground_edge_z=round(g.edge_z, 2))

    planes = facade_planes(corners, normals, g, eave_z, doors, rooms)
    frames = door_frames(corners, normals, doors, floor_z)
    report["doors_opened"] = len(doors)
    cover = roof_coverage(scan, outline, top)
    report["roof_scan_coverage"] = round(cover, 2)
    eaves = None
    patches = ground_patches(g)
    if cover < 0.7:
        eaves = Polygon(orient(walls, 1.0).buffer(OVERHANG, join_style="mitre").exterior)
        pitch = measure_pitch(scan, eaves, eave_z)
        report["roof"] = {"generated": True, "pitch_deg": round(math.degrees(pitch), 1)}
        floors = [(r.poly.buffer(0.1), rb.room_top(r) + 0.1) for r in rooms]  # roof never below a ceiling
        patches += roof_patches(eaves, walls, eave_z, pitch, floors)
        planes += fascia_planes(eaves, eave_z)
    else:
        report["roof"] = {"generated": False}

    centre = np.mean(out_panos.positions[:, :2], axis=0)
    centre = np.asarray(filled(g.lot.union(walls)).centroid.coords[0]) * 0.5 + centre * 0.5
    patches += backdrop_patches(g, centre, backdrop_radius, walls, floor_z)

    seen = {}
    for p in frames:  # seen from inside and outside: every camera
        rb.bake_plane(p, scan, panos, FACADE_RES)
    blank = []
    for p in planes:
        st, _ = rb.bake_plane(p, scan, out_panos, FACADE_RES, fallback=True)
        seen.setdefault("facade", []).append((st["seen"], st["area"]))
        if st["seen"] < 0.02:
            blank.append(p)
    # A facade no camera saw (and the scan has no outer surface for) gets the house's typical facade colour
    # instead of _fill's flat grey.
    blank_ids = {id(p) for p in blank}
    lit = [p.image[..., :3].reshape(-1, 3) for p in planes if id(p) not in blank_ids]
    if blank and lit:
        colour = np.median(np.concatenate(lit)[::7], axis=0).astype(np.uint8)
        for p in blank:
            p.image[..., :3] = colour
    for p in patches:
        far = p.kind == "backdrop"
        f = bake_patch(p, out_panos, scan, max_dist=backdrop_radius * 2.5 if far else 30.0)
        seen.setdefault(p.kind, []).append((f, p.w * p.h))
    report["seen"] = {
        k: round(sum(a * b for a, b in v) / max(1e-9, sum(b for _, b in v)), 3) for k, v in seen.items()
    }

    # Planes and patches share texture pages.
    items = list(planes) + list(frames) + list(patches)
    mpx: dict[str, float] = {}
    for it in items:
        k = "facade" if isinstance(it, rb.Plane) else it.kind
        mpx[k] = mpx.get(k, 0.0) + it.image.shape[0] * it.image.shape[1] / 1e6
    report["megatexels"] = {k: round(v, 1) for k, v in mpx.items()}
    pages = rb.pack(items)
    mats = [rb._material(pg, f"outdoor_shell_{i}") for i, pg in enumerate(pages)]
    groups = {"facade": [], "roof": [], "yard": [], "backdrop": []}
    for it in items:
        m = rb.plane_mesh(it) if isinstance(it, rb.Plane) else patch_mesh(it)
        if m is None:
            continue
        kind = "facade" if isinstance(it, rb.Plane) else it.kind
        groups[kind].append((it.page, m))
    for kind, ms in groups.items():
        for pi in range(len(pages)):
            parts = [m for p, m in ms if p == pi]
            if not parts:
                continue
            m = trimesh.util.concatenate(parts)
            m.visual = trimesh.visual.TextureVisuals(uv=m.visual.uv, material=mats[pi])
            m.apply_transform(hm.Z_UP_TO_Y_UP)
            scene.add_geometry(m, node_name=f"{kind}_{pi}", geom_name=f"{kind}_{pi}")

    faces = outdoor_faces(scan, outline, walls, g, top, eaves, eave_z)
    faces = rb._drop_crumbs(scan, _seen_faces(scan, faces, out_panos), min_area=0.03)
    report["pages"] = {"shell": len(pages)}
    tree = np.zeros(len(faces), bool)
    if len(faces):
        c = scan.tris[faces].mean(1)
        tree = c[:, 2] - g.height(c[:, 0], c[:, 1]) > TREE_MIN_Z
        if eaves is not None:
            tree &= ~shapely.contains_xy(eaves.buffer(0.5), c[:, 0], c[:, 1])
        else:
            tree &= c[:, 2] > top + 1.5  # scanned roof: only well above it
    for sel, name, res in ((~tree, "outdoor", PIECE_RES), (tree, "trees", TREE_RES)):
        if not sel.any():
            continue
        pg_imgs, uv, pg = rb.bake_triangles(scan, faces[sel], out_panos, res)
        if name == "outdoor" and eaves is None:  # scanned roof: no sky on it
            fc = scan.tris[faces[sel]].mean(1)
            roof = shapely.contains_xy(walls.buffer(0.8), fc[:, 0], fc[:, 1]) & (fc[:, 2] > eave_z - 0.1)
            roof &= scan.normals[faces[sel], 2] > 0.3  # roof surface, not white gutters and soffits
            unsky(pg_imgs, uv, pg, roof)
        if name == "trees":
            pg_imgs = cut_sky(pg_imgs, uv, pg, np.ones(int(sel.sum()), bool))
        for node, m in _soup(scan.tris[faces[sel]], uv, pg_imgs, pg, name):
            scene.add_geometry(m, node_name=node, geom_name=node)
        report["pages"][name] = len(pg_imgs)
    report["outdoor_faces"] = int((~tree).sum())
    report["tree_faces"] = int(tree.sum())
    report["texture_pages"] = sum(report["pages"].values())

    # Collision ground: the same heightfield on a coarser grid (players do not feel 15 cm cells).
    v2, f2 = clipped_grid(g.lot, 0.45)
    v3 = np.c_[v2, g.height(v2[:, 0], v2[:, 1])]
    gt = [v3[f2]]
    low = np.zeros(0, int)
    if len(faces):
        c = scan.tris[faces].mean(1)
        low = faces[(c[:, 2] - g.height(c[:, 0], c[:, 1]) < 2.2) & ~tree]
    return Exterior(
        report=report,
        ground_tris=np.concatenate(gt) if gt else np.zeros((0, 3, 3)),
        piece_tris=scan.tris[low],
        bounds=filled(g.lot.union(walls.buffer(0.05, join_style="mitre"))),
        outline=walls,
    )


def collision_meshes(ext: Exterior, fence_height: float = 3.0) -> dict[str, trimesh.Trimesh]:
    """Z-up collision for the lot: `ground` (the yard heightfield), `outdoor` (fences, deck, railings, the AC
    unit, shrubs, tree trunks: scan pieces below 2.2 m) and `bounds` (an invisible fence along the lot's
    edge, so nobody walks off the scanned world)."""
    out: dict[str, trimesh.Trimesh] = {}
    for name, tris in (("ground", ext.ground_tris), ("outdoor", ext.piece_tris)):
        if len(tris):
            v = tris.reshape(-1, 3)
            m = trimesh.Trimesh(v, np.arange(len(v)).reshape(-1, 3), process=False)
            m.merge_vertices()
            out[name] = m
    base = ext.report.get("ground_edge_z", 0.0) - 1.0
    segs = []
    for q in polygons(ext.bounds):
        ring = np.asarray(q.simplify(0.3).exterior.coords)
        segs += [
            hm.WallSegment(tuple(a), tuple(b), 0.3, base, fence_height + 1.0)
            for a, b in zip(ring[:-1], ring[1:], strict=True)
        ]
    boxes = [m for m in (hm.wall_box(s) for s in segs) if m is not None]
    if boxes:
        out["bounds"] = trimesh.util.concatenate(boxes)
    return out


# ---------------------------------------------------------------- preview CLI


def main(argv: list[str] | None = None) -> int:
    """Bake only the outdoors into out/cache/glb/<model>_exterior.glb (uncompressed, for the preview
    scripts); build_house bakes it into interior.glb."""
    import argparse
    import json
    from pathlib import Path

    from worldgen import build_house as bh
    from worldgen.pack import load_block
    from worldgen.sources import matterport as mp

    ap = argparse.ArgumentParser(description=main.__doc__.splitlines()[0])
    ap.add_argument("--block", required=True)
    ap.add_argument("--listing", required=True)
    ap.add_argument("--out", type=Path, default=bh.HERE / "out")
    args = ap.parse_args(argv)
    house = next(h for h in load_block(args.block)["houses"] if h["listing_id"] == args.listing)
    model_id = house["matterport_model_id"]
    model = mp.fetch_model(model_id, args.out / "cache" / "matterport")
    ov = bh.OVERRIDES / f"{model_id}.json"
    parts = bh.apply_splits(bh.read_rooms(model), json.loads(ov.read_text()) if ov.exists() else {})
    raw = args.out / "raw" / model_id
    scan = rb.load_scan(bh.extract_matterpak(model_id, raw))
    fz = bh.scan_floor_z(scan, parts)
    for p in parts:
        p.floor_z = fz.get(p.floor, 0.0)
    spans = bh.opening_spans(parts, lambda e, z: bh.runs_from_rays(e, scan.rays, z), np.zeros((0, 3)))
    footprint = house_footprint(scan, bh.shell_rooms(parts), bh.void_polys(parts))
    doors = bh.play_doors(parts, spans, footprint)
    rooms = bh.shell_rooms(parts, spans)
    for r in rooms:
        rb.measure_ceiling(scan, r)
    panos = PanoSet(model, raw / "skybox_2k")
    scene = trimesh.Scene()
    ext = add_exterior(scene, scan, rooms, panos, doors, footprint)
    dest = args.out / "cache" / "glb" / f"{model_id}_exterior.glb"
    scene.export(dest)
    print(json.dumps(ext.report, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
