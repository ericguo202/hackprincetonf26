"""Trees and shrubs of the scanned lot as models that fill the space the scan measured.

A scanned tree is a set of torn, crumpled sheets: the depth camera sees leaves as noise, and the photos show
sky between them. So the scan is used only to measure each tree (where its trunk stands, how wide and tall its
crown is, where the crown starts, what colour the leaves are), and a model is placed to those measurements.

Steps (`measure`, then `replace_vegetation`):

1. Foliage (`planarity`): per scan face, how much the face normals around it agree. On a roof, a fence or a
   wall they share one axis; in foliage they point every way.
2. Crowns (`canopy_grid`): the foliage more than 2 m above the ground, seen from above.
3. Trunks (`find_trunks`): compact, tall, round columns of scan under a crown.
4. Trees (`segment_crowns`): each patch of crown is shared out between the trunks under it. A crown with no
   trunk in the scan is a neighbour's tree hanging over the fence: its model stands outside the lot.
5. Models (`place_tree`): a library tree (procedural live oak: trunk, limbs, leaf cards) stretched so its
   trunk reaches the measured crown bottom, its crown fills the measured crown, and it leans the way the
   scanned crown does; leaves tinted to the photographed colour.
6. Shrubs (`find_shrubs`, `bush_cards`): leafy, plant-coloured scan blobs standing on the ground. One the
   cameras saw well stays as scanned (with the photo on it, it looks better than a model); one they barely
   saw becomes a leaf-card bush of the same size.

Everything in the house file is rendered unlit, so the leaf texture holds four brightness levels of the leaf
atlas per tint, and each card picks a level from where it sits in its crown (top and outside: light; low and
inside: dark). Nodes: `plants_0` (leaves, alpha MASK), `plants_1` (bark), `plants_2` (bark in the crown's
shade).

Called by exterior.add_exterior after the outdoor scan faces are chosen. All geometry is Matterport Z-up plan
coordinates; nodes added to the scene are Y-up (house_mesh.Z_UP_TO_Y_UP)."""

from __future__ import annotations

import json
import math
import zlib
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import shapely
import trimesh
from PIL import Image
from scipy import ndimage
from shapely.geometry import Point, Polygon
from shapely.ops import nearest_points, unary_union

from worldgen import house_mesh as hm
from worldgen import rebake as rb
from worldgen.panos import PanoSet, shade

# The model library: live_oak_*.npz, leaf_rgba.png, bark_diff.jpg, LICENSE.json (all CC0; made by
# scripts/block_trees on lane-a/block-combined). Missing folder: nothing is replaced.
LIBRARY = Path(__file__).resolve().parent / "out" / "vegetation" / "v1"

CANOPY_MIN = 2.0  # m above the ground: foliage above this is a crown (exterior.TREE_MIN_Z)
VOXEL = 0.5  # m: cell of the normal-agreement test (summed over 3 x 3 x 3 cells, 1.5 m)
LEAFY_MAX = 0.62  # planarity under this is foliage (a plane is 1.0, normals pointing every way 0.33)
CROWN_FLAT = 0.85  # inside a crown, scan up to this planarity goes too (limbs, flatter sheets of leaves)
# m around the facade outline where anything above CANOPY_MIN is roof, eave or gutter. Not less: at 0.7 m
# the eaves (crumpled corners of soffit, fascia and gutter) joined a neighbour's crown into a ring around
# the house.
HOUSE_CLEAR = 1.0
CROWN_CELL = 0.25  # m: plan grid of the crowns
CROWN_MIN_AREA = 4.0  # m2 in plan: a smaller patch of foliage gets no model
TRUNK_CELL = 0.1  # m: plan grid of the trunk search
TRUNK_Z = (0.3, 2.05)  # m above the ground: a trunk fills at least TRUNK_BINS of the 0.25 m bins in here
TRUNK_BINS = 5
TRUNK_MAX_SIZE = 1.6  # m: a column wider than this in plan is a fence or a wall
TRUNK_MAX_FLAT = 0.8  # share of the horizontal normals along one axis: above it the column is a flat panel
TRUNK_SPAN = 45.0  # a trunk carries foliage up to this many trunk radii away (3 m at least, 14 m at most)
TRUNK_SLENDER = 90.0  # tree height over trunk radius: above it the column is a pole, not this crown's trunk
POST_WHITE = (140, 45)  # a column whose colour has min channel over 140 and spread under 45 is painted
POST_WALL = 0.3  # m: a column this close to the house wall is a downspout or a pier
SLAB_THIN = 0.5  # m: canopy cells thinner than this ...
SLAB_FLAT = 0.5  # ... whose top varies less than this within 1.25 m ...
SLAB_MIN_AREA = 3.0  # ... in patches of at least this many m2 are a structure (a pergola, an awning, cables)
SMALL_TREE = (2.5, 8.0)  # a trunkless crown starting below 2.5 m and ending below 8 m is a tree in the lot
OUTSIDE = 1.5  # m beyond the lot's edge: where a neighbour's tree (crown without a trunk) is stood
SHRUB_VOXEL = 0.25  # m: cell for grouping low foliage into shrubs
SHRUB_TOP = 1.6  # m above the ground: shrubs are looked for below this (above: a tree's own low branches)
SHRUB_SIZE = (0.6, 5.0)  # m: a shrub's footprint is between these across
SHRUB_MIN_H = 0.5  # m
SHRUB_MIN_AREA = 0.6  # m2 of plan it covers (less: a trunk's foot, a weed)
SHRUB_MIN_FACES = 150
SHRUB_LEAFY = 0.6  # share of the scan surface inside the shrub's box that is foliage
# A shrub the cameras saw at least this much of stays as scanned: with the photo on it, it looks better than a
# ball of leaf cards (6915's rose bush by the back wall, seen 0.73; 7014's sago palm, 0.50). A model replaces
# only what the photos barely show (7014's ground cover by the side gate, 0.11).
SHRUB_SEEN_MAX = 0.35
NEIGHBOUR_MIN_AREA = 8.0  # m2: a trunkless crown smaller than this gets no model outside the lot
FAR_CARDS = 3  # a tree past the lot (Tree.far) keeps every n-th leaf card
HOUSE_TOP = 6.0  # m above the lowest ground: below it no leaf or limb may reach into the house outline
# Unlit shading baked in the texture: brightness of the four leaf levels, and a slight colour shift (shade is
# bluer, sunlit leaves yellower).
LEVELS = (0.42, 0.66, 0.92, 1.2)
LEVEL_TINT = ((0.94, 1.0, 1.06), (0.98, 1.0, 1.02), (1.02, 1.0, 0.96), (1.07, 1.03, 0.86))
SUN = np.array([-0.3, -0.64, 0.7])  # toward the sun, plan x y z (south-south-west, 45 degrees up): a guess
SLOT = 512  # px: one tint at one level (the 2 x 2 leaf atlas at 256 px per cluster)
MAX_TINTS = 4  # rows of the leaf page (4 x 512 = 2048 px)
LEAF_GREEN = np.array([92.0, 116.0, 66.0])  # default leaf colour; measured colours are pulled 40% toward it
BARK_SHADE = 0.8  # bark inside the crown is this much darker (0.62 showed as a ring around the limbs)


# ---------------------------------------------------------------- measuring the scan


def planarity(centres: np.ndarray, normals: np.ndarray, areas: np.ndarray, cell: float = VOXEL) -> np.ndarray:
    """Per face: how much the face normals around it share one axis (1 = a plane; 0.33 = every direction).
    The area-weighted orientation tensor sum(a n n^T) is accumulated per `cell`, summed over the 3 x 3 x 3
    cells around each, and the largest eigenvalue is divided by the trace. Two-sided sheets count as one
    plane (n and -n give the same tensor)."""
    if not len(centres):
        return np.zeros(0)
    ijk = np.floor((centres - centres.min(0)) / cell).astype(int) + 1
    shape = tuple(ijk.max(0) + 2)
    comps = ((0, 0), (1, 1), (2, 2), (0, 1), (0, 2), (1, 2))
    m = np.empty((len(centres), 3, 3))
    for p, q in comps:
        grid = np.zeros(shape)
        np.add.at(grid, tuple(ijk.T), areas * normals[:, p] * normals[:, q])
        grid = ndimage.uniform_filter(grid, size=3, mode="constant")
        m[:, p, q] = m[:, q, p] = grid[ijk[:, 0], ijk[:, 1], ijk[:, 2]]
    w = np.linalg.eigvalsh(m)
    return w[:, 2] / np.maximum(w.sum(1), 1e-12)


