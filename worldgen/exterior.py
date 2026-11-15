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

import dataclasses
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

from worldgen import aerial as ae
from worldgen import fences, railings, vegetation
from worldgen import house_mesh as hm
from worldgen import rebake as rb
from worldgen import roof as rf
from worldgen import splat as sp
from worldgen.panos import PanoSet, Raycaster, shade

CELL = 0.15  # ground heightfield sample spacing (m)
GROUND_RES = 96.0  # texels per metre on the yard (about 1 cm)
FACADE_RES = 128.0  # texels per metre on the facades (seen up close, like interior walls)
FACADE_RELIEF = 0.28  # facade texels are sampled at the real surface up to this far in front of the plane (m)
ROOF_RES = 64.0  # the roof is seen from below and far away
PIECE_RES = 96.0  # outdoor scan pieces
TREE_RES = 40.0  # tree canopies: seen from below, noisy geometry; their own (alpha-cut) texture pages
DOME_RES = 16.0  # backdrop texels per degree (the 2k skybox faces have about 23)
DISK_RES = 32.0  # backdrop ground texels per metre
OVERHANG = 0.45  # generated roof: eave overhang past the facade (m)
MIN_OVERHANG = 0.3  # roof from the aerial data: it reaches at least this far past the facade (m)
SOFFIT = (
    0.18  # roof from the aerial data: thickness at the edge (fascia height); the underside is this far below
)
ROOF_GAP = 0.12  # roof from the aerial data: never closer than this above the ceilings below (m)
# The roof's underside is measured from the scan (eave_underside). Where the area outside the walls is
# wider than 2 x EAVE_EDGE (a porch, an entry alcove) any measured height counts; in a narrower strip (an
# ordinary overhang) only one less than EAVE_DROP under the guess (the top less SOFFIT) does: a soffit is
# (boxed level from the fascia, it lies 0.2 to 0.6 m under the top at the wall), the head of a recessed
# garage door 1.5 m under a gable's rake is not. Within EAVE_RIM of the roof's edge
# the underside is the guess, so it meets the SOFFIT-high band around the edge.
EAVE_EDGE = 0.6
EAVE_DROP = 0.7
EAVE_RIM = 0.3
# A measured underside becomes a flat plane only where it is flat: at least EAVE_FLAT of the measurements
# within about 0.5 m lie within EAVE_FLAT_TOL of their smoothed height (a drywall or plywood ceiling). Where
# they do not (open rafters: 7014's back porch) the scan stays as it is and no plane is built under the roof.
EAVE_FLAT = 0.7
EAVE_FLAT_TOL = 0.04
EAVE_OVER = 0.5  # undersides are looked for up to this far above the fitted roof top (m)
# The aerial photo colours the roof only when it shows the roof evenly lit: its per-channel scale to the
# ground photos' shingles within ORTHO_GAIN, and its brightness over the roof (90th over 10th percentile)
# under ORTHO_SPREAD. A roof under bare winter branches is a pattern of shadows, not of shingles.
ORTHO_GAIN = (0.6, 1.6)
ORTHO_SPREAD = 3.0
SPLAT_PLANE = (
    1.0,
    24,
)  # roof from the splat: smallest plane (m2) and most planes (its sheet is sharp enough)
SPLAT_LEVEL = 0.2  # the splat's ground must sit within this of the scan's (offset and spread), m
SPLAT_GAIN = (0.8, 1.25)  # the splat's shingles and walls are scaled to the ground photos by at most this
SUN = (
    205.0,
    50.0,
)  # azimuth (clockwise from north) and elevation, degrees, for shading a plain-coloured roof
DEFAULT_PITCH = math.atan(5 / 12)
TREE_MIN_Z = 2.0  # pieces this far above the ground (and away from the roof) may be cut out where sky shows
# What add_exterior bakes. A subset (the CLI's --parts) skips the rest, for a quick look at one part.
PARTS = frozenset({"yard", "facade", "roof", "backdrop", "pieces"})


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
    stays outside. What this cannot decide goes in `voids` by hand (overrides/<model>.json "inside": 6915's
    corner fireplace is a triangular notch whose firebox floor counts as ground)."""
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
    dropped Matterport "Void" rooms (closets, chases) and hand-listed masonry (build_house.void_polys):
    inside the house, so not yard."""
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
    rgb, valid = shade(xyz, nrm, panos, scan.eyes, snap=p.snap, max_dist=max_dist)
    if p.no_sky:
        valid &= ~_sky(np.clip(rgb, 0, 255).astype(np.uint8)[None])[0]
    img = np.zeros((H, W, 3), np.uint8)
    img[mask] = np.clip(rgb, 0, 255).astype(np.uint8)
    ok = np.zeros((H, W), bool)
    ok[mask] = valid
    if "default" in p.meta:
        # The roof's underside and edge band are plain painted surfaces. Specks seen through leaves are not
        # the surface (opened away); what no camera saw takes a plain colour, blended in over 0.25 m: the
        # seen part's median when the tile is seen at least half, else the default (the roof's trim colour,
        # set by add_exterior), which is also the whole tile's colour when under 15% is seen. Blurring a few
        # seen texels across a porch ceiling gave a mosaic of leaf colours.
        if p.meta.get("plain"):
            p.meta["samples"] = rgb[valid][::3]
        ok &= ndimage.binary_opening(ok, iterations=max(3, int(0.12 * res)))
        if ok.sum() < 0.15 * max(1, mask.sum()):
            img[:] = p.meta["default"]
        else:
            # A tile seen at least half takes its own median colour where unseen, else the default.
            mostly = ok.sum() >= 0.5 * max(1, mask.sum())
            plain = np.median(img[ok], axis=0) if mostly else np.asarray(p.meta["default"], float)
            wgt = np.clip(ndimage.distance_transform_edt(~ok) / (0.25 * res), 0, 1)[..., None]
            img = (rb._fill(img, ok) * (1 - wgt) + plain * wgt).astype(np.uint8)
        ok[:] = True
    if p.kind == "roof" and p.no_sky and p.meta.get("colour") is not None:
        # Seen from above: what no ground camera saw takes the aerial photo's colour, scaled per channel to
        # the photographed shingles where both exist (another day, another exposure). Texels the aerial
        # photo shows something else at (a tree over the roof) are filled from the roof around them. The two
        # sources are cross-faded over 0.3 m, and the roof's edge always takes the aerial colour: that is
        # where a ground photo projected onto a roof placed to 0.3 m lands on gutter or sky.
        air, air_ok = p.meta["colour"](xyz)
        both = valid & air_ok
        gain = np.ones(3)
        if both.sum() > 500:
            gain = np.median(rgb[both], axis=0) / np.maximum(np.median(air[both], axis=0), 1.0)
        if p.meta.get("whole"):
            # The drone splat (splat.roof_colour): real shingles over the whole roof, from straight above,
            # with the tree over the roof cut away. It takes every texel: a ground camera sees a roof at a
            # grazing angle and through leaves, and two sources on one slope show as a seam. No exposure
            # match to the ground photos: their few roof texels are leaves and sky as often as shingles (the
            # gain came out at its limit and the roof pale pink). What the drone did not see (under a dense
            # canopy) is filled from the shingles around it.
            p.meta["aerial"] = {"source": "splat", "covered": round(float(air_ok.mean()), 3)}
            img[mask] = np.clip(air, 0, 255).astype(np.uint8)
            ok[mask] = air_ok
            ok &= ndimage.binary_opening(ok, iterations=2)
            p.image = np.dstack([rb._fill(img, ok), np.full((H, W), 255, np.uint8)])
            return float(valid.sum()) / max(1, mask.sum())
        lum = air[air_ok] @ np.array([0.299, 0.587, 0.114])
        spread = float(np.percentile(lum, 90) / max(np.percentile(lum, 10), 1.0)) if len(lum) > 500 else 99.0
        usable = bool(
            both.sum() > 500
            and gain.min() > ORTHO_GAIN[0]
            and gain.max() < ORTHO_GAIN[1]
            and spread < ORTHO_SPREAD
        )
        p.meta["aerial"] = {
            "gain": [round(float(v), 2) for v in gain],
            "brightness_spread": round(spread, 2),
            "used": usable,
        }
        fill = np.zeros((H, W, 3), np.uint8)
        fill_ok = np.zeros((H, W), bool)
        if usable:
            fill[mask] = np.clip(air * gain, 0, 255).astype(np.uint8)
            fill_ok[mask] = air_ok
        else:
            # Plain shingle colour (the median of the dark texels the ground photos saw, else asphalt grey),
            # a little lighter on the planes facing the sun so ridges and hips still read: everything is
            # rendered unlit.
            seen = rgb[valid]
            dark = seen[seen.mean(1) < 140] if len(seen) else seen
            base = np.median(dark, axis=0) if len(dark) > 50 else np.array([92.0, 88.0, 84.0])
            k = np.clip(
                0.84 + 0.3 * np.maximum(nrm @ np.asarray(p.meta.get("sun", (0.0, 0.0, 1.0))), 0.0), 0.8, 1.12
            )
            fill[mask] = np.clip(base * k[:, None], 0, 255).astype(np.uint8)
            fill_ok[mask] = True
        fill = rb._fill(fill, fill_ok) if fill_ok.any() else fill
        ok &= ndimage.binary_opening(ok, iterations=max(3, int(0.12 * res)))  # specks seen through leaves
        wgt = np.clip(ndimage.distance_transform_edt(ok) / (0.3 * res), 0, 1)[..., None]
        img = (img * wgt + fill * (1 - wgt)).astype(np.uint8)
        ok[:] = True
    elif p.kind == "roof" and p.no_sky and "default" not in p.meta:
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
    roof: rf.Roof | None = None,
) -> list[rb.Plane]:
    """One plane per outline segment, from just below the ground to the soffit (or, next to a room taller
    than the soffit such as a garage, to that room's top + 0.1 m, where the generated roof starts), with the
    open doors cut out. With a `roof` from the aerial data the wall ends at the roof's underside all along
    the segment instead (sampled every 0.1 m, 2 cm inside the wall face), so a gable end is a pentagon."""
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
        if roof is not None:
            ss = np.linspace(0.0, L, max(2, int(L / 0.1) + 1))
            at = a + np.outer(ss, u2) - normals[k] * 0.02
            under = np.maximum(roof.height(at[:, 0], at[:, 1]) - SOFFIT - bottom, 0.5)
            hgt = float(under.max())
            shape = Polygon([(0.0, 0.0), *zip(ss, under, strict=True), (L, 0.0)]).buffer(0)
        else:
            tops = [rb.room_top(r) + 0.1 for r in rooms if r.poly.distance(seg) < 0.6]
            hgt = max([eave_z, *tops]) - bottom
            shape = box(0, 0, L, hgt)
        u = np.array([u2[0], u2[1], 0.0])
        nm = np.array([normals[k][0], normals[k][1], 0.0])
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


