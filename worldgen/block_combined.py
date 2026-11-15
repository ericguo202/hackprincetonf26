"""Combined block for hou_navidad_01: procedural near zone + Vexcel 3D Cities far zone.

    uv run python -m worldgen.block_combined                       # build render.glb + collision.glb
    uv run python -m worldgen.block_combined --hero-exterior hou_236595179=path/to/exterior.glb

Merges the two Lane A block prototypes (block_procedural.py, block_photo.py), all from cached data:

1. Near zone (a polygon in the block frame: Navidad Rd and Bellaire Blvd right of way plus every lot
   that fronts them, cut along parcel lines): procedural houses, roofs, curbs and fences.
2. Wall colours per near-zone house come from the Vexcel 3D Cities mesh (its facade texture where it
   faces each footprint wall), applied as a tint over crisp tiled CC0 brick or siding.
3. Near-zone ground is split into lawn, concrete, road and beds from the ortho and the OSM streets,
   each a tiled CC0 texture tinted per vertex by the ortho's low-frequency colour (cars painted out).
4. Far zone: the 3D Cities mesh (unlit photo materials) inside its two tile columns, cut cleanly at the
   near zone; render only. Outside those columns (no mesh fetched) the procedural builder continues.
5. Trees: an instanced CC0-textured live-oak model scaled to every DSM crown outside the mesh zone.
6. Cars painted into the ortho are removed from the ground.
7. Hero lots stay tagged (`hero_<listing_id>`); `--hero-exterior` swaps in a scanned exterior glb.

Output: out/block_compare/combined/{render.glb, collision.glb, build_summary.json}.
Block frame: origin at the ground at the block centre, +X east, +Y up, +Z south, meters.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
import zlib
from pathlib import Path

import numpy as np
import shapely
from PIL import Image, ImageFilter
from scipy import ndimage
from scipy.spatial import cKDTree
from shapely import contains_xy
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import nearest_points, unary_union

from worldgen import block_data as bd
from worldgen import block_hardscape as bh
from worldgen import block_photo as bp
from worldgen import block_procedural as bpr
from worldgen.block_buildings import (
    FASCIA,
    OVERHANG,
    Building,
    decompose,
    fit_roof,
    flat_roof,
    gable_ends,
    horizontal,
    outer_walls,
    polys_of,
    roof_mesh,
)
from worldgen.block_facade import facade_atlas, fence_texture, place_openings
from worldgen.block_glb import Material, MeshBuf, Node, compress, write_glb
from worldgen.block_ground import CURB, fence_lines, fence_mesh, gridded, load_roads
from worldgen.block_raster import Ortho, derive_dtm, grid_xz, read_geotiff, resample_to_grid
from worldgen.block_veg import segment_crowns

OUT = bd.OUT / "combined"
HALF = bpr.HALF
RES = bpr.RES
PH = bd.CACHE / "polyhaven"
TREES = bd.CACHE / "trees"
HEROES = bpr.HEROES
HERO_WORLD = {  # Listing.world (listings/index.json): block = position + rotY(heading_deg) * local
    "hou_236595179": {
        "position": (-20.94, 98.06),
        "heading_deg": 4.3,
        "model": "y1cVPR1HsnL",
        "door": (5.407, -4.476),
    },
    "hou_237901269": {
        "position": (19.89, -95.08),
        "heading_deg": 0.2,
        "model": "xpspAtb5H59",
        "door": (-3.191, -0.125),
    },
}
NEAR_STREETS = ("Navidad Road", "Bellaire Boulevard")


def log(*a) -> None:
    print(*a, file=sys.stderr, flush=True)


def srgb_to_lin(c):
    c = np.asarray(c, np.float64)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def lin_to_srgb(c):
    c = np.clip(np.asarray(c, np.float64), 0, 1)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * c ** (1 / 2.4) - 0.055)


# ---------------------------------------------------------------- zones


def named_streets(names=NEAR_STREETS) -> list[LineString]:
    osm = json.loads(bd.fetch_osm().read_text())
    nodes = {e["id"]: e for e in osm["elements"] if e["type"] == "node" and "lat" in e}
    out = []
    for e in osm["elements"]:
        if e["type"] == "way" and e.get("tags", {}).get("name") in names:
            pts = [bd.to_xz(nodes[n]["lat"], nodes[n]["lon"]) for n in e["nodes"] if n in nodes]
            if len(pts) >= 2:
                out.append(LineString(pts))
    return out


def near_zone(streets: list[LineString], parcels: list[dict], half: float = HALF) -> Polygon:
    """Right of way (18 m either side of the centreline) plus every lot fronting the streets (a parcel
    within 12 m of a centreline), cut along the parcel lines; big parcels (park, utility yard) only
    to 38 m from the street."""
    row = unary_union([s.buffer(18.0) for s in streets])
    front = unary_union([s.buffer(12.0) for s in streets])
    deep = unary_union([s.buffer(38.0) for s in streets])
    parts = [row]
    for pc in parcels:
        p = pc["poly"]
        if p.is_empty or not p.intersects(front):
            continue
        parts.append(p if p.area < 2500 else p.intersection(deep))
    m = unary_union(parts).buffer(0.6, join_style=2).buffer(-0.6, join_style=2)
    return m.intersection(box(-half, -half, half, half)).buffer(0)


# ---------------------------------------------------------------- 3D Cities mesh (far zone)


def load_mesh_tiles():
    """Cached z19 3D Cities tiles in the block frame, y = 0 at the street at the origin."""
    core = [t for t in bp.tiles_covering(bp.CORE_ZOOM, HALF) if t[0] in bp.CORE_COLS]
    have = [t for t in core if bp.tile_path(bp.CORE_ZOOM, *t).exists()]
    decoded = bp.decode([bp.tile_path(bp.CORE_ZOOM, *t) for t in have])
    tiles = [
        p
        for p in (
            bp.load_tile(d, bp.tile_rect_xz(bp.CORE_ZOOM, *t)) for d, t in zip(decoded, have, strict=True)
        )
        if p
    ]
    v = np.concatenate([p.vertices for p in tiles])
    near = np.hypot(v[:, 0], v[:, 2]) < 15.0
    g0 = float(np.percentile(v[near, 1], 10))
    for p in tiles:
        p.vertices[:, 1] -= g0
    x0 = min(bp.tile_rect_xz(bp.CORE_ZOOM, x, y)[0] for x, y in have)
    x1 = max(bp.tile_rect_xz(bp.CORE_ZOOM, x, y)[1] for x, y in have)
    return tiles, g0, (x0, x1)


def piece_colours(p, faces: np.ndarray, bary: np.ndarray) -> np.ndarray:
    """Texture colour (0..1 sRGB) at barycentric points of the given faces of a 3D Cities piece."""
    uv = (p.uv[p.faces[faces]] * bary[:, :, None]).sum(1)
    img = np.asarray(p.image, np.float32) / 255.0
    h, w = img.shape[:2]
    col = np.clip(uv[:, 0] * w, 0, w - 1).astype(int)
    row = np.clip((1 - uv[:, 1]) * h, 0, h - 1).astype(int)
    return img[row, col]


class FacadeSampler:
    """Wall colour per footprint wall from the 3D Cities mesh: mesh faces near the wall plane that face
    the same way, below the eave, sampled at a few points each (area weighted)."""

    def __init__(self, tiles) -> None:
        self.items = []
        for p in tiles:
            fv = p.vertices[p.faces]
            c = fv.mean(1)
            n = np.cross(fv[:, 1] - fv[:, 0], fv[:, 2] - fv[:, 0])
            area = 0.5 * np.linalg.norm(n, axis=1)
            n = n / np.maximum(2 * area[:, None], 1e-9)
            wall = (np.abs(n[:, 1]) < 0.55) & (area > 0.02)
            idx = np.flatnonzero(wall)
            self.items.append((p, idx, c[idx], n[idx], area[idx]))
        self.trees = [cKDTree(it[2][:, [0, 2]]) if len(it[1]) else None for it in self.items]

    def wall(self, a, b, nrm2, y0: float, y1: float, rng) -> np.ndarray:
        """Colour samples (k, 3) sRGB for the wall a-b (xz) with outward normal nrm2, heights y0..y1."""
        a, b = np.asarray(a, float), np.asarray(b, float)
        L = float(np.linalg.norm(b - a))
        if L < 1.0:
            return np.zeros((0, 3))
        t = (b - a) / L
        mid = (a + b) / 2
        out = []
        for (p, idx, c, n, area), tree in zip(self.items, self.trees, strict=True):
            if tree is None:
                continue
            cand = tree.query_ball_point(mid, L / 2 + 2.5)
            if not cand:
                continue
            cand = np.asarray(cand)
            d = c[cand][:, [0, 2]] - a
            s, off = d @ t, d @ nrm2
            nn = n[cand][:, [0, 2]] @ nrm2
            ok = (s > 0.2) & (s < L - 0.2) & (off > -1.6) & (off < 2.2) & (nn > 0.35)
            ok &= (c[cand][:, 1] > y0 + 0.3) & (c[cand][:, 1] < y1 - 0.2)
            sel = cand[ok]
            if not len(sel):
                continue
            k = np.clip((area[sel] / 0.15).astype(int), 1, 6)
            faces = np.repeat(idx[sel], k)
            bary = rng.dirichlet([2, 2, 2], size=len(faces))
            out.append(piece_colours(p, faces, bary))
        return np.concatenate(out) if out else np.zeros((0, 3))


def wall_style(samples: np.ndarray) -> dict | None:
    """Robust wall colour (sunlit-ish samples) and kind (brick vs siding) from facade samples."""
    if len(samples) < 12:
        return None
    lin = srgb_to_lin(samples)
    lum = lin @ [0.2126, 0.7152, 0.0722]
    lo, hi = np.percentile(lum, [45, 92])
    sel = (lum >= lo) & (lum <= hi)
    c = lin[sel].mean(0) if sel.sum() >= 4 else lin.mean(0)
    s = lin_to_srgb(c)
    s = np.clip(s.mean() + (s - s.mean()) * 1.25, 0, 1)  # oblique textures are hazy: give back some chroma
    c = srgb_to_lin(s)
    mx, mn = s.max(), s.min()
    sat = (mx - mn) / max(mx, 1e-3)
    hue_red = s[0] > s[1] * 1.06 and s[1] >= s[2] * 0.9
    kind = "brick" if (hue_red and sat > 0.16) else "siding"
    return {"lin": c.tolist(), "srgb": s.tolist(), "sat": float(sat), "kind": kind, "n": int(len(samples))}


# ---------------------------------------------------------------- materials


# 3D Cities facade textures are dark and yellow next to the Matterport facade photos of 6915
# (white balance per channel, linear): a mild blue lift toward the scanned colours
WALL_WB = np.array([1.0, 1.03, 1.2])
GABLE_SIDING = np.clip(srgb_to_lin(np.array([0.78, 0.74, 0.66])) / 0.75, 0, 1)
GROUND_MEAN = 0.6
# class -> (Poly Haven texture, tile size m, contrast)
GROUND_TEX = {
    "lawn": ("leafy_grass", 2.0, 1.0),
    "concrete": ("brushed_concrete", 2.0, 1.0),
    "beds": ("brown_mud_leaves_01", 1.3, 0.8),
    "pavers": ("concrete_pavement", 1.6, 1.0),
    "road": ("brushed_concrete", 3.0, 0.9),
}


def tiled_uv(tile: float):
    return lambda x, z: np.c_[np.asarray(x), np.asarray(z)] / tile


def road_tint(gc):
    """Road surface: the ortho's road colour (stains, tree shadows), desaturated and pulled toward plain
    concrete so a street never reads as lawn."""
    base = srgb_to_lin(np.array([0.62, 0.61, 0.58]))

    def f(x, z):
        c = srgb_to_lin(gc.colour("road", x, z) / 255.0)
        lum = (c @ [0.2126, 0.7152, 0.0722])[:, None]
        c = 0.3 * c + 0.7 * lum
        c = 0.6 * c + 0.4 * base * np.clip(lum / 0.3, 0.5, 1.2)
        return np.clip(c / GROUND_MEAN, 0, 1)

    return f


def class_tint(gc, name: str):
    """Vertex colour = linear low-frequency ortho colour / the texture's neutral mean."""
    return lambda x, z: np.clip(srgb_to_lin(gc.colour(name, x, z) / 255.0) / GROUND_MEAN, 0, 1)


def neutral_texture(path: Path, mean: float = 0.55, contrast: float = 1.0) -> Image.Image:
    """Tiled diffuse -> same pattern with a neutral grey mean (linear `mean`), so a vertex colour sets
    the real hue: final colour = vertex colour * texture."""
    a = srgb_to_lin(np.asarray(Image.open(path).convert("RGB"), np.float64) / 255.0)
    m = a.reshape(-1, 3).mean(0)
    a = a / m * mean
    if contrast != 1.0:
        a = mean + (a - mean) * contrast
    return Image.fromarray((lin_to_srgb(a) * 255 + 0.5).astype(np.uint8))


# ---------------------------------------------------------------- trees


def load_tree_variants() -> list[dict]:
    out = []
    for p in sorted(TREES.glob("live_oak_*.npz")):
        d = np.load(p)
        out.append(
            {
                "name": p.stem,
                "crown_base": float(d["crown_base"]),
                "crown_radius": float(d["crown_radius"]),
                "bark": (d["bark_pos"], d["bark_nrm"], d["bark_uv"], d["bark_idx"]),
                "leaf": (d["leaf_pos"], d["leaf_nrm"], d["leaf_uv"], d["leaf_idx"]),
                "source_h": float(d["source_height_m"]),
            }
        )
    return out


def crowns_from_dsm(nd, dtm_rel, X, Z, bmask) -> list[dict]:
    lab, sm = segment_crowns(nd, RES, bmask)
    out = []
    for li, sl in enumerate(ndimage.find_objects(lab), start=1):
        if sl is None:
            continue
        m = lab[sl] == li
        area = m.sum() * RES * RES
        if area < 3.0:
            continue
        h = float(sm[sl][m].max())
        if area < 7.0 and h > 6.0:
            continue  # pole or mast
        xs, zs = X[sl][m], Z[sl][m]
        w = np.clip(sm[sl][m], 0, None)
        cx, cz = float((xs * w).sum() / w.sum()), float((zs * w).sum() / w.sum())
        out.append(
            {"x": cx, "z": cz, "h": h, "r": math.sqrt(area / math.pi), "g": float(np.median(dtm_rel[sl][m]))}
        )
    return out