@dataclass
class Trunk:
    x: float
    y: float
    radius: float  # m, about 1 m above the ground


def find_trunks(
    centres: np.ndarray, normals: np.ndarray, hag: np.ndarray, covered=None
) -> tuple[list[Trunk], list[np.ndarray]]:
    """Tree trunks among scan faces (`hag` = height above the ground): plan cells (TRUNK_CELL) where steep
    faces fill at least TRUNK_BINS of the 0.25 m height bins in TRUNK_Z (counted over the 3 x 3 cells around,
    because a thin trunk wanders across cells on its way up), grown by one cell and grouped. A group is a
    trunk when it is at most TRUNK_MAX_SIZE across (longer: a fence) and its horizontal normals do not share
    one axis (TRUNK_MAX_FLAT: a panel, a gate). `covered(x, y) -> bool` says whether foliage hangs over a
    point; a column with none above it is a post. Returns the trunks and, per trunk, the indices (into the
    given arrays) of its faces."""
    lo, hi = TRUNK_Z
    sel = np.nonzero((hag > lo) & (hag < hi) & (np.abs(normals[:, 2]) < 0.7))[0]
    if not len(sel):
        return [], []
    c = centres[sel]
    x0, y0 = c[:, 0].min() - TRUNK_CELL, c[:, 1].min() - TRUNK_CELL
    i = ((c[:, 0] - x0) / TRUNK_CELL).astype(int) + 1
    j = ((c[:, 1] - y0) / TRUNK_CELL).astype(int) + 1
    nb = int(round((hi - lo) / 0.25))
    b = np.clip(((hag[sel] - lo) / 0.25).astype(int), 0, nb - 1)
    fill = np.zeros((i.max() + 3, j.max() + 3, nb), bool)
    fill[i, j, b] = True
    near = ndimage.maximum_filter(fill, size=(3, 3, 1))
    column = ndimage.binary_dilation(near.sum(2) >= TRUNK_BINS)
    lab, n = ndimage.label(column, structure=np.ones((3, 3)))
    trunks, members = [], []
    for k in range(1, n + 1):
        mine = lab[i, j] == k
        if mine.sum() < 12:
            continue
        p, nm, h = c[mine], normals[sel][mine], hag[sel][mine]
        size = p[:, :2].max(0) - p[:, :2].min(0)
        if size.max() > TRUNK_MAX_SIZE:
            continue
        hn = nm[:, :2]
        w = np.linalg.eigvalsh(hn.T @ hn)
        if w[1] / max(w.sum(), 1e-12) > TRUNK_MAX_FLAT:
            continue
        low = p[h < 1.0] if (h < 1.0).sum() >= 4 else p
        cx, cy = np.median(low[:, 0]), np.median(low[:, 1])
        if covered is not None and not covered(cx, cy):
            continue
        s = (h > 0.6) & (h < 1.6)
        mid = p[s] if s.sum() >= 4 else p
        r = float(np.median(np.hypot(mid[:, 0] - np.median(mid[:, 0]), mid[:, 1] - np.median(mid[:, 1]))))
        trunks.append(Trunk(float(cx), float(cy), float(np.clip(r, 0.04, 0.6))))
        members.append(sel[mine])
    return trunks, members


@dataclass
class CanopyGrid:
    x0: float
    y0: float
    top: np.ndarray  # (nx, ny) highest foliage above the ground per cell (NaN = none)
    bottom: np.ndarray  # lowest foliage per cell

    @property
    def mask(self) -> np.ndarray:
        return np.isfinite(self.top)

    def xy(self, i: np.ndarray, j: np.ndarray) -> np.ndarray:
        return np.stack([self.x0 + (i + 0.5) * CROWN_CELL, self.y0 + (j + 0.5) * CROWN_CELL], 1)

    def cell(self, x, y) -> tuple[np.ndarray, np.ndarray]:
        i = np.clip(((np.asarray(x) - self.x0) / CROWN_CELL).astype(int), 0, self.top.shape[0] - 1)
        j = np.clip(((np.asarray(y) - self.y0) / CROWN_CELL).astype(int), 0, self.top.shape[1] - 1)
        return i, j


def canopy_grid(centres: np.ndarray, hag: np.ndarray) -> CanopyGrid:
    """Foliage faces (centres, heights above the ground) from above on a CROWN_CELL grid: highest and lowest
    foliage per cell. Gaps up to two cells are closed, with heights from the nearest filled cell."""
    x0, y0 = centres[:, 0].min() - 4 * CROWN_CELL, centres[:, 1].min() - 4 * CROWN_CELL
    i = ((centres[:, 0] - x0) / CROWN_CELL).astype(int)
    j = ((centres[:, 1] - y0) / CROWN_CELL).astype(int)
    shape = (i.max() + 5, j.max() + 5)
    top = np.full(shape, -np.inf)
    bottom = np.full(shape, np.inf)
    np.maximum.at(top, (i, j), hag)
    np.minimum.at(bottom, (i, j), hag)
    have = np.isfinite(top)
    closed = ndimage.binary_closing(have, iterations=2)
    idx = ndimage.distance_transform_edt(~have, return_distances=False, return_indices=True)
    top, bottom = top[tuple(idx)], bottom[tuple(idx)]
    top[~closed] = np.nan
    bottom[~closed] = np.nan
    return CanopyGrid(float(x0), float(y0), top, bottom)


def slab_cells(grid: CanopyGrid) -> np.ndarray:
    """Cells of the canopy grid that are a thin, level sheet rather than foliage: thinner than SLAB_THIN, the
    top within SLAB_FLAT over the 5 x 5 cells around (1.25 m), in connected patches of SLAB_MIN_AREA or more.
    A pergola's lattice, an awning and a bundle of cables scan as crumpled as leaves do, but a crown is
    neither thin nor level over three square metres."""
    mask = grid.mask
    thin = mask & ((grid.top - grid.bottom) < SLAB_THIN)
    hi = ndimage.maximum_filter(np.where(mask, grid.top, -np.inf), size=5)
    lo = ndimage.minimum_filter(np.where(mask, grid.top, np.inf), size=5)
    slab = thin & ((hi - lo) < SLAB_FLAT)
    lab, n = ndimage.label(slab, structure=np.ones((3, 3)))
    if not n:
        return slab
    sizes = np.asarray(ndimage.sum(slab, lab, range(1, n + 1))) * CROWN_CELL**2
    return np.isin(lab, 1 + np.nonzero(sizes >= SLAB_MIN_AREA)[0])


def trunk_reach(t: Trunk) -> float:
    """How far from its trunk a tree's foliage is looked for."""
    return float(np.clip(TRUNK_SPAN * t.radius, 3.0, 14.0))


@dataclass
class Crown:
    cells: np.ndarray  # (n, 2) plan positions of its canopy cells
    top: float  # m above the ground
    bottom: float
    trunk: Trunk | None

    @property
    def area(self) -> float:
        return len(self.cells) * CROWN_CELL * CROWN_CELL