def roof_region(walls: Polygon, vexcel: Polygon | None) -> Polygon:
    """Where the roof is, in plan: the outer walls plus MIN_OVERHANG, united with Vexcel's outline of the
    roof when there is one (real eaves, porch roofs, an attached garage the floor plan lacks); OVERHANG past
    the walls when there is none. Gaps under 0.6 m between the two are closed."""
    if vexcel is None:
        r = orient(walls, 1.0).buffer(OVERHANG, join_style="mitre")
    else:
        r = orient(walls, 1.0).buffer(MIN_OVERHANG, join_style="mitre").union(vexcel.buffer(0))
        r = r.buffer(0.3, join_style="mitre").buffer(-0.3, join_style="mitre")
    r = max(polygons(r), key=lambda q: q.area)
    return Polygon(r.exterior).simplify(0.05)


def ceiling_floor(rooms: list[rb.ShellRoom], gap: float = ROOF_GAP, step: float = 0.15) -> rf.Height:
    """(x, y) -> the lowest the roof may be there: the ceiling below plus `gap`, NaN outside the rooms. Flat
    rooms: the listed height. Vaulted rooms: the scanned ceiling (rebake.ceiling_z), and the room's top where
    the scan has none. Each room is grown 0.12 m (the walls are under the roof too) and the result is
    max-filtered over 0.45 m, because a vaulted room's walls end a little above the ceiling next to them."""
    x0, y0, x1, y1 = unary_union([r.poly for r in rooms]).buffer(0.5).bounds
    X, Y = np.meshgrid(np.arange(x0, x1 + step, step), np.arange(y0, y1 + step, step), indexing="ij")
    low = np.full(X.shape, -np.inf)
    for r in rooms:
        inside = shapely.contains_xy(r.poly.buffer(0.12, join_style="mitre"), X, Y)
        h = rb.ceiling_z(r, X[inside], Y[inside])
        low[inside] = np.maximum(low[inside], np.where(np.isfinite(h), h, rb.room_top(r)) + gap)
    low = ndimage.maximum_filter(low, size=3)
    low[~np.isfinite(low)] = np.nan

    def floor(x: np.ndarray, y: np.ndarray) -> np.ndarray:
        i = np.rint((np.asarray(x, float) - x0) / step).astype(int)
        j = np.rint((np.asarray(y, float) - y0) / step).astype(int)
        ok = (i >= 0) & (i < low.shape[0]) & (j >= 0) & (j < low.shape[1])
        out = np.full(np.shape(x), np.nan)
        out[ok] = low[i[ok], j[ok]]
        return out

    return floor


