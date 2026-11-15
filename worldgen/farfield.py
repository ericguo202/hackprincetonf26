"""What stands just past the scanned lot, as geometry from the drone splat, and a clean sky.

Before this the backdrop was the ground photos projected on a flat disk and a 45 m dome
(exterior.backdrop_patches). That is right for what is about 45 m away (the houses across the street, seen
from the driveway) and wrong for what stands just behind the fence: a neighbour's house 8 m away was painted
on the dome and on the ground under it, stretched, the wrong size, doubled where two cameras disagreed and
sliding as the player walked. Ground cameras cannot fix that: they never see behind a fence. The drone does
(docs/house-pipeline-handoff.md 3.11 and 3.13). With a splat:

- `build`: a height surface of everything within FAR_RADIUS that is outside the lot (the neighbours' houses
  and yards, the street, cars), from the splat's top view, with trees taken out of the surface (they come
  back as tree models: `FarField.trees`), simplified to FAR_FACES triangles. Texture: the ground photos
  wherever a camera sees the surface, else nine orthographic renders of the splat (straight down and eight
  obliques), each face from the render that sees it most squarely.
- Past FAR_RADIUS the photo disk and dome stay (the caller shades them with `FarField.solid` in the line of
  sight, so the neighbour's house is no longer painted on them).
- `sky` and `clear`: one panorama of the sky by direction, from the outdoor photos, shown wherever the
  dome is open sky (above the splat's skyline) or a photo shows a sky colour: no slabs where one camera's
  exposure met another's, no leaves of trees that became models.

Nothing of the splat ships: a mesh, its textures, and tree models.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import fast_simplification
import numpy as np
import shapely
import trimesh
from PIL import Image
from scipy import ndimage
from scipy.cluster.vq import kmeans2
from shapely.geometry import Polygon, box
from shapely.geometry.polygon import orient
from shapely.ops import unary_union

from worldgen import rebake as rb
from worldgen import splat as sp
from worldgen import vegetation as vg
from worldgen.panos import PanoSet, Raycaster, shade

FAR_RADIUS = 34.0  # the surface reaches this far from the house; past it the photo backdrop takes over, m
SKY_REACH = 56.0  # the skyline (what stands against the sky) is read this far out, m
FAR_CELL = 0.25  # grid of the height surface, m
FAR_FACES = 60000  # triangles after simplification
FAR_UNDER = 0.3  # the surface ends this far inside the house's outer wall faces (under the floor), m
FAR_HOUSE = 1.0  # within this of the walls the surface lies at least FAR_SINK under the floor, m
FAR_SINK = 0.1
FAR_BLEND = 3.0  # over this much before an edge the surface's ground is brought to the disk's height, m
FAR_OPEN = 2.0  # open ground (seen by a camera) is closed and opened by this, m
FAR_BUILDING = 2.2  # higher than this above the ground, and no crown, is a building (a car is lower), m
FAR_SKIRT = 1.5  # a building in open ground keeps this much surface around it, m
FAR_LAP = 0.75  # the photo backdrop's ground disk starts this far inside the surface's edge, m
FAR_THIN = (5, 1.0)  # something narrower than this many cells and higher than this (m) is cut off (a pole)
FAR_INSIDE = 0.4  # the surface starts this far inside the lot's edge (under the yard, FAR_DROP lower), m
FAR_DROP = 0.06
FAR_APRON = 1.5  # within this of the lot's edge the surface is the ground alone (no fence, no shrub), m
FAR_PHOTO = 45.0  # a ground camera's photo is used on the far field up to this distance, m
FAR_COS = 0.2  # ... when it sees the surface less obliquely than this (cosine to the normal)
FAR_COS_LEVEL = 0.03  # ... or than this on level ground (a street is flat: a grazing photo lands right)
FAR_LEVEL = 0.97  # level = the face's normal is this upright
FAR_SKY = 2.0  # ... and above this height not where the photo shows a sky colour, m
FAR_FADE = 4  # ... faded into the splat's colour over this many texels
TOP_RES = 12.0  # texels per metre of the straight-down render
SIDE_RES = 14.0  # ... of the eight oblique renders
SIDE_TILT = math.radians(25.0)  # the obliques look down by this much
FAR_PAD = 2  # px of its render kept around each obliquely textured face
FAR_DIP = 6.0  # the ground is nowhere more than this below its median height (lower Gaussians are strays), m
FAR_DEPTH = 0.6  # a face is seen by a render when it lies within this of the render's depth, m
TREE_MIN = 2.0  # a tree: this high above the ground ...
TREE_SPREAD = 0.8  # ... with its Gaussians spread over this much height (a roof is a sheet), or green
CROWN_UNDER = 2.0  # a crown's underside is at least this far above the ground (line of sight), m
TREE_MIN_AREA = 6.0  # m2 of plan: a smaller patch is no tree (it stays in the surface)
TREE_MIN_TOP = 3.5  # m: nor is one lower than this
TREE_SPLIT = 90.0  # m2 of plan: a larger patch of crowns is several trees
PANEL_OFFSET = 4.0  # wall_panels: the walls stand this far outside the explorable area, m
PANEL_MIN = 3.0  # ... a side shorter than this gets no wall, m
PANEL_TOP = 12.0  # ... they reach this high above the ground, m
PANEL_FOOT = 0.5  # ... and this far under it
PANEL_DEPTH = 45.0  # ... and show what lies within this beyond them, m
PANEL_RES = 32.0  # ... at this many texels per metre
SKY_RES = 8.0  # texels per degree of the sky panorama
SKY_MIN_EL = 8.0  # degrees: lower than this the sky is a smooth continuation (haze), never a photo
SKY_GAIN = (0.7, 1.4)  # a camera's sky is scaled to the sky already laid down by at most this
SKY_MARGIN = 3.0  # degrees: the dome is sky alone from this far above the skyline
SKY_PHOTO_EL = 6.0  # degrees: above this a sky-coloured photo texel on the dome is replaced by the panorama
SKY_FEATHER = 2.0  # degrees over which one camera's sky fades into the next


@dataclass
class FarField:
    meshes: list[tuple[str, trimesh.Trimesh]]  # Z-up, textured: nodes `backdrop_far_N`
    trees: list[vg.Tree]
    skyline: np.ndarray  # (n, 3, 3) the raw top surface with trees out to SKY_REACH, for the sky
    solid: np.ndarray  # (n, 3, 3) the surface and a shell around every crown: what blocks a ground camera
    horizon: np.ndarray  # (n, 3, 3) the skyline past `radius` only: what the photo dome may show
    covered: Polygon  # the ground the surface covers, FAR_LAP inside its edge: the disk's hole
    centre: tuple[float, float]
    radius: float
    report: dict = field(default_factory=dict)


def _expose(rgb: np.ndarray, gain: np.ndarray) -> np.ndarray:
    """Brighten (or darken) by `gain` per channel without clipping: 255 (1 - (1 - c / 255) ^ gain). Dark and
    middle tones are scaled by about `gain`; a sunlit street stays below white instead of burning out (the
    gain is measured on grass, the far field is mostly concrete and roofs)."""
    return 255.0 * (1.0 - (1.0 - np.clip(rgb, 0, 255) / 255.0) ** np.asarray(gain, float))


def _per_cell(cell: np.ndarray, z: np.ndarray, n: int, q: float) -> np.ndarray:
    """The q-quantile (0..1) of z among the points of each cell (ids 0..n-1); NaN for empty cells."""
    order = np.lexsort((z, cell))
    c, zs = cell[order], z[order]
    start = np.searchsorted(c, np.arange(n))
    count = np.searchsorted(c, np.arange(n), side="right") - start
    out = np.full(n, np.nan)
    has = count > 0
    out[has] = zs[start[has] + np.minimum((count[has] * q).astype(int), count[has] - 1)]
    return out


def _grid_mesh(x0: float, y1: float, cell: float, z: np.ndarray, keep: np.ndarray):
    """Vertices at the centres of the kept cells (row j runs down in y) and two triangles per square of four
    kept neighbours, facing up."""
    idx = np.full(z.shape, -1)
    idx[keep] = np.arange(int(keep.sum()))
    jj, ii = np.nonzero(keep)
    verts = np.stack([x0 + (ii + 0.5) * cell, y1 - (jj + 0.5) * cell, z[keep]], 1)
    q = keep[:-1, :-1] & keep[1:, :-1] & keep[:-1, 1:] & keep[1:, 1:]
    a, b, c, d = idx[:-1, :-1][q], idx[1:, :-1][q], idx[1:, 1:][q], idx[:-1, 1:][q]
    return verts, np.concatenate([np.stack([a, b, c], 1), np.stack([a, c, d], 1)])


def _crown_shell(mask: np.ndarray, under: np.ndarray, top: np.ndarray, x0: float, y1: float, cell: float):
    """Triangles that close the crowns in `mask` for line of sight: a sheet at each cell's underside, one at
    its top, and a curtain between them wherever a crown cell borders a cell that is none. A ground camera
    then does not print a crown's leaves on what is behind it, and still sees out under the crown."""
    lo_v, lo_f = _grid_mesh(x0, y1, cell, under, mask)
    hi_v, hi_f = _grid_mesh(x0, y1, cell, top, mask)
    tris = [lo_v[lo_f], hi_v[hi_f]]
    jj, ii = np.nonzero(mask)
    for dj, di in ((0, 1), (0, -1), (1, 0), (-1, 0)):
        nj, ni = jj + dj, ii + di
        inside = (nj >= 0) & (nj < mask.shape[0]) & (ni >= 0) & (ni < mask.shape[1])
        edge = ~inside
        edge[inside] = ~mask[nj[inside], ni[inside]]
        j, i = jj[edge], ii[edge]
        cx, cy = x0 + (i + 0.5) * cell, y1 - (j + 0.5) * cell
        # the shared side of the two cells: half a cell toward the neighbour, a cell long across
        mx, my = cx + di * cell / 2, cy - dj * cell / 2
        ax, ay = mx - dj * cell / 2, my - di * cell / 2
        bx, by = mx + dj * cell / 2, my + di * cell / 2
        a0, b0 = np.c_[ax, ay, under[j, i]], np.c_[bx, by, under[j, i]]
        a1, b1 = np.c_[ax, ay, top[j, i]], np.c_[bx, by, top[j, i]]
        tris += [np.stack([a0, b0, b1], 1), np.stack([a0, b1, a1], 1)]
    return np.concatenate(tris) if tris else np.zeros((0, 3, 3))