def segment_crowns(grid: CanopyGrid, trunks: list[Trunk], mask: np.ndarray | None = None) -> list[Crown]:
    """Crowns from the canopy grid (`mask`: which cells count, default all). Each connected patch of foliage
    is shared out between the trunks under it (within 1.5 m of it), every cell to the nearest trunk whose
    reach (trunk_reach) covers it. Cells no trunk claims form trunkless crowns, one per connected piece.
    Pieces under CROWN_MIN_AREA are dropped (a trunk's crown: under 1 m2)."""
    mask = grid.mask if mask is None else mask
    lab, n = ndimage.label(mask, structure=np.ones((3, 3)))
    out: list[Crown] = []
    for k in range(1, n + 1):
        ii, jj = np.nonzero(lab == k)
        xy = grid.xy(ii, jj)
        tops, bots = grid.top[ii, jj], grid.bottom[ii, jj]
        owner = np.full(len(xy), -1)
        mine = [t for t in trunks if np.hypot(xy[:, 0] - t.x, xy[:, 1] - t.y).min() < 1.5]
        if mine:
            d = np.stack([np.hypot(xy[:, 0] - t.x, xy[:, 1] - t.y) for t in mine], 1)
            d[d > np.array([trunk_reach(t) for t in mine])] = np.inf
            owner = np.where(np.isfinite(d.min(1)), d.argmin(1), -1)
        for t_i, t in enumerate(mine):
            s = owner == t_i
            if s.sum() * CROWN_CELL**2 >= 1.0:
                out.append(_crown(xy[s], tops[s], bots[s], t))
            else:
                owner[s] = -1
        rest = owner == -1
        if rest.any():
            sub = np.zeros(grid.top.shape, bool)
            sub[ii[rest], jj[rest]] = True
            lab2, n2 = ndimage.label(sub, structure=np.ones((3, 3)))
            for q in range(1, n2 + 1):
                s = rest & (lab2[ii, jj] == q)
                if s.sum() * CROWN_CELL**2 >= CROWN_MIN_AREA:
                    out.append(_crown(xy[s], tops[s], bots[s], None))
    return out


def _crown(xy: np.ndarray, tops: np.ndarray, bots: np.ndarray, trunk: Trunk | None) -> Crown:
    return Crown(xy, float(np.percentile(tops, 98)) + 0.3, float(np.percentile(bots, 10)), trunk)


# ---------------------------------------------------------------- the model library


@dataclass
class Variant:
    name: str
    bark_pos: np.ndarray  # (n, 3) model frame: x, y up, z; height 1, base centre at the origin
    bark_uv: np.ndarray
    bark_idx: np.ndarray
    leaf_pos: np.ndarray  # (cards, 4, 3)
    leaf_uv: np.ndarray  # (cards, 4, 2) in the 2 x 2 atlas, v down
    crown_base: float  # height where the leaves start
    crown_radius: float
    trunk_radius: float
    axis: np.ndarray  # (2, 2): trunk axis (x, z) = axis[0] + axis[1] * y

    @property
    def shape(self) -> float:
        """Crown radius over crown height."""
        return self.crown_radius / (1.0 - self.crown_base)


@dataclass
class Library:
    variants: list[Variant]
    atlas: np.ndarray  # (SLOT, SLOT, 4) the 2 x 2 leaf-cluster atlas
    bark: np.ndarray  # RGB, tiles
    licence: str


def load_library(path: Path | None = None) -> Library | None:
    """The tree models and textures, or None when the folder is not there."""
    path = LIBRARY if path is None else path
    files = sorted(path.glob("live_oak_*.npz")) if path.is_dir() else []
    if not files or not (path / "leaf_rgba.png").exists() or not (path / "bark_diff.jpg").exists():
        return None
    variants = []
    for f in files:
        d = np.load(f)
        bark = d["bark_pos"].astype(float)
        cb = float(d["crown_base"])
        low = bark[bark[:, 1] < 0.8 * cb]
        a = np.c_[np.ones(len(low)), low[:, 1]]
        axis = np.linalg.lstsq(a, low[:, [0, 2]], rcond=None)[0]  # rows: offset, slope
        mid = low[(low[:, 1] > 0.25 * cb) & (low[:, 1] < 0.7 * cb)]
        off = mid[:, [0, 2]] - (axis[0] + np.outer(mid[:, 1], axis[1]))
        variants.append(
            Variant(
                name=f.stem,
                bark_pos=bark,
                bark_uv=d["bark_uv"].astype(float),
                bark_idx=d["bark_idx"].astype(int),
                leaf_pos=d["leaf_pos"].astype(float).reshape(-1, 4, 3),
                leaf_uv=d["leaf_uv"].astype(float).reshape(-1, 4, 2),
                crown_base=cb,
                crown_radius=float(d["crown_radius"]),
                trunk_radius=float(np.median(np.hypot(off[:, 0], off[:, 1]))),
                axis=axis,
            )
        )
    atlas = np.array(Image.open(path / "leaf_rgba.png").convert("RGBA").resize((SLOT, SLOT), Image.LANCZOS))
    # Leaves one texel fatter: an alpha-tested texture thins out in the distance (mipmaps average the alpha
    # below the cutoff), and the clusters are only 30% leaf.
    atlas[..., 3] = ndimage.maximum_filter(atlas[..., 3], size=3)
    lic = path / "LICENSE.json"
    return Library(
        variants,
        atlas,
        np.asarray(Image.open(path / "bark_diff.jpg").convert("RGB")),
        json.loads(lic.read_text()).get("summary", "") if lic.exists() else "",
    )


# ---------------------------------------------------------------- placing a tree


@dataclass
class Tree:
    x: float  # trunk base, plan
    y: float
    z: float  # ground height there
    top: float  # m above the ground
    bottom: float  # where the leaves start
    r_major: float  # crown radii along and across `angle`
    r_minor: float
    angle: float  # radians, direction of the major axis
    lean: tuple[float, float]  # how far the crown's middle stands from the trunk base (x, y)
    colour: np.ndarray  # leaf colour (RGB 0-255)
    trunk_r: float | None  # measured trunk radius
    in_lot: bool  # stands in the yard (gets a collision prism)
    variant: int = 0
    tint: int = 0
    note: str = ""
    far: bool = False  # stands past the lot (farfield.py): fewer, larger leaf cards and only the trunk's bark


def crown_axes(cells: np.ndarray, centre: np.ndarray | None = None) -> tuple[np.ndarray, float, float, float]:
    """(centre, major radius, minor radius, angle of the major axis) of a crown's plan cells: principal axes
    of the cells about `centre` (default: their mean), radius = 95th percentile of the distance from the
    centre along each, times 1.08 (a filled disc's 95th percentile is 0.93 of its radius). The minor radius
    is at least half the major."""
    centre = cells.mean(0) if centre is None else np.asarray(centre, float)
    if len(cells) < 3:
        return centre, 1.0, 1.0, 0.0
    d0 = cells - cells.mean(0)
    _, v = np.linalg.eigh(d0.T @ d0 / len(d0))
    major, minor = v[:, 1], v[:, 0]
    d = cells - centre
    r1 = max(float(np.percentile(np.abs(d @ major), 95)) * 1.08, 0.6)
    r2 = float(np.percentile(np.abs(d @ minor), 95)) * 1.08
    return centre, r1, max(r2, 0.5 * r1), float(math.atan2(major[1], major[0]))


def pick_variant(variants: list[Variant], shape: float) -> int:
    """The model whose crown is nearest in shape (radius over height)."""
    return int(np.argmin([abs(math.log(max(shape, 0.05) / v.shape)) for v in variants]))


def _seed(*vals: float) -> int:
    """A repeatable seed from positions (rounded to 5 cm), so a rebuild gives the same plant."""
    return zlib.crc32(",".join(f"{round(v * 20)}" for v in vals).encode())


def card_light(
    mid: np.ndarray, centre: np.ndarray, radii: np.ndarray, rel: np.ndarray, seed: int
) -> np.ndarray:
    """How bright each leaf card is (0-1), for card centres `mid` in a crown (or bush) with the given centre
    and radii (plan x, y, z): 0.12 + 0.30 x height in the crown (`rel`, 0-1) + 0.22 x distance from the
    middle + 0.30 x how much the card's side of the crown faces SUN, with a little noise (sd 0.07)."""
    d = (mid - centre) / radii
    depth = np.clip(np.linalg.norm(d, axis=1), 0, 1)
    n = d / np.maximum(np.linalg.norm(d, axis=1), 1e-9)[:, None]
    facing = np.clip(0.5 + 0.5 * (n @ (SUN / np.linalg.norm(SUN))), 0, 1)
    noise = np.random.default_rng(seed).normal(0, 0.07, len(mid))
    return np.clip(0.12 + 0.30 * rel + 0.22 * depth + 0.30 * facing + noise, 0, 0.999)