def aerial_roof(
    aerial: ae.Aerial, g: Ground, walls: Polygon, rooms: list[rb.ShellRoom], floor_z: float, report: dict
) -> rf.Roof | None:
    """The roof from the aerial surface model: tie its heights to the scan's (Aerial.level on the yard's
    ground away from the house), fit planes over roof_region (roof.fit_roof; planes under floor + 1.9 m are
    ground, not roof), never below the ceilings (ceiling_floor). None when no plane is found."""
    ii, jj = np.nonzero(np.isfinite(g.z))
    x, y = g.x0 + ii[::7] * CELL, g.y0 + jj[::7] * CELL
    away = ~shapely.dwithin(walls, shapely.points(x, y), 1.5)
    try:
        spread = aerial.level(x[away], y[away], g.z[ii[::7], jj[::7]][away])
    except ValueError:
        return None
    region = roof_region(walls, aerial.roof_outline(walls))
    roof = rf.fit_roof(aerial.surface, region, floor=ceiling_floor(rooms), min_z=floor_z + 1.9)
    if roof is None:
        return None
    frac, most = roof.lifted()
    report["roof"] = {
        "source": "aerial",
        "planes": len(roof.planes),
        "plane_m2": [round(a, 1) for a in roof.areas],
        "pitch_deg": round(math.degrees(roof.pitch), 1),
        "region_m2": round(region.area, 1),
        "vexcel_outline": aerial.roof_outline(walls) is not None,
        "ground_spread_m": round(spread, 3),
        "lifted_by_ceilings": round(frac, 3),
        "max_lift_m": round(most, 2),
        "attributes": aerial.attributes,
        "rasters": aerial.source,
    }
    return roof


def splat_level(splat: sp.Splat, g: Ground, walls: Polygon) -> tuple[float, float]:
    """(offset, spread): how far the scan's ground is above the splat's, on the yard away from the house
    (median over every 7th ground cell more than 1.5 m from the walls), and the median absolute deviation.
    The two are in one frame; this is the check, and the few cm are taken out before anything is read."""
    ii, jj = np.nonzero(np.isfinite(g.z))
    x, y = g.x0 + ii[::7] * CELL, g.y0 + jj[::7] * CELL
    z = g.z[ii[::7], jj[::7]]
    away = ~shapely.dwithin(walls, shapely.points(x, y), 1.5)
    x, y, z = x[away], y[away], z[away]
    if len(x) < 50:
        raise ValueError("too little yard to level the splat on")
    sheet = splat.roof_sheet(
        (x.min() - 1, y.min() - 1, x.max() + 1, y.max() + 1), z.min() - 1.0, z.max() + 1.0
    )
    d = z - sheet(x, y)
    d = d[np.isfinite(d)]
    if len(d) < 50:
        raise ValueError("the splat has no ground under the yard")
    off = float(np.median(d))
    return off, float(np.median(np.abs(d - off)))


def splat_roof(
    splat: sp.Splat,
    vexcel: Polygon | None,
    walls: Polygon,
    rooms: list[rb.ShellRoom],
    floor_z: float,
    report: dict,
) -> tuple[rf.Roof, Callable] | tuple[None, None]:
    """The roof from the drone splat (preferred over the aerial surface model: 3 cm Gaussians in the scan's
    own frame against a 13 cm raster registered to about 0.3 m). Planes are fitted (roof.fit_roof, on
    splat.SHEET_CELL) to the splat's roof sheet over everything within splat.EDGE_REACH of the walls (and
    Vexcel's outline when there is one); the roof's edge is then where the sheet stops agreeing with those
    planes (splat.roof_outline), and the planes are fitted again inside it, never below the ceilings.
    Returns the roof and its shingles (splat.roof_colour), or (None, None) when no plane is found."""
    limit = orient(walls, 1.0).buffer(sp.EDGE_REACH, join_style="mitre")
    if vexcel is not None:
        limit = limit.union(vexcel.buffer(0.6, join_style="mitre"))
    limit = Polygon(max(polygons(limit), key=lambda q: q.area).exterior)
    sheet = splat.roof_sheet(limit.buffer(0.5).bounds, floor_z + 1.9, floor_z + 9.0)
    first = rf.fit_roof(
        sheet,
        limit,
        min_z=floor_z + 1.9,
        step=sp.SHEET_CELL,
        min_area=SPLAT_PLANE[0],
        max_planes=SPLAT_PLANE[1],
    )
    if first is None:
        return None, None
    edge = splat.roof_outline(
        sheet, first.surface, orient(walls, 1.0).buffer(MIN_OVERHANG, join_style="mitre"), limit
    )
    region = roof_region(walls, edge)
    roof = rf.fit_roof(
        sheet,
        region,
        floor=ceiling_floor(rooms),
        min_z=floor_z + 1.9,
        step=sp.SHEET_CELL,
        snap=True,
        min_area=SPLAT_PLANE[0],
        max_planes=SPLAT_PLANE[1],
    )
    if roof is None:
        return None, None
    nx, ny = roof.label.shape
    X, Y = np.meshgrid(
        roof.x0 + np.arange(nx) * roof.step, roof.y0 + np.arange(ny) * roof.step, indexing="ij"
    )
    inside = shapely.contains_xy(region, X, Y)
    d = sheet(X[inside], Y[inside]) - roof.surface(X[inside], Y[inside])
    d = d[np.isfinite(d)]
    frac, most = roof.lifted()
    colour = splat.roof_colour(roof.height, region, ROOF_RES)
    report["roof"] = {
        "source": "splat",
        "planes": len(roof.planes),
        "plane_m2": [round(a, 1) for a in roof.areas],
        "pitch_deg": round(math.degrees(roof.pitch), 1),
        "region_m2": round(region.area, 1),
        "vexcel_outline": vexcel is not None,
        "sheet_within_5cm": round(float((np.abs(d) < 0.05).mean()), 3) if len(d) else 0.0,
        "sheet_within_10cm": round(float((np.abs(d) < 0.10).mean()), 3) if len(d) else 0.0,
        "lifted_by_ceilings": round(frac, 3),
        "max_lift_m": round(most, 2),
        "shingles_covered": round(colour.covered, 3),
        "splat": splat.source,
    }
    return roof, colour


