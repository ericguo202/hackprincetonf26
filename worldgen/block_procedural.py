"""Procedural (clean-geometry) block prototype for hou_navidad_01.

Real footprints (Vexcel structures), heights and roof pitch (Vexcel DSM), parcels (Vexcel) and roads
(OpenStreetMap), built as clean geometry and dressed with the Vexcel ortho (ground and roofs) and
CC0 materials (walls, fences, bark). Output goes to out/block_compare/procedural/.

    uv run python -m worldgen.block_procedural            # build render.glb + collision.glb
    uv run python -m worldgen.block_procedural --fetch    # fetch missing source data first (billed!)

Block frame: origin at the ground at the block centre, +X east, +Y up, +Z south, meters.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import zlib
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage
from shapely import contains_xy
from shapely import wkt as shp_wkt
from shapely.geometry import Polygon, box
from shapely.ops import unary_union

from worldgen import block_data as bd
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
    triangulate,
)
from worldgen.block_facade import facade_atlas, fence_texture, place_openings
from worldgen.block_glb import Material, MeshBuf, Node, compress, write_glb
from worldgen.block_ground import (
    CURB,
    curbs,
    fence_lines,
    fence_mesh,
    gridded,
    load_roads,
    overlay_meshes,
    road_polygon,
)
from worldgen.block_raster import Ortho, derive_dtm, grid_xz, read_geotiff, resample_to_grid
from worldgen.block_veg import build_shrubs, build_trees, leaf_texture, segment_crowns

OUT = bd.OUT / "procedural"
HALF = 160.0  # terrain half-size (m); the ortho covers +-170 (its attribution text sits at +163 m south)
RES = 0.25  # raster grid (m)
TERRAIN_STEP = 2.0
ORTHO_PX = 4096
PH = bd.CACHE / "polyhaven"

HEROES = {  # final house placement (CoStar index world.position), block frame x, z
    "hero_hou_236595179": (-20.94, 98.06),
    "hero_hou_237901269": (19.89, -95.08),
}


def log(*a) -> None:
    print(*a, file=sys.stderr, flush=True)


# ---------------- data ----------------


def load_structures() -> list[tuple[Polygon, bool]]:
    r = bd.fetch_structures()
    fc = json.loads(r["content"][0]["text"])
    out = []
    for f in fc["features"]:
        g = shp_wkt.loads(f["geometry"])
        for p in getattr(g, "geoms", [g]):
            poly = Polygon([bd.to_xz(lat, lon) for lon, lat in p.exterior.coords]).buffer(0)
            if poly.area > 4:
                out.append((poly, bool(f["properties"].get("primary-structure"))))
    return out


def load_parcels() -> list[dict]:
    """Unique parcels (latest collection wins) with structure roof shapes."""
    r = bd.fetch_parcels()
    fs = json.loads(r["content"][0]["text"])["features"]
    best: dict[str, tuple[str, dict]] = {}
    for f in fs:
        g = shp_wkt.loads(f["geometry"])
        c = g.centroid
        key = f"{c.x:.5f},{c.y:.5f}"
        coll = f["properties"].get("metadata.collection") or ""
        date = f["properties"].get("metadata.ortho-date-estimate") or 0
        rank = (coll.endswith("2026"), str(date))
        if key not in best or rank > best[key][0]:
            best[key] = (rank, f)
    out = []
    for _, f in sorted(best.values(), key=lambda rf: rf[0], reverse=True):
        g = shp_wkt.loads(f["geometry"])
        polys = [
            Polygon([bd.to_xz(lat, lon) for lon, lat in p.exterior.coords]).buffer(0)
            for p in getattr(g, "geoms", [g])
        ]
        structs = []
        for s in f["properties"].get("structures") or []:
            fp = s.get("footprint.wkt")
            if not fp:
                continue
            sg = shp_wkt.loads(fp)
            sp = (
                Polygon([bd.to_xz(lat, lon) for lon, lat in sg.exterior.coords])
                if sg.geom_type == "Polygon"
                else None
            )
            structs.append(
                {
                    "poly": sp,
                    "shape": s.get("roof-characteristics.shape.value"),
                    "material": s.get("roof-characteristics.material.class"),
                }
            )
        out.append({"poly": unary_union(polys), "structures": structs})
    return out


# ---------------- terrain ----------------


def detail_ao(path: Path, lo: float) -> Image.Image:
    """Tiled CC0 diffuse -> normalised grey detail used as occlusion on TEXCOORD_1 (range lo..1)."""
    g = np.asarray(Image.open(path).convert("L"), np.float32)
    g = (g - np.percentile(g, 2)) / max(np.percentile(g, 98) - np.percentile(g, 2), 1)
    g = lo + (1 - lo) * np.clip(g, 0, 1)
    return Image.fromarray((g * 255).astype(np.uint8)).convert("RGB")


class Ground:
    def __init__(self, dtm: np.ndarray, half: float, res: float, g0: float) -> None:
        self.dtm, self.half, self.res, self.g0 = dtm, half, res, g0

    def __call__(self, x, z) -> np.ndarray:
        x, z = np.atleast_1d(np.asarray(x, float)), np.atleast_1d(np.asarray(z, float))
        c = (x + self.half) / self.res - 0.5
        r = (z + self.half) / self.res - 0.5
        return ndimage.map_coordinates(self.dtm, [r, c], order=1, mode="nearest") - self.g0


def uv_ortho(x, z) -> np.ndarray:
    return np.c_[(np.asarray(x) + HALF) / (2 * HALF), (np.asarray(z) + HALF) / (2 * HALF)]


def terrain_mesh(ground: Ground, holes=None) -> MeshBuf:
    buf = MeshBuf()
    n = int(2 * HALF / TERRAIN_STEP)
    xs = np.linspace(-HALF, HALF, n + 1)
    X, Z = np.meshgrid(xs, xs)
    Y = ground(X, Z)
    P = np.c_[X.ravel(), Y.ravel(), Z.ravel()]
    idx = np.arange((n + 1) ** 2).reshape(n + 1, n + 1)
    a, b, c, d = idx[:-1, :-1], idx[:-1, 1:], idx[1:, 1:], idx[1:, :-1]
    tris = np.r_[np.c_[a.ravel(), b.ravel(), c.ravel()], np.c_[a.ravel(), c.ravel(), d.ravel()]]
    buf.add(P, tris, uv=uv_ortho(P[:, 0], P[:, 2]), facing=np.array([0, 1, 0]))
    return buf


def skirt_mesh() -> MeshBuf:
    """Flat surround beyond the modelled square, textured with the mirrored ortho (no geometry)."""
    buf = MeshBuf()
    outer, inner = 2500.0, HALF - 0.05
    ring = box(-outer, -outer, outer, outer).difference(box(-inner, -inner, inner, inner))
    for p in polys_of(ring.segmentize(160.0)):
        v, t = triangulate(p)
        buf.add(
            np.c_[v[:, 0], np.full(len(v), -0.25), v[:, 1]],
            t,
            uv=uv_ortho(v[:, 0], v[:, 1]),
            normals=np.array([0, 1, 0]),
            facing=np.array([0, 1, 0]),
        )
    return buf


# ---------------- build ----------------


def build(args) -> dict:
    t0 = time.time()
    OUT.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(7)

    log("loading rasters")
    dsm_r = read_geotiff(next(bd.VX_CACHE.glob("dsm_extract_urban_*_z20.tif")))
    ortho = Ortho.load(next(bd.VX_CACHE.glob("ortho_extract_urban_*_z21.jpg")), bd.bbox_ll())
    dsm = resample_to_grid(dsm_r, HALF, RES)
    X, Z = grid_xz(HALF, RES)

    structs = [
        (p, pr)
        for p, pr in load_structures()
        if abs(p.centroid.x) < HALF - 4 and abs(p.centroid.y) < HALF - 4
    ]
    parcels = load_parcels()
    log(f"{len(structs)} structures, {len(parcels)} parcels")
    bmask = contains_xy(unary_union([p.buffer(1.0) for p, _ in structs]), X, Z)
    dtm = derive_dtm(dsm, RES, bmask)
    n0 = dtm.shape[0] // 2
    g0 = float(dtm[n0 - 1 : n0 + 1, n0 - 1 : n0 + 1].mean())
    ground = Ground(dtm, HALF, RES, g0)
    nd = dsm - dtm
    log(f"ground at origin {g0:.2f} m; dtm range {dtm.min():.2f}..{dtm.max():.2f}")
    oc = ortho.sample(X.ravel(), Z.ravel()).reshape(X.shape + (3,))
    canopy = (oc[..., 1] > oc[..., 0] + 4) & (oc[..., 1] > oc[..., 2] + 4)
    canopy = ndimage.binary_dilation(ndimage.binary_opening(canopy, iterations=2), iterations=3)

    # ---- textures / materials
    ways, circles = load_roads()
    road = road_polygon(ways, circles, HALF, ortho)
    from PIL import ImageFilter

    ortho_img = ortho.crop_xz(-HALF, -HALF, HALF, HALF, ORTHO_PX)
    ortho_img = ortho_img.filter(ImageFilter.UnsharpMask(radius=2, percent=60, threshold=2))
    from PIL import ImageFilter

    images: dict = {
        "fence": fence_texture(),
        "ortho": ortho_img,
        "ortho_far": ortho_img.resize((512, 512)).filter(ImageFilter.GaussianBlur(2.5)),
        "leaf": leaf_texture(),
        "facade": facade_atlas(),
        "leafy_grass_n": PH / "leafy_grass_nor_1k.jpg",
        "concrete_pavement_n": PH / "concrete_pavement_nor_1k.jpg",
        "brushed_concrete": PH / "brushed_concrete_diff_1k.jpg",
        "grass_ao": detail_ao(PH / "leafy_grass_diff_1k.jpg", 0.3),
        "concrete_ao": detail_ao(PH / "concrete_pavement_diff_1k.jpg", 0.45),
        "grass_detail": detail_ao(PH / "leafy_grass_diff_1k.jpg", 0.45),
        "concrete_detail": detail_ao(PH / "concrete_pavement_diff_1k.jpg", 0.62),
    }
    for k in ("red_brick", "brick_wall_02", "red_brick_03", "exterior_wall_cladding", "bark_brown_02"):
        images[k] = PH / f"{k}_diff_1k.jpg"
        images[k + "_n"] = PH / f"{k}_nor_1k.jpg"
    mats = {
        "ground": Material(
            "ground",
            texture="ortho",
            unlit=args.unlit_ground,
            roughness=1.0,
            sampler_wrap=33071,
            normal_texture="leafy_grass_n",
            normal_texcoord=1,
            normal_scale=1.4,
            occlusion_texture="grass_ao",
        ),
        "road": Material(
            "road",
            texture="ortho",
            unlit=args.unlit_ground,
            roughness=0.95,
            sampler_wrap=33071,
            normal_texture="concrete_pavement_n",
            normal_texcoord=1,
            normal_scale=0.7,
            occlusion_texture="concrete_ao",
        ),
        "curb": Material("curb", color=(0.52, 0.51, 0.49, 1), roughness=0.9),
        "curb_top": Material("curb_top", texture="ortho", sampler_wrap=33071, roughness=0.9),
        "fence": Material("fence", texture="fence", color=(0.85, 0.85, 0.85, 1), roughness=0.95),
        "facade": Material("facade", texture="facade", roughness=0.55),
        "grass_overlay": Material("grass_overlay", texture="grass_detail", roughness=1.0, blend=True),
        "paving_overlay": Material("paving_overlay", texture="concrete_detail", roughness=0.95, blend=True),
        "roof": Material("roof", texture="ortho", roughness=0.85, sampler_wrap=33071),
        "trim": Material("trim", color=(0.86, 0.84, 0.79, 1), roughness=0.7),
        "soffit": Material("soffit", color=(0.80, 0.78, 0.74, 1), roughness=0.8),
        "skirt": Material(
            "skirt",
            texture="ortho_far",
            unlit=args.unlit_ground,
            roughness=1.0,
            sampler_wrap=33648,
            color=(0.92, 0.92, 0.92, 1),
        ),
        "leaves": Material("leaves", texture="leaf", unlit=True, double_sided=True, alpha_mask=0.3),
        "bark": Material(
            "bark", texture="bark_brown_02", normal_texture="bark_brown_02_n", color=(0.92, 0.9, 0.86, 1)
        ),
        "flat_roof": Material("flat_roof", texture="ortho", roughness=0.9, sampler_wrap=33071),
    }
    wall_kinds = {
        "brick_a": ("red_brick", 1.4, (1, 1, 1, 1)),
        "brick_b": ("brick_wall_02", 2.0, (1, 1, 1, 1)),
        "brick_c": ("red_brick_03", 1.0, (1, 1, 1, 1)),
        "siding_a": ("exterior_wall_cladding", 2.0, (1.0, 0.97, 0.9, 1)),
        "siding_b": ("exterior_wall_cladding", 2.0, (0.85, 0.88, 0.86, 1)),
    }
    for k, (tex, _, col) in wall_kinds.items():
        mats[k] = Material(k, texture=tex, normal_texture=tex + "_n", color=col, roughness=0.9)

    # ---- buildings
    log("buildings")
    roof_hint = []
    for pc in parcels:
        for s in pc["structures"]:
            if s["poly"] is not None:
                roof_hint.append((s["poly"], s["shape"]))
    road_lines = [ln for ln, k in ways if k != "service"]
    bufs = {k: MeshBuf() for k in ["roof", "trim", "soffit", "flat_roof", "facade", *wall_kinds]}
    hero_bufs: dict[str, dict[str, MeshBuf]] = {}
    buildings: list[Building] = []
    stats = {"hip": 0, "gable": 0, "flat": 0}
    for i, (poly, primary) in enumerate(structs):
        b = Building(poly=poly, primary=primary, name=f"b{i:03d}")
        iou = decompose(b)
        hint = None
        for hp, shape in roof_hint:
            if hp.intersection(poly).area > 0.5 * poly.area:
                hint = shape
                break
        if iou < 0.82 or hint == "flat" or poly.area > 600:
            b.kind = "flat"
        inner = poly.buffer(-0.6)
        sel = contains_xy(inner if not inner.is_empty else poly, X, Z)
        if (sel & ~canopy).sum() > 0.4 * sel.sum():
            sel &= ~canopy  # tree crowns over the roof are not roof
        fit_roof(b, X[sel], Z[sel], nd[sel], hint)
        c = poly.centroid
        b.ground = float(ground(c.x, c.y)[0])
        for tag, (hx, hz) in HEROES.items():
            if poly.buffer(1.0).contains(
                Polygon([(hx - 0.1, hz - 0.1), (hx + 0.1, hz - 0.1), (hx + 0.1, hz + 0.1)])
            ):
                b.tag = tag
        buildings.append(b)
        stats[b.kind] += 1
        target = bufs
        if b.tag:
            target = hero_bufs.setdefault(b.tag, {k: MeshBuf() for k in bufs})
        emit_building(b, target, wall_kinds, rng, ground, road_lines, ortho)
    log(f"roofs {stats}")

    # ---- terrain
    log("terrain")
    land_buf, road_buf, curb_buf = MeshBuf(), MeshBuf(), MeshBuf()
    sq = box(-HALF, -HALF, HALF, HALF)
    gridded(land_buf, sq.difference(road), ground, HALF, TERRAIN_STEP, 0.0, uv_ortho, 2.0)
    gridded(road_buf, road, ground, HALF, TERRAIN_STEP, -CURB, uv_ortho, 1.8)
    curb_top = MeshBuf()
    curbs(curb_buf, road, ground, HALF, uv_ortho, curb_top)
    built = unary_union([b.poly.buffer(-0.2) for b in buildings])
    grass_ov, pave_ov = (
        overlay_meshes(ortho, ground, road, built, HALF) if args.overlay else (MeshBuf(), MeshBuf())
    )
    fence_buf = MeshBuf()
    flines = fence_lines(parcels, buildings, road, road_lines)
    fence_mesh(fence_buf, flines, ground, HALF)
    log(f"fences: {len(flines)} runs, {sum(ln.length for ln in flines):.0f} m")
    skirt = skirt_mesh()

    # ---- trees
    log("trees")
    lab, nd_sm = segment_crowns(nd, RES, contains_xy(unary_union([p.buffer(0.6) for p, _ in structs]), X, Z))
    dtm_rel = dtm - g0
    leaves, bark, tree_info = build_trees(nd_sm, lab, X, Z, dtm_rel, ortho, RES, rng)
    excl = contains_xy(unary_union([p.buffer(0.3) for p, _ in structs]), X, Z) | (lab > 0)
    excl |= contains_xy(road, X, Z)
    n_shrubs = build_shrubs(nd_sm, X, Z, dtm_rel, ortho, RES, excl, canopy, rng, leaves)
    log(f"{len(tree_info)} trees, {n_shrubs} shrubs")

    # ---- assemble
    roots = [
        Node(
            "terrain",
            mesh=[
                (land_buf, "ground"),
                (road_buf, "road"),
                (curb_buf, "curb"),
                (curb_top, "curb_top"),
                (skirt, "skirt"),
                (grass_ov, "grass_overlay"),
                (pave_ov, "paving_overlay"),
            ],
        ),
        Node("fences", mesh=[(fence_buf, "fence")]),
        Node("buildings", mesh=[(bufs[k], k) for k in bufs]),
        Node("trees", mesh=[(leaves, "leaves"), (bark, "bark")]),
    ]
    for tag, hb in hero_bufs.items():
        roots.append(
            Node(tag, mesh=[(hb[k], k) for k in hb], extras={"listing_id": tag.removeprefix("hero_")})
        )
    raw = OUT / "render_raw.glb"
    st = write_glb(raw, roots, mats, images)
    log(f"raw glb {st}")
    compress(raw, OUT / "render.glb")
    raw.unlink()
    size = (OUT / "render.glb").stat().st_size
    write_collision(buildings, ground)
    summary = {
        "triangles": st["triangles"],
        "render_glb_mb": round(size / 1e6, 2),
        "buildings": len(buildings),
        "roofs": stats,
        "two_storey": sum(1 for b in buildings if b.kind != "flat" and b.eave > 4.5),
        "trees": len(tree_info),
        "shrubs": n_shrubs,
        "heroes": {b.tag: b.name for b in buildings if b.tag},
        "ground_elev_m": g0,
        "build_s": round(time.time() - t0, 1),
    }
    (OUT / "build_summary.json").write_text(json.dumps(summary, indent=2))
    log(json.dumps(summary, indent=2))
    return summary


def pick_wall(b: Building, rng) -> str:
    h = zlib.crc32(b.name.encode()) % 100
    if b.kind == "flat":
        return "siding_b"
    return ["brick_a", "brick_b", "brick_c", "brick_a", "siding_a"][h % 5]


def emit_building(b: Building, bufs: dict[str, MeshBuf], wall_kinds, rng, ground, road_lines, ortho) -> None:
    y0 = b.ground
    wall = pick_wall(b, rng)
    tex_m = wall_kinds[wall][1]
    if b.kind == "flat":
        top = y0 + b.flat_h
        for p in polys_of(b.poly):
            outer_walls(bufs[wall], p, y0 - 0.3, top, tex_m)
            flat_roof(bufs["flat_roof"], p, top, uv_ortho)
        return
    ov = OVERHANG if b.poly.area > 40 else 0.15
    wp = b.poly.buffer(-ov, join_style=2)
    e = y0 + b.eave
    for p in polys_of(wp):
        outer_walls(bufs[wall], p, y0 - 0.3, e - FASCIA, tex_m)
        if b.primary and p.area > 30:
            place_openings(bufs["facade"], p, y0, e - FASCIA, road_lines, ortho, b.eave > 4.5, rng)
    for p in polys_of(b.poly):
        outer_walls(bufs["trim"], p, e - FASCIA, e + 0.02, 1.0)
    horizontal(bufs["soffit"], b.poly.difference(wp), e - FASCIA, up=False)
    roof_mesh(bufs["roof"], b, uv_ortho, y0)
    gable_ends(bufs["siding_a"], b, y0, 2.0)


def write_collision(buildings: list[Building], ground: Ground) -> None:
    """Terrain (coarse grid) + one box per building, under 20k triangles."""
    import trimesh

    parts = []
    n = 40
    xs = np.linspace(-HALF, HALF, n + 1)
    Xg, Zg = np.meshgrid(xs, xs)
    Yg = ground(Xg, Zg)
    V = np.c_[Xg.ravel(), Yg.ravel(), Zg.ravel()]
    idx = np.arange((n + 1) ** 2).reshape(n + 1, n + 1)
    a, b_, c, d = idx[:-1, :-1], idx[:-1, 1:], idx[1:, 1:], idx[1:, :-1]
    F = np.r_[np.c_[a.ravel(), d.ravel(), c.ravel()], np.c_[a.ravel(), c.ravel(), b_.ravel()]]
    parts.append(trimesh.Trimesh(V, F, process=False))
    for b in buildings:
        top = b.ground + (b.flat_h if b.kind == "flat" else b.ridge_h())
        for p in polys_of(b.poly.buffer(-0.3, join_style=2).simplify(0.3)):
            try:
                m = trimesh.creation.extrude_polygon(p, top - b.ground + 0.3)
            except Exception:
                continue
            # extrude_polygon builds along +Z in (x, y); map (x, y, h) -> (x, h, y)
            v = m.vertices.copy()
            m = trimesh.Trimesh(
                np.c_[v[:, 0], v[:, 2] + b.ground - 0.3, v[:, 1]], m.faces[:, ::-1], process=False
            )
            parts.append(m)
    mesh = trimesh.util.concatenate(parts)
    mesh.export(OUT / "collision.glb")
    log(f"collision: {len(mesh.faces)} triangles")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--fetch", action="store_true", help="fetch missing Vexcel/OSM data (billed)")
    ap.add_argument(
        "--lit-ground",
        dest="unlit_ground",
        action="store_false",
        help="PBR-lit ground instead of unlit ortho",
    )
    ap.add_argument(
        "--overlay",
        action="store_true",
        help="add semi-transparent grass/concrete grain layers (off: they band at street level)",
    )
    args = ap.parse_args(argv)
    if args.fetch:
        bd.fetch_all()
    build(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