def place_tree(v: Variant, t: Tree) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The model stretched to the measured tree, in plan coordinates (Z up). Vertically the trunk part
    (model height 0 to crown_base) maps onto ground to `bottom`, the crown part onto `bottom` to `top`.
    Horizontally the crown is scaled to r_major and r_minor along `angle` and sheared so that its middle
    stands `lean` away from the trunk base; the trunk is made as thick as the measured one (0.45 to 1.6
    times the model's). Returns (bark positions (n, 3), leaf card corners (cards, 4, 3), per-card light
    0-1 from card_light)."""
    cb = v.crown_base
    sx, sy = t.r_major / v.crown_radius, t.r_minor / v.crown_radius
    ca, sa = math.cos(t.angle), math.sin(t.angle)
    y_mid = 0.5 * (cb + 1.0)

    def world(p):
        # model x -> along the major axis, model z -> across it (plan y = -model z keeps it right-handed)
        a, b = p[:, 0] * sx, -p[:, 2] * sy
        k = np.clip(p[:, 1] / y_mid, 0.0, 1.3)
        h = np.where(
            p[:, 1] < cb, p[:, 1] / cb * t.bottom, t.bottom + (p[:, 1] - cb) / (1 - cb) * (t.top - t.bottom)
        )
        return np.stack(
            [t.x + a * ca - b * sa + k * t.lean[0], t.y + a * sa + b * ca + k * t.lean[1], t.z + h], 1
        )

    bark = v.bark_pos.copy()
    if t.trunk_r is not None and v.trunk_radius > 0:
        k = float(np.clip(t.trunk_r / (v.trunk_radius * 0.5 * (sx + sy)), 0.45, 1.6))
        axis = v.axis[0] + np.outer(bark[:, 1], v.axis[1])
        w = k + (1 - k) * np.clip((bark[:, 1] - 0.85 * cb) / (0.3 * cb), 0, 1)  # back to 1 in the crown
        bark[:, [0, 2]] = axis + (bark[:, [0, 2]] - axis) * w[:, None]
    cards = world(v.leaf_pos.reshape(-1, 3)).reshape(-1, 4, 3)
    mid = cards.mean(1)
    rel = np.clip((v.leaf_pos.mean(1)[:, 1] - cb) / (1 - cb), 0, 1)  # 0 at the crown's bottom, 1 at its top
    centre = np.array([t.x + t.lean[0], t.y + t.lean[1], t.z + 0.5 * (t.bottom + t.top)])
    radii = np.array([0.5 * (t.r_major + t.r_minor)] * 2 + [0.5 * (t.top - t.bottom)])
    return world(bark), cards, card_light(mid, centre, radii, rel, _seed(t.x, t.y))


# ---------------------------------------------------------------- textures


def leaf_page(atlas: np.ndarray, tints: list[np.ndarray]) -> np.ndarray:
    """One RGBA page: a row per tint, a column per brightness level, each slot the leaf atlas recoloured so
    that its mean opaque colour is tint x level."""
    opaque = atlas[..., 3] > 128
    mean = atlas[..., :3][opaque].mean(0) if opaque.any() else np.array([128.0, 128.0, 128.0])
    s = atlas.shape[0]
    page = np.zeros((s * len(tints), s * len(LEVELS), 4), np.uint8)
    for r, tint in enumerate(tints):
        for c, level in enumerate(LEVELS):
            rgb = atlas[..., :3].astype(float) * (np.asarray(tint, float) * level * LEVEL_TINT[c] / mean)
            slot = page[r * s : (r + 1) * s, c * s : (c + 1) * s]
            slot[..., :3] = np.clip(rgb, 0, 255).astype(np.uint8)
            slot[..., 3] = atlas[..., 3]
    return page


def slot_uv(uv: np.ndarray, tint: np.ndarray, level: np.ndarray, n_tints: int) -> np.ndarray:
    """Atlas uv (v down) of cards -> page uv (v up, as trimesh wants) in the slot of each card's tint and
    level. `uv` is (cards, 4, 2); `tint` and `level` are per card."""
    u = (level[:, None] + uv[..., 0]) / len(LEVELS)
    v = (tint[:, None] + uv[..., 1]) / n_tints
    return np.stack([u, 1.0 - v], -1)


def leaf_colour(rgb: np.ndarray) -> np.ndarray:
    """A plant's leaf colour from photographed colours (n, 3): the median, pulled 40% toward LEAF_GREEN
    (leaves photographed from below against the sky come out grey and dark) and held to a brightness leaves
    have (luma 55 to 125). Under 30 samples: LEAF_GREEN."""
    c = np.median(rgb, axis=0) if len(rgb) >= 30 else LEAF_GREEN
    c = 0.6 * c + 0.4 * LEAF_GREEN
    luma = float(c @ [0.299, 0.587, 0.114])
    return np.clip(c * np.clip(luma, 55.0, 125.0) / max(luma, 1.0), 0, 255)


def merge_tints(colours: list[np.ndarray], limit: int = MAX_TINTS) -> tuple[list[np.ndarray], list[int]]:
    """At most `limit` tints for the given colours: the two nearest are merged until they fit. Returns the
    tints and, per colour, its tint index."""
    groups = [[i] for i in range(len(colours))]
    cols = [np.asarray(c, float) for c in colours]
    while len(groups) > limit:
        pairs = (
            (np.linalg.norm(cols[a] - cols[b]), a, b)
            for a in range(len(cols))
            for b in range(a + 1, len(cols))
        )
        _, a, b = min(pairs, key=lambda t: t[0])
        na, nb = len(groups[a]), len(groups[b])
        cols[a] = (cols[a] * na + cols[b] * nb) / (na + nb)
        groups[a] += groups.pop(b)
        cols.pop(b)
    index = [0] * len(colours)
    for g_i, grp in enumerate(groups):
        for i in grp:
            index[i] = g_i
    return cols, index


# ---------------------------------------------------------------- shrubs


@dataclass
class Shrub:
    x: float
    y: float
    z: float  # ground
    rx: float
    ry: float
    height: float
    colour: np.ndarray
    tint: int = 0
    members: np.ndarray = field(default_factory=lambda: np.zeros(0, int))
    seen: float = 0.0  # share of its scan faces a camera saw


def plant_coloured(rgb: np.ndarray) -> np.ndarray:
    """Colours a leaf can have: green (g over r + 4 and over b + 10), or yellowish to olive (g at least
    0.86 r, b under 0.8 g) and not near white. Grey metal, white paint, red decking, brick and brown dead
    leaves are not; tan wood is (fences are told apart by being flat)."""
    r, g, b = (rgb[..., k].astype(float) for k in range(3))
    green = (g > r + 4) & (g > b + 10)
    olive = (g >= 0.86 * r) & (b < 0.8 * g) & (r + g + b < 570)
    return green | olive


def scan_colours(scan: rb.Scan, ids: np.ndarray) -> np.ndarray:
    """The scan's own texture colour at the middle of each face (every face has one, unlike a photo view)."""
    uv = scan.uvs[ids].mean(1)
    out = np.zeros((len(ids), 3))
    for k, img in enumerate(scan.images):
        sel = scan.tex[ids] == k
        if sel.any():
            h, w = img.shape[:2]
            row = np.clip(((1 - uv[sel, 1]) * h).astype(int), 0, h - 1)
            out[sel] = img[row, np.clip((uv[sel, 0] * w).astype(int), 0, w - 1)]
    return out