def eave_underside(
    scan: rb.Scan, roof: rf.Roof, walls: Polygon, g: Ground, floor_z: float, step: float = 0.15
) -> rf.Height:
    """(x, y) -> the height of the roof's underside outside the walls: the scanned soffit, porch ceiling or
    alcove ceiling where the scan has a flat one, else the roof's top less SOFFIT (and never above that).

    Measured like the ground, upside down: per `step` cell a ray goes up from 1.2 m above the ground and
    notes the downward-facing surfaces (normal z under -0.5) it meets between floor + 1.9 m and the roof's
    top + EAVE_OVER, the first and the highest (up to 6 hits). Cells within 0.3 m of a measured one take the
    nearest value; a 0.75 m median filter flattens the scan's crumple (a porch ceiling is flat, and the raw
    scan of one is what made 7014's entry alcove look torn).

    Flat or built: where under EAVE_FLAT of the measurements lie within EAVE_FLAT_TOL of their smoothed
    height (open rafters, beams), the area is `under.built` (a plan polygon, grown 0.6 m to the roof's edge).
    There no plane is made and the scan is kept: flattening 7014's open-raftered back porch gave a mosaic.
    `under.cover(x, y)` is how high the roof's top must be there to clear the scanned boards (NaN elsewhere).

    Elsewhere: in a strip narrower than 2 x EAVE_EDGE a measurement more than EAVE_DROP under the guess is
    not believed, and within EAVE_RIM of the roof's edge the underside is the guess, so it rises to meet the
    edge band (gutters measure as a wavy line there). `under.scanned(x, y)` is the same without that rise:
    where the scanned ceiling is, for deciding which scan faces the plane replaces. `under.measured` is the
    share of cells with a measurement."""
    region = roof.region.difference(walls.buffer(-0.02, join_style="mitre"))
    x0, y0, x1, y1 = region.bounds
    X, Y = np.meshgrid(np.arange(x0, x1 + step, step), np.arange(y0, y1 + step, step), indexing="ij")
    inside = shapely.contains_xy(region.buffer(0.2), X, Y)
    x, y = X[inside], Y[inside]
    top = roof.height(x, y)
    o = np.c_[x, y, np.maximum(g.height(x, y), floor_z) + 1.2]
    z = np.full(len(o), np.nan)  # the first underside met
    zmax = np.full(len(o), np.nan)  # the highest one (the boards above open rafters)
    todo = np.arange(len(o))
    for _ in range(6):
        if not len(todo):
            break
        t, prim = scan.rays.cast(o[todo], np.tile([0.0, 0.0, 1.0], (len(todo), 1)))
        hit = (prim >= 0) & np.isfinite(t)
        todo, t, prim = todo[hit], t[hit], prim[hit]
        zz = o[todo, 2] + t
        # The fitted top can sit a little under the real roof (it is placed to about 0.3 m): look EAVE_OVER
        # above it too.
        low = zz < top[todo] + EAVE_OVER
        todo, zz, prim = todo[low], zz[low], prim[low]
        ok = (scan.normals[prim, 2] < -0.5) & (zz > floor_z + 1.9)
        first = ok & ~np.isfinite(z[todo])
        z[todo[first]] = zz[first]
        zmax[todo[ok]] = np.fmax(zmax[todo[ok]], zz[ok])
        o[todo, 2] = zz + 1e-3
    grid = np.full(X.shape, np.nan)
    grid[inside] = z
    highest = np.full(X.shape, np.nan)
    highest[inside] = zmax
    have = np.isfinite(grid)
    built = np.zeros(X.shape, bool)
    if have.any():
        dist, idx = ndimage.distance_transform_edt(~have, return_indices=True)
        filled_ = ndimage.median_filter(grid[tuple(idx)], size=5)
        # Flat, or built (rafters, beams)? The share of measurements near their smoothed height, over 7 cells.
        near = have & (np.abs(np.where(have, grid, 0.0) - filled_) < EAVE_FLAT_TOL)
        share = ndimage.uniform_filter(near.astype(float), 7) / np.maximum(
            ndimage.uniform_filter(have.astype(float), 7), 1e-6
        )
        built = (ndimage.uniform_filter(have.astype(float), 7) > 0.3) & (share < EAVE_FLAT)
        built = ndimage.binary_opening(built, iterations=2)  # at least 0.6 m across
        # ... and out to the roof's edge (0.6 m): the rafter tails and the boards at the rim are scan too.
        built = ndimage.binary_dilation(built, iterations=4) & inside
        grid = np.where((dist * step <= 0.3) & ~built, filled_, np.nan)
    wide = region.buffer(-EAVE_EDGE, join_style="mitre").buffer(EAVE_EDGE + 0.15, join_style="mitre")
    deep = shapely.contains_xy(wide, X, Y)
    rim = ~shapely.contains_xy(roof.region.buffer(-EAVE_RIM, join_style="mitre"), X, Y)

    def measured(x: np.ndarray, y: np.ndarray, to_edge: bool) -> np.ndarray:
        x, y = np.asarray(x, float), np.asarray(y, float)
        guess = roof.height(x, y) - SOFFIT
        i = np.clip(np.rint((x - x0) / step).astype(int), 0, grid.shape[0] - 1)
        j = np.clip(np.rint((y - y0) / step).astype(int), 0, grid.shape[1] - 1)
        m = grid[i, j]
        ok = np.isfinite(m) & (deep[i, j] | (guess - m < EAVE_DROP)) & (to_edge | ~rim[i, j])
        return np.where(ok, np.minimum(guess, m), guess)

    def under(x: np.ndarray, y: np.ndarray) -> np.ndarray:
        return measured(x, y, False)

    # Where the scanned ceiling is, right up to the roof's edge: which scan faces the plane replaces.
    under.scanned = lambda x, y: measured(x, y, True)
    under.measured = float(have[inside].mean()) if inside.any() else 0.0  # for the report
    # Where the underside is built, not flat: the scan stays there and no plane is made (plan polygon).
    ii, jj = np.nonzero(built)
    half = step / 2
    cells = shapely.box(X[ii, jj] - half, Y[ii, jj] - half, X[ii, jj] + half, Y[ii, jj] + half)
    under.built = unary_union(cells).buffer(0.1, join_style="mitre") if len(ii) else Polygon()
    # ... and the roof's top must clear the scanned boards there: their highest underside + 6 cm, over 0.45 m.
    cover = np.where(ndimage.binary_dilation(built, iterations=1), highest, -np.inf)
    cover = ndimage.maximum_filter(np.where(np.isfinite(cover), cover, -np.inf), size=3) + 0.06

    def over(x: np.ndarray, y: np.ndarray) -> np.ndarray:
        i = np.rint((np.asarray(x, float) - x0) / step).astype(int)
        j = np.rint((np.asarray(y, float) - y0) / step).astype(int)
        ok = (i >= 0) & (i < cover.shape[0]) & (j >= 0) & (j < cover.shape[1])
        out = np.full(np.shape(x), np.nan)
        out[ok] = cover[i[ok], j[ok]]
        return np.where(np.isfinite(out), out, np.nan)

    under.cover = over
    return under