def build(
    splat: sp.Splat,
    hole: Polygon,
    roofed: Polygon,
    centre: tuple[float, float],
    gain: np.ndarray,
    radius: float = FAR_RADIUS,
    debug: Path | None = None,
    panos: PanoSet | None = None,
    sight: np.ndarray | None = None,
    is_sky: Callable[[np.ndarray], np.ndarray] | None = None,
    yard: Callable[[np.ndarray, np.ndarray], np.ndarray] | None = None,
    disk: Callable[[np.ndarray, np.ndarray], np.ndarray] | None = None,
    house: Polygon | None = None,
    floor_z: float = 0.0,
) -> FarField:
    """The far field around `centre` out to `radius`. `hole` is the scanned lot with the house (nothing is
    built inside it but a FAR_INSIDE rim under the yard's edge), `roofed` the house with its roof's overhang
    (the splat's picture of our own roof is not ground), `gain` the per-channel factor that brings the
    splat's colours to the ground photos (splat.GroundFill).

    With `panos` (the outdoor panoramas) and `sight` (triangles that block their view: the scan and the
    fitted roof), the photos come first here too: every texel of the renders that a camera within FAR_PHOTO
    sees less obliquely than FAR_COS (the far field itself and the crowns' stand-in prisms block the view)
    takes the photo, faded into the splat's colour over FAR_FADE px, and the splat's colours are then
    scaled to those photos (the median ratio per channel over the texels both have, at least 5000, within
    splat.GROUND_GAIN) instead of by `gain`: the yard's gain is measured on grass, the far field is
    concrete, roofs and walls. `yard(x, y)` is the scanned yard's height: within FAR_APRON of the lot the
    surface continues it (the splat's own ground under an eave or beside a fence is a guess). `disk(x, y)` is
    the height of the photo backdrop's ground disk: over the last FAR_BLEND metres before an edge the surface
    is shifted so that its ground meets the disk (the splat's ground drifts by up to a metre 30 m out, and
    a street lower than the disk beside it showed as a gap under the disk's edge).

    `house` (the outer wall faces) and `floor_z`: nothing of the surface may show in a room. It ends
    FAR_UNDER inside the walls, and within FAR_HOUSE of them it lies at least FAR_SINK under the floor (the
    rim under the yard's edge and the unscanned strip beside a wall once stood 0.4 m inside the walls at
    yard or deck height: dark wedges along the skirting and a spike in a corner of the dining room).

    Where the far field is built: with `panos`, only where it helps. Ground that a camera sees (the open
    street in front of the house: line of sight to the ground against `sight`, closed and opened by
    FAR_OPEN) stays the photo disk, which is sharper there and right for flat ground; the surface covers
    the ground no camera sees (behind the fences) and every building (over FAR_BUILDING high, no crown)
    with FAR_SKIRT around it, whoever sees it: a house is not flat ground. `FarField.covered` is that
    region, for the disk's hole. Near the scan
    positions (the street in front of the house) the far field is then as sharp as the photos; behind
    fences and houses, where no ground camera sees, it is the splat."""
    cx, cy = centre
    reach = max(radius, min(SKY_REACH, sp.RADIUS - 2.0))  # the grid; the surface itself ends at `radius`
    n = int(round(2 * reach / FAR_CELL))
    x0, y1 = cx - reach, cy + reach
    # Per cell: the lowest Gaussians (the ground where the drone saw it) and how far they spread in height.
    gi = np.floor((splat.xyz[:, 0] - x0) / FAR_CELL).astype(int)
    gj = np.floor((y1 - splat.xyz[:, 1]) / FAR_CELL).astype(int)
    inb = (gi >= 0) & (gi < n) & (gj >= 0) & (gj < n)
    solid = inb & (splat.alpha > 0.3)
    cell = gj[solid] * n + gi[solid]
    zs = splat.xyz[solid, 2].astype(float)
    # Stray Gaussians far under the ground (the splat has some at -50 m) are not ground: a cell's low point
    # counts within FAR_DIP of the median low point, and single low cells are median-filtered away.
    floor = float(np.median(_per_cell(cell, zs, n * n, 0.05)[np.unique(cell)])) - FAR_DIP
    sound = (
        splat.xyz[:, 2] > floor
    )  # nothing below this is rendered: a stray under thin ground drags the depth
    sub = 4
    top = float(np.percentile(splat.xyz[:, 2], 99.9)) + 2.0
    _, al, dep = splat.flatten(
        (x0, y1, top),
        (1, 0, 0),
        (0, -1, 0),
        2 * reach,
        2 * reach,
        sub / FAR_CELL,
        (0, 0, -1),
        0.0,
        1e3,
        keep=sound,
    )
    with np.errstate(all="ignore"):
        ztop = np.nanmedian(
            (top - dep).reshape(n, sub, n, sub).transpose(0, 2, 1, 3).reshape(n, n, sub * sub), axis=2
        )
    valid = np.isfinite(ztop)
    real = zs > floor
    low = _per_cell(cell[real], zs[real], n * n, 0.05).reshape(n, n)
    low = np.where(np.isfinite(low), ndimage.median_filter(np.nan_to_num(low, nan=1e3), size=3), np.nan)
    ground = ndimage.minimum_filter(np.where(np.isfinite(low), low, 1e3), size=13)
    ground = ndimage.uniform_filter(np.where(ground > 500, np.nanmedian(low), ground), size=9)
    above = zs > ground.ravel()[cell] + 1.0
    hi = _per_cell(cell[above], zs[above], n * n, 0.9).reshape(n, n)
    lo = _per_cell(cell[above], zs[above], n * n, 0.1).reshape(n, n)
    spread = np.nan_to_num(hi - lo)
    height = np.where(valid, ztop - ground, 0.0)
    # Under a building wider than the 13-cell minimum the drone saw no ground and `ground` is its roof. The
    # ground there is the bare ground around it: the nearest cell whose top is within 0.4 m of its low
    # points.
    bare = valid & (height < 0.4)
    if bare.any():
        idx = ndimage.distance_transform_edt(~bare, return_distances=False, return_indices=True)
        terrain = ndimage.uniform_filter(ground[tuple(idx)], size=9)
    else:
        terrain = ground
    rgb, _, _ = splat.flatten(
        (x0, y1, top),
        (1, 0, 0),
        (0, -1, 0),
        2 * reach,
        2 * reach,
        1 / FAR_CELL,
        (0, 0, -1),
        0.0,
        1e3,
        keep=sound,
    )
    green = (rgb[..., 1] > rgb[..., 0] * 1.02) & (rgb[..., 1] > rgb[..., 2] * 1.15)
    tree = (height > TREE_MIN) & ((spread > TREE_SPREAD) | green)
    tree = ndimage.binary_opening(ndimage.binary_closing(tree, iterations=2), iterations=2)
    tree = ndimage.binary_dilation(tree, iterations=2) & (height > 0.6)
    # Only patches big and tall enough to be a tree: a speck on a roof's edge would become a pit in the roof.
    lab, count = ndimage.label(tree)
    if count:
        ids = np.arange(1, count + 1)
        big = np.asarray(ndimage.sum(tree, lab, ids)) * FAR_CELL**2 >= TREE_MIN_AREA
        tall = np.asarray(ndimage.maximum(height, lab, ids)) >= TREE_MIN_TOP
        tree = np.isin(lab, ids[big & tall])
    if debug is not None:  # top view | crowns in red over it | height above the ground (0 to 10 m) | spread
        over = rgb.copy()
        over[tree] = over[tree] * 0.4 + np.array([255.0, 0, 0]) * 0.6
        ramp = np.repeat(np.clip(height / 10, 0, 1)[..., None] * 255, 3, 2)
        spr = np.repeat(np.clip(spread / 3, 0, 1)[..., None] * 255, 3, 2)
        debug.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(np.hstack([rgb, over, ramp, spr]).astype(np.uint8)).save(debug)
    X, Y = np.meshgrid(x0 + (np.arange(n) + 0.5) * FAR_CELL, y1 - (np.arange(n) + 0.5) * FAR_CELL)
    # Our own roof's overhang outside the walls is not ground either: it is dropped like a crown.
    own = shapely.contains_xy(roofed.buffer(0.3), X, Y)
    drop = tree | own | ~valid
    # Under what is dropped the surface is the ground, never higher than the surface next to it.
    idx = ndimage.distance_transform_edt(drop, return_distances=False, return_indices=True)
    beside = np.where(valid, ztop, ground)[tuple(idx)]
    z = np.where(drop, np.minimum(ground, beside + 0.5), ztop)
    z = np.maximum(ndimage.median_filter(z, size=3), ground - 0.5)  # no pit under the ground
    inside = shapely.contains_xy(hole.buffer(-FAR_INSIDE), X, Y)
    rim = shapely.contains_xy(hole, X, Y) & ~inside
    # Along the lot's edge the surface is bare ground: the fence there is a panel of its own, and a ridge of
    # fence and shrub Gaussians beside it stood in front of it as a smeared slab.
    apron = shapely.contains_xy(hole.buffer(FAR_APRON), X, Y)
    z[apron] = np.minimum(z[apron], ground[apron]) if yard is None else yard(X[apron], Y[apron])
    z[rim | (apron & (yard is not None))] -= FAR_DROP
    dist = np.hypot(X - cx, Y - cy)
    keep = ~inside & (dist < radius)
    if panos is not None and len(panos) and sight is not None:
        # Open ground, on a 1 m grid: some camera within FAR_PHOTO has a free line to the ground there.
        c = 4
        gx, gy, gz = X[::c, ::c], Y[::c, ::c], ground[::c, ::c] + 0.15
        pts = np.stack([gx.ravel(), gy.ravel(), gz.ravel()], 1)
        want = keep[::c, ::c].ravel()
        seen = np.zeros(len(pts), bool)
        rays = Raycaster(sight)
        for pos in panos.positions:
            v = pts - pos
            d = np.linalg.norm(v, axis=1)
            ask = want & ~seen & (d < FAR_PHOTO) & (d > 0.5)
            if not ask.any():
                continue
            t, _ = rays.cast(np.tile(pos, (int(ask.sum()), 1)), v[ask] / d[ask, None])
            seen[ask] = t >= d[ask] - 0.3
        k = max(1, int(round(FAR_OPEN)))
        open_ = ndimage.binary_opening(
            ndimage.binary_closing(seen.reshape(gx.shape), iterations=k), iterations=k
        )
        open_ = np.repeat(np.repeat(open_, c, 0), c, 1)[:n, :n]
        building = valid & ~tree & (z - terrain > FAR_BUILDING)
        building = ndimage.binary_opening(building, iterations=2)  # not a pole or a parked car's roof rack
        building = ndimage.binary_dilation(building, iterations=int(round(FAR_SKIRT / FAR_CELL)))
        keep &= ~open_ | building
    if house is not None:
        keep &= ~shapely.contains_xy(house.buffer(-FAR_UNDER), X, Y)
    # Where the surface ends against the photo disk its ground is brought to the disk's height.
    if disk is not None:
        outside = ~keep & ~shapely.contains_xy(hole, X, Y)
        k = np.clip(1 - ndimage.distance_transform_edt(~outside) * FAR_CELL / FAR_BLEND, 0, 1)
        z = z + (disk(X.ravel(), Y.ravel()).reshape(X.shape) + 0.03 - terrain) * k
    if house is not None:
        by_house = shapely.contains_xy(house.buffer(FAR_HOUSE), X, Y)
        z[by_house] = np.minimum(z[by_house], floor_z - FAR_SINK)
    # A pole or a mast is a spike a cell or two wide: after simplification a fat pyramid. Cut off.
    opened = ndimage.grey_opening(z, size=(FAR_THIN[0], FAR_THIN[0]))
    z = np.where(z - opened > FAR_THIN[1], opened, z)
    verts, faces = _grid_mesh(x0, y1, FAR_CELL, z, keep)
    full = len(faces)
    if full > FAR_FACES:
        verts, faces = fast_simplification.simplify(verts, faces, target_count=FAR_FACES)
        verts, faces = np.asarray(verts, float), np.asarray(faces, int)
    tri = verts[faces]
    cen = tri.mean(1)
    nrm = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    nrm /= np.linalg.norm(nrm, axis=1)[:, None] + 1e-12
    nrm[nrm[:, 2] < 0] *= -1

    # Gaussians of the crowns (and of our roof) are left out of the renders: the walls and ground behind
    # and under them are what the surface there is.
    g_drop = np.zeros(len(splat.xyz), bool)
    g_drop[inb] = (tree | own)[gj[inb], gi[inb]]
    g_surf = np.full(len(splat.xyz), np.inf)
    g_surf[inb] = z[gj[inb], gi[inb]]
    shown = sound & ~(g_drop & (splat.xyz[:, 2] > g_surf + 0.5))
    views = [(np.array([1.0, 0, 0]), np.array([0, -1.0, 0]), TOP_RES)]
    for k in range(8):
        az = k * math.pi / 4
        look = -np.array(
            [math.cos(az) * math.cos(SIDE_TILT), math.sin(az) * math.cos(SIDE_TILT), math.sin(SIDE_TILT)]
        )
        u = np.cross(look, [0, 0, 1.0])
        u /= np.linalg.norm(u)
        v = np.cross(look, u)
        if np.cross(u, v) @ look < 0:
            u = -u
        views.append((u, v, SIDE_RES))
    best = np.full(len(faces), -1)
    score = np.full(len(faces), -1.0)
    images, frames = [], []
    zc = float(np.median(z[keep]))
    for k, (u, v, res) in enumerate(views):
        look = np.cross(u, v)
        # The render's window: the far field's bounding box in this view.
        rel = verts - np.array([cx, cy, zc])
        a0, a1 = float((rel @ u).min()) - 0.5, float((rel @ u).max()) + 0.5
        b0, b1 = float((rel @ v).min()) - 0.5, float((rel @ v).max()) + 0.5
        origin = np.array([cx, cy, zc]) + u * a0 + v * b0 - look * 200.0
        img, alv, dp = splat.flatten(origin, u, v, a1 - a0, b1 - b0, res, look, 0.0, 1e3, keep=shown)
        seen = alv > 0.4
        near = ndimage.distance_transform_edt(~seen, return_distances=False, return_indices=True)
        img = img[tuple(near)]  # unseen: the nearest seen colour; exposed below
        H, W = alv.shape
        pu, pv, pd = (cen - origin) @ u * res, (cen - origin) @ v * res, (cen - origin) @ look
        iu, iv = np.clip(pu.astype(int), 0, W - 1), np.clip(pv.astype(int), 0, H - 1)
        vis = (alv[iv, iu] > 0.5) & (np.abs(pd - np.nan_to_num(dp[iv, iu], nan=-1e9)) < FAR_DEPTH)
        sc = np.where(vis, -(nrm @ look), -1.0)
        if k == 0:
            sc = np.maximum(sc * 0.999, 0.02)  # straight down is everyone's fallback
        take = sc > score
        best[take], score[take] = k, sc[take]
        images.append(img)
        frames.append((origin, u, v, res, W, H))
    # Every crown within `radius` gets a model, also over open ground (a tree is no flatter than a house);
    # crowns further out stay photos on the dome.
    modelled = tree & ~own & ~inside & (dist < radius)
    trees = _trees(modelled, height, ground, rgb, gain, x0, y1)
    photo_share = 0.0
    photos: dict[int, tuple[np.ndarray, np.ndarray]] = {}  # render -> (photo colours, weight 0..1)
    # Every crown the splat has, in the lot and past it, as a closed shell from its underside (the 10th
    # percentile of its Gaussians' heights, at least CROWN_UNDER above the ground) to its top, on a 0.5 m
    # grid: in the cameras' line of sight for the far field, the disk and the dome.
    # Not over our own roof (a porch with open rafters reads as a crown), and only the crowns that are models
    # in the scene (the yard's and those within `radius`): a far one must stay on the dome as a photo.
    crown = (tree & ~own & (dist < radius))[::2, ::2]
    under = np.where(np.isfinite(lo), lo, ground + CROWN_UNDER)
    under = np.maximum(ndimage.median_filter(under, size=5), ground + CROWN_UNDER)[::2, ::2]
    roof_of = np.maximum(np.where(valid, ztop, ground), ground + CROWN_UNDER + 0.5)[::2, ::2]
    solid = np.concatenate(
        [tri, _crown_shell(crown, np.minimum(under, roof_of - 0.5), roof_of, x0, y1, 2 * FAR_CELL)]
    )
    if panos is not None and len(panos):
        mesh_rays = Raycaster(tri)
        eyes = Raycaster(np.concatenate([solid] + ([sight] if sight is not None else [])))
        took = total = 0
        pairs = []
        for k, (origin, u, v, res, W, H) in enumerate(frames):
            look = np.cross(u, v)
            jj, ii = np.mgrid[0:H, 0:W]
            o = origin + ((ii.ravel() + 0.5) / res)[:, None] * u + ((jj.ravel() + 0.5) / res)[:, None] * v
            t, prim = mesh_rays.cast(o, np.tile(look, (len(o), 1)))
            mine = prim >= 0
            mine[mine] = best[prim[mine]] == k
            if not mine.any():
                continue
            pts = o[mine] + t[mine, None] * look
            normal = nrm[prim[mine]]
            col, ok = np.zeros((len(pts), 3)), np.zeros(len(pts), bool)
            level = normal[:, 2] > FAR_LEVEL
            for part, cos in ((level, FAR_COS_LEVEL), (~level, FAR_COS)):
                if part.any():
                    col[part], ok[part] = shade(
                        pts[part], normal[part], panos, eyes, snap=False, max_dist=FAR_PHOTO,
                        max_candidates=len(panos), min_cos=cos,
                    )  # fmt: skip
            if is_sky is not None:
                # More than FAR_SKY above the ground a sky-coloured photo texel is sky seen past a roof's
                # edge (the surface is placed to a cell or two), not the roof.
                gi2 = np.clip(((pts[:, 0] - x0) / FAR_CELL).astype(int), 0, n - 1)
                gj2 = np.clip(((y1 - pts[:, 1]) / FAR_CELL).astype(int), 0, n - 1)
                up = pts[:, 2] - ground[gj2, gi2] > FAR_SKY
                ok &= ~(up & is_sky(np.clip(col, 0, 255).astype(np.uint8)[None])[0])
            seen = np.zeros(H * W, bool)
            seen[mine] = ok
            seen = ndimage.binary_opening(seen.reshape(H, W), iterations=2)
            pic = np.zeros((H * W, 3))
            pic[mine] = col
            pic = pic.reshape(H, W, 3)
            photos[k] = (pic, np.clip(ndimage.distance_transform_edt(seen) / FAR_FADE, 0, 1)[..., None])
            pairs.append((pic[seen][::5], images[k][seen][::5]))
            took += int(seen.sum())
            total += int(mine.sum())
        photo_share = took / max(1, total)
        both = (
            [np.concatenate([a for a, _ in pairs]), np.concatenate([b for _, b in pairs])] if pairs else None
        )
        if both is not None and len(both[0]) >= 5000:
            gain = np.clip(np.median(both[0], 0) / np.maximum(np.median(both[1], 0), 1.0), *sp.GROUND_GAIN)
    for k in range(len(images)):
        img = _expose(images[k], gain)
        if k in photos:
            pic, w = photos[k]
            img = pic * w + img * (1 - w)
        images[k] = np.clip(img, 0, 255).astype(np.uint8)
    meshes = []

    def add(vv: np.ndarray, ff: np.ndarray, uv: np.ndarray, img: np.ndarray) -> None:
        m = trimesh.Trimesh(vv, ff, process=False)
        name = f"backdrop_far_{len(meshes)}"
        m.visual = trimesh.visual.TextureVisuals(uv=uv, material=rb._material(img, name))
        meshes.append((name, m))

    # Straight down: one picture for most of the surface.
    origin, u, v, res, W, H = frames[0]
    used, inv = np.unique(faces[best == 0], return_inverse=True)
    vv = verts[used]
    add(
        vv,
        inv.reshape(-1, 3),
        np.stack([(vv - origin) @ u * res / W, 1 - (vv - origin) @ v * res / H], 1),
        images[0],
    )
    # The obliques texture few faces (walls, fences), scattered over big renders: each face keeps only its
    # own rectangle of its render (padded by FAR_PAD px, so filtering reads real neighbours), on shared pages.
    charts, members = [], []  # (render, x0, y0, w, h), and per chart the (face index, pixel coordinates)
    for k in range(1, len(frames)):
        origin, u, v, res, W, H = frames[k]
        fk = np.nonzero(best == k)[0]
        px = np.stack([(tri[fk] - origin) @ u * res, (tri[fk] - origin) @ v * res], -1)  # (n, 3, 2)
        lo = np.clip(np.floor(px.min(1)).astype(int) - FAR_PAD, 0, [W - 1, H - 1])
        hi = np.clip(np.ceil(px.max(1)).astype(int) + FAR_PAD, 1, [W, H])
        for f, a, b, q in zip(fk, lo, hi, px, strict=True):
            charts.append((k, int(a[0]), int(a[1]), int(b[0] - a[0]), int(b[1] - a[1])))
            members.append((np.array([f]), (q - a)[None]))
    side = 2048
    order = sorted(range(len(charts)), key=lambda c: -charts[c][4])
    pages, place = [], {}
    x = y = row = 0
    for c in order:
        _, _, _, w, h = charts[c]
        w, h = min(w, side), min(h, side)
        if x + w > side:
            x, y, row = 0, y + row, 0
        if y + h > side or not pages:
            pages.append(np.zeros((side, side, 3), np.uint8))
            x = y = row = 0
        place[c] = (len(pages) - 1, x, y)
        k, cx0, cy0, _, _ = charts[c]
        pages[-1][y : y + h, x : x + w] = images[k][cy0 : cy0 + h, cx0 : cx0 + w]
        x, row = x + w, max(row, h)
    for pi, page in enumerate(pages):
        fs, uvs = [], []
        for c, (pg, x, y) in place.items():
            if pg != pi:
                continue
            fi, px = members[c]
            fs.append(fi)
            uvs.append(np.stack([(px[..., 0] + x) / side, 1 - (px[..., 1] + y) / side], -1))
        if not fs:
            continue
        fi, uv = np.concatenate(fs), np.concatenate(uvs)
        rows = int(np.ceil((1 - uv[..., 1].min()) * side / 256) * 256)  # only the rows that are used
        uv[..., 1] = 1 - (1 - uv[..., 1]) * side / rows
        add(tri[fi].reshape(-1, 3), np.arange(3 * len(fi)).reshape(-1, 3), uv.reshape(-1, 2), page[:rows])
    textures = sum(
        m.visual.material.baseColorTexture.size[0] * m.visual.material.baseColorTexture.size[1]
        for _, m in meshes
    )

    # The skyline for the sky's line of sight: the raw top surface, crowns included, on a 0.5 m grid.
    raw = np.where(valid, ztop, ground)[::2, ::2]
    away = np.hypot(X - cx, Y - cy)[::2, ::2]
    sv, sf = _grid_mesh(x0, y1, 2 * FAR_CELL, raw, away < reach)
    hv, hf = _grid_mesh(x0, y1, 2 * FAR_CELL, raw, (away < reach) & (away > radius - 1.0))
    report = {
        "radius_m": radius,
        "triangles": int(len(faces)),
        "triangles_before_simplifying": int(full),
        "faces_per_render": np.bincount(best[best >= 0], minlength=len(views)).tolist(),
        "texture_megapixels": round(textures / 1e6, 2),
        "from_photos": round(photo_share, 3),
        "splat_gain": [round(float(k), 2) for k in np.broadcast_to(gain, 3)],
        "tree_m2": round(float(modelled.sum()) * FAR_CELL**2, 1),
        "trees": len(trees),
    }
    cj, ci = np.nonzero(keep[::4, ::4] & keep[1::4, 1::4] & keep[2::4, 2::4])
    side = 4 * FAR_CELL
    cells = shapely.box(x0 + ci * side, y1 - (cj + 1) * side, x0 + (ci + 1) * side, y1 - cj * side)
    covered = unary_union(cells).buffer(-FAR_LAP) if len(cells) else Polygon()
    report["covered_m2"] = round(float(keep.sum()) * FAR_CELL**2, 1)
    return FarField(meshes, trees, sv[sf], solid, hv[hf], covered, (cx, cy), radius, report)