def tree_tint(ortho: Ortho, x: float, z: float, r: float) -> np.ndarray:
    """Crown colour from the ortho over the crown (sunlit part), as a multiplier for the leaf atlas."""
    rng = np.random.default_rng(int(abs(x * 31 + z * 17)) % 2**31)
    a = rng.uniform(0, 2 * math.pi, 60)
    rr = r * np.sqrt(rng.uniform(0, 0.8, 60))
    c = ortho.sample(x + rr * np.cos(a), z + rr * np.sin(a)) / 255.0
    lum = c @ [0.3, 0.5, 0.2]
    c = c[lum >= np.percentile(lum, 50)].mean(0)
    leaf_mean = np.array([0.482, 0.572, 0.381])  # mean of the opaque atlas texels (cache/trees report)
    tint = srgb_to_lin(c) / srgb_to_lin(leaf_mean)
    # the photo crown is darker than a lit leaf (self-shadowing is already in the model): lift it a bit
    # Winter capture: bare deciduous crowns read grey-brown and live oaks near black in the photo.
    # Keep the photo's relative variation but stay a plausible lit-foliage green.
    base = np.array([0.50, 0.55, 0.45])
    tint = 0.45 * np.clip(tint * 1.4, 0.2, 1.2) + 0.55 * base
    lum = float(tint @ [0.3, 0.5, 0.2])
    tint = tint * np.clip(lum, 0.42, 0.7) / max(lum, 1e-3)
    return np.clip(tint, 0.15, 1.0)


# ---------------------------------------------------------------- build


def build(args) -> dict:
    t0 = time.time()
    OUT.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(7)
    summary: dict = {}

    log("rasters")
    dsm_r = read_geotiff(next(bd.VX_CACHE.glob("dsm_extract_urban_*_z20.tif")))
    ortho = Ortho.load(next(bd.VX_CACHE.glob("ortho_extract_urban_*_z21.jpg")), bd.bbox_ll())
    dsm = resample_to_grid(dsm_r, HALF, RES)
    X, Z = grid_xz(HALF, RES)
    structs = [
        (p, pr)
        for p, pr in bpr.load_structures()
        if abs(p.centroid.x) < HALF - 4 and abs(p.centroid.y) < HALF - 4
    ]
    parcels = bpr.load_parcels()
    bmask = contains_xy(unary_union([p.buffer(1.0) for p, _ in structs]), X, Z)
    dtm = derive_dtm(dsm, RES, bmask)
    n0 = dtm.shape[0] // 2
    g0 = float(dtm[n0 - 1 : n0 + 1, n0 - 1 : n0 + 1].mean())
    ground = bpr.Ground(dtm, HALF, RES, g0)
    nd = dsm - dtm
    oc = ortho.sample(X.ravel(), Z.ravel()).reshape(X.shape + (3,))
    canopy = (oc[..., 1] > oc[..., 0] + 4) & (oc[..., 1] > oc[..., 2] + 4)
    canopy = ndimage.binary_dilation(ndimage.binary_opening(canopy, iterations=2), iterations=3)

    log("3D Cities tiles")
    tiles, mesh_g0, (core_x0, core_x1) = load_mesh_tiles()
    log(f"  {len(tiles)} tiles, x {core_x0:.1f}..{core_x1:.1f}, mesh ground {mesh_g0:.2f} m ellipsoidal")

    # ---- zones
    streets = named_streets()
    mask = near_zone(streets, parcels)
    square = box(-HALF, -HALF, HALF, HALF)
    core_inner = box(core_x0 + 0.6, -HALF, core_x1 - 0.6, HALF)
    proc_region = mask.union(square.difference(core_inner))
    near_b, mesh_b = [], []
    for poly, primary in structs:
        c = poly.centroid
        in_mesh = core_inner.contains(poly) and not proc_region.contains(c)
        (mesh_b if in_mesh else near_b).append((poly, primary))
    mesh_fp = unary_union([p.buffer(1.5) for p, _ in mesh_b])
    proc_fp = unary_union([p.buffer(1.0) for p, _ in near_b])
    P = proc_region.union(proc_fp).difference(mesh_fp).intersection(square).buffer(0)
    far_ground = square.difference(P).buffer(0)
    log(
        f"near zone {mask.area:.0f} m2; procedural region {P.area:.0f} m2; "
        f"mesh region {far_ground.area:.0f} m2"
    )
    log(f"buildings: {len(near_b)} procedural, {len(mesh_b)} 3D mesh")

    # ---- ground texture: ortho with tree crowns (and cars) painted out
    log("ground ortho (crowns painted out)")
    dsm_p = bp.Dsm(bp.DSM)
    gx, gz = np.meshgrid(np.linspace(-15, 15, 31), np.linspace(-15, 15, 31))
    disk = np.hypot(gx, gz).ravel() < 15
    dsm_g0 = float(np.percentile(dsm_p.sample(*bp.xz_to_lonlat(gx.ravel()[disk], gz.ravel()[disk])), 10))
    surf = bp.Surface(dsm_p, dsm_g0, [p for p, _ in structs])
    ortho_g = Ortho(np.asarray(surf.ortho_ground), ortho.mx0, ortho.my0, ortho.mx1, ortho.my1)

    # ---- materials
    roof_img = ortho.crop_xz(-HALF, -HALF, HALF, HALF, 4096).filter(
        ImageFilter.UnsharpMask(radius=2, percent=60, threshold=2)
    )
    images: dict = {
        "ortho_roof": roof_img,
        "fence": fence_texture(),
        "curb_tex": neutral_texture(PH / "brushed_concrete_diff_1k.jpg", 0.62, 0.4),
        "facade": facade_atlas(),
        "brick_n": neutral_texture(PH / "red_brick_diff_1k.jpg", 0.6),
        "brick_nor": PH / "red_brick_nor_1k.jpg",
        "brick3_n": neutral_texture(PH / "red_brick_03_diff_1k.jpg", 0.6),
        "brick3_nor": PH / "red_brick_03_nor_1k.jpg",
        "siding_n": neutral_texture(PH / "exterior_wall_cladding_diff_1k.jpg", 0.75, 1.6),
        "siding_nor": PH / "exterior_wall_cladding_nor_1k.jpg",
        "bark": TREES / "bark_diff.jpg",
        "bark_nor": TREES / "bark_nor.jpg",
        "leaf": Image.open(TREES / "leaf_rgba.png").convert("RGBA"),
    }
    mats = {
        "ground": Material("ground", texture="ortho_ground", unlit=True, sampler_wrap=33071),
        "road": Material("road", texture="ortho_ground", unlit=True, sampler_wrap=33071),
        # unlit like the ground it meets (lit, the face toward the road goes near black in shade)
        "curb": Material(
            "curb", texture="curb_tex", color=(0.78, 0.77, 0.74, 1), unlit=True, double_sided=True
        ),
        "curb_top": Material("curb_top", texture="curb_tex", color=(0.95, 0.94, 0.91, 1), unlit=True),
        "fence": Material("fence", texture="fence", color=(0.85, 0.85, 0.85, 1), roughness=0.95),
        "facade": Material("facade", texture="facade", roughness=0.55),
        "roof": Material("roof", texture="ortho_roof", roughness=0.85, sampler_wrap=33071),
        "flat_roof": Material("flat_roof", texture="ortho_roof", roughness=0.9, sampler_wrap=33071),
        "trim": Material("trim", color=(0.86, 0.84, 0.79, 1), roughness=0.7),
        "soffit": Material("soffit", color=(0.80, 0.78, 0.74, 1), roughness=0.8),
        "wall_brick": Material("wall_brick", texture="brick_n", normal_texture="brick_nor", roughness=0.9),
        "wall_brick3": Material(
            "wall_brick3", texture="brick3_n", normal_texture="brick3_nor", roughness=0.9
        ),
        "wall_siding": Material(
            "wall_siding", texture="siding_n", normal_texture="siding_nor", roughness=0.8
        ),
        "bark": Material("bark", texture="bark", normal_texture="bark_nor", roughness=1.0),
    }
    wall_tex_m = {"wall_brick": 1.4, "wall_brick3": 1.0, "wall_siding": 2.0}
    wall_mean = {"wall_brick": 0.6, "wall_brick3": 0.6, "wall_siding": 0.75}

    # ---- buildings (procedural region), wall colours from the 3D mesh
    log("buildings")
    sampler = FacadeSampler(tiles)
    roof_hint = [(s["poly"], s["shape"]) for pc in parcels for s in pc["structures"] if s["poly"] is not None]
    road_lines = [ln for ln, k in load_roads()[0] if k != "service"]
    bufs = {
        k: MeshBuf()
        for k in ["roof", "trim", "soffit", "flat_roof", "facade", "wall_brick", "wall_brick3", "wall_siding"]
    }
    hero_bufs: dict[str, dict[str, MeshBuf]] = {}
    buildings: list[Building] = []
    styles = {"brick": 0, "siding": 0, "default": 0}
    gain = args.wall_gain
    for i, (poly, primary) in enumerate(near_b):
        b = Building(poly=poly, primary=primary, name=f"b{i:03d}")
        iou = decompose(b)
        hint = next((sh for hp, sh in roof_hint if hp.intersection(poly).area > 0.5 * poly.area), None)
        if iou < 0.82 or hint == "flat" or poly.area > 600:
            b.kind = "flat"
        inner = poly.buffer(-0.6)
        sel = contains_xy(inner if not inner.is_empty else poly, X, Z)
        if (sel & ~canopy).sum() > 0.4 * sel.sum():
            sel &= ~canopy
        fit_roof(b, X[sel], Z[sel], nd[sel], hint)
        c = poly.centroid
        b.ground = float(ground(c.x, c.y)[0])
        for tag, (hx, hz) in HEROES.items():
            if poly.buffer(1.0).contains(Point(hx, hz)):
                b.tag = tag
        # facade colour from the 3D mesh
        top = b.ground + (b.flat_h if b.kind == "flat" else b.eave)
        samples = []
        ring = np.asarray(poly.exterior.coords)
        ccw = float(np.sum(ring[:-1, 0] * ring[1:, 1] - ring[1:, 0] * ring[:-1, 1])) > 0
        for k in range(len(ring) - 1):
            a, e = ring[k], ring[k + 1]
            L = float(np.hypot(*(e - a)))
            if L < 1.5:
                continue
            t = (e - a) / L
            n2 = np.array([t[1], -t[0]]) if ccw else np.array([-t[1], t[0]])
            samples.append(sampler.wall(a - n2 * OVERHANG, e - n2 * OVERHANG, n2, b.ground, top, rng))
        st = wall_style(np.concatenate(samples)) if samples else None
        b.style = st
        if st is not None:
            summary.setdefault("wall_samples", {})[b.name] = {
                "kind": st["kind"],
                "srgb": [round(v, 3) for v in st["srgb"]],
                "sat": round(st["sat"], 2),
                "n": st["n"],
            }
        buildings.append(b)
    # houses the 3D mesh does not cover (outside its tile columns) borrow a sampled neighbour's style
    sampled = [b.style for b in buildings if b.style is not None]
    for b in buildings:
        st = b.style
        if st is None:
            st = sampled[zlib.crc32(b.name.encode()) % len(sampled)] if sampled else None
            styles["default"] += 1
        else:
            styles[st["kind"]] += 1
        if st is None:
            wall, col = "wall_brick", np.array([0.2, 0.12, 0.08])
        else:
            wall = (
                "wall_siding"
                if st["kind"] == "siding"
                else ("wall_brick3" if st["sat"] > 0.3 else "wall_brick")
            )
            col = np.asarray(st["lin"]) * WALL_WB
        b.wall_mat, b.wall_col = wall, np.clip(col * gain / wall_mean[wall], 0, 1)
    log(f"  wall styles {styles} (default = borrowed from a sampled house)")

    # ---- sun of the ortho capture, then cars out of the ground photo
    sun = estimate_sun(ortho, buildings, ground) if args.sun else {"azimuth_deg": 205.0}
    cars = car_polys(sun["azimuth_deg"])
    cm = car_mask_px(ortho_g, cars)
    ortho_g = Ortho(inpaint(ortho_g.img, cm), ortho.mx0, ortho.my0, ortho.mx1, ortho.my1)
    log(f"cars painted out: {len(cars)}")
    summary["cars_removed"] = len(cars)
    ortho_img = ortho_g.crop_xz(-HALF, -HALF, HALF, HALF, 4096)
    images["ortho_ground"] = ortho_img.filter(ImageFilter.UnsharpMask(radius=2, percent=60, threshold=2))
    summary["wall_styles"] = styles

    # ---- roads, ground classes and paving traced from the ortho, before the facades (garage doors go
    # where the traced driveways meet the houses, front doors where the traced walks do)
    log("roads and paving")
    hways, hcircles = bh.load_ways()
    road, road_recs = bh.fit_roads(hways, hcircles, HALF, ortho, refit_names=set(NEAR_STREETS))
    summary["street_widths_m"] = {
        r["name"]: round(r["width"], 2) for r in road_recs if r["name"] and r["line"].intersects(mask)
    }
    exteriors = {}
    for spec in args.hero_exterior:
        for lid, path in resolve_exteriors(spec).items():
            exteriors[lid] = load_exterior(path, lid, ground, images, mats)
            log(
                f"hero exterior {lid}: {path.name}, {exteriors[lid]['triangles']} triangles, "
                f"dy {exteriors[lid]['dy']:+.2f} m"
            )
    skip_zone = unary_union([e["yard"] for e in exteriors.values()]) if exteriors else Polygon()
    built0 = unary_union([b.poly.buffer(-0.3) for b in buildings])
    crown_px = ndimage.binary_dilation((nd > 1.8) & ~bmask, iterations=2)

    def crown_fn(x, z):
        c = np.clip(((x + HALF) / RES).astype(int), 0, crown_px.shape[1] - 1)
        r = np.clip(((z + HALF) / RES).astype(int), 0, crown_px.shape[0] - 1)
        return crown_px[r, c]

    gc = GroundClasses(ortho_g, road, built0, crown_fn=crown_fn)
    tr = trace_hard(gc, road, road_recs, mask, parcels, buildings, skip_zone, summary)
    for b in buildings:
        target = bufs if not b.tag else hero_bufs.setdefault(b.tag, {k: MeshBuf() for k in bufs})
        emit_building(b, target, wall_tex_m, ground, road_lines, ortho, tr["hints"].get(b.name))

    # ---- ground
    log("ground")
    land_buf, road_buf, curb_buf, curb_top, far_buf = MeshBuf(), MeshBuf(), MeshBuf(), MeshBuf(), MeshBuf()
    # the lawn stops at the curb top: no overlap with curb, gutter or road
    near_land = areal(P.difference(road.buffer(bh.CURB_TOP_W, join_style=1)).difference(skip_zone))
    class_bufs: dict[str, MeshBuf] = {}
    hard_meshes: list = []
    curb_cut = None
    if args.ground_classes:
        cp = gc.polygons()
        images["g_concrete"] = neutral_texture(PH / "brushed_concrete_diff_1k.jpg", GROUND_MEAN, 1.0)
        hard, curb_cut, hard_meshes = hardscape(buildings, road, gc, ground, tr, images, mats, summary)
        summary["paving_iou"] = paving_iou(hard, gc, tr, parcels, mask)
        hs_now = {
            k: shapely.geometry.shape(v) for k, v in json.loads((OUT / "hardscape.json").read_text()).items()
        }
        built_all = unary_union([b.poly for b in buildings])
        summary["continuity"] = continuity(hs_now, built_all, road)
        log(f"  continuity: {_short(summary['continuity'])}")
        r4 = bd.CACHE.parent / "combined" / "hardscape_round4.json"
        if r4.exists():
            hs_r4 = {k: shapely.geometry.shape(v) for k, v in json.loads(r4.read_text()).items()}
            summary["continuity_round4"] = continuity(hs_r4, built_all, road)
            log(f"  continuity round 4: {_short(summary['continuity_round4'])}")
        iou_short = {k: v for k, v in summary["paving_iou"].items() if k != "lots"}
        log(f"  paving IoU per lot: {json.dumps(iou_short)}")
        lots = tr["lots"]
        # on house lots in the near zone, paving is vector only: raster concrete becomes lawn there
        for name in ("concrete", "pavers"):
            cp[name] = areal(cp[name].difference(lots))
        # the parkway (curb to sidewalk and 1 m beyond) is grass: no raster beds or photo patches there
        # the right of way (8 m from the curb) is grass outside the vector paving; raster beds on house
        # lots become lawn too (they rendered as pink mud patches)
        parkway = areal(road.buffer(8.0).intersection(mask).difference(road))
        for name in ("concrete", "pavers", "beds", "photo"):
            cp[name] = areal(cp[name].difference(parkway))
        cp["beds"] = areal(cp["beds"].difference(lots))
        rest = areal(near_land.difference(hard))
        areas = {}
        for name in ("concrete", "pavers", "beds", "photo"):
            reg = areal(cp[name].intersection(rest))
            rest = areal(rest.difference(reg))
            cp[name] = reg
        cp["lawn"] = rest
        for name in GROUND_CLASSES:
            cb = MeshBuf()
            reg = cp[name]
            areas[name] = round(reg.area)
            if name == "photo":
                gridded(cb, reg, ground, HALF, 2.0, 0.0, bpr.uv_ortho, 2.0)
            else:
                # finer tint vertices where people walk (near zone), coarser behind the lots
                for sub, step in (
                    (areal(reg.intersection(mask)), args.near_step),
                    (areal(reg.difference(mask)), args.ground_step),
                ):
                    gridded(
                        cb,
                        sub,
                        ground,
                        HALF,
                        step,
                        0.0,
                        tiled_uv(GROUND_TEX[name][1]),
                        2.0,
                        colorf=class_tint(gc, name),
                    )
            class_bufs[name] = cb
        gutter = bh.gutter_band(road)
        gridded(
            road_buf,
            areal(road.difference(gutter)),
            ground,
            HALF,
            args.near_step,
            -CURB,
            tiled_uv(GROUND_TEX["road"][1]),
            1.8,
            colorf=road_tint(gc),
        )
        gutter_buf = MeshBuf()
        gridded(
            gutter_buf,
            gutter,
            ground,
            HALF,
            args.near_step,
            -CURB + 0.004,
            tiled_uv(1.5),
            1.8,
            colorf=lambda x, z: np.tile(srgb_to_lin(np.array([0.70, 0.69, 0.66])) / GROUND_MEAN, (len(x), 1)),
        )
        class_bufs["gutter"] = gutter_buf
        for name, (tex, _tile, contrast) in GROUND_TEX.items():
            images[f"g_{name}"] = neutral_texture(PH / f"{tex}_diff_1k.jpg", GROUND_MEAN, contrast)
            mats[f"g_{name}"] = Material(f"g_{name}", texture=f"g_{name}", unlit=True, vertex_colors=True)
        summary["ground_class_m2"] = areas
        log(f"  ground classes m2: {areas}")
    else:
        gridded(land_buf, near_land, ground, HALF, 2.0, 0.0, bpr.uv_ortho, 2.0)
        gridded(road_buf, road, ground, HALF, 2.0, -CURB, bpr.uv_ortho, 1.8)
    bh.curb_mesh(curb_buf, curb_top, road, ground, CURB, skip=curb_cut, half=HALF)
    gridded(far_buf, far_ground.difference(road), ground, HALF, 2.0, 0.0, bpr.uv_ortho, 2.0)
    skirt = bpr.skirt_mesh()
    # underlay: a dark surface 0.2 m under the near ground, so no crack at a curb cut or slab edge
    # ever shows the sky
    under = MeshBuf()
    gridded(under, P, ground, HALF, 4.0, -CURB - 0.25, tiled_uv(4.0), 4.0)
    mats["underlay"] = Material("underlay", color=(0.30, 0.30, 0.29, 1), unlit=True)
    images["ortho_far"] = roof_img.resize((512, 512)).filter(ImageFilter.GaussianBlur(2.5))
    mats["skirt"] = Material(
        "skirt", texture="ortho_far", unlit=True, sampler_wrap=33648, color=(0.92, 0.92, 0.92, 1)
    )

    # ---- fences (everywhere: the 3D mesh's own fences are cut with the tree skirts)
    fence_buf, hero_fence = MeshBuf(), {t: MeshBuf() for t in HEROES}
    flines = fence_lines(parcels, buildings, road, road_lines)
    hero_lots = hero_parcels(parcels)
    plain = []
    for ln in flines:
        hit = next((t for t, lot in hero_lots.items() if ln.intersects(lot.buffer(0.5))), None)
        if hit:
            fence_mesh(hero_fence[hit], [ln], ground, HALF)
        else:
            plain.append(ln)
    fence_mesh(fence_buf, plain, ground, HALF)
    log(f"fences: {len(flines)} runs, {sum(ln.length for ln in flines):.0f} m")

    # ---- far zone: 3D Cities mesh
    log("far zone mesh")
    tops = []  # mesh houses: roof top above ground from the DSM (85th percentile inside the footprint)
    for poly, _ in mesh_b:
        sel = contains_xy(poly, X, Z)
        if sel.sum() > 8:
            tops.append((poly, float(np.percentile(nd[sel], 85))))
    far_nodes, far_tris = far_zone(tiles, surf, P, mesh_fp, proc_fp, mats, images, tops=tops)
    summary["far_mesh_triangles"] = far_tris

    # ---- trees (procedural region)
    log("trees")
    variants = load_tree_variants()
    crowns = crowns_from_dsm(
        nd, dtm - g0, X, Z, contains_xy(unary_union([p.buffer(0.6) for p, _ in structs]), X, Z)
    )
    tree_nodes, tree_info = place_trees(
        crowns,
        variants,
        square.difference(mesh_fp),
        road,
        buildings,
        hero_lots,
        ortho,
        ground,
        mats,
        rng,
        mesh_fp,
    )
    summary["trees"] = tree_info

    # ---- assemble
    roots = [
        Node(
            "terrain",
            mesh=[
                (land_buf, "ground"),
                *[
                    (cb, {"photo": "ground", "gutter": "g_concrete"}.get(name, f"g_{name}"))
                    for name, cb in class_bufs.items()
                ],
                *hard_meshes,
                (road_buf, "g_road" if args.ground_classes else "road"),
                (curb_buf, "curb"),
                (curb_top, "curb_top"),
                (far_buf, "ground"),
                (skirt, "skirt"),
                (under, "underlay"),
            ],
        ),
        Node("fences", mesh=[(fence_buf, "fence")]),
        Node("buildings", mesh=[(bufs[k], k) for k in bufs]),
        Node("far_zone", children=far_nodes, extras={"render_only": True, "source": "Vexcel 3D Cities"}),
        Node("trees", children=tree_nodes),
    ]
    lot_trees: dict[str, list[Node]] = {t: [] for t in HEROES}
    for nd_ in [n for n in tree_nodes if n.name.endswith("_hero_lot")]:
        x, z = nd_.matrix[12], nd_.matrix[14]
        tag = next((t for t, lot in hero_lots.items() if lot.contains(Point(x, z))), None)
        if tag:
            tree_nodes.remove(nd_)
            lot_trees[tag].append(nd_)
    for tag, hb in hero_bufs.items():
        lid = tag.removeprefix("hero_")
        ext = exteriors.get(lid)
        if ext is None:
            roots.append(
                Node(tag, mesh=[(hb[k], k) for k in hb], extras={"listing_id": lid, "source": "procedural"})
            )
            # fences and trees on the hero lot: a scanned exterior replaces them (see --hero-exterior)
            roots.append(
                Node(
                    f"lot_props_{lid}",
                    mesh=[(hero_fence[tag], "fence")],
                    children=lot_trees[tag],
                    extras={"listing_id": lid},
                )
            )
        else:
            keep = (
                []
                if ext["has_roof"]
                else [(hb["roof"], "roof"), (hb["trim"], "trim"), (hb["soffit"], "soffit")]
            )
            roots.append(
                Node(
                    tag,
                    mesh=keep,
                    children=ext["nodes"],
                    extras={"listing_id": lid, "source": f"matterport exterior {ext['file']}"},
                )
            )
    raw = OUT / "render_raw.glb"
    st = write_glb(raw, roots, mats, images)
    log(f"raw glb {st}")
    if args.compress:
        # tiled ground UVs span +-80: 12-bit texcoords smear the textures into streaks
        compress(
            raw, OUT / "render.glb", meshopt_args=["--quantize-texcoord", "16", "--quantize-position", "16"]
        )
        raw.unlink()
    else:
        raw.replace(OUT / "render.glb")
    size = (OUT / "render.glb").stat().st_size
    trunks = [t for t in tree_info.pop("trunks_for_collision", []) if P.contains(Point(t[0], t[1]))]
    ctris = write_collision(buildings, ground, P, plain, trunks)
    summary.update(
        {
            "render_glb_mb": round(size / 1e6, 2),
            "triangles_written": st["triangles"],
            "collision_triangles": ctris,
            "buildings_procedural": len(buildings),
            "buildings_3d_mesh": len(mesh_b),
            "near_zone_m2": round(mask.area),
            "procedural_region_m2": round(P.area),
            "mesh_region_m2": round(far_ground.area),
            "heroes": {b.tag: b.name for b in buildings if b.tag},
            "ground_elev_m": g0,
            "sun": sun,
            "build_s": round(time.time() - t0, 1),
        }
    )
    (OUT / "near_zone.json").write_text(json.dumps(shapely.geometry.mapping(mask)))
    (OUT / "build_summary.json").write_text(json.dumps(summary, indent=2))
    log(json.dumps({k: v for k, v in summary.items() if k != "trees"}, indent=2))
    return summary