def aerial_roof_patches(
    roof: rf.Roof,
    walls: Polygon,
    aerial: ae.Aerial | None,
    under: rf.Height,
    shingles: Callable | None = None,
) -> list[Patch]:
    """The fitted roof as patches: the top (with `shingles` = splat.roof_colour: the drone splat's, over the
    whole top; else ground photos where a camera saw shingles and the aerial photo elsewhere), the underside
    outside the walls (eaves, porch and alcove ceilings: `under`, see eave_underside), and a SOFFIT-high
    band around the edge (fascia, rake boards)."""
    region = roof.region
    x0, y0, x1, y1 = region.bounds

    def top(s, t):
        return np.stack([s + x0, t + y0, roof.height(s + x0, t + y0)], 1)

    def colour(xyz):
        x, y = xyz[:, 0], xyz[:, 1]
        return aerial.colour(x, y), aerial.surface(x, y) - xyz[:, 2] < 0.4

    sun = (0.0, 0.0, 1.0)
    if aerial is not None:
        # The sun's direction in plan coordinates (the block frame is east, south; plan y is house-local -z).
        az, el = math.radians(SUN[0]), math.radians(SUN[1])
        sx, sy = aerial.from_block(
            aerial.position[0] + math.sin(az) * math.cos(el), aerial.position[1] - math.cos(az) * math.cos(el)
        )
        sun = (sx, sy, math.sin(el))
    top_meta = {"colour": colour if aerial is not None else None, "sun": sun}
    if shingles is not None:
        top_meta = {"colour": shingles, "whole": True}

    local = affinity.translate(region, -x0, -y0)
    out = [
        Patch(
            "roof",
            x1 - x0,
            y1 - y0,
            ROOF_RES,
            top,
            lambda s, t, xyz: roof.normal(s + x0, t + y0),
            local,
            step=0.2,
            no_sky=True,
            meta=top_meta,
        )
    ]
    pale = {"default": (226, 222, 214)}  # painted eaves, when no camera saw one at all

    eaves = region.difference(walls.buffer(-0.02, join_style="mitre")).difference(under.built)
    tiles = plan_tiles(
        eaves,
        6.0,
        "roof",
        ROOF_RES,
        under,
        lambda x, y, xyz: np.tile([0.0, 0.0, -1.0], (len(x), 1)),
        0.2,
        snap=True,  # sample the photos at the real ceiling, a few cm from the flattened one
    )
    for q in tiles:
        q.no_sky = True  # the roof's edge is placed to about 0.3 m: a ground photo there can show sky
        q.meta.update(pale)
    out += tiles
    ring = np.asarray(orient(region, -1.0).exterior.coords)  # clockwise: the left of each edge is outside
    for a, b in zip(ring[:-1], ring[1:], strict=True):
        L = float(np.linalg.norm(b - a))
        if L < 0.05:
            continue
        u2 = (b - a) / L
        nm = np.array([-u2[1], u2[0], 0.0])
        inner = a - nm[:2] * 0.03  # sample the roof just inside its edge

        def band(s, t, inner=inner, u2=u2, a=a):
            at = inner + np.outer(s, u2)
            edge = a + np.outer(s, u2)
            return np.stack([edge[:, 0], edge[:, 1], roof.height(at[:, 0], at[:, 1]) - SOFFIT + t], 1)

        out.append(
            Patch(
                "roof",
                L,
                SOFFIT,
                ROOF_RES,
                band,
                lambda s, t, xyz, nm=nm: np.tile(nm, (len(s), 1)),
                None,
                step=0.25,
                no_sky=True,
                meta={**pale, "plain": True},
            )
        )
    return out


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
    roof: rf.Roof | None = None,
    under: rf.Height | None = None,
) -> np.ndarray:
    """Scan faces to keep outdoors: outside the house volume, not on the ground heightfield, not on the
    facade planes, not under a generated roof, and inside the lot. With a `roof` from the aerial data: not
    what that roof replaces either (the scanned roof, gutters, fascia and eaves: faces under its region from
    SOFFIT + 0.3 m below its top to 0.6 m above; higher ones are tree canopy and stay), nor the
    downward-facing faces within 0.2 m of its measured underside `under` (porch and alcove ceilings,
    soffits)."""
    c = scan.tris.mean(1)
    nz = scan.normals[:, 2]
    in_lot = shapely.contains_xy(g.lot.buffer(1.0).union(walls.buffer(1.0)), c[:, 0], c[:, 1])
    in_house = shapely.contains_xy(walls.buffer(-0.02), c[:, 0], c[:, 1]) & (c[:, 2] < top + 0.1)
    near_wall = shapely.dwithin(walls.exterior, shapely.points(c[:, 0], c[:, 1]), 0.1)
    wall_top = np.full(len(c), eave_z)
    replaced = np.zeros(len(c), bool)
    if roof is not None:
        roofed = np.nonzero(shapely.contains_xy(roof.region.buffer(0.05), c[:, 0], c[:, 1]))[0]
        rz = roof.height(c[roofed, 0], c[roofed, 1])
        wall_top[roofed] = rz - SOFFIT
        replaced[roofed] = (c[roofed, 2] > rz - SOFFIT - 0.3) & (c[roofed, 2] < rz + 0.6)
        if under is not None:
            uz = getattr(under, "scanned", under)(c[roofed, 0], c[roofed, 1])
            replaced[roofed] |= (np.abs(c[roofed, 2] - uz) < 0.2) & (nz[roofed] < -0.3)
            # Where the scan has a soffit or ceiling lower than the guess, everything from 0.1 m under it up
            # to the roof goes (gutter fronts, fascia, framing above a ceiling): the band above only reaches
            # SOFFIT + 0.3 m down and left ragged scraps hanging under 6915's back eave.
            outside = ~shapely.contains_xy(walls.buffer(-0.02), c[roofed, 0], c[roofed, 1])
            lower = uz < rz - SOFFIT - 0.02
            replaced[roofed] |= outside & lower & (c[roofed, 2] > uz - 0.1) & (c[roofed, 2] < rz + 0.6)
            kept_area = getattr(under, "built", None)
            if kept_area is not None and not kept_area.is_empty:  # open rafters: the scan is the underside
                replaced[roofed] &= ~(
                    shapely.contains_xy(kept_area, c[roofed, 0], c[roofed, 1]) & (nz[roofed] < 0.3)
                )
        in_house[roofed] |= shapely.contains_xy(walls.buffer(-0.02), c[roofed, 0], c[roofed, 1]) & (
            c[roofed, 2] < rz
        )
    on_facade = near_wall & (np.abs(nz) < 0.5) & (c[:, 2] < wall_top + 0.05)
    in_wall_slab = shapely.contains_xy(walls, c[:, 0], c[:, 1]) & ~shapely.contains_xy(
        outline.buffer(-0.02), c[:, 0], c[:, 1]
    )
    gz = g.height(c[:, 0], c[:, 1])
    on_ground = (c[:, 2] - gz < 0.1) & (nz > 0.6) & shapely.contains_xy(g.lot, c[:, 0], c[:, 1])
    below = c[:, 2] < gz - 0.25
    keep = in_lot & ~in_house & ~on_facade & ~in_wall_slab & ~on_ground & ~below & ~replaced
    if eaves is not None and roof is None:
        keep &= ~(shapely.contains_xy(eaves.buffer(0.05), c[:, 0], c[:, 1]) & (c[:, 2] > eave_z - 0.25))
    return np.nonzero(keep)[0]