def _trees(
    mask: np.ndarray, height: np.ndarray, ground: np.ndarray, rgb: np.ndarray, gain: np.ndarray, x0, y1
) -> list[vg.Tree]:
    """One tree per patch of crown cells (a patch over TREE_SPLIT m2 is cut into several by k-means): where
    it stands, how high and wide its crown is and its leaf colour, all as the splat has them. No trunk is
    measured (the model's own is used)."""
    out = []
    lab, count = ndimage.label(mask)
    for i in range(1, count + 1):
        jj, ii = np.nonzero(lab == i)
        area = len(jj) * FAR_CELL**2
        if area < TREE_MIN_AREA:
            continue
        xy = np.stack([x0 + (ii + 0.5) * FAR_CELL, y1 - (jj + 0.5) * FAR_CELL], 1)
        parts = max(1, int(round(area / TREE_SPLIT)))
        group = np.zeros(len(xy), int)
        if parts > 1:
            _, group = kmeans2(xy, parts, minit="++", seed=int(xy[0, 0] * 20) % 10007)
        for k in range(parts):
            sel = group == k
            if sel.sum() * FAR_CELL**2 < TREE_MIN_AREA:
                continue
            c, r1, r2, angle = vg.crown_axes(xy[sel])
            h = height[jj[sel], ii[sel]]
            top = float(np.percentile(h, 97))
            if top < TREE_MIN_TOP - 0.5:
                continue
            colour = vg.leaf_colour(np.clip(rgb[jj[sel], ii[sel]] * gain, 0, 255))
            out.append(
                vg.Tree(
                    x=float(c[0]),
                    y=float(c[1]),
                    z=float(np.median(ground[jj[sel], ii[sel]])),
                    top=top,
                    bottom=float(np.clip(0.4 * top, 2.2, 5.0)),
                    r_major=r1,
                    r_minor=r2,
                    angle=angle,
                    lean=(0.0, 0.0),
                    colour=colour,
                    trunk_r=None,
                    in_lot=False,
                    note="from the drone splat, outside the lot",
                    far=True,
                )
            )
    return out