def voxel_share(centres: np.ndarray, flag: np.ndarray, cell: float) -> np.ndarray:
    """Per face: the share of faces in its `cell`-sized voxel for which `flag` holds."""
    if not len(centres):
        return np.zeros(0)
    ijk = np.floor((centres - centres.min(0)) / cell).astype(int)
    shape = tuple(ijk.max(0) + 1)
    flat = np.ravel_multi_index(tuple(ijk.T), shape)
    n = np.bincount(flat, minlength=int(np.prod(shape)))
    k = np.bincount(flat, weights=flag.astype(float), minlength=int(np.prod(shape)))
    return (k / np.maximum(n, 1))[flat]


def find_shrubs(
    centres: np.ndarray, hag: np.ndarray, areas: np.ndarray, foliage: np.ndarray, rejected: list | None = None
) -> list[np.ndarray]:
    """Shrubs among scan faces: `foliage` faces (leafy and plant-coloured) below SHRUB_TOP, grouped by
    touching SHRUB_VOXEL cells. A group is a shrub when it stands on the ground (reaches below 0.6 m), is at
    least SHRUB_MIN_H tall, SHRUB_SIZE across, covers SHRUB_MIN_AREA of plan, has SHRUB_MIN_FACES faces, and
    foliage makes up SHRUB_LEAFY of all the scan surface inside its box (a fence behind a few leaves does not
    pass). Returns the member indices of each shrub; `rejected` collects (x, y, faces, why) of the groups of
    SHRUB_MIN_FACES or more that failed a test."""
    low = np.nonzero(foliage & (hag <= SHRUB_TOP))[0]
    if len(low) < SHRUB_MIN_FACES:
        return []
    c = centres[low]
    ijk = np.floor((c - c.min(0)) / SHRUB_VOXEL).astype(int) + 1
    vox = np.zeros(tuple(ijk.max(0) + 2), bool)
    vox[tuple(ijk.T)] = True
    lab, n = ndimage.label(vox, structure=np.ones((3, 3, 3)))
    which = lab[ijk[:, 0], ijk[:, 1], ijk[:, 2]]
    out = []
    for k in range(1, n + 1):
        sel = which == k
        if sel.sum() < SHRUB_MIN_FACES:
            continue
        mine = low[sel]
        p, h = centres[mine], hag[mine]
        size = p[:, :2].max(0) - p[:, :2].min(0)
        lo, hi = p.min(0) - 0.05, p.max(0) + 0.05
        box = np.all((centres >= lo) & (centres <= hi), axis=1)
        share = areas[box & foliage].sum() / max(areas[box].sum(), 1e-9)
        plan = len(np.unique(ijk[sel][:, :2], axis=0)) * SHRUB_VOXEL**2
        why = ""
        if h.min() > 0.6:
            why = f"hangs: lowest {h.min():.2f} m"
        elif np.percentile(h, 98) < SHRUB_MIN_H:
            why = f"low: {np.percentile(h, 98):.2f} m"
        elif size.max() > SHRUB_SIZE[1] or size.max() < SHRUB_SIZE[0]:
            why = f"size {size.max():.1f} m"
        elif plan < SHRUB_MIN_AREA:
            why = f"covers {plan:.2f} m2"
        elif share < SHRUB_LEAFY:
            why = f"foliage is {share:.2f} of its box"
        if why:
            if rejected is not None:
                rejected.append((float(np.median(p[:, 0])), float(np.median(p[:, 1])), int(sel.sum()), why))
            continue
        out.append(mine)
    return out


def bush_cards(s: Shrub) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Leaf cards filling a shrub's half ellipsoid: corners (cards, 4, 3), light 0-1 per card and which
    atlas cluster (0-3) each uses. About 7 cards per m2 of its surface, 0.5 to 0.85 m across, a little
    brighter than a tree's (+0.1: a bush has no crown above it)."""
    rng = np.random.default_rng(_seed(s.x, s.y, s.height))
    surface = math.pi * (s.rx * s.ry + (s.rx + s.ry) * s.height)
    n = int(np.clip(7 * surface, 12, 220))
    size = float(np.clip(0.6 * max(s.rx, s.ry, s.height), 0.5, 0.85))
    d = rng.normal(size=(n, 3))
    d[:, 2] = np.abs(d[:, 2]) * 0.9 + 0.1
    d /= np.linalg.norm(d, axis=1)[:, None]
    rad = rng.uniform(0.45, 0.92, n)
    centre = np.array([s.x, s.y, s.z]) + d * rad[:, None] * [s.rx, s.ry, s.height]
    nrm = d + rng.normal(0, 0.45, (n, 3))  # each card faces outward, with some scatter
    nrm /= np.linalg.norm(nrm, axis=1)[:, None]
    up = np.array([0.0, 0.0, 1.0]) - nrm * nrm[:, 2:3]
    bad = np.linalg.norm(up, axis=1) < 0.2
    up[bad] = np.array([1.0, 0.0, 0.0]) - nrm[bad] * nrm[bad, 0:1]
    up /= np.linalg.norm(up, axis=1)[:, None]
    right = np.cross(up, nrm)
    sz = (size * rng.uniform(0.8, 1.2, n))[:, None]
    corners = np.stack(
        [
            centre - right * sz / 2 - up * sz * 0.45,
            centre + right * sz / 2 - up * sz * 0.45,
            centre + right * sz / 2 + up * sz * 0.55,
            centre - right * sz / 2 + up * sz * 0.55,
        ],
        1,
    )
    corners[..., 2] = np.maximum(corners[..., 2], s.z + 0.02)
    rel = np.clip((centre[:, 2] - s.z) / max(s.height, 1e-6), 0, 1)
    light = card_light(
        centre, np.array([s.x, s.y, s.z]), np.array([s.rx, s.ry, s.height]), rel, _seed(s.x, s.y)
    )
    return corners, np.clip(light + 0.1, 0, 0.999), rng.integers(0, 4, n)


CARD_UV = np.array([[0.002, 0.498], [0.498, 0.498], [0.498, 0.002], [0.002, 0.002]])  # one cluster, v down


# ---------------------------------------------------------------- collision


def prism(x: float, y: float, r: float, z0: float, z1: float, sides: int = 8) -> np.ndarray:
    """An upright prism as (n, 3, 3) triangles (sides and top)."""
    a = np.linspace(0, 2 * math.pi, sides, endpoint=False)
    ring = np.stack([x + r * np.cos(a), y + r * np.sin(a)], 1)
    tris = []
    for k in range(sides):
        p, q = ring[k], ring[(k + 1) % sides]
        tris.append([[*p, z0], [*q, z0], [*q, z1]])
        tris.append([[*p, z0], [*q, z1], [*p, z1]])
        tris.append([[x, y, z1], [*p, z1], [*q, z1]])
    return np.asarray(tris, float)


# ---------------------------------------------------------------- measuring a lot


def _polys(geom) -> list[Polygon]:
    if geom.is_empty:
        return []
    if geom.geom_type == "Polygon":
        return [geom]
    return [q for part in getattr(geom, "geoms", []) for q in _polys(part)]


def _sky(rgb: np.ndarray) -> np.ndarray:
    """Colours that look like sky or cloud (as exterior._sky; that module imports this one)."""
    r, g, b = (rgb[..., k].astype(float) for k in range(3))
    mx, mn = np.maximum(np.maximum(r, g), b), np.minimum(np.minimum(r, g), b)
    return ((b > r + 20) & (b >= g) & (mx > 110)) | ((mn > 185) & (mx - mn < 40))


def outside_point(outer, c0: np.ndarray, clear: float = OUTSIDE) -> np.ndarray:
    """A point `clear` m outside `outer` (the lot and house, without holes), across the edge nearest to c0."""
    q = np.asarray(nearest_points(outer.boundary, Point(*c0))[0].coords[0])
    d = (q - c0) if outer.contains(Point(*c0)) else (c0 - q)
    if np.linalg.norm(d) < 0.3:
        d = c0 - np.asarray(outer.centroid.coords[0])
    d = d / max(np.linalg.norm(d), 1e-9)
    p = q + d * clear
    for _ in range(8):
        if not outer.buffer(0.6 * clear).contains(Point(*p)):
            break
        p = p + d
    return p