def _seen_faces(scan: rb.Scan, faces: np.ndarray, panos: PanoSet) -> np.ndarray:
    """The faces whose centre an outdoor camera sees from the front, judged face by face. Not rb._seen:
    indoors it keeps a piece whole once a quarter of it is seen (half a toilet looked wrong), but outdoors a
    tree canopy or a fence run is one huge piece, and keeping them whole took 7014's interior.glb from 22 to
    33 MB (255k tree triangles instead of 140k) for surfaces no photo shows."""
    if not len(faces):
        return faces
    # Per 3 m cell (rebake._shade_local): `shade` only asks the 12 panoramas nearest the middle of the points
    # it is given. Given the whole lot at once it ignored 3 of 7014's 15 outdoor cameras, and which pieces
    # at the lot's edges survived changed whenever the set of faces (and so its middle) changed.
    _, valid = rb._shade_local(scan.tris[faces].mean(1), scan.normals[faces], panos, scan.eyes)
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


def _clip_plants(scene: trimesh.Scene, roof: rf.Roof, clear: float = 0.3) -> int:
    """Drop the triangles of the plant models (`plants_N` nodes) that reach under the roof: any vertex inside
    the roof's region and less than `clear` above its top. vegetation.py keeps models out of the walls; a
    porch roof reaches further (7014's sapling by the back porch grew through it). Returns how many went."""
    back = np.linalg.inv(hm.Z_UP_TO_Y_UP)
    cut = 0
    for name in [n for n in scene.geometry if n.startswith("plants_")]:
        m = scene.geometry[name]
        v = trimesh.transform_points(m.vertices, back)
        under = shapely.contains_xy(roof.region, v[:, 0], v[:, 1])
        under[under] &= v[under, 2] < roof.height(v[under, 0], v[under, 1]) + clear
        bad = under[m.faces].any(1)
        if bad.any():
            cut += int(bad.sum())
            m.update_faces(~bad)
    return cut


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
    bake: frozenset[str] = PARTS,
    aerial: ae.Aerial | None = None,
    remove: list[tuple[float, float, float, float]] = (),
    splat: sp.Splat | None = None,
) -> Exterior:
    """Bake the lot, facades, roof, outdoor pieces and backdrop into `scene` (Y-up nodes). `doors` are the
    outside doors opened for play (also cut from the rooms' walls via ShellRoom.cuts, and from collision).
    `bake` (a subset of PARTS) bakes only those; the geometry of the others is still measured. With
    `aerial` (aerial.load) the roof is fitted to the aerial surface model; without it the scan's roof is kept
    when it has one, else a hip roof is generated. `remove` = (x, y, z, radius) hand removals
    (overrides/<model>.json "remove_outdoor"): outdoor scan faces within `radius` of the point horizontally
    and within max(radius, 1 m) of its height are dropped (scan artefacts that are not in the photos)."""
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

    roof, shingles = None, None
    if splat is not None:
        # The drone splat (splat.py): levelled on the yard, then the roof's shape and shingles, and further
        # down the walls no camera saw. Off by more than SPLAT_LEVEL it is not this house's: not used.
        try:
            lift, spread = splat_level(splat, g, walls)
        except ValueError:
            lift, spread = math.inf, math.inf
        report["splat"] = {"ground_offset_m": round(lift, 3), "ground_spread_m": round(spread, 3)}
        if abs(lift) > SPLAT_LEVEL or spread > SPLAT_LEVEL:
            splat = None
        else:
            splat = dataclasses.replace(splat, xyz=splat.xyz + np.array([0.0, 0.0, lift], np.float32))
            roof, shingles = splat_roof(
                splat,
                aerial.roof_outline(walls) if aerial is not None else None,
                walls,
                rooms,
                floor_z,
                report,
            )
    if roof is None and aerial is not None:
        roof = aerial_roof(aerial, g, walls, rooms, floor_z, report)
    under = None
    if roof is not None:
        under = eave_underside(scan, roof, walls, g, floor_z)
        ceilings, boards = roof.floor, under.cover
        roof.floor = lambda x, y: np.fmax(ceilings(x, y), boards(x, y))  # NaN only where both are
        report["roof"]["underside_measured"] = round(under.measured, 2)
        report["roof"]["underside_kept_as_scan_m2"] = round(under.built.area, 1)
    # Line of sight outdoors: the scan plus the fitted roof. A scan often has no roof (7014: 2% of it), and
    # without one a camera in the yard "sees" the far slope, a porch ceiling or the sky through the roof.
    eyes = scan
    if roof is not None:
        v2, f2 = clipped_grid(roof.region, 0.25)
        lid = np.c_[v2, roof.height(v2[:, 0], v2[:, 1])][f2]
        eyes = dataclasses.replace(scan, sight=Raycaster(np.concatenate([scan.tris, lid])))
    planes = facade_planes(corners, normals, g, eave_z, doors, rooms, roof)
    frames = door_frames(corners, normals, doors, floor_z)
    report["doors_opened"] = len(doors)
    cover = roof_coverage(scan, outline, top)
    report["roof_scan_coverage"] = round(cover, 2)
    eaves = None
    patches = ground_patches(g)
    if roof is not None:
        eaves = roof.region
        patches += aerial_roof_patches(roof, walls, aerial, under, shingles)
    elif cover < 0.7:
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
    if "facade" not in bake:
        planes, frames = [], []
    patches = [p for p in patches if p.kind in bake]

    seen = {}
    for p in frames:  # seen from inside and outside: every camera
        rb.bake_plane(p, scan, panos, FACADE_RES)
    blank = []
    # Walls no ground camera saw (a side strip nobody walked, a gable behind a tree): the drone splat's view
    # of them, texel by texel, before the scan's own texture and the plain colour (splat.WallFill).
    wall_fill = sp.WallFill(splat, SPLAT_GAIN) if splat is not None else None
    for p in planes:
        st, _ = rb.bake_plane(
            p, eyes, out_panos, FACADE_RES, fallback=True, relief=FACADE_RELIEF, extra=wall_fill
        )
        seen.setdefault("facade", []).append((st["seen"], st["area"]))
        if st["seen"] + st["extra"] < 0.02:
            blank.append(p)
    if wall_fill is not None and wall_fill.filled:
        total = sum(a for a, _ in wall_fill.filled)
        report["splat"]["facade_filled"] = round(
            sum(a * f for a, f in wall_fill.filled) / max(total, 1e-9), 3
        )
        report["splat"]["facade_gains"] = [round(k, 2) for k in wall_fill.gains]
        report["splat"]["facade_walls"] = wall_fill.walls
    # A facade no camera saw (and the scan has no outer surface for) gets the house's typical facade colour
    # instead of _fill's flat grey.
    blank_ids = {id(p) for p in blank}
    lit = [p.image[..., :3].reshape(-1, 3) for p in planes if id(p) not in blank_ids]
    if blank and lit:
        colour = np.median(np.concatenate(lit)[::7], axis=0).astype(np.uint8)
        for p in blank:
            p.image[..., :3] = colour

    def shade_patch(p: Patch) -> None:  # not `bake`: that is this function's argument
        far = p.kind == "backdrop"
        f = bake_patch(p, out_panos, eyes, max_dist=backdrop_radius * 2.5 if far else 30.0)
        seen.setdefault(p.kind, []).append((f, p.w * p.h))

    # The band around the roof's edge is painted boards: one plain colour (the median of its bright,
    # photographed texels), not photos. Leaves between a camera and the edge, and the 0.3 m the roof may be
    # off, made it a strip of leaf and sky colours. Baked first: eaves no camera saw take the same colour
    # (soffits are painted like the trim; a tile's few dark seen texels made them brown).
    bands = [p for p in patches if p.meta.get("plain")]
    for p in bands:
        shade_patch(p)
    if bands:
        got = np.concatenate([p.meta.pop("samples") for p in bands])
        bright = got[got.mean(1) > 150] if len(got) else got
        trim = np.median(bright, axis=0) if len(bright) > 200 else np.asarray(bands[0].meta["default"], float)
        for p in bands:
            p.image[..., :3] = trim.astype(np.uint8)
        report["roof"]["trim_rgb"] = [int(v) for v in trim]
        for p in patches:
            if "default" in p.meta and not p.meta.get("plain"):
                p.meta["default"] = tuple(int(v) for v in trim)
    for p in patches:
        if not p.meta.get("plain"):
            shade_patch(p)
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
    pages = rb.pack(items) if items else []
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

    report["roof"]["aerial_photo"] = next((p.meta["aerial"] for p in patches if "aerial" in p.meta), None)
    faces = outdoor_faces(scan, outline, walls, g, top, eaves, eave_z, roof, under)
    if "pieces" not in bake:
        faces = faces[:0]
    before = len(faces)
    for x, y, z, r in remove:
        fc = scan.tris[faces].mean(1)
        hit = (np.hypot(fc[:, 0] - x, fc[:, 1] - y) < r) & (np.abs(fc[:, 2] - z) < max(r, 1.0))
        faces = faces[~hit]
    report["removed_by_hand"] = {"places": len(remove), "faces": before - len(faces)}
    faces = rb._drop_crumbs(scan, _seen_faces(eyes, faces, out_panos), min_area=0.03)
    report["pages"] = {"shell": len(pages)}
    tree = np.zeros(len(faces), bool)
    if len(faces):
        c = scan.tris[faces].mean(1)
        tree = c[:, 2] - g.height(c[:, 0], c[:, 1]) > TREE_MIN_Z
        if roof is not None:  # over the roof only what outdoor_faces left above it is canopy
            tree &= ~shapely.contains_xy(eaves.buffer(0.5), c[:, 0], c[:, 1]) | (
                c[:, 2] > roof.height(c[:, 0], c[:, 1]) + 0.6
            )
        elif eaves is not None:
            tree &= ~shapely.contains_xy(eaves.buffer(0.5), c[:, 0], c[:, 1])
        else:
            tree &= c[:, 2] > top + 1.5  # scanned roof: only well above it
    # Generated geometry that replaces scan pieces: fence panels, then tree and shrub models. Each adds its
    # own nodes to the scene and returns the faces it replaces (not rendered, not in collision) and the
    # collision triangles of what it built.
    avail = np.ones(len(faces), bool)
    solids = []
    if len(faces):
        # For plants the house is everything under the roof: a sapling beside a porch post, measured and
        # modelled, came out as a crown growing through the porch roof. It stays scan.
        roofed = walls if roof is None else filled(walls.union(roof.region))

        def rails(*args):  # railings come from the splat (railings.py); without one nothing is replaced
            return railings.replace_railings(*args, splat=splat)

        for replace, house in (
            (fences.replace_fences, walls),
            (rails, walls),
            (vegetation.replace_vegetation, roofed),
        ):
            taken, tris = replace(scene, eyes, g, faces, tree, avail, out_panos, house, report)
            avail &= ~taken
            solids.append(np.asarray(tris, float).reshape(-1, 3, 3))
    if roof is not None:
        report["plants_cut_by_roof"] = _clip_plants(scene, roof)
    for sel, name, res in ((~tree & avail, "outdoor", PIECE_RES), (tree & avail, "trees", TREE_RES)):
        if not sel.any():
            continue
        pg_imgs, uv, pg = rb.bake_triangles(eyes, faces[sel], out_panos, res)
        if name == "outdoor" and eaves is None and roof is None:  # scanned roof: no sky on it
            fc = scan.tris[faces[sel]].mean(1)
            roof = shapely.contains_xy(walls.buffer(0.8), fc[:, 0], fc[:, 1]) & (fc[:, 2] > eave_z - 0.1)
            roof &= scan.normals[faces[sel], 2] > 0.3  # roof surface, not white gutters and soffits
            unsky(pg_imgs, uv, pg, roof)
        if name == "trees":
            pg_imgs = cut_sky(pg_imgs, uv, pg, np.ones(int(sel.sum()), bool))
        for node, m in _soup(scan.tris[faces[sel]], uv, pg_imgs, pg, name):
            scene.add_geometry(m, node_name=node, geom_name=node)
        report["pages"][name] = len(pg_imgs)
    report["outdoor_faces"] = int((~tree & avail).sum())
    report["tree_faces"] = int((tree & avail).sum())
    report["texture_pages"] = sum(report["pages"].values())

    # Collision ground: the same heightfield on a coarser grid (players do not feel 15 cm cells).
    v2, f2 = clipped_grid(g.lot, 0.45)
    v3 = np.c_[v2, g.height(v2[:, 0], v2[:, 1])]
    gt = [v3[f2]]
    low = np.zeros(0, int)
    if len(faces):
        c = scan.tris[faces].mean(1)
        low = faces[(c[:, 2] - g.height(c[:, 0], c[:, 1]) < 2.2) & ~tree & avail]
    return Exterior(
        report=report,
        ground_tris=np.concatenate(gt) if gt else np.zeros((0, 3, 3)),
        piece_tris=np.concatenate([scan.tris[low], *solids]),
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
    ap.add_argument(
        "--dest", type=Path, help="write the glb here instead of out/cache/glb/<model>_exterior.glb"
    )
    ap.add_argument("--parts", help=f"comma-separated subset of {sorted(PARTS)} to bake (default: all)")
    ap.add_argument("--no-aerial", action="store_true", help="roof from the scan alone (no Vexcel layers)")
    ap.add_argument("--no-splat", action="store_true", help="do not use the drone splat (out/raw/splat)")
    args = ap.parse_args(argv)
    bake = frozenset(args.parts.split(",")) if args.parts else PARTS  # not `parts`: those are the rooms below
    if bake - PARTS:
        ap.error(f"unknown parts {sorted(bake - PARTS)}")
    house = next(h for h in load_block(args.block)["houses"] if h["listing_id"] == args.listing)
    model_id = house["matterport_model_id"]
    model = mp.fetch_model(model_id, args.out / "cache" / "matterport")
    ov = bh.OVERRIDES / f"{model_id}.json"
    overrides = json.loads(ov.read_text()) if ov.exists() else {}
    parts = bh.apply_splits(bh.read_rooms(model), overrides)
    raw = args.out / "raw" / model_id
    scan = rb.load_scan(bh.extract_matterpak(model_id, raw))
    fz = bh.scan_floor_z(scan, parts)
    for p in parts:
        p.floor_z = fz.get(p.floor, 0.0)
    spans = bh.opening_spans(parts, lambda e, z: bh.runs_from_rays(e, scan.rays, z), np.zeros((0, 3)))
    footprint = house_footprint(scan, bh.shell_rooms(parts), bh.void_polys(parts, overrides))
    doors = bh.play_doors(parts, spans, footprint)
    rooms = bh.shell_rooms(parts, spans)
    for r in rooms:
        rb.measure_ceiling(scan, r)
    panos = PanoSet(model, raw / "skybox_2k")
    scene = trimesh.Scene()
    air = (
        None
        if args.no_aerial
        else ae.load(args.out, load_block(args.block), bh.listing_entry(args.out, args.listing))
    )
    hand = overrides.get("remove_outdoor") or []
    ext = add_exterior(
        scene,
        scan,
        rooms,
        panos,
        doors,
        footprint,
        bake=bake,
        aerial=air,
        remove=[(*d["at"], float(d["radius"])) for d in hand],
        splat=None if args.no_splat else sp.load(args.out, model_id, footprint.centroid.coords[0]),
    )
    dest = args.dest or args.out / "cache" / "glb" / f"{model_id}_exterior.glb"
    dest.parent.mkdir(parents=True, exist_ok=True)
    scene.export(dest)
    print(json.dumps(ext.report, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