def wall_panels(splat: sp.Splat, bounds: Polygon, base_z: float, gain: np.ndarray) -> list[rb.Plane]:
    """An experiment (2026-11-15, the user's idea): instead of geometry far off, upright walls just outside
    the explorable area with a picture of what lies beyond, like a painted backdrop on a stage.

    Four walls: the smallest rectangle around `bounds` (the lot with the house), PANEL_OFFSET further out
    (a wall per side of the lot's own outline, 18 on 7014, cut the picture wherever the outline turns: a
    block of tree hanging in the air beside a roof that belongs to the next wall). One
    wall per side of at least PANEL_MIN, from PANEL_FOOT under `base_z` to PANEL_TOP above it. Its picture is
    the splat seen from the wall looking outward, orthographically, PANEL_DEPTH deep (the nearest thing
    wins), at PANEL_RES; where the splat has nothing (alpha under 0.5, specks opened away) the wall is cut
    out and the sky dome shows. There is no parallax by construction: the picture does not move as the
    player walks, and what is near and what is far are one flat image."""
    ring = orient(bounds.minimum_rotated_rectangle.buffer(PANEL_OFFSET, join_style="mitre"), 1.0)
    pts = np.asarray(ring.exterior.coords)
    sound = splat.xyz[:, 2] > base_z - 3.0
    out = []
    for a, b in zip(pts[:-1], pts[1:], strict=True):
        length = float(np.linalg.norm(b - a))
        if length < PANEL_MIN:
            continue
        along = (b - a) / length
        outward = np.array([along[1], -along[0]])  # the ring runs counter-clockwise: outside is to the right
        u = np.array([*-along, 0.0])  # left to right for someone inside looking out
        v = np.array([0.0, 0.0, 1.0])
        origin = np.array([*b, base_z - PANEL_FOOT])
        h = PANEL_TOP + PANEL_FOOT
        look = np.array([*outward, 0.0])
        img, al, _ = splat.flatten(
            origin + v * h, u, -v, length, h, PANEL_RES, look, 0.0, PANEL_DEPTH, keep=sound
        )
        solid = ndimage.binary_opening(al > 0.5, iterations=2)
        if solid.mean() < 0.02:
            continue
        near = ndimage.distance_transform_edt(~solid, return_distances=False, return_indices=True)
        rgb = _expose(img[tuple(near)], gain)  # colour under the cut-out's edge: the nearest solid texel
        p = rb.Plane("wall", origin, u, v, -look, length, h, box(0, 0, length, h))
        p.image = np.dstack([rgb, np.where(solid, 255, 0)]).astype(np.uint8)
        out.append(p)
    return out