def _in_ellipse(xy: np.ndarray, t: Tree, grow: float = 0.0) -> np.ndarray:
    """Which plan points lie under a tree's crown (its ellipse grown by `grow` m)."""
    d = xy - [t.x + t.lean[0], t.y + t.lean[1]]
    ca, sa = math.cos(t.angle), math.sin(t.angle)
    u, v = d[:, 0] * ca + d[:, 1] * sa, -d[:, 0] * sa + d[:, 1] * ca
    return (u / (t.r_major + grow)) ** 2 + (v / (t.r_minor + grow)) ** 2 < 1.0


def measure(scan: rb.Scan, g, walls: Polygon, panos: PanoSet, library: Library | None = None) -> dict:
    """Everything replace_vegetation needs from the scan: `trees` and `shrubs` to build, `gone` (a bool mask
    over all scan faces of what they replace), `bark_gain` (bark brightness against the library texture), and
    the intermediate results under `debug`."""
    lib = library or load_library()
    c = scan.tris.mean(1)
    hag = c[:, 2] - g.height(c[:, 0], c[:, 1])
    in_house = shapely.contains_xy(walls.buffer(0.05), c[:, 0], c[:, 1])
    cand = np.nonzero((hag > 0.25) & ~in_house)[0]
    gone = np.zeros(len(c), bool)
    out = {"trees": [], "shrubs": [], "gone": gone, "bark_gain": 1.0, "debug": {}}
    if len(cand) < 100:
        return out
    cc, cn, ca, ch = c[cand], scan.normals[cand], scan.areas[cand], hag[cand]
    far = ~shapely.contains_xy(walls.buffer(HOUSE_CLEAR), cc[:, 0], cc[:, 1])
    flat = planarity(cc, cn, ca)
    leafy = flat < LEAFY_MAX
    can = leafy & (ch > CANOPY_MIN) & far  # near the house anything that high is roof, eave or gutter
    # Below the crowns foliage must also have a plant's colour (an AC unit, a railing and a pile of bricks
    # scan as crumpled as a bush): most faces in its SHRUB_VOXEL cell are plant-coloured in the scan's own
    # texture.
    green = voxel_share(cc, plant_coloured(scan_colours(scan, cand)), SHRUB_VOXEL) >= 0.5
    outer = unary_union([Polygon(q.exterior) for q in _polys(g.lot.union(walls))])

    def photo(ids: np.ndarray) -> np.ndarray:
        """Photographed colour per face (NaN where no camera sees it or it shows sky)."""
        rgb = np.full((len(ids), 3), np.nan)
        if len(ids):
            col, valid = shade(cc[ids], cn[ids], panos, scan.eyes, snap=False)
            col = np.clip(col, 0, 255)
            ok = valid & ~_sky(col)
            rgb[ok] = col[ok]
        return rgb

    rgb = np.full((len(cand), 3), np.nan)
    leaf_ids = np.nonzero(leafy)[0]
    rgb[leaf_ids] = photo(leaf_ids)

    trees: list[Tree] = []
    trunks: list[Trunk] = []
    posts: list[Trunk] = []
    crowns: list[Crown] = []
    grid = None
    keep = np.zeros(len(cand), bool)  # scan the models must not replace: posts, poles, pergolas
    take = np.zeros(len(cand), bool)
    if can.sum() >= 50:
        grid = canopy_grid(cc[can], ch[can])
        slab = slab_cells(grid)
        foliage = grid.mask & ~slab
        cover = ndimage.binary_dilation(foliage, iterations=4)  # 1 m
        under_slab = ndimage.binary_dilation(slab, iterations=4)

        # Columns under the foliage: painted ones, ones on the house wall and ones that carry a slab are
        # posts and downspouts.
        columns, members = find_trunks(cc, cn, ch, lambda x, y: bool((cover | under_slab)[grid.cell(x, y)]))
        col_rgb = [photo(mem) for mem in members]
        member_of: dict[int, np.ndarray] = {}
        for t, mem, pc in zip(columns, members, col_rgb, strict=True):
            seen = pc[np.isfinite(pc[:, 0])]
            med = np.median(seen, axis=0) if len(seen) >= 5 else None
            painted = med is not None and med.min() > POST_WHITE[0] and med.max() - med.min() < POST_WHITE[1]
            carries = bool(under_slab[grid.cell(t.x, t.y)])
            if painted or carries or walls.exterior.distance(Point(t.x, t.y)) < POST_WALL:
                posts.append(t)
            else:
                trunks.append(t)
                member_of[id(t)] = mem
        # A trunk too slender for the crown it would carry is a pole: drop the worst and share out again.
        while True:
            crowns = segment_crowns(grid, trunks, foliage)
            bad = [(cr.top / cr.trunk.radius, cr.trunk) for cr in crowns if cr.trunk is not None]
            bad = [b for b in bad if b[0] > TRUNK_SLENDER]
            if not bad:
                break
            worst = max(bad, key=lambda b: b[0])[1]
            trunks = [t for t in trunks if t is not worst]
            posts.append(worst)
        owned = [t for t in trunks if any(cr.trunk is t for cr in crowns)]
        posts += [t for t in trunks if not any(t is q for q in owned)]
        trunks = owned
        for t in posts:  # whatever stands within 0.3 m of a post, at any height, stays scan
            keep |= np.hypot(cc[:, 0] - t.x, cc[:, 1] - t.y) < t.radius + 0.3

        owner = np.full(grid.top.shape, -1)
        for k, cr in enumerate(crowns):
            i, j = grid.cell(cr.cells[:, 0], cr.cells[:, 1])
            owner[i, j] = k
        fi, fj = grid.cell(cc[:, 0], cc[:, 1])
        in_grid = (
            (cc[:, 0] >= grid.x0)
            & (cc[:, 1] >= grid.y0)
            & (cc[:, 0] < grid.x0 + grid.top.shape[0] * CROWN_CELL)
            & (cc[:, 1] < grid.y0 + grid.top.shape[1] * CROWN_CELL)
        )
        face_crown = np.where(in_grid, owner[fi, fj], -1)
        keep |= in_grid & slab[fi, fj] & (ch > CANOPY_MIN - 0.3)
        low_soft = (ch > 0.3) & (ch < 1.5) & leafy & green & ~keep
        near_crown = ndimage.binary_dilation(owner >= 0, iterations=6)  # 1.5 m
        for k, cr in enumerate(crowns):
            seen = rgb[can & (face_crown == k)]
            colour = leaf_colour(seen[np.isfinite(seen[:, 0])])
            centre0 = cr.cells.mean(0)
            note = ""
            if cr.trunk is not None:
                base = np.array([cr.trunk.x, cr.trunk.y])
                centre, r1, r2, ang = crown_axes(cr.cells)
                in_lot = bool(outer.buffer(0.5).contains(Point(*base)))
            elif (
                cr.bottom < SMALL_TREE[0]
                and cr.top < SMALL_TREE[1]
                and outer.buffer(-1.0).contains(Point(*centre0))
            ):
                # A small tree in the lot whose thin trunk the column search missed: it stands where the scan
                # has most low growth under (or within 1.5 m of) the crown, else under the crown's middle.
                centre, r1, r2, ang = crown_axes(cr.cells)
                d0 = np.hypot(cc[:, 0] - centre0[0], cc[:, 1] - centre0[1])
                under = low_soft & in_grid & near_crown[fi, fj] & (d0 < max(r1, r2) + 1.5)
                base = np.median(cc[under][:, :2], axis=0) if under.sum() >= 30 else centre0
                in_lot, note = True, "no trunk found in the scan: stood under its crown"
            elif cr.area < NEIGHBOUR_MIN_AREA:
                owner[owner == k] = -1  # a scrap of someone else's tree: no model
                continue
            else:
                base = outside_point(outer, centre0)
                centre, r1, r2, ang = crown_axes(cr.cells, base)
                in_lot, note = False, "no trunk in the scan: a neighbour's tree, stood outside the lot"
            top, bottom = cr.top, max(cr.bottom, CANOPY_MIN)
            top = max(top, bottom + 1.0)
            lean = centre - base
            reach = float(np.linalg.norm(lean))
            if reach > 0.35 * top:
                lean = lean * 0.35 * top / reach
            t = Tree(
                x=float(base[0]),
                y=float(base[1]),
                z=float(g.height(base[:1], base[1:])[0]),
                top=top,
                bottom=bottom,
                r_major=r1,
                r_minor=r2,
                angle=ang,
                lean=(float(lean[0]), float(lean[1])),
                colour=colour,
                trunk_r=None if cr.trunk is None else cr.trunk.radius,
                in_lot=in_lot,
                note=note,
            )
            if lib is not None:
                t.variant = pick_variant(lib.variants, 0.5 * (r1 + r2) / (top - bottom))
            trees.append(t)

        # What the models replace: the foliage of the crowns that got a model and whatever else the scan has
        # inside them that is not clearly a plane (limbs, flatter sheets of leaves); other foliage above
        # CANOPY_MIN when it is within 1.5 m of such a crown or plant-coloured (the crumpled eave of a shed
        # the plan does not have is neither) ...
        face_crown = np.where(in_grid, owner[fi, fj], -1)
        in_crown = face_crown >= 0
        fringe = in_grid & ndimage.binary_dilation(owner >= 0, iterations=6)[fi, fj]
        take |= can & (in_crown | fringe | green)
        take |= in_crown & (ch > CANOPY_MIN) & (flat < CROWN_FLAT)
        # ... each replaced tree's trunk: its column, and the scan within its radius + 0.25 m below the crown
        # that is not a plane (a fence the trunk stands against stays) ...
        for tr in trunks:
            cr = next(q for q in crowns if q.trunk is tr)
            take[member_of[id(tr)]] = True
            near = np.hypot(cc[:, 0] - tr.x, cc[:, 1] - tr.y) < tr.radius * 1.5 + 0.25
            take |= near & (ch < max(cr.bottom, CANOPY_MIN) + 1.0) & (flat < TRUNK_MAX_FLAT)
        mem = [pc for t, pc in zip(columns, col_rgb, strict=True) if any(t is q for q in trunks)]
        if lib is not None and mem:
            seen = np.concatenate(mem)
            seen = seen[np.isfinite(seen[:, 0])]
            if len(seen) >= 30:
                luma = float(np.median(seen @ [0.299, 0.587, 0.114]))
                ref = float(lib.bark.reshape(-1, 3).mean(0) @ [0.299, 0.587, 0.114])
                out["bark_gain"] = float(np.clip(luma / max(ref, 1.0), 0.6, 1.5))

    # Shrubs: plant-coloured foliage standing on the ground. One at the foot of a tree in the lot (within 1 m
    # of its base) is that tree's own low growth, not a shrub.
    low_foliage = leafy & green & ~keep & (ch <= CANOPY_MIN + 0.3)
    shrubs: list[Shrub] = []
    rejected: list = []
    yard_trees = [t for t in trees if t.in_lot]
    for mine in find_shrubs(cc, ch, ca, low_foliage, rejected):
        p = cc[mine]
        cx, cy = float(np.median(p[:, 0])), float(np.median(p[:, 1]))
        if any(math.hypot(cx - t.x, cy - t.y) < 1.0 for t in yard_trees):
            rejected.append((cx, cy, len(mine), "at the foot of a tree"))
            continue
        rx = max(float(np.percentile(np.abs(p[:, 0] - cx), 95)) * 1.05, 0.3)
        ry = max(float(np.percentile(np.abs(p[:, 1] - cy), 95)) * 1.05, 0.3)
        inside = ((cc[:, 0] - cx) / rx) ** 2 + ((cc[:, 1] - cy) / ry) ** 2 < 1.0
        seen_share = float(np.isfinite(rgb[mine][:, 0]).mean())
        if seen_share >= SHRUB_SEEN_MAX:
            rejected.append(
                (cx, cy, len(mine), f"a shrub the cameras saw {seen_share:.2f} of: stays as scanned")
            )
            keep |= inside & low_foliage  # also when a tree's crown hangs over it
            continue
        height = float(np.percentile(ch[mine], 98))
        if not any(_in_ellipse(np.array([[cx, cy]]), t, 0.5)[0] for t in trees):
            # a shrub taller than SHRUB_TOP is as tall as the foliage above it (under a crown, that is the
            # tree's)
            own = inside & low_foliage
            height = float(np.percentile(ch[own], 98)) if own.sum() >= 30 else height
        seen = rgb[mine]
        z = float(g.height(np.array([cx]), np.array([cy]))[0])
        colour = leaf_colour(seen[np.isfinite(seen[:, 0])])
        shrubs.append(Shrub(cx, cy, z, rx, ry, height, colour, members=cand[mine], seen=seen_share))
        # it replaces its faces and whatever non-flat scan sits inside its ellipse below its top
        take |= inside & (ch < height + 0.3) & (flat < CROWN_FLAT)
        take[mine] = True
    # A tree's own low growth, for trees standing in the lot: foliage hanging under the crown (above 1.2 m),
    # plant-coloured foliage below that, the scan's attempt at the trunk within 0.6 m of where the model
    # stands, and foliage right next to the house that the crown search left out.
    for t in yard_trees:
        under = _in_ellipse(cc[:, :2], t, 0.5)
        take |= under & leafy & ((ch > 1.2) | green)
        foot = np.hypot(cc[:, 0] - t.x, cc[:, 1] - t.y) < 0.6
        take |= foot & (ch < CANOPY_MIN + 0.2) & (flat < CROWN_FLAT)
        take |= leafy & ~far & (ch > 1.2) & _in_ellipse(cc[:, :2], t, 1.0)
    take &= ~keep
    gone[cand[take]] = True
    # Scraps of the same plants lying just above the ground (5 to 30 cm) go with them.
    low_taken = take & (ch < CANOPY_MIN)
    if low_taken.any():
        x0, y0 = cc[:, 0].min(), cc[:, 1].min()
        shape = (int((cc[:, 0].max() - x0) / SHRUB_VOXEL) + 3, int((cc[:, 1].max() - y0) / SHRUB_VOXEL) + 3)
        cleared = np.zeros(shape, bool)
        cleared[
            ((cc[low_taken, 0] - x0) / SHRUB_VOXEL).astype(int) + 1,
            ((cc[low_taken, 1] - y0) / SHRUB_VOXEL).astype(int) + 1,
        ] = True
        cleared = ndimage.binary_dilation(cleared)
        litter = np.nonzero((hag > 0.05) & (hag <= 0.3) & ~in_house)[0]
        i = np.clip(((c[litter, 0] - x0) / SHRUB_VOXEL).astype(int) + 1, 0, shape[0] - 1)
        j = np.clip(((c[litter, 1] - y0) / SHRUB_VOXEL).astype(int) + 1, 0, shape[1] - 1)
        gone[litter[cleared[i, j]]] = True

    out.update(trees=trees, shrubs=shrubs)
    out["debug"] = {
        "cand": cand, "flat": flat, "hag": ch, "rgb": rgb, "grid": grid, "trunks": trunks, "posts": posts,
        "crowns": crowns, "outer": outer, "keep": keep, "green": green, "rejected_shrubs": rejected,
    }  # fmt: skip
    return out