def resolve_exteriors(spec: str) -> dict[str, Path]:
    """'LISTING=path.glb', or 'auto': every hero with out/cache/glb/<model>_exterior.glb (exterior.py,
    lane-a/outdoor). The glb is house-local (Y up, floor y = 0), placed by Listing.world."""
    if spec == "auto":
        out = {}
        for lid, w in HERO_WORLD.items():
            p = bd.WORLDGEN / "out" / "cache" / "glb" / f"{w['model']}_exterior.glb"
            if p.exists():
                out[lid] = p
        return out
    lid, _, path = spec.partition("=")
    return {lid: Path(path)}


def load_exterior(path: Path, lid: str, ground, images: dict, mats: dict, tex_px: int = 1024) -> dict:
    """A scanned hero exterior (yard, facades, roof, outdoor pieces, trees; the backdrop dome is
    dropped) placed into the block: block = position + rotY(heading_deg) * local, lifted so its yard
    meets the block ground. Photo-baked: unlit materials, textures down to tex_px."""
    import trimesh

    w = HERO_WORLD[lid]
    h = math.radians(w["heading_deg"])
    c, s_ = math.cos(h), math.sin(h)
    R = np.array([[c, 0, s_], [0, 1, 0], [-s_, 0, c]])
    px, pz = w["position"]
    scene = trimesh.load(path, force="scene")
    parts = []
    for node in scene.graph.nodes_geometry:
        if node.startswith("backdrop"):
            continue
        tf, gname = scene.graph[node]
        g = scene.geometry[gname].copy()
        g.apply_transform(tf)
        v = np.asarray(g.vertices) @ R.T + [px, 0.0, pz]
        parts.append((node, g, v))
    yard = [v for n, _, v in parts if n.startswith("yard")]
    if yard:
        yv = np.concatenate(yard)
        dy = float(np.median(ground(yv[:, 0], yv[:, 2]) - yv[:, 1]))
    else:
        dy = float(ground(px, pz)[0]) + 0.3
    nodes, tris, yard_polys = [], 0, []
    for i, (node, g, v) in enumerate(parts):
        v = v + [0, dy, 0]
        key = f"ext_{lid}_{i}"
        mat = getattr(g.visual, "material", None)
        img = getattr(mat, "baseColorTexture", None) if mat is not None else None
        uv = getattr(g.visual, "uv", None)
        buf = MeshBuf()
        buf.add(
            v,
            np.asarray(g.faces),
            uv=None if uv is None else np.c_[uv[:, 0], 1 - uv[:, 1]],
            normals=np.asarray(g.vertex_normals),
        )
        if img is not None:
            img = img.copy()
            img.thumbnail((tex_px, tex_px))
            images[key] = img
        mats[key] = Material(
            key,
            texture=key if img is not None else None,
            unlit=True,
            double_sided=not node.startswith("facade"),
            alpha_mask=0.5 if (img is not None and img.mode == "RGBA") else None,
        )
        nodes.append(Node(node, mesh=[(buf, key)]))
        tris += len(g.faces)
        if node.startswith("yard"):
            f = np.asarray(g.faces)
            tri = shapely.polygons(v[f][:, :, [0, 2]])
            yard_polys.append(shapely.union_all(shapely.make_valid(tri)))
    return {
        "nodes": nodes,
        "triangles": tris,
        "dy": dy,
        "file": path.name,
        "yard": unary_union(yard_polys).buffer(0.05) if yard_polys else Polygon(),
        "has_roof": any(n.startswith("roof") for n, _, _ in parts),
    }


def house_to_block(p, position, heading_deg: float) -> np.ndarray:
    """Listing.world: block = position + rotY(heading) * local (three.js rotation, costar.py)."""
    h = math.radians(heading_deg)
    c, s_ = math.cos(h), math.sin(h)
    return np.array([position[0] + p[0] * c + p[1] * s_, position[1] - p[0] * s_ + p[1] * c])


def _frac_lawn(lab_vis, poly, step: float = 0.25) -> tuple[float, float]:
    """(lawn fraction among visible ortho pixels, visible fraction) inside poly."""
    x0, z0, x1, z1 = poly.bounds
    gx, gz = np.meshgrid(np.arange(x0, x1, step), np.arange(z0, z1, step))
    ins = contains_xy(poly, gx, gz)
    if ins.sum() == 0:
        return 0.0, 0.0
    c = np.clip(((gx[ins] + HALF) / GRES).astype(int), 0, lab_vis.shape[1] - 1)
    r = np.clip(((gz[ins] + HALF) / GRES).astype(int), 0, lab_vis.shape[0] - 1)
    L = lab_vis[r, c]
    vis = L >= 0
    return (float((L[vis] == 0).mean()) if vis.any() else 0.0), float(vis.mean())