def clear(skyline: Raycaster, eye: np.ndarray, dirs: np.ndarray) -> np.ndarray:
    """Which directions (n, 3, unit) from `eye` are open sky: SKY_MARGIN degrees lower nothing of `skyline`
    is in the way. The caller passes `FarField.horizon`, what stands past the far field: everything nearer
    is geometry or a tree model in the scene and must not be on the dome as well. The dome there shows the
    sky panorama and never a photo (no leaves of a tree that became a model, no slab of another camera's
    exposure)."""
    el = np.arcsin(np.clip(dirs[:, 2], -1, 1)) - math.radians(SKY_MARGIN)
    flat = dirs[:, :2] / np.maximum(np.linalg.norm(dirs[:, :2], axis=1), 1e-9)[:, None]
    lower = np.c_[flat * np.cos(el)[:, None], np.sin(el)]
    _, prim = skyline.cast(np.tile(eye, (len(dirs), 1)), lower)
    return prim < 0


def sky(panos: PanoSet, eyes: Raycaster, is_sky: Callable[[np.ndarray], np.ndarray]) -> tuple[Callable, dict]:
    """The sky by direction, from the outdoor photos. Returns (`colour(dirs (n, 3)) -> rgb (n, 3)`, report).

    The sky is far away, so every camera sees the same sky in the same direction (clouds move a little
    during the scan). Per camera: the directions above SKY_MIN_EL where its ray meets nothing (`eyes`: the
    scan, the fitted roof and the far field's skyline) and the photo has a sky colour (`is_sky`), shrunk by
    two texels (leaf fringes). The camera with the most sky is laid down first; each next one fills what is
    still open, scaled per channel to the sky already there where they overlap, faded in over SKY_FEATHER.
    Directions no camera has (behind a crown, under SKY_MIN_EL) continue the sky around them smoothly."""
    W, H = int(360 * SKY_RES), int(90 * SKY_RES)
    az = np.radians((np.arange(W) + 0.5) / SKY_RES)
    el = np.radians(90 - (np.arange(H) + 0.5) / SKY_RES)  # row 0 = straight up
    A, E = np.meshgrid(az, el)
    dirs = np.stack([np.cos(E) * np.cos(A), np.cos(E) * np.sin(A), np.sin(E)], -1).reshape(-1, 3)
    high = (E > math.radians(SKY_MIN_EL)).ravel()
    layers = []
    for i in range(len(panos)):
        pos = panos.positions[i]
        _, prim = eyes.cast(np.tile(pos, (int(high.sum()), 1)), dirs[high])
        open_ = np.zeros(W * H, bool)
        open_[high] = prim < 0
        col, _ = panos.sample(i, pos + dirs * 10.0)
        ok = (open_ & is_sky(np.clip(col, 0, 255).astype(np.uint8)[None])[0]).reshape(H, W)
        ok = ndimage.binary_erosion(ok, iterations=2)
        layers.append((int(ok.sum()), i, col.reshape(H, W, 3), ok))
    layers.sort(key=lambda t: -t[0])
    acc = np.zeros((H, W, 3))
    wsum = np.zeros((H, W))
    used = []
    for count, i, col, ok in layers:
        if count < 500:
            continue
        both = ok & (wsum > 0.99)
        k = np.ones(3)
        if both.sum() > 2000:
            k = np.clip(np.median(acc[both], 0) / np.maximum(np.median(col[both], 0), 1.0), *SKY_GAIN)
        elif used:
            continue  # nothing to match it to: another exposure next to the first would show as a patch
        w = np.clip(ndimage.distance_transform_edt(ok) / (SKY_FEATHER * SKY_RES), 0, 1) * (1 - wsum)
        acc += col * k * w[..., None]
        wsum += w
        used.append({"camera": i, "sky_share": round(count / max(1, int(high.sum())), 3)})
    got = wsum > 0.5
    img = acc / np.maximum(wsum, 1e-6)[..., None]
    if got.any():
        # What no camera has: a wide blend of the sky around it (one scale, so no contour shows where a
        # finer blend ran out), taking over from a fine blend over the first SKY_FEATHER degrees.
        m = got.astype(float)
        fills = []
        for sigma in (SKY_FEATHER * SKY_RES, 12 * SKY_FEATHER * SKY_RES):
            ws = ndimage.gaussian_filter(m, sigma, mode=("nearest", "wrap"))
            cs = np.stack(
                [ndimage.gaussian_filter(img[..., c] * m, sigma, mode=("nearest", "wrap")) for c in range(3)],
                -1,
            )
            fills.append((cs / np.maximum(ws, 1e-6)[..., None], ws))
        mean = img[got].mean(0)
        wide = np.where(fills[1][1][..., None] > 0.02, fills[1][0], mean)
        near = np.clip(fills[0][1] / 0.3, 0, 1)[..., None]
        smooth = fills[0][0] * near + wide * (1 - near)
        f = np.clip(wsum, 0, 1)[..., None]
        img = img * f + smooth * (1 - f)
    else:
        img[:] = (150, 185, 225)
    img = np.clip(img, 0, 255)

    def colour(d: np.ndarray) -> np.ndarray:
        d = d / np.linalg.norm(d, axis=1)[:, None]
        a = np.degrees(np.arctan2(d[:, 1], d[:, 0])) % 360.0
        e = np.clip(np.degrees(np.arcsin(np.clip(d[:, 2], -1, 1))), 0.0, 89.99)
        return img[np.clip(((90 - e) * SKY_RES).astype(int), 0, H - 1), (a * SKY_RES).astype(int) % W]

    return colour, {"cameras": used, "covered": round(float(got[high.reshape(H, W)].mean()), 3)}