# ---------------------------------------------------------------- the hook


def replace_vegetation(
    scene: trimesh.Scene,
    scan: rb.Scan,
    g,  # exterior.Ground: height(x, y), normal(x, y), lot
    faces: np.ndarray,
    tree: np.ndarray,
    avail: np.ndarray,
    panos: PanoSet,
    walls: Polygon,
    report: dict,
    more: list[Tree] = (),
) -> tuple[np.ndarray, np.ndarray]:
    """Replace scanned trees and shrubs by models that fill the same space (nodes `plants_N`).

    `more`: trees measured elsewhere (farfield.py: the drone splat's crowns past the lot). One that overlaps
    a tree of the yard is dropped; a tree this module only guessed at past the lot's edge (a crown without a
    trunk) gives way to one of `more` that overlaps it, because the splat saw where it really stands.

    `faces`: indices into scan.tris of the outdoor scan faces an outdoor camera sees; `tree`: which of them
    are more than exterior.TREE_MIN_Z above the ground and away from the roof (canopies); `avail`: which are
    not yet replaced by an earlier step; `panos`: the outdoor panoramas; `walls`: the facade outline.
    Returns (taken, solids): a bool mask over `faces` of the scan faces this replaces (the caller then
    neither renders nor collides them) and (n, 3, 3) Z-up collision triangles for what was built. Adds its
    nodes to `scene` and its numbers to `report`."""
    none = np.zeros((0, 3, 3))
    lib = load_library()
    if lib is None:
        report["vegetation"] = {"replaced": False, "why": f"no model library at {LIBRARY}"}
        return np.zeros(len(faces), bool), none
    m = measure(scan, g, walls, panos, lib)
    trees, shrubs = m["trees"], m["shrubs"]
    taken = m["gone"][faces] & avail
    if more:

        def overlap(a: Tree, b: Tree) -> bool:
            d = math.hypot(a.x + a.lean[0] - b.x - b.lean[0], a.y + a.lean[1] - b.y - b.lean[1])
            return d < max(a.r_major, b.r_major)

        trees = [t for t in trees if t.in_lot or not any(overlap(t, f) for f in more)]
        extra = [f for f in more if not any(overlap(f, t) for t in trees)]
        for f in extra:
            f.variant = pick_variant(lib.variants, 0.5 * (f.r_major + f.r_minor) / max(f.top - f.bottom, 0.5))
        trees = trees + extra
    if not trees and not shrubs and not taken.any():
        report["vegetation"] = {"replaced": False, "why": "no foliage found in the scan"}
        return taken, none

    colours = [t.colour for t in trees] + [s.colour for s in shrubs]
    tints, index = merge_tints(colours) if colours else ([LEAF_GREEN], [])
    for k, t in enumerate(trees):
        t.tint = index[k]
    for k, s in enumerate(shrubs):
        s.tint = index[len(trees) + k]

    # Nothing may reach into the house: a crown next to a wall would show its leaves in the rooms behind it.
    house = walls.buffer(0.2)
    z_house = float(np.nanmin(g.z)) + HOUSE_TOP

    def indoors(pts: np.ndarray) -> np.ndarray:
        return (pts[:, 2] < z_house) & shapely.contains_xy(house, pts[:, 0], pts[:, 1])

    leaf_v, leaf_uv, bark_v, bark_f, bark_uv, bark_dark, solids = [], [], [], [], [], [], []
    nb = 0
    for t in trees:
        v = lib.variants[t.variant]
        bark, cards, light = place_tree(v, t)
        ok = ~indoors(cards.reshape(-1, 3)).reshape(-1, 4).any(1)
        if t.far:  # seen from tens of metres: a third of the cards, each grown to close the gaps
            ok &= np.arange(len(cards)) % FAR_CARDS == 0
            mid = cards.mean(1, keepdims=True)
            cards = mid + (cards - mid) * math.sqrt(FAR_CARDS)
        leaf_v.append(cards[ok])
        level = (light * len(LEVELS)).astype(int)
        leaf_uv.append(slot_uv(v.leaf_uv, np.full(len(cards), t.tint), level, len(tints))[ok])
        tri_ok = ~indoors(bark)[v.bark_idx].any(1)
        if t.far:
            tri_ok &= v.bark_pos[v.bark_idx].mean(1)[:, 1] < v.crown_base
        bark_v.append(bark)
        bark_f.append(v.bark_idx[tri_ok] + nb)
        nb += len(bark)
        bark_uv.append(np.c_[v.bark_uv[:, 0], 1.0 - v.bark_uv[:, 1]])
        bark_dark.append((v.bark_pos[v.bark_idx].mean(1)[:, 1] > v.crown_base)[tri_ok])
        if t.in_lot:
            r = t.trunk_r
            if r is None:
                r = v.trunk_radius * 0.5 * (t.r_major + t.r_minor) / v.crown_radius
            solids.append(prism(t.x, t.y, float(np.clip(r, 0.08, 0.6)), t.z - 0.2, t.z + t.bottom))
    for s in shrubs:
        cards, light, cluster = bush_cards(s)
        uv = np.tile(CARD_UV, (len(cards), 1, 1)) + np.stack([cluster % 2, cluster // 2], 1)[:, None] * 0.5
        ok = ~indoors(cards.reshape(-1, 3)).reshape(-1, 4).any(1)
        leaf_v.append(cards[ok])
        level = (light * len(LEVELS)).astype(int)
        leaf_uv.append(slot_uv(uv, np.full(len(cards), s.tint), level, len(tints))[ok])
        solids.append(prism(s.x, s.y, 0.7 * min(s.rx, s.ry), s.z - 0.2, s.z + 0.8 * s.height))

    triangles = 0
    megapixels = 0.0
    if leaf_v:
        cards = np.concatenate(leaf_v)
        quad = np.arange(len(cards))[:, None] * 4
        f = np.concatenate([quad + [0, 1, 2], quad + [0, 2, 3]])
        page = leaf_page(lib.atlas, tints)
        mesh = trimesh.Trimesh(cards.reshape(-1, 3), f, process=False)
        mesh.visual = trimesh.visual.TextureVisuals(
            uv=np.concatenate(leaf_uv).reshape(-1, 2), material=rb._material(page, "plants_leaves")
        )
        mesh.apply_transform(hm.Z_UP_TO_Y_UP)
        scene.add_geometry(mesh, node_name="plants_0", geom_name="plants_0")
        triangles += len(f)
        megapixels += page.shape[0] * page.shape[1] / 1e6
    if bark_v:
        verts, fs, uv = np.concatenate(bark_v), np.concatenate(bark_f), np.concatenate(bark_uv)
        dark = np.concatenate(bark_dark)
        for node, sel, k in (("plants_1", ~dark, 1.0), ("plants_2", dark, BARK_SHADE)):
            if not sel.any():
                continue
            img = np.clip(lib.bark.astype(float) * k * m["bark_gain"], 0, 255).astype(np.uint8)
            used, inv = np.unique(fs[sel], return_inverse=True)
            mesh = trimesh.Trimesh(verts[used], inv.reshape(-1, 3), process=False)
            mesh.visual = trimesh.visual.TextureVisuals(uv=uv[used], material=rb._material(img, node))
            mesh.apply_transform(hm.Z_UP_TO_Y_UP)
            scene.add_geometry(mesh, node_name=node, geom_name=node)
            triangles += int(sel.sum())
            megapixels += img.shape[0] * img.shape[1] / 1e6

    report["vegetation"] = {
        "replaced": bool(trees or shrubs),  # False with scan_faces_replaced > 0: only stray foliage removed
        "trees": [
            {
                "at": [round(t.x, 2), round(t.y, 2)],
                "height_m": round(t.top, 2),
                "crown_from_m": round(t.bottom, 2),
                "crown_radii_m": [round(t.r_major, 2), round(t.r_minor, 2)],
                "trunk_radius_m": None if t.trunk_r is None else round(t.trunk_r, 2),
                "leaf_rgb": [int(c) for c in t.colour],
                "model": lib.variants[t.variant].name,
                "in_lot": t.in_lot,
                "far": t.far,
                "note": t.note,
            }
            for t in trees
        ],
        "shrubs": [
            {
                "at": [round(s.x, 2), round(s.y, 2)],
                "size_m": [round(2 * s.rx, 2), round(2 * s.ry, 2), round(s.height, 2)],
                "seen_by_cameras": round(s.seen, 2),
                "leaf_rgb": [int(c) for c in s.colour],
            }
            for s in shrubs
        ],
        "scan_faces_replaced": int(taken.sum()),
        "triangles": triangles,
        "texture_megapixels": round(megapixels, 2),
        "leaf_tints": len(tints),
        "library": lib.licence,
    }
    return taken, np.concatenate(solids) if solids else none