def _wall_contact(wp: Polygon, target, min_len: float = 2.2):
    """Longest run along an outer wall of `wp` whose outside (0.8 m out) lies in `target`:
    ((x, z) centre, length) or None."""
    from shapely.geometry.polygon import orient

    best = None
    ring = np.asarray(orient(wp, 1.0).exterior.coords)
    for i in range(len(ring) - 1):
        a, b = ring[i], ring[i + 1]
        L = float(np.hypot(*(b - a)))
        if L < min_len:
            continue
        t = (b - a) / L
        nrm = np.array([t[1], -t[0]])  # right of a CCW ring = outside
        s_ = np.arange(0.1, L - 0.05, 0.2)
        hit = np.zeros(len(s_), bool)
        for off in (0.8, 1.4, 2.2):  # the slab may stop short of the wall (eaves, shade)
            pts = a + s_[:, None] * t + off * nrm
            hit |= contains_xy(target, pts[:, 0], pts[:, 1])
        if not hit.any():
            continue
        lab_, n = ndimage.label(hit)
        for k in range(1, n + 1):
            idx = np.flatnonzero(lab_ == k)
            run = (idx[-1] - idx[0] + 1) * 0.2
            if run >= min_len and (best is None or run > best[1]):
                mid = a + t * s_[(idx[0] + idx[-1]) // 2]
                best = (mid, run)
    return best


def _hidden_drive(wp: Polygon, road, lab_vis, allowed, width: float = 5.0):
    """Fallback for a house the ortho shows no driveway for because shade or a crown hides it: on the
    wall facing the street, the 5 m wide strip to the curb that is least lawn-like; accepted only if
    the ortho shows almost no lawn in it (hidden or paved). Returns (polygon, ((x, z), width))."""
    from shapely.geometry.polygon import orient

    ring = np.asarray(orient(wp, 1.0).exterior.coords)
    best = None
    for i in range(len(ring) - 1):
        a, b = ring[i], ring[i + 1]
        L = float(np.hypot(*(b - a)))
        if L < width + 0.6:
            continue
        t = (b - a) / L
        nrm = np.array([t[1], -t[0]])
        mid = (a + b) / 2
        ray = LineString([mid + nrm * 0.1, mid + nrm * 30.0])
        hit = ray.intersection(road)
        if hit.is_empty:
            continue
        dist = min(Point(mid).distance(g) for g in getattr(hit, "geoms", [hit]))
        if dist > 25.0:
            continue
        for s0 in np.arange(0.3, L - width - 0.3 + 1e-6, 0.5):
            p0 = a + t * s0
            rect = Polygon([p0, p0 + t * width, p0 + t * width + nrm * dist, p0 + nrm * dist])
            lawn, vis = _frac_lawn(lab_vis, rect, 0.4)
            if lawn < 0.2 and (best is None or lawn < best[0]):
                best = (lawn, rect.intersection(allowed.buffer(0.05)), (p0 + t * width / 2, width))
    if best is None or best[1].is_empty:
        return None
    return best[1], best[2]


# a walk end "touches" a surface if its centreline end is within this distance (half a walk width plus
# slack: the band's end cap can meet a drive edge at an angle)
END_TOL = 1.2


def _short(d: dict) -> dict:
    return {k: v for k, v in d.items() if k not in ("dangling", "gaps")}


def continuity(hs: dict, built, road) -> dict:
    """Continuity check on the hard-surface polygons: sidewalk gaps = pairs of sidewalk piece ends
    closer than 30 m with no street between them; dangling walk ends = ends of a walk's centreline that
    touch no other hard surface, wall or curb."""
    walks = areal(unary_union([hs.get("walk", Polygon()), hs.get("pavers", Polygon())]))
    side = hs.get("sidewalk", Polygon())
    drive = hs.get("driveway", Polygon())
    stoop = hs.get("stoop", Polygon())
    base = unary_union([drive, side, stoop, road.buffer(0.25), built.buffer(0.35)])
    pieces = [p for p in polys_of(walks) if p.area >= 0.5]
    dangling, walk_ends = [], 0
    for i, p in enumerate(pieces):
        ln = bh.centreline(p)
        if ln is None:
            continue
        others = unary_union([base] + [q for k, q in enumerate(pieces) if k != i])
        for e in (Point(ln.coords[0]), Point(ln.coords[-1])):
            walk_ends += 1
            if others.distance(e) > END_TOL:
                dangling.append((round(e.x, 1), round(e.y, 1)))
    # sidewalk pieces are re-joined where a driveway crosses them before counting
    sw = areal(unary_union([side, drive.intersection(side.buffer(2.0))]).buffer(0.05))
    ends = []
    for p in polys_of(sw):
        if p.area < 2.0:
            continue
        ln = bh.centreline(p, step=0.4)
        if ln is None:
            continue
        c = np.asarray(ln.coords)
        for at, nb in ((0, min(5, len(c) - 1)), (-1, max(-6, -len(c)))):
            t = c[at] - c[nb]
            ends.append((Point(c[at]), id(p), t / max(np.linalg.norm(t), 1e-9)))
    gaps = []
    for i in range(len(ends)):
        for j in range(i + 1, len(ends)):
            a, pa, ta = ends[i]
            b, pb, tb = ends[j]
            dist = a.distance(b)
            if pa == pb or dist > 30 or dist < 1e-6:
                continue
            g = np.array([b.x - a.x, b.y - a.y]) / dist
            if ta @ g < 0.8 or tb @ (-g) < 0.8:
                continue  # not one walk continuing: corners, opposite sides
            if LineString([a, b]).intersection(road.buffer(-0.5)).length > 0.5:
                continue  # a street between them
            gaps.append(((round(a.x, 1), round(a.y, 1)), (round(b.x, 1), round(b.y, 1)), round(dist, 1)))
    return {
        "walk_ends": walk_ends,
        "dangling_walk_ends": len(dangling),
        "dangling": dangling[:20],
        "sidewalk_pieces": len([p for p in polys_of(sw) if p.area >= 2.0]),
        "sidewalk_gaps": len(gaps),
        "gaps": gaps[:20],
    }


def paving_iou(hard, gc, tr, parcels, mask) -> dict:
    """Objective check: per house lot in the near zone, IoU between our hardscape polygons and the
    ortho paving mask (bright concrete or red-brick pixels), over visible pixels outside roads, curbs
    and houses."""
    lab_vis = gc.lab_visible
    # scored only where the ortho can tell: visible, not in shade (shaded walks read as dark grey)
    seen = (lab_vis >= 0) & (gc.rgb.mean(-1) >= 70)
    omask = (gc.pale | gc.paver_px) & seen
    allowed = tr["allowed"]
    shapely.prepare(hard)
    out = []
    for pc in parcels:
        lot = pc["poly"]
        if lot.area >= 2500 or not mask.contains(lot.centroid):
            continue
        reg = lot.intersection(allowed)
        if reg.is_empty:
            continue
        x0, z0, x1, z1 = reg.bounds
        c0, c1 = int((x0 + HALF) / GRES), int((x1 + HALF) / GRES) + 1
        r0, r1 = int((z0 + HALF) / GRES), int((z1 + HALF) / GRES) + 1
        xs = -HALF + (np.arange(c0, c1) + 0.5) * GRES
        zs = -HALF + (np.arange(r0, r1) + 0.5) * GRES
        XX, ZZ = np.meshgrid(xs, zs)
        inside = contains_xy(reg, XX, ZZ) & seen[r0:r1, c0:c1]
        A = contains_xy(hard, XX, ZZ) & inside
        B = omask[r0:r1, c0:c1] & inside
        uni = (A | B).sum() * GRES * GRES
        if uni < 4.0:
            continue
        # tolerant IoU: the 0.4 m either side of the ortho mask's edge (blur, shadow fringe) is ignored
        Bf = omask[r0:r1, c0:c1]
        ring = ndimage.binary_dilation(Bf, iterations=2) & ~ndimage.binary_erosion(Bf, iterations=2)
        At, Bt = A & ~ring, B & ~ring
        ut = (At | Bt).sum()
        out.append(
            {
                "x": round(lot.centroid.x, 1),
                "z": round(lot.centroid.y, 1),
                "iou": round(float((A & B).sum() * GRES * GRES / uni), 3),
                "iou_tolerant": round(float((At & Bt).sum() / ut), 3) if ut else None,
                "union_m2": round(float(uni), 1),
            }
        )
    out.sort(key=lambda r: r["iou"])
    ious = [r["iou"] for r in out]
    tol = [r["iou_tolerant"] for r in out if r["iou_tolerant"] is not None]
    return {
        "lots": out,
        "n": len(out),
        "median": round(float(np.median(ious)), 3) if ious else None,
        "mean": round(float(np.mean(ious)), 3) if ious else None,
        "median_tolerant": round(float(np.median(tol)), 3) if tol else None,
        "worst": out[:5],
    }


def _comp_poly(m: np.ndarray, r0: int, c0: int) -> Polygon:
    """Pixels of a boolean sub-raster (top-left at row r0, col c0 of the GRES grid) -> polygon."""
    rows, starts, ends = [], [], []
    for r in range(m.shape[0]):
        d = np.diff(np.r_[0, m[r].astype(np.int8), 0])
        s_, e_ = np.flatnonzero(d == 1), np.flatnonzero(d == -1)
        rows.append(np.full(len(s_), r))
        starts.append(s_)
        ends.append(e_)
    r, s_, e_ = np.concatenate(rows), np.concatenate(starts), np.concatenate(ends)
    if not len(r):
        return Polygon()
    x0 = -HALF + c0 * GRES
    z0 = -HALF + r0 * GRES
    return shapely.union_all(shapely.box(x0 + s_ * GRES, z0 + r * GRES, x0 + e_ * GRES, z0 + (r + 1) * GRES))


def join_and_connect(lines, hard, road, built, doors, gc, lab_vis, hidden, tol: float = 3.5):
    """Walk centrelines -> continuous walks. (1) Join pieces whose ends are within `tol` m when the
    directions and widths agree (same material), into one smoothed polyline. (2) Each end must touch a
    hard surface (drive, sidewalk, curb, wall, another walk); if it does not, extend it along its
    direction (up to 4 m) or to the nearest hard surface (up to 3 m), but never across lawn the ortho
    clearly shows. (3) Walks still loose at an end, and stubs under 1.5 m, are dropped (beds, mulch).
    Hero front doors without a walk get a short connector to the nearest walk or drive."""

    def tangent(ln, at_start):
        c = np.asarray(ln.coords)
        if at_start:
            v = c[0] - c[min(3, len(c) - 1)]
        else:
            v = c[-1] - c[max(-4, -len(c))]
        return v / max(np.linalg.norm(v), 1e-9)

    def lawn_frac(geom):
        x0, z0, x1, z1 = geom.bounds
        gx, gz = np.meshgrid(np.arange(x0, x1 + 0.1, 0.2), np.arange(z0, z1 + 0.1, 0.2))
        ins = contains_xy(geom, gx, gz)
        if ins.sum() == 0:
            return 0.0
        c = np.clip(((gx[ins] + HALF) / GRES).astype(int), 0, lab_vis.shape[1] - 1)
        r = np.clip(((gz[ins] + HALF) / GRES).astype(int), 0, lab_vis.shape[0] - 1)
        rgb = gc.rgb[r, c]
        grass = (lab_vis[r, c] == 0) & (rgb[:, 1] >= rgb[:, 0] * 0.97) & ~hidden[r, c]
        return float(grass.mean())

    st = {"pieces_in": len(lines), "joins": 0, "extended": 0, "dropped": 0, "door_connectors": 0}
    merged = True
    while merged:
        merged = False
        for i in range(len(lines)):
            for j in range(len(lines)):
                if i == j or lines[i]["pav"] != lines[j]["pav"]:
                    continue
                A, B = lines[i], lines[j]
                if not (0.65 < A["w"] / B["w"] < 1.55):
                    continue
                best = None
                for ra in (False, True):  # use A's end (False) or start (True)
                    for rb in (False, True):  # use B's start (False) or end (True)
                        pa = np.asarray(A["ln"].coords[0 if ra else -1])
                        pb = np.asarray(B["ln"].coords[-1 if rb else 0])
                        gap = float(np.linalg.norm(pb - pa))
                        if gap > tol:
                            continue
                        ta = tangent(A["ln"], ra)
                        tb = tangent(B["ln"], not rb)  # direction leaving B's joined end, outward
                        if gap > 0.3:
                            g = (pb - pa) / gap
                            if ta @ g < 0.6 or (-tb) @ g < 0.6:
                                continue
                        elif ta @ (-tb) < 0.5:
                            continue
                        if best is None or gap < best[0]:
                            best = (gap, ra, rb)
                if best is None:
                    continue
                _, ra, rb = best
                ca = np.asarray(A["ln"].coords)[::-1] if ra else np.asarray(A["ln"].coords)
                cb = np.asarray(B["ln"].coords)[::-1] if rb else np.asarray(B["ln"].coords)
                xy = np.vstack([ca, cb])
                ln = LineString(xy)
                if len(xy) >= 5:  # open moving average, ends kept: a smooth join, no kink
                    dense = np.asarray(LineString(xy).segmentize(0.3).coords)
                    if len(dense) >= 7:
                        k_ = 7
                        sm = np.vstack(
                            [np.convolve(dense[:, c_], np.ones(k_) / k_, mode="valid") for c_ in range(2)]
                        ).T
                        ln = LineString(np.vstack([dense[:1], sm, dense[-1:]]))
                w = (A["w"] * A["ln"].length + B["w"] * B["ln"].length) / (A["ln"].length + B["ln"].length)
                lines[i] = {"ln": ln.simplify(0.03), "w": w, "pav": A["pav"]}
                del lines[j]
                st["joins"] += 1
                merged = True
                break
            if merged:
                break
    # hero doors without a walk: connector to the nearest walk or the drive/sidewalk
    for dp in doors:
        bands = [L_["ln"].buffer(L_["w"] / 2) for L_ in lines]
        if bands and min(b_.distance(dp) for b_ in bands) < 1.5:
            continue
        targets = bands + [g for g in polys_of(hard) if g.distance(dp) < 12]
        if not targets:
            continue
        tgt = min(targets, key=lambda g: g.distance(dp))
        if tgt.distance(dp) > 12:
            continue
        q = nearest_points(tgt, dp)[0]
        ln = LineString([dp.coords[0], q.coords[0]])
        col = gc.colour_raw(
            *np.asarray([ln.interpolate(t, normalized=True).coords[0] for t in np.linspace(0.2, 0.8, 7)]).T
        )
        cm = col.mean(0)
        lines.append({"ln": ln, "w": 1.0, "pav": bool(cm[0] > cm[2] * 1.1)})
        st["door_connectors"] += 1
    # hero doors also connect to their own driveway when it is close (the short branch most tract
    # homes have from the drive to the front door), unless a walk already joins them
    for dp in doors:
        near_d = [g for g in polys_of(hard) if 0.8 < g.distance(dp) < 8.0]
        if not near_d:
            continue
        drv = min(near_d, key=lambda g: g.distance(dp))
        q = nearest_points(drv, dp)[0]
        ln = LineString([dp.coords[0], q.coords[0]])
        if any(L_["ln"].buffer(L_["w"] / 2).intersection(ln).length > 0.6 * ln.length for L_ in lines):
            continue
        col = gc.colour_raw(
            *np.asarray([ln.interpolate(t, normalized=True).coords[0] for t in np.linspace(0.2, 0.8, 7)]).T
        )
        cm = col.mean(0)
        lines.append({"ln": ln, "w": 1.0, "pav": bool(cm[0] > cm[2] * 1.1)})
        st["door_connectors"] += 1
    # connect both ends of every walk to a hard surface
    wall = built.buffer(0.35)
    out = []
    for i, L_ in enumerate(lines):
        others = unary_union(
            [hard, road.buffer(0.25), wall]
            + [M["ln"].buffer(M["w"] / 2) for k, M in enumerate(lines) if k != i]
        )
        coords = list(np.asarray(L_["ln"].coords))
        ok = True
        for at_start in (True, False):
            pe = coords[0] if at_start else coords[-1]
            if others.distance(Point(pe)) < 0.6:
                continue
            t = tangent(LineString(coords), at_start)
            ray = LineString([pe, pe + t * 6.0])
            hit = ray.intersection(others)
            new = None
            if not hit.is_empty:
                new = np.asarray(nearest_points(hit, Point(pe))[0].coords[0])
            else:
                q = nearest_points(others, Point(pe))[0]
                if q.distance(Point(pe)) < 4.5:
                    new = np.asarray(q.coords[0])
            if new is not None and lawn_frac(LineString([pe, new]).buffer(L_["w"] / 2 * 0.8)) < 0.6:
                if at_start:
                    coords.insert(0, new)
                else:
                    coords.append(new)
                st["extended"] += 1
            else:
                ok = False
        ln = LineString(coords)
        if L_["pav"] and L_["w"] >= 1.45 and ln.length < 6.0:
            ok = False  # a short wide red band is a mulch bed, not a walk
        if not ok or ln.length < 1.5:
            st["dropped"] += 1
            continue
        out.append({"ln": ln, "w": L_["w"], "pav": L_["pav"]})
    st["walks_out"] = len(out)
    return out, st


def trace_hard(gc, road, recs, mask, parcels, buildings, skip_zone, summary) -> dict:
    """Paving fitted to the ortho before the facades are built (shape fitting, not outline tracing):
    sidewalk strips of constant width; driveways as oriented rectangles square to the street from the
    garage wall to the curb, with a flared apron; walks as constant-width bands along the smoothed
    centreline of the concrete/red-brick walk mask. Per house: where a driveway meets a wall (garage
    door, sized by the driveway width) and where a walk ends at a wall (front door)."""
    lab_vis = gc.lab_visible
    names = {r["name"] for r in recs if r["name"] and r["line"].intersects(mask)}
    shade = (gc.rgb.mean(-1) < 70) & (lab_vis >= 0)
    lab_sw = np.where(shade, -2, np.where(lab_vis >= 0, np.where(gc.pale, 1, 0), lab_vis)).astype(np.int8)
    strips, report = bh.find_sidewalks(recs, road, lab_vis, HALF, GRES, mask, names, width_lab=lab_sw)
    summary["sidewalk_real_ends"] = report.pop()["real_ends"]
    summary["sidewalks"] = report
    side_u = areal(unary_union([s_["poly"] for s_ in strips])) if strips else Polygon()
    built = unary_union([b.poly for b in buildings])
    side_u = areal(side_u.difference(road).difference(skip_zone).difference(built))
    ramps = [r_ for s_ in strips for r_ in s_.get("ramps", [])]
    allowed_poly = areal(mask.difference(road.buffer(bh.CURB_TOP_W)).difference(built).difference(skip_zone))
    lots = unary_union([pc["poly"] for pc in parcels if pc["poly"].area < 2500]).intersection(mask)
    Xc, Zc = grid_xz(HALF, GRES)
    allowed = contains_xy(allowed_poly, Xc, Zc)
    hidden = lab_vis == -2
    st3 = np.ones((3, 3), bool)

    def clean(m):
        m = m & allowed & ~hidden
        return ndimage.binary_closing(ndimage.binary_opening(m, st3), st3)

    pale_m, pav_m = clean(gc.pale), clean(gc.paver_px)
    # ---- driveways: oriented rectangles + apron
    # the concrete class too (shaded concrete is not "pale"); rectangles landing on lawn are rejected
    drive_m = clean(gc.pale | (lab_vis == 1))
    drives = bh.driveways_from_ortho(
        drive_m,
        recs,
        road,
        built,
        lots.difference(skip_zone),
        HALF,
        GRES,
        names,
        zone=mask.difference(skip_zone),
    )
    drives = [d for d in drives if _frac_lawn(lab_vis, d["poly"])[0] < 0.3]
    drive_u = (
        areal(unary_union([d["poly"] for d in drives] + [d["apron"] for d in drives]))
        if drives
        else Polygon()
    )
    # ---- walks: what is left of the paving mask, fitted to constant-width centreline bands
    # the 1 m beside a fitted sidewalk is its blurred edge or the curb band: never a separate walk
    excl = unary_union([drive_u.buffer(0.3), side_u.buffer(1.0)])
    rest = (pale_m | pav_m) & ~contains_xy(excl, Xc, Zc) if not excl.is_empty else (pale_m | pav_m)
    comp, n = ndimage.label(rest, structure=st3)
    walk_c, walk_p, patios, ends, pav_cols, lines = [], [], [], [], [], []
    for k, sl in enumerate(ndimage.find_objects(comp), start=1):
        if sl is None:
            continue
        m = comp[sl] == k
        if m.sum() * GRES * GRES < 1.0:
            continue
        poly = _comp_poly(m, sl[0].start, sl[1].start).buffer(0.1).buffer(-0.1)
        if poly.is_empty:
            continue
        paver = pav_m[sl][m].mean() > 0.5
        todo = list(polys_of(poly))
        for _round in range(3):  # main arc, then branches
            nxt = []
            for q in todo:
                if q.area < 1.0:
                    continue
                fw = bh.fit_walk(q)
                if fw is None:
                    if not paver and q.area >= 3.0 and q.distance(built) < 0.6:
                        patios.append(q.simplify(0.2).buffer(0.2, join_style=2).buffer(-0.2, join_style=2))
                    continue
                # material per walk from its mean ortho colour: brick reads pink-red even when faded
                x0_, z0_, x1_, z1_ = fw[0].bounds
                gx_, gz_ = np.meshgrid(np.arange(x0_, x1_, 0.2), np.arange(z0_, z1_, 0.2))
                ins_ = contains_xy(fw[0], gx_, gz_)
                cc_ = np.clip(((gx_[ins_] + HALF) / GRES).astype(int), 0, gc.rgb.shape[1] - 1)
                rr_ = np.clip(((gz_[ins_] + HALF) / GRES).astype(int), 0, gc.rgb.shape[0] - 1)
                okp = ~hidden[rr_, cc_] & (
                    (lab_vis[rr_, cc_] != 0) | pav_m[rr_, cc_]
                )  # walk pixels, not lawn
                col_ = gc.rgb[rr_[okp], cc_[okp]].mean(0) if okp.sum() > 5 else np.array([150.0, 148, 140])
                is_pav = paver or (col_[0] > col_[2] * 1.12 and col_[0] > col_[1] * 1.04)
                (walk_p if is_pav else walk_c).append(fw[0])
                lines.append({"ln": fw[1], "w": fw[2], "pav": bool(is_pav)})
                if is_pav:
                    pav_cols.append(col_)
                ends.extend([Point(fw[1].coords[0]), Point(fw[1].coords[-1])])
                nxt.extend(polys_of(q.difference(fw[0].buffer(0.25))))
            todo = nxt
    # one continuous band per walk: join pieces split by shade, connect both ends, drop beds and stubs
    hero_doors = []
    for b in buildings:
        if b.tag and mask.contains(b.poly.centroid) and not skip_zone.intersects(b.poly):
            w_ = HERO_WORLD[b.tag.removeprefix("hero_")]
            hero_doors.append(Point(*house_to_block(w_["door"], w_["position"], w_["heading_deg"])))
    lines, wstats = join_and_connect(
        lines, unary_union([side_u, drive_u]), road, built, hero_doors, gc, lab_vis, hidden
    )
    summary["walk_joining"] = wstats
    walk_c = [
        L_["ln"].buffer(L_["w"] / 2, cap_style=2, join_style=1, quad_segs=6) for L_ in lines if not L_["pav"]
    ]
    walk_p = [
        L_["ln"].buffer(L_["w"] / 2, cap_style=2, join_style=1, quad_segs=6) for L_ in lines if L_["pav"]
    ]
    walk_c += [q for q in patios if q.distance(built) < 0.8]
    ends = [Point(L_["ln"].coords[i]) for L_ in lines for i in (0, -1)]
    pav = areal(unary_union(walk_p).difference(drive_u)) if walk_p else Polygon()
    # brick colour from the clearly red paver pixels (the walk means include faded and grey bricks)
    strict = pav_m & contains_xy(lots, Xc, Zc) if not lots.is_empty else pav_m
    summary["paver_colour_srgb"] = (
        [round(float(v), 1) for v in np.median(gc.rgb[strict], 0)] if strict.sum() > 50 else None
    )
    walks_c = areal(unary_union(walk_c).difference(drive_u).difference(pav)) if walk_c else Polygon()
    # walks stop at the sidewalk edge (one surface per spot: no overlap, no z-fighting)
    walls = unary_union([b.poly.buffer(-OVERHANG, join_style=2) for b in buildings])
    walks_c = areal(walks_c.difference(side_u).difference(walls))
    pav = areal(pav.difference(side_u).difference(walls))
    # final check on the clipped shapes: every walk piece must touch hard surfaces at both ends of its
    # centreline (clipping can leave a loose stub); loose pieces go, repeated until stable
    base = unary_union([side_u, drive_u, road.buffer(0.25), built.buffer(0.35)])
    for _ in range(4):
        pieces = [(q, "c") for q in polys_of(walks_c)] + [(q, "p") for q in polys_of(pav)]
        loose = set()
        for i, (q, _k) in enumerate(pieces):
            ln = bh.centreline(q)
            if ln is None or ln.length < 1.5:
                loose.add(i)
                continue
            others = unary_union([base] + [r for k, (r, _kk) in enumerate(pieces) if k != i])
            if any(others.distance(Point(ln.coords[e])) > END_TOL for e in (0, -1)):
                loose.add(i)
        if not loose:
            break
        walks_c = areal(unary_union([q for i, (q, k) in enumerate(pieces) if k == "c" and i not in loose]))
        pav = areal(unary_union([q for i, (q, k) in enumerate(pieces) if k == "p" and i not in loose]))
        summary.setdefault("walk_pieces_dropped_loose", 0)
        summary["walk_pieces_dropped_loose"] += len(loose)
    # ---- per house: garage where a fitted drive ends at the wall, door where a walk ends at the wall
    hints, n_gar, n_door, bridges, n_fb = {}, 0, 0, [], 0
    walk_all = unary_union([walks_c, pav])
    for b in buildings:
        if not b.primary or not mask.contains(b.poly.centroid) or skip_zone.intersects(b.poly):
            continue
        wp = b.poly.buffer(-OVERHANG, join_style=2) if b.poly.area > 40 else b.poly
        if wp.is_empty or wp.geom_type != "Polygon":
            continue
        h = {}
        best = None
        for d in drives:
            end = d["p"] + d["t"] * (d["v0"] + d["v1"]) / 2  # the drive's house end, centred
            dist = wp.exterior.distance(Point(end))
            if dist < 2.5 and (best is None or dist < best[0]):
                best = (dist, end, d)
        if best is not None:
            d = best[2]
            width = d["v1"] - d["v0"]
            h["garage_at"] = (best[1], 2.7 if width < 4.2 else 4.9)
            n_gar += 1
        elif b.kind != "flat":
            fb = _hidden_drive(wp, road, lab_vis, allowed_poly)
            if fb is not None:
                bridges.append(fb[0])
                h["garage_at"] = (fb[1][0], 4.9)
                n_fb += 1
        if b.tag:
            w = HERO_WORLD[b.tag.removeprefix("hero_")]
            h["door_at"] = house_to_block(w["door"], w["position"], w["heading_deg"])
        else:
            near = [e for e in ends if wp.exterior.distance(e) < 1.8 and walk_all.distance(e) < 0.3]
            if near:
                e = min(near, key=lambda q: wp.exterior.distance(q))
                h["door_at"] = np.asarray(nearest_points(wp.exterior, e)[0].coords[0])
                n_door += 1
        hints[b.name] = h
    drives_u = areal(unary_union([drive_u, *bridges])) if bridges else drive_u
    walks_c = areal(unary_union([walks_c, *patios]).difference(drives_u)) if patios else walks_c
    summary["traced"] = {
        "driveways_fitted": len(drives),
        "driveways_heuristic_under_shade": n_fb,
        "garage_doors_at_fitted_drives": n_gar,
        "walks_concrete": len(walk_c),
        "walks_pavers": len(walk_p),
        "patios": len(patios),
        "front_doors_at_walk_ends": n_door,
    }
    log(f"  traced {summary['traced']}")
    return {
        "strips": strips,
        "ramps": areal(unary_union(ramps)) if ramps else Polygon(),
        "side": side_u,
        "drives": drives_u,
        "walks": walks_c,
        "pav": pav,
        "hints": hints,
        "lots": lots,
        "allowed": allowed_poly,
    }


def hardscape(buildings, road, gc, ground, tr, images, mats, summary):
    """Meshes for the traced paving plus fallbacks: a garage without a traced drive gets the straight
    rectangle drive, a front door without paving at its stoop gets the heuristic walk, both only where
    the ortho does not show lawn. Returns (hard union, curb cut zone, [(MeshBuf, material)])."""
    lab_vis = gc.lab_visible
    built = unary_union([b.poly.buffer(-0.1) for b in buildings])
    side_u, drives_u, walks_c, pav = tr["side"], tr["drives"], tr["walks"], tr["pav"]
    fb_drives, stoops, fb_walks = [], [], []
    for b in buildings:
        if b.name not in tr["hints"]:
            continue
        g = getattr(b, "garage", None)
        if g is not None and drives_u.distance(Point(*g["p"])) > 1.5:
            d = bh.driveway(g, road, lab_vis, HALF, GRES)
            if d is not None:
                rect = bh._rect(d["p"], d["n"], d["t"], 0.0, d["L"], -g["w"] / 2 - 0.15, g["w"] / 2 + 0.15)
                lawn, _vis = _frac_lawn(lab_vis, rect)
                if lawn < 0.3 and not rect.buffer(-0.6).intersects(built.difference(b.poly.buffer(0.5))):
                    fb_drives.append(rect)
    all_drives = areal(unary_union([drives_u, *fb_drives])) if fb_drives else drives_u
    paved = unary_union([side_u, all_drives, walks_c, pav])
    for b in buildings:
        if b.name not in tr["hints"] or not getattr(b, "door", None):
            continue
        st = bh.stoop(b.door)
        stoops.append(st["poly"])
        front = Point(*(np.asarray(b.door["p"]) + np.asarray(b.door["n"]) * 1.4))
        if paved.distance(front) < 1.2:
            continue  # the ortho's paving already reaches the door
        near = [p for p in polys_of(all_drives) if p.distance(front) < 14.0]
        drv = None
        if near:
            pn = min(near, key=lambda p: p.distance(front))
            drv = {"poly": pn}
        ln = bh.front_walk(b.door, drv, side_u, road, lab_vis, HALF, GRES)
        if ln is None:
            continue
        wp = ln.buffer(0.5, cap_style=2, join_style=2)
        lawn, _vis = _frac_lawn(lab_vis, wp)
        if lawn < 0.3 and not wp.intersects(built.buffer(-0.5)):
            fb_walks.append(wp)
    stoop_u = areal(unary_union(stoops)) if stoops else Polygon()
    walk_fb = (
        areal(
            unary_union(fb_walks)
            .difference(all_drives)
            .difference(stoop_u)
            .difference(built)
            .difference(road)
        )
        if fb_walks
        else Polygon()
    )
    walks_c = areal(walks_c.difference(stoop_u).difference(all_drives))
    pav = areal(pav.difference(stoop_u).difference(all_drives))
    # drives reach over the curb band to the road: that is the curb cut
    mouth = all_drives.buffer(0.4).intersection(road.buffer(bh.CURB_TOP_W + 0.02)).difference(road)
    drive_full = areal(unary_union([all_drives, mouth]).difference(road).difference(built))
    # the driveway crosses the sidewalk flush (same height, the drive's joints win there)
    side_u = areal(side_u.difference(drive_full))
    ramps = areal(tr["ramps"].difference(road)) if not tr["ramps"].is_empty else Polygon()
    cut = unary_union([drive_full.buffer(0.05), ramps.buffer(0.05)]).intersection(
        road.buffer(bh.CURB_TOP_W + 0.1)
    )
    if not walk_fb.is_empty:  # fallback walks too must touch hard surfaces at both ends
        base = unary_union([side_u, drive_full, road.buffer(0.25), built.buffer(0.35), stoop_u, walks_c, pav])
        keep = []
        for q in polys_of(walk_fb):
            ln = bh.centreline(q)
            if ln is not None and all(base.distance(Point(ln.coords[e])) <= END_TOL for e in (0, -1)):
                keep.append(q)
        walk_fb = areal(unary_union(keep)) if keep else Polygon()
    hard = areal(unary_union([side_u, drive_full, walks_c, pav, walk_fb, stoop_u]))
    summary["hardscape_m2"] = {
        "sidewalk": round(side_u.area),
        "driveway_traced": round(drives_u.area),
        "driveway_fallback": len(fb_drives),
        "walk_traced_concrete": round(walks_c.area),
        "walk_traced_pavers": round(pav.area),
        "walk_fallback": len(fb_walks),
        "stoops": len(stoops),
    }
    log(f"  hardscape {summary['hardscape_m2']}")
    (OUT / "hardscape.json").write_text(
        json.dumps(
            {
                k: shapely.geometry.mapping(g)
                for k, g in (
                    ("road", road),
                    ("sidewalk", side_u),
                    ("driveway", drive_full),
                    ("walk", unary_union([walks_c, walk_fb])),
                    ("pavers", pav),
                    ("stoop", stoop_u),
                )
            }
        )
    )

    base = srgb_to_lin(np.array([0.74, 0.73, 0.70]))

    def conc_tint(x, z):
        c = srgb_to_lin(gc.colour("concrete", x, z) / 255.0)
        lum = (c @ [0.2126, 0.7152, 0.0722])[:, None]
        c = 0.35 * c + 0.65 * lum
        c = 0.5 * c + 0.5 * base * np.clip(lum / 0.35, 0.55, 1.15)
        return np.clip(c / GROUND_MEAN, 0, 1)

    ps = np.asarray(summary.get("paver_colour_srgb") or [168, 120, 100], float) / 255.0
    ps = np.clip(ps.mean() + (ps - ps.mean()) * 1.3, 0, 1)  # the ortho greys brick out: give back chroma
    pcol = srgb_to_lin(ps)

    def paver_tint(x, z):
        # one brick colour (median of the traced paver walks), shaded by the local ortho luminance so
        # tree shade stays; the lawn next to a walk never leaks into it
        c = srgb_to_lin(gc.colour_raw(x, z) / 255.0)
        lum = c @ [0.2126, 0.7152, 0.0722]
        ref = float(pcol @ [0.2126, 0.7152, 0.0722])
        k = np.clip(lum / max(ref, 1e-3), 0.6, 1.1)[:, None]
        return np.clip(pcol[None, :] * k * 1.1 / GROUND_MEAN, 0, 1)

    neutral = neutral_texture(PH / "brushed_concrete_diff_1k.jpg", GROUND_MEAN, 0.9)
    images["conc_joint"] = bh.joint_texture(neutral)
    images["conc_grid"] = bh.joint_texture(neutral, grid=True)
    images["pavers"] = neutral_texture(PH / "concrete_pavement_diff_1k.jpg", GROUND_MEAN, 1.1)
    for k_, tex in (
        ("hs_sidewalk", "conc_joint"),
        ("hs_drive", "conc_grid"),
        ("hs_plain", "g_concrete"),
        ("hs_pavers", "pavers"),
    ):
        mats[k_] = Material(k_, texture=tex, unlit=True, vertex_colors=True)
    top_s, top_d, top_p, top_b, edge = MeshBuf(), MeshBuf(), MeshBuf(), MeshBuf(), MeshBuf()
    shapely.prepare(road)
    ramp_zone = ramps.buffer(0.3) if not ramps.is_empty else Polygon()
    shapely.prepare(ramp_zone)

    def side_h(x, z):
        # curb ramps: inside a ramp the walk slopes down to the road over the last 1.2 m
        pts = shapely.points(np.asarray(x, float), np.asarray(z, float))
        d = shapely.distance(road, pts)
        inr = shapely.contains(ramp_zone, pts) if not ramp_zone.is_empty else np.zeros(len(d), bool)
        ramp = np.where(inr, np.clip(1.0 - d / 1.2, 0, 1), 0.0)
        return bh.SIDEWALK_H - ramp * (bh.SIDEWALK_H + CURB)

    drawn = Polygon()
    for s_ in tr["strips"]:
        # strips of neighbouring ways overlap where they meet: draw each patch of sidewalk once
        reg = areal(s_["poly"].intersection(side_u).difference(drawn))
        drawn = drawn.union(s_["poly"])
        if not reg.is_empty:
            bh.strip_mesh(
                top_s, edge, reg, ground, side_h, bh.line_uv(s_["line"], bh.JOINT_SIDEWALK), conc_tint
            )
    shapely.prepare(road)

    def drive_h(x, z):
        d = shapely.distance(road, shapely.points(np.asarray(x, float), np.asarray(z, float)))
        ramp = np.clip(1.0 - (d - bh.CURB_TOP_W * 0) / 0.9, 0, 1)
        return bh.DRIVE_H - ramp * (bh.DRIVE_H + CURB)

    bh.strip_mesh(top_d, edge, drive_full, ground, drive_h, tiled_uv(bh.JOINT_DRIVE), conc_tint)
    bh.strip_mesh(
        top_p, edge, areal(unary_union([walks_c, walk_fb])), ground, bh.DRIVE_H, tiled_uv(2.0), conc_tint
    )
    bh.strip_mesh(top_b, edge, pav, ground, bh.DRIVE_H, tiled_uv(1.6), paver_tint)
    bh.strip_mesh(top_p, edge, stoop_u, ground, bh.STOOP_H, tiled_uv(2.0), conc_tint)
    meshes = [
        (top_s, "hs_sidewalk"),
        (top_d, "hs_drive"),
        (top_p, "hs_plain"),
        (top_b, "hs_pavers"),
        (edge, "hs_plain"),
    ]
    return hard, cut, meshes


def hero_parcels(parcels) -> dict[str, Polygon]:
    out = {}
    for tag, (hx, hz) in HEROES.items():
        pt = Point(hx, hz)
        cands = [pc["poly"] for pc in parcels if pc["poly"].contains(pt) and pc["poly"].area < 2500]
        if cands:
            out[tag] = min(cands, key=lambda p: p.area)
    return out


def emit_building(b: Building, bufs, wall_tex_m, ground, road_lines, ortho, hints=None) -> None:
    y0 = b.ground
    wall, col = b.wall_mat, np.r_[b.wall_col, 1.0]
    tex_m = wall_tex_m[wall]
    if b.kind == "flat":
        top = y0 + b.flat_h
        for p in polys_of(b.poly):
            outer_walls(bufs[wall], p, y0 - 0.3, top, tex_m, col)
            flat_roof(bufs["flat_roof"], p, top, bpr.uv_ortho)
        return
    ov = OVERHANG if b.poly.area > 40 else 0.15
    wp = b.poly.buffer(-ov, join_style=2)
    e = y0 + b.eave
    rng = np.random.default_rng(zlib.crc32(b.name.encode()))
    hints = hints or {}
    door_at, garage_at = hints.get("door_at"), hints.get("garage_at")
    if b.tag and door_at is None:
        w = HERO_WORLD[b.tag.removeprefix("hero_")]
        door_at = house_to_block(w["door"], w["position"], w["heading_deg"])
    b.garage, b.door = None, None
    for p in polys_of(wp):
        outer_walls(bufs[wall], p, y0 - 0.3, e - FASCIA, tex_m, col)
        if b.primary and p.area > 30:
            res = place_openings(
                bufs["facade"],
                p,
                y0,
                e - FASCIA,
                road_lines,
                ortho,
                b.eave > 4.5,
                rng,
                door_at=door_at,
                garage_at=garage_at,
            )
            b.garage = b.garage or res.get("garage")
            b.door = b.door or res.get("door")
    for p in polys_of(b.poly):
        outer_walls(bufs["trim"], p, e - FASCIA, e + 0.02, 1.0)
    horizontal(bufs["soffit"], b.poly.difference(wp), e - FASCIA, up=False)
    roof_mesh(bufs["roof"], b, bpr.uv_ortho, y0)
    # Houston tract homes: brick walls, painted lap siding in the gables
    gable = np.r_[GABLE_SIDING, 1.0] if wall != "wall_siding" else col
    gable_ends(bufs["wall_siding"], b, y0, 2.0, gable)


def far_zone(
    tiles, surf, P, mesh_fp, proc_fp, mats, images, keep_crowns=False, tops=()
) -> tuple[list[Node], int]:
    """3D Cities faces outside the procedural region. Mesh buildings keep all their faces; everything
    else (crowns) only if no vertex is inside the procedural region and it is not a low skirt."""
    shapely.prepare(P)
    shapely.prepare(mesh_fp)
    shapely.prepare(proc_fp)
    nodes, tris = [], 0
    for p in tiles:
        fv = p.vertices[p.faces]
        c = fv.mean(1)
        keep = (np.abs(c[:, 0]) <= HALF) & (np.abs(c[:, 2]) <= HALF)
        bld = contains_xy(mesh_fp, c[:, 0], c[:, 2]) & ~contains_xy(proc_fp, c[:, 0], c[:, 2])
        inP = np.zeros(len(c), bool)
        for k in range(3):
            inP |= contains_xy(P, fv[:, k, 0], fv[:, k, 2])
        hag = fv[:, :, 1] - surf.ground_y(fv[:, :, 0].ravel(), fv[:, :, 2].ravel()).reshape(-1, 3)
        low = hag.min(axis=1) < bp.TREE_CUT_M
        # crowns of the 2023 mesh are faceted shells cut at 3.4 m: model trees replace them everywhere
        keep &= bld if not keep_crowns else bld | (~inP & ~low)
        keep &= ~(~bld & (c[:, 1] < 0.3))  # mesh ground: replaced by the ortho ground layer
        # tree crowns hanging over mesh houses (green faces above 2 m): gone, model trees stand there
        col = piece_colours(p, np.arange(len(c)), np.full((len(c), 3), 1 / 3))
        green = (col[:, 1] > col[:, 0] - 0.01) & (col[:, 1] > col[:, 2] + 0.04)  # olive crowns too
        keep &= ~(bld & green & (hag.min(axis=1) > 2.0))
        # and anything above the house's DSM roof, or hanging outside its eaves above 2.2 m
        hc = hag.mean(axis=1)
        for poly, top in tops:
            near = contains_xy(poly.buffer(1.5), c[:, 0], c[:, 2])
            if not near.any():
                continue
            out_eave = ~contains_xy(poly.buffer(0.6), c[:, 0], c[:, 2])
            keep &= ~(near & ((hc > top + 0.4) | (out_eave & (hc > 2.2))))
        sp = bp.submesh(p, keep, p.name)
        if sp is None:
            continue
        buf = MeshBuf()
        uv = np.c_[sp.uv[:, 0], 1.0 - sp.uv[:, 1]]
        buf.add(sp.vertices, sp.faces, uv=uv, normals=np.array([0, 1, 0]))
        key = f"tile_{p.name[-13:]}"
        images[key] = sp.image
        mats[key] = Material(key, texture=key, unlit=True, double_sided=True, sampler_wrap=33071)
        nodes.append(Node(key, mesh=[(buf, key)]))
        tris += len(sp.faces)
    return nodes, tris


def place_trees(crowns, variants, P, road, buildings, hero_lots, ortho, ground, mats, rng, blocked=None):
    """One instanced live-oak per DSM crown in the procedural region: variant by crown shape, scaled
    to crown height and radius, tinted from the ortho (3 tint classes per variant)."""
    if not variants:
        return [], {"count": 0}
    shapely.prepare(P)
    blds = unary_union([b.poly.buffer(0.6) for b in buildings] + ([blocked] if blocked is not None else []))
    lots = unary_union(list(hero_lots.values())) if hero_lots else Polygon()
    road_in = road.buffer(-0.3)
    placed = []
    for cr in crowns:
        x, z, h, r = cr["x"], cr["z"], cr["h"], cr["r"]
        if not P.contains(Point(x, z)) or h < 3.0:
            continue
        pt = Point(x, z)
        # trunks never stand in the road or in a house: move to the nearest free spot under the crown
        for zone in (road_in, blds):
            if zone.contains(pt):
                q = nearest_points(zone.boundary, pt)[0]
                d = np.array([q.x - pt.x, q.y - pt.y])
                d = d / max(np.linalg.norm(d), 1e-6)
                pt = Point(q.x + d[0] * 0.8, q.y + d[1] * 0.8)
        if pt.distance(Point(x, z)) > max(r, 3.0) or road_in.contains(pt) or blds.contains(pt):
            continue
        tx, tz = pt.x, pt.y
        ratio = r / h
        # the variant whose crown shape fits best is the likeliest, but not the only one (variety;
        # the non-uniform scale absorbs the rest)
        d = np.array([math.log(var_["crown_radius"] / max(ratio, 0.1)) for var_ in variants])
        w = np.exp(-(d**2) / 0.35)
        v = int(rng.choice(len(variants), p=w / w.sum()))
        var = variants[v]
        sy = h * rng.uniform(0.97, 1.05)
        sxz = float(np.clip(r / var["crown_radius"], 0.6 * sy, 1.7 * sy))
        tint = tree_tint(ortho, x, z, r)
        lum = float(tint @ [0.3, 0.5, 0.2])
        tclass = 0 if lum < 0.42 else (1 if lum < 0.6 else 2)
        placed.append((v, tclass, tx, tz, sy, sxz, rng.uniform(0, 360), tint, lots.contains(pt)))
    # tint per class = mean of its members
    nodes = []
    meshes: dict[tuple[int, int], tuple[MeshBuf, MeshBuf]] = {}
    for v, tc, *_ in placed:
        if (v, tc) not in meshes:
            bp_, bn, bu, bi = variants[v]["bark"]
            lp, ln_, lu, li = variants[v]["leaf"]
            bark, leaf = MeshBuf(), MeshBuf()
            bark.add(bp_, bi, uv=bu, normals=bn)
            leaf.add(lp, li, uv=lu, normals=ln_)
            meshes[(v, tc)] = (bark, leaf)
    for v, tc in meshes:
        tints = np.array([p[7] for p in placed if p[0] == v and p[1] == tc])
        t = tints.mean(0)
        key = f"leaves_{v}_{tc}"
        mats[key] = Material(key, texture="leaf", color=(*t, 1.0), roughness=0.95, alpha_mask=0.5)
    hero_count = 0
    for i, (v, tc, x, z, sy, sxz, yaw, _t, in_lot) in enumerate(placed):
        bark, leaf = meshes[(v, tc)]
        a = math.radians(yaw)
        c, s = math.cos(a), math.sin(a)
        gy = float(ground(x, z)[0])
        m = np.array(
            [[c * sxz, 0, s * sxz, x], [0, sy, 0, gy - 0.05], [-s * sxz, 0, c * sxz, z], [0, 0, 0, 1]], float
        )
        nodes.append(
            Node(
                f"tree_{i:03d}" + ("_hero_lot" if in_lot else ""),
                mesh=[(bark, "bark"), (leaf, f"leaves_{v}_{tc}")],
                mesh_key=f"tree_{v}_{tc}",
                matrix=m.T.ravel().tolist(),
            )
        )
        hero_count += int(in_lot)
    trunk_r = {}
    for v, var in enumerate(variants):
        bpos = var["bark"][0]
        m = (bpos[:, 1] > 0.03) & (bpos[:, 1] < 0.12)
        trunk_r[v] = 0.7 * float(np.hypot(bpos[m, 0], bpos[m, 2]).max()) if m.any() else 0.04
    trunks = [
        (x, z, trunk_r[v] * sxz, variants[v]["crown_base"] * sy, float(ground(x, z)[0]))
        for v, _tc, x, z, sy, sxz, *_ in placed
    ]
    info = {
        "trunks_for_collision": trunks,
        "count": len(placed),
        "in_hero_lots": hero_count,
        "variants": {variants[v]["name"]: sum(1 for p in placed if p[0] == v) for v in range(len(variants))},
        "triangles_per_tree": {
            variants[v]["name"]: int(len(variants[v]["bark"][3]) + len(variants[v]["leaf"][3]))
            for v in range(len(variants))
        },
    }
    return nodes, info


def solar_elevation(azimuth_deg: float, lat: float = bd.ORIGIN[0], decl: float = -23.2) -> float:
    """Sun elevation on the day (declination `decl`) when its azimuth (from north, clockwise) is
    `azimuth_deg`, at latitude `lat`."""
    phi, d = math.radians(lat), math.radians(decl)
    best = (1e9, 0.0)
    for hdeg in np.arange(-90, 90, 0.05):
        H = math.radians(hdeg)
        sin_el = math.sin(phi) * math.sin(d) + math.cos(phi) * math.cos(d) * math.cos(H)
        el = math.asin(sin_el)
        az = (
            math.degrees(math.atan2(math.sin(H), math.cos(H) * math.sin(phi) - math.tan(d) * math.cos(phi)))
            + 180.0
        )
        err = abs((az - azimuth_deg + 180) % 360 - 180)
        if err < best[0]:
            best = (err, math.degrees(el))
    return best[1]


def estimate_sun(ortho: Ortho, buildings: list[Building], ground) -> dict:
    """Sun azimuth/elevation of the ortho capture from building shadows: for each house, the direction
    (of 72) where the ground just outside the roof outline is darkest is away from the sun; the shadow
    length there over the eave height gives the elevation."""
    az = np.radians(np.arange(0, 360, 5))
    dark_votes = np.zeros(len(az))
    lens = []
    for b in buildings:
        if b.kind == "flat" or b.poly.area < 80 or b.poly.area > 400:
            continue
        c = b.poly.centroid
        prof = []
        for a in az:
            d = np.array([math.sin(a), -math.cos(a)])  # azimuth from north, clockwise; +z is south
            ray = LineString([(c.x, c.y), (c.x + d[0] * 40, c.y + d[1] * 40)])
            ex = ray.intersection(b.poly.boundary)
            if ex.is_empty:
                prof.append(np.zeros(16))
                continue
            pts = [ex] if ex.geom_type == "Point" else list(ex.geoms)
            far = max(pts, key=lambda p: p.distance(c))
            s = np.arange(0.25, 8.25, 0.5)
            col = ortho.sample(far.x + d[0] * s, far.y + d[1] * s)
            prof.append(col.mean(-1))
        prof = np.array(prof)
        darkness = -prof[:, :3].mean(1)
        dark_votes += (darkness - darkness.mean()) / max(darkness.std(), 1e-3)
    k = int(
        np.argmax(np.convolve(np.r_[dark_votes[-2:], dark_votes, dark_votes[:2]], np.ones(5) / 5, "valid"))
    )
    shadow_az = float(np.degrees(az[k]))
    sun_az = (shadow_az + 180) % 360
    # shadow length along shadow_az: where brightness recovers to the median of the ring
    a = az[k]
    d = np.array([math.sin(a), -math.cos(a)])
    for b in buildings:
        if b.kind == "flat" or b.poly.area < 80 or b.poly.area > 400:
            continue
        c = b.poly.centroid
        ray = LineString([(c.x, c.y), (c.x + d[0] * 40, c.y + d[1] * 40)])
        ex = ray.intersection(b.poly.boundary)
        if ex.is_empty:
            continue
        pts = [ex] if ex.geom_type == "Point" else list(ex.geoms)
        far = max(pts, key=lambda p: p.distance(c))
        s = np.arange(0.1, 12.0, 0.1)
        lum = ortho.sample(far.x + d[0] * s, far.y + d[1] * s).mean(-1)
        ref = np.median(lum[-30:])
        lit = np.flatnonzero(lum > 0.75 * ref)
        if len(lit) and lit[0] > 2:
            # the ridge casts the far end of the shadow; the shadow starts at the eave line
            h = b.ridge_h()
            lens.append((s[lit[0]], h))
    if lens:
        L = np.array(lens)
        elev = float(np.degrees(np.arctan(np.median(L[:, 1] / np.maximum(L[:, 0], 0.3)))))
    else:
        elev = 45.0
    shadow_elev = elev
    elev = solar_elevation(
        sun_az
    )  # the ortho was captured 2025-12-19 .. 2026-01-02 (elements_detect metadata)
    el, azr = math.radians(elev), math.radians(sun_az)
    # direction TO the sun in the block frame (+X east, +Y up, +Z south)
    vec = [math.cos(el) * math.sin(azr), math.sin(el), -math.cos(el) * math.cos(azr)]
    log(f"sun: azimuth {sun_az:.0f} deg (from north, clockwise), elevation {elev:.0f} deg, n={len(lens)}")
    return {
        "azimuth_deg": round(sun_az, 1),
        "elevation_deg": round(elev, 1),
        "direction_to_sun": [round(v, 3) for v in vec],
        "houses_used": len(lens),
        "elevation_from_shadow_lengths_deg": round(shadow_elev, 1),
        "method": "azimuth from house shadows in the ortho; elevation from solar geometry at that azimuth "
        "for the capture dates (declination -23.2 deg, latitude 29.70)",
    }


def write_collision(buildings, ground, P, fence_lines_, trunks=()) -> int:
    """Near zone only: terrain (coarse grid over the procedural region) + one prism per procedural
    building + thin fence boxes. Under 20k triangles."""
    import trimesh

    parts = []
    n = 40
    xs = np.linspace(-HALF, HALF, n + 1)
    Xg, Zg = np.meshgrid(xs, xs)
    Yg = ground(Xg, Zg)
    V = np.c_[Xg.ravel(), Yg.ravel(), Zg.ravel()]
    idx = np.arange((n + 1) ** 2).reshape(n + 1, n + 1)
    a, b_, c, d = idx[:-1, :-1], idx[:-1, 1:], idx[1:, 1:], idx[1:, :-1]
    cx, cz = (Xg[:-1, :-1] + Xg[1:, 1:]) / 2, (Zg[:-1, :-1] + Zg[1:, 1:]) / 2
    keep = contains_xy(P.buffer(12.0), cx, cz).ravel()
    F = np.r_[np.c_[a.ravel(), d.ravel(), c.ravel()][keep], np.c_[a.ravel(), c.ravel(), b_.ravel()][keep]]
    parts.append(trimesh.Trimesh(V, F, process=False))
    for b in buildings:
        top = b.ground + (b.flat_h if b.kind == "flat" else b.ridge_h())
        for p in polys_of(b.poly.buffer(-0.3, join_style=2).simplify(0.3)):
            try:
                m = trimesh.creation.extrude_polygon(p, top - b.ground + 0.3)
            except Exception:
                continue
            v = m.vertices.copy()
            parts.append(
                trimesh.Trimesh(
                    np.c_[v[:, 0], v[:, 2] + b.ground - 0.3, v[:, 1]], m.faces[:, ::-1], process=False
                )
            )
    for ln in fence_lines_:
        if not P.intersects(ln):
            continue
        for seg in polys_of(ln.intersection(P).buffer(0.06, cap_style=2, join_style=2).simplify(0.05)):
            try:
                m = trimesh.creation.extrude_polygon(seg, 1.9)
            except Exception:
                continue
            v = m.vertices.copy()
            y = float(ground(seg.centroid.x, seg.centroid.y)[0])
            parts.append(
                trimesh.Trimesh(np.c_[v[:, 0], v[:, 2] + y - 0.05, v[:, 1]], m.faces[:, ::-1], process=False)
            )
    for x, z, r, h, gy in trunks:  # tree trunks up to the crown: cover in a foam fight
        ring = Point(x, z).buffer(max(r, 0.12), quad_segs=2)
        m = trimesh.creation.extrude_polygon(ring, h + 0.3)
        v = m.vertices.copy()
        parts.append(
            trimesh.Trimesh(np.c_[v[:, 0], v[:, 2] + gy - 0.3, v[:, 1]], m.faces[:, ::-1], process=False)
        )
    mesh = trimesh.util.concatenate(parts)
    mesh.export(OUT / "collision.glb")
    log(f"collision: {len(mesh.faces)} triangles")
    return int(len(mesh.faces))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--wall-gain", type=float, default=2.0, help="photo wall colour -> albedo gain")
    ap.add_argument("--no-sun", dest="sun", action="store_false", help="skip the sun estimate")
    ap.add_argument("--no-compress", dest="compress", action="store_false", help="debug: raw glb")
    ap.add_argument("--no-ground-classes", dest="ground_classes", action="store_false")
    ap.add_argument(
        "--ground-step", type=float, default=1.0, help="ground mesh cell (m): vertex tint spacing"
    )
    ap.add_argument("--near-step", type=float, default=1.0, help="ground cell inside the near zone (m)")
    ap.add_argument(
        "--hero-exterior",
        action="append",
        default=[],
        metavar="LISTING=GLB",
        help="swap a hero house node for a built exterior glb (house-local, placed by Listing.world)",
    )
    args = ap.parse_args(argv)
    build(args)
    return 0


# ---------------------------------------------------------------- Vexcel (billed, cached)

LEDGER = bd.VX_CACHE / "ledger_combined.jsonl"


def fetch_cars(half: float = 160.0) -> dict:
    """Vexcel elements_detect (SAM3, label 'car') on the ortho over the block square. One billed call,
    cached in cache/vexcel/ and logged in ledger_combined.jsonl."""
    path = bd.VX_CACHE / f"elements_detect_car_ortho_bbox{bd._tag(half)}.json"
    if path.exists():
        return json.loads(path.read_text())
    from datetime import UTC, datetime

    from worldgen.sources.vexcel import VexcelMCP

    bd._env()
    args = {
        "wkt": bd.bbox_wkt(half),
        "srid": 4326,
        "endpoint": "ortho",
        "model-input": {
            "elements": [{"model": "sam3", "label": "car", "detection-threshold": 0.35}],
            "include": ["geometry", "confidence"],
        },
    }
    mcp = VexcelMCP()
    try:
        mcp.initialize()
        res = mcp.call_tool("elements_detect", args)
    finally:
        mcp.close()
    row = {
        "ts": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "by": "combined",
        "kind": "mcp",
        "tool": "elements_detect",
        "arguments": args,
        "is_error": bool(res.get("isError")),
        "file": path.name,
    }
    with open(LEDGER, "a") as f:
        f.write(json.dumps(row) + "\n")
    path.write_text(json.dumps(res))
    return res


# ---------------------------------------------------------------- ground classes and cars

GRES = 0.2  # classification raster (m)
GROUND_CLASSES = ("lawn", "concrete", "beds", "photo", "pavers")


def car_polys(sun_az_deg: float) -> list[Polygon]:
    """Detected cars (Vexcel elements_detect) as block-frame polygons, grown 0.4 m and stretched 1.4 m
    away from the sun so the car's own shadow goes with it."""
    from shapely import affinity
    from shapely import wkt as shp_wkt

    fc = json.loads(fetch_cars()["content"][0]["text"])
    a = math.radians(sun_az_deg + 180.0)
    dx, dz = 1.4 * math.sin(a), -1.4 * math.cos(a)
    out = []
    for f in fc["features"]:
        g = shp_wkt.loads(f["geometry"])
        p = Polygon([bd.to_xz(lat, lon) for lon, lat in g.exterior.coords])
        if p.area > 40:  # not a car
            continue
        out.append(unary_union([p, affinity.translate(p, dx, dz)]).convex_hull.buffer(0.4))
    return out


def inpaint(img: np.ndarray, mask: np.ndarray, f: int = 4) -> np.ndarray:
    """Fill `mask` from the surrounding pixels (normalised convolution, coarse to fine, at 1/f
    resolution) and add back a little of the original fine grain, as block_photo does for crowns."""
    H, W = mask.shape
    w, h = W // f, H // f
    small = np.asarray(Image.fromarray(img).resize((w, h), Image.BILINEAR), np.float32)
    msmall = np.asarray(Image.fromarray(mask.astype(np.uint8) * 255).resize((w, h), Image.BILINEAR)) > 20
    valid = (~msmall).astype(np.float32)
    fill = small.copy()
    for sigma in (24, 8, 3):
        num = np.stack([ndimage.gaussian_filter(small[..., k] * valid, sigma) for k in range(3)], -1)
        den = ndimage.gaussian_filter(valid, sigma)[..., None]
        est = num / np.maximum(den, 1e-4)
        fill = np.where(den > (0.02 if sigma == 24 else 0.3), est, fill)
    fill_full = np.stack(
        [np.asarray(Image.fromarray(fill[..., k]).resize((W, H), Image.BILINEAR)) for k in range(3)], -1
    )
    soft = ndimage.gaussian_filter(mask.astype(np.float32), 2.0)[..., None]
    full = img.astype(np.float32)
    detail = full - np.stack([ndimage.uniform_filter(full[..., k], 9) for k in range(3)], -1)
    out = full * (1 - soft) + (fill_full + 0.25 * detail) * soft
    return np.clip(out, 0, 255).astype(np.uint8)


def car_mask_px(ortho: Ortho, polys: list[Polygon]) -> np.ndarray:
    from PIL import ImageDraw

    h, w = ortho.img.shape[:2]
    m = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(m)
    for p in polys:
        xy = np.asarray(p.exterior.coords)
        col, row = ortho.uv_px(xy[:, 0], xy[:, 1])
        d.polygon(list(zip(col.tolist(), row.tolist(), strict=True)), fill=255)
    return np.asarray(m) > 0


def raster_polys(lab: np.ndarray, k: int, half: float, res: float):
    """Pixels of class k on the (rows = z, cols = x) raster -> polygon (row runs, coverage union)."""
    rows, starts, ends = [], [], []
    for r in range(lab.shape[0]):
        m = np.r_[False, lab[r] == k, False].astype(np.int8)
        dm = np.diff(m)
        s, e = np.flatnonzero(dm == 1), np.flatnonzero(dm == -1)
        rows.append(np.full(len(s), r))
        starts.append(s)
        ends.append(e)
    r, s, e = np.concatenate(rows), np.concatenate(starts), np.concatenate(ends)
    if not len(r):
        return Polygon()
    boxes = shapely.box(-half + s * res, -half + r * res, -half + e * res, -half + (r + 1) * res)
    return shapely.union_all(boxes)


def areal(g):
    """Only the polygonal part of a geometry, as one valid (Multi)Polygon: overlays return
    GeometryCollections with stray points and lines, which make every later predicate slow."""
    parts = []
    stack = [g]
    while stack:
        h = stack.pop()
        if h.is_empty:
            continue
        if h.geom_type == "Polygon":
            parts.append(h)
        elif hasattr(h, "geoms"):
            stack.extend(h.geoms)
    return shapely.make_valid(shapely.MultiPolygon(parts)).buffer(0) if parts else Polygon()


def pd_fill(a: np.ndarray) -> np.ndarray:
    """Fill NaNs in a 1D array with the nearest valid value."""
    ok = np.isfinite(a)
    if not ok.any():
        return np.zeros_like(a)
    idx = ndimage.distance_transform_edt(~ok, return_distances=False, return_indices=True)[0]
    return a[idx]


def chaikin(xy: np.ndarray, iters: int = 3) -> np.ndarray:
    """Corner cutting on a closed ring: removes the raster staircase, keeps the shape."""
    pts = xy[:-1] if np.allclose(xy[0], xy[-1]) else xy
    for _ in range(iters):
        nxt = np.roll(pts, -1, axis=0)
        pts = np.stack([0.75 * pts + 0.25 * nxt, 0.25 * pts + 0.75 * nxt], 1).reshape(-1, 2)
    return np.vstack([pts, pts[:1]])


def smooth_polys(g, min_area: float = 0.6, iters: int = 3):
    out = []
    for p in polys_of(g):
        if p.area < min_area or len(p.exterior.coords) < 4:
            continue
        holes = [chaikin(np.asarray(r.coords), iters) for r in p.interiors if Polygon(r).area >= min_area]
        q = Polygon(chaikin(np.asarray(p.exterior.coords), iters), holes).buffer(0)
        if not q.is_empty:
            out.append(q)
    return unary_union(out) if out else Polygon()


class GroundClasses:
    """Lawn / concrete / beds / photo (pools, decks, anything else) from the ortho (crowns and cars
    painted out), on a 0.2 m raster; plus per-class low-frequency colour fields for vertex tints."""

    def __init__(self, og: Ortho, road, built, half: float = HALF, crown_fn=None) -> None:
        """crown_fn(X, Z) -> bool: ground hidden under a tree crown in the ortho (painted out there,
        so its colour says nothing about the class: it takes the nearest visible class instead)."""
        self.half = half
        Xc, Zc = grid_xz(half, GRES)
        n = Xc.shape[0]
        rgb = og.sample(Xc.ravel(), Zc.ravel()).reshape(n, n, 3)
        rgb = np.stack([ndimage.gaussian_filter(rgb[..., k], 1.0) for k in range(3)], -1)
        s = rgb.sum(-1) + 1e-3
        ch = rgb / s[..., None]
        mx, mn = rgb.max(-1), rgb.min(-1)
        sat = (mx - mn) / np.maximum(mx, 1)
        lum = rgb.mean(-1)
        green = ch[..., 1] - np.maximum(ch[..., 0], ch[..., 2])
        score = np.zeros((len(GROUND_CLASSES), n, n), np.float32)
        score[0] = np.clip((green + 0.002) / 0.02, 0, 1) + 0.05  # lawn (default)
        # concrete: grey, also in tree shadow (dark but still unsaturated)
        score[1] = np.clip((0.18 - sat) / 0.06, 0, 1) * np.clip((lum - 22) / 25, 0, 1) * 1.3
        score[2] = np.clip((ch[..., 0] - ch[..., 1] + 0.005) / 0.02, 0, 1) * np.clip(
            (sat - 0.14) / 0.08, 0, 1
        )
        score[3] = (
            np.clip((ch[..., 2] - ch[..., 0] - 0.05) / 0.04, 0, 1) * np.clip((sat - 0.3) / 0.1, 0, 1) * 2
        )
        # pavers: the red brick walks and patios (bright, red, saturated); beds are darker browns
        score[4] = (
            np.clip((ch[..., 0] - 0.37) / 0.025, 0, 1)
            * np.clip((lum - 85) / 25, 0, 1)
            * np.clip((sat - 0.18) / 0.08, 0, 1)
            * 1.6
        )
        score = np.stack([ndimage.gaussian_filter(sc, 1.5) for sc in score])
        lab = score.argmax(0).astype(np.int8)
        self.road_m = contains_xy(road, Xc, Zc)
        self.built_m = contains_xy(built, Xc, Zc)
        lab[self.road_m | self.built_m] = -1
        # despeckle: small blobs take the label of their surroundings
        for k in range(len(GROUND_CLASSES)):
            comp, nc = ndimage.label(lab == k)
            if nc == 0:
                continue
            sizes = ndimage.sum(np.ones_like(comp), comp, index=np.arange(1, nc + 1)) * GRES * GRES
            small = np.isin(comp, np.flatnonzero(sizes < 0.8) + 1)
            if small.any():
                sc = score.copy()
                sc[k] = -1
                lab[small] = sc.argmax(0)[small]
        self.pale = (lum > 112) & (sat < 0.2)
        self.paver_px = bh.paver_mask(rgb)
        # under crowns and in deep shadow the photo cannot tell lawn from pavement: nearest visible class
        hidden = (lum < 38) if crown_fn is None else (crown_fn(Xc, Zc) | (lum < 38))
        crown = np.zeros_like(hidden) if crown_fn is None else crown_fn(Xc, Zc)
        src = (lab >= 0) & ~hidden
        self.lab_visible = np.where(hidden & (lab >= 0), -2, lab)
        dist, (ii, jj) = ndimage.distance_transform_edt(~src, return_indices=True)
        fix = hidden & (lab >= 0)
        lab[fix] = lab[ii[fix], jj[fix]]
        # far inside a crown's shade, assume lawn (trees stand in yards); sidewalks are added back
        # geometrically (sidewalks())
        lab[fix & (dist * GRES > 1.2) & (lab == 1)] = 0
        self.lab = lab
        self.rgb = rgb
        self.fields = {}
        sig = 0.5 / GRES
        blur_all = np.stack([ndimage.gaussian_filter(rgb[..., k], 2 * sig) for k in range(3)], -1)
        masks = {name: lab == i for i, name in enumerate(GROUND_CLASSES)}
        # lawn colour from grass-coloured pixels only (no brick, concrete or faded-brick fringe)
        masks["lawn"] = masks["lawn"] & ~self.paver_px & ~self.pale & (rgb[..., 1] >= rgb[..., 0] * 0.97)
        masks["road"] = self.road_m
        for name, m in masks.items():
            w = (m & ~crown).astype(np.float32)
            num = np.stack([ndimage.gaussian_filter(rgb[..., k] * w, sig) for k in range(3)], -1)
            den = ndimage.gaussian_filter(w, sig)[..., None]
            num2 = np.stack([ndimage.gaussian_filter(rgb[..., k] * w, 4 * sig) for k in range(3)], -1)
            den2 = ndimage.gaussian_filter(w, 4 * sig)[..., None]
            # no class pixels nearby: the class's own median colour (never the blur of everything,
            # which made far-from-grass lawn look like pale concrete)
            fallback = np.median(rgb[m], 0) if m.sum() > 100 else blur_all
            f = np.where(
                den > 0.05,
                num / np.maximum(den, 1e-4),
                np.where(den2 > 0.15, num2 / np.maximum(den2, 1e-4), fallback),
            )
            f = np.where(crown[..., None], f * 0.78, f)  # shade under the (unshadowed) model crowns
            self.fields[name] = f.astype(np.float32)

    def polygons(self) -> dict:
        out = {}
        for i, name in enumerate(GROUND_CLASSES):
            p = raster_polys(self.lab, i, self.half, GRES)
            # paving is man-made: straighter outlines (coarser simplify, lighter corner cutting)
            tol, it = (0.35, 2) if name == "concrete" else (0.15, 3)
            out[name] = smooth_polys(p.simplify(tol), iters=it)
        return out

    def sidewalks(self, road, zone, max_off: float = 5.0):
        """Straight sidewalk strips along the curb: per 1 m station, a concrete band 0.9-1.8 m wide
        within 5 m of the curb with lawn behind it, voted over +-10 m along the street (visible
        pixels only, so the walk continues under crowns and across driveways). Inside `zone`."""
        offs = np.arange(0.1, max_off + 1.2, 0.2)
        out = []
        for poly in polys_of(road.intersection(zone.buffer(5.0))):
            for ring in [poly.exterior, *poly.interiors]:
                ln = LineString(ring.coords)
                n = int(ln.length // 1.0)
                if n < 20:
                    continue
                P = np.array([ln.interpolate(i * 1.0).coords[0] for i in range(n + 1)])
                T = np.gradient(P, axis=0)
                T /= np.maximum(np.linalg.norm(T, axis=1, keepdims=True), 1e-9)
                N = np.c_[T[:, 1], -T[:, 0]]
                probe = P + 0.5 * N
                flip = contains_xy(road, probe[:, 0], probe[:, 1])
                N[flip] *= -1  # outward: away from the road
                Q = P[:, None, :] + offs[None, :, None] * N[:, None, :]
                c = np.clip(((Q[..., 0] + self.half) / GRES).astype(int), 0, self.lab.shape[1] - 1)
                r = np.clip(((Q[..., 1] + self.half) / GRES).astype(int), 0, self.lab.shape[0] - 1)
                L = self.lab_visible[r, c]
                vis = (L >= 0).astype(float)
                conc = (L == 1).astype(float)
                k = np.ones(21)
                cs = ndimage.convolve1d(conc, k, axis=0, mode="nearest")
                vs = ndimage.convolve1d(vis, k, axis=0, mode="nearest")
                frac = np.where(vs > 3, cs / np.maximum(vs, 1e-6), np.nan)
                best = np.full((len(P), 3), np.nan)  # score, o1, o2
                for w in (5, 6, 7, 8):  # band widths in 0.2 m steps: 1.0 .. 1.6 m
                    for i0 in range(0, len(offs) - w - 4):
                        band = np.nanmean(frac[:, i0 : i0 + w], axis=1)
                        behind = np.nanmean(frac[:, i0 + w + 1 : i0 + w + 4], axis=1)
                        front = (
                            np.nanmean(frac[:, max(0, i0 - 3) : i0], axis=1) if i0 >= 3 else np.zeros(len(P))
                        )
                        sc = band - behind - 0.5 * np.clip(front - 0.5, 0, None)
                        ok = (band > 0.55) & (behind < 0.45) & ~(sc <= np.nan_to_num(best[:, 0], nan=-9))
                        best[ok] = np.c_[
                            sc[ok], np.full(ok.sum(), offs[i0] - 0.1), np.full(ok.sum(), offs[i0 + w] - 0.1)
                        ]
                has = np.isfinite(best[:, 0])
                if has.sum() < 10:
                    continue
                # smooth offsets along the street, keep runs of at least 6 m
                o1 = ndimage.median_filter(np.where(has, best[:, 1], np.nan), 15, mode="nearest")
                o2 = ndimage.median_filter(np.where(has, best[:, 2], np.nan), 15, mode="nearest")
                has = ndimage.binary_closing(has, np.ones(9)) & ndimage.binary_opening(has, np.ones(3))
                o1 = pd_fill(o1)
                o2 = pd_fill(o2)
                lab_, nr = ndimage.label(has)
                for k_ in range(1, nr + 1):
                    idx = np.flatnonzero(lab_ == k_)
                    if len(idx) < 6:
                        continue
                    a = P[idx] + o1[idx, None] * N[idx]
                    b = P[idx] + o2[idx, None] * N[idx]
                    strip = Polygon(np.r_[a, b[::-1]]).buffer(0)
                    if not strip.is_empty:
                        out.append(strip)
        if not out:
            return Polygon()
        w = unary_union(out).difference(road).intersection(zone)
        return w.simplify(0.1).buffer(0)

    def colour_raw(self, x, z) -> np.ndarray:
        """The ortho colour itself (0.2 m smoothed), sRGB 0..255, for traced pavers."""
        c = (np.asarray(x) + self.half) / GRES - 0.5
        r = (np.asarray(z) + self.half) / GRES - 0.5
        return np.stack(
            [ndimage.map_coordinates(self.rgb[..., k], [r, c], order=1, mode="nearest") for k in range(3)], -1
        )

    def colour(self, name: str, x, z) -> np.ndarray:
        """Low-frequency sRGB 0..255 colour of class `name` at (x, z)."""
        f = self.fields[name]
        c = (np.asarray(x) + self.half) / GRES - 0.5
        r = (np.asarray(z) + self.half) / GRES - 0.5
        return np.stack(
            [ndimage.map_coordinates(f[..., k], [r, c], order=1, mode="nearest") for k in range(3)], -1
        )


if __name__ == "__main__":
    raise SystemExit(main())
