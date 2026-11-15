"""Block prototype, photo-surface approach: Vexcel 3D Cities + DSM/ortho -> render.glb in the block frame.

uv run python -m worldgen.block_photo [--budget 19] [--offline]

Lane A QA prototype for the block comparison (worldgen/out/block_compare/). Route:
1. Core: Vexcel `3d/tile` (3D Cities, collection us-tx-houstonr2-2023; OGC 3D Tiles content = glTF with
   Draco + KTX2/Basis textures, facades textured from Vexcel's obliques) at zoom 19 (~66 m tiles, 1.5 m
   geometric error) for the two tile columns holding Navidad Rd and both heroes (x ~ -82..+50 m, all z).
   block_photo_decode.mjs turns Draco + KTX2 into plain glb; here the tile skirts go, ECEF goes to the block
   frame, and the square is cropped. Outside building footprints everything below 3.4 m above ground is cut
   (photogrammetry trees come down to the street as solid blobs): crowns float, plain trunks go under them.
2. Ground everywhere, and the side strips outside the core: a height grid from the Vexcel DSM (urban, 2026,
   z20) draped with the true ortho (z21, tree crowns painted out of the ground texture). In the strips,
   buildings come from the DSM inside Vexcel footprints and tree crowns are open DSM sheets. Both rasters
   and the footprints were fetched by the procedural prototype into the shared cache; not fetched again.
3. Hero houses split into nodes `hero_<listing_id>` (Vexcel footprint + 1.5 m); unlit photo materials;
   meshopt (with 6 cm simplification) + WebP. collision.glb: ground slab + one prism per footprint.

Every Vexcel response is cached under block_compare/cache/vexcel/ and counted in ledger_photo.jsonl; the
client stops at --budget network calls. Offline only; the game never calls Vexcel.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import time
import zlib
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import shapely
import trimesh
from dotenv import load_dotenv
from PIL import Image
from scipy import ndimage
from shapely import wkt as shapely_wkt
from shapely.geometry import MultiPolygon, Polygon
from shapely.ops import unary_union

HERE = Path(__file__).resolve().parent
OUT = HERE / "out" / "block_compare"
PHOTO = OUT / "photo"
VCACHE = OUT / "cache" / "vexcel"
CACHE3D = VCACHE / "3d"
WORK = OUT / "cache" / "photo_work"
LEDGER = VCACHE / "ledger_photo.jsonl"

BLOCK_ID = "hou_navidad_01"
LAT0, LON0 = 29.70113, -95.670841
HALF_M = 160.0  # the square is 2 * HALF_M on a side
COLLECTION = "us-tx-houstonr2-2023"
LAYER = "3d-cities"
CORE_ZOOM = 19
CORE_COLS = (122812, 122813)
TREE_CUT_M = 3.4  # tree/fence skirts below this height above ground are removed outside buildings
GROUND_TOL_M = 0.5  # WMTS columns at CORE_ZOOM holding Navidad Rd (x ~ 0) and both heroes

# Shared rasters (fetched once by the procedural prototype; same bbox for both).
_BBOX = "bbox29.69960_-95.67260_29.70266_-95.66908"
ORTHO = VCACHE / f"ortho_extract_urban_{_BBOX}_z21.jpg"
DSM = VCACHE / f"dsm_extract_urban_{_BBOX}_z20.tif"
STRUCTURES = VCACHE / f"structure_geometry_metadata_extract_{_BBOX}_roofheight.json"

HEROES = {
    "hou_236595179": (29.700279, -95.671051),  # 7014 Navidad Rd (hero)
    "hou_237901269": (29.701982, -95.670631),  # 6915 Navidad Rd
}

_A = 6378137.0  # WGS84, also the Web Mercator sphere radius
_E2 = 6.69437999014e-3


# ---------------------------------------------------------------- geo


def lla_to_ecef(lat: np.ndarray, lon: np.ndarray, h: np.ndarray) -> np.ndarray:
    lat, lon = np.radians(lat), np.radians(lon)
    n = _A / np.sqrt(1 - _E2 * np.sin(lat) ** 2)
    return np.stack(
        [
            (n + h) * np.cos(lat) * np.cos(lon),
            (n + h) * np.cos(lat) * np.sin(lon),
            (n * (1 - _E2) + h) * np.sin(lat),
        ],
        axis=-1,
    )


def ecef_to_block(p: np.ndarray) -> np.ndarray:
    """ECEF (N,3) -> block frame (N,3): x east, y up (ellipsoidal height), z south."""
    lat, lon = math.radians(LAT0), math.radians(LON0)
    o = lla_to_ecef(np.array(LAT0), np.array(LON0), np.array(0.0))
    d = p - o
    sl, cl, sp, cp = math.sin(lon), math.cos(lon), math.sin(lat), math.cos(lat)
    e = -sl * d[:, 0] + cl * d[:, 1]
    n = -sp * cl * d[:, 0] - sp * sl * d[:, 1] + cp * d[:, 2]
    u = cp * cl * d[:, 0] + cp * sl * d[:, 1] + sp * d[:, 2]
    return np.stack([e, u, -n], axis=1)


def lonlat_to_xz(lon, lat) -> np.ndarray:
    """lon/lat (degrees) -> block-frame [x, z] (N,2) on the ellipsoid tangent plane."""
    lon, lat = np.atleast_1d(lon).astype(float), np.atleast_1d(lat).astype(float)
    return ecef_to_block(lla_to_ecef(lat, lon, np.zeros_like(lat)))[:, [0, 2]]


def xz_to_lonlat(x, z) -> tuple[np.ndarray, np.ndarray]:
    """Inverse of lonlat_to_xz (vectorised Newton; millimetre accurate over the block)."""
    x, z = np.atleast_1d(x).astype(float), np.atleast_1d(z).astype(float)
    k_lat, k_lon = 110850.0, 111320.0 * math.cos(math.radians(LAT0))
    lat, lon = LAT0 - z / k_lat, LON0 + x / k_lon
    for _ in range(3):
        p = lonlat_to_xz(lon, lat)
        lon = lon + (x - p[:, 0]) / k_lon
        lat = lat - (z - p[:, 1]) / k_lat
    return lon, lat


def merc(lon, lat) -> tuple[np.ndarray, np.ndarray]:
    lon, lat = np.asarray(lon, float), np.asarray(lat, float)
    return _A * np.radians(lon), _A * np.log(np.tan(np.pi / 4 + np.radians(lat) / 2))


def wmts_tile(lat: float, lon: float, z: int) -> tuple[float, float]:
    n = 2**z
    return (lon + 180.0) / 360.0 * n, (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n


def tile_lonlat(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    """WMTS tile -> (lon_west, lon_east, lat_south, lat_north)."""
    n = 2**z

    def lat(yy: float) -> float:
        return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * yy / n))))

    return x / n * 360 - 180, (x + 1) / n * 360 - 180, lat(y + 1), lat(y)


def tile_rect_xz(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    """Tile -> (x_west, x_east, z_north, z_south) in the block frame (edges are straight to < 1 cm here)."""
    w, e, s, n = tile_lonlat(z, x, y)
    mid_lat, mid_lon = (s + n) / 2, (w + e) / 2
    return (
        float(lonlat_to_xz(w, mid_lat)[0, 0]),
        float(lonlat_to_xz(e, mid_lat)[0, 0]),
        float(lonlat_to_xz(mid_lon, n)[0, 1]),
        float(lonlat_to_xz(mid_lon, s)[0, 1]),
    )


def tiles_covering(z: int, half_m: float) -> list[tuple[int, int]]:
    lon, lat = xz_to_lonlat(
        np.array([-half_m, half_m, -half_m, half_m]), np.array([-half_m, -half_m, half_m, half_m])
    )
    xs, ys = zip(*(wmts_tile(la, lo, z) for lo, la in zip(lon, lat, strict=True)), strict=True)
    return [
        (x, y)
        for x in range(int(math.floor(min(xs))), int(math.floor(max(xs))) + 1)
        for y in range(int(math.floor(min(ys))), int(math.floor(max(ys))) + 1)
    ]


# ---------------------------------------------------------------- 3D Cities tiles


def tile_path(z: int, x: int, y: int) -> Path:
    return CACHE3D / f"3d_tile_{LAYER}_{COLLECTION}_z{z}_{x}_{y}.bin"


def fetch_tiles(z: int, tiles: list[tuple[int, int]], budget: int) -> list[tuple[int, int]]:
    """Download 3D tiles not yet cached (params mirror the URIs the 3D Cities tilesets hand out)."""
    missing = [t for t in tiles if not tile_path(z, *t).exists()]
    if missing and budget <= 0:
        print(f"  offline: {len(missing)} tiles not cached, skipped")
    elif missing:
        load_dotenv(HERE.parent / ".env")
        from worldgen.sources.vexcel import VexcelClient

        cl = VexcelClient(cache_dir=VCACHE / "raw", ledger_path=LEDGER, max_network_calls=budget)
        try:
            for x, y in missing:
                params = {
                    "zoom": z,
                    "tile-x": x,
                    "tile-y": y,
                    "childAOI": COLLECTION,
                    "collection": COLLECTION,
                    "latest": "true",
                }
                meta, body = cl.get("3d/tile", params)
                print(
                    f"  3d/tile z{z} {x},{y}: {meta['status']} {len(body)} B "
                    f"{'cache' if meta['cached'] else 'net'}"
                )
                if meta["status"] == 200 and body[:4] == b"glTF":
                    tile_path(z, x, y).parent.mkdir(parents=True, exist_ok=True)
                    tile_path(z, x, y).write_bytes(body)
        finally:
            cl.close()
    return [t for t in tiles if tile_path(z, *t).exists()]


def decode(paths: list[Path]) -> list[Path]:
    dec = CACHE3D / "decoded"
    outs = [dec / (p.stem + ".glb") for p in paths]
    todo = [
        p for p, o in zip(paths, outs, strict=True) if not o.exists() or o.stat().st_mtime < p.stat().st_mtime
    ]
    if todo:
        subprocess.run(
            ["node", str(HERE / "block_photo_decode.mjs"), str(dec), *map(str, todo)],
            check=True,
            capture_output=True,
        )
    return outs


@dataclass
class Piece:
    name: str
    vertices: np.ndarray  # block frame
    faces: np.ndarray
    uv: np.ndarray  # trimesh convention (v up)
    image: Image.Image


def load_tile(path: Path, rect: tuple[float, float, float, float], skirt_tol: float = 0.3) -> Piece:
    """Decoded tile -> block frame (ellipsoidal height), minus its skirts (walls hanging off tile edges)."""
    scene = trimesh.load(path, force="scene")
    meshes = []
    for node in scene.graph.nodes_geometry:
        tf, gname = scene.graph[node]
        g = scene.geometry[gname].copy()
        g.apply_transform(tf)
        meshes.append(g)
    assert len(meshes) == 1, f"{path.name}: expected one mesh per tile, got {len(meshes)}"
    m = meshes[0]
    v = np.asarray(m.vertices, dtype=np.float64)
    v = ecef_to_block(
        np.stack([v[:, 0], -v[:, 2], v[:, 1]], axis=1)
    )  # 3D Tiles glTF is Y-up: ECEF = (x, -z, y)
    f = np.asarray(m.faces)
    fv = v[f]
    x0, x1, z0, z1 = rect
    skirt = np.zeros(len(f), bool)
    for axis, edge in ((0, x0), (0, x1), (2, z0), (2, z1)):
        skirt |= (np.abs(fv[:, :, axis] - edge) < skirt_tol).all(axis=1)
    p = Piece(
        path.stem,
        v,
        f,
        np.asarray(m.visual.uv, np.float32),
        m.visual.material.baseColorTexture.convert("RGB"),
    )
    return submesh(p, ~skirt, p.name)


def submesh(p: Piece, keep: np.ndarray, name: str) -> Piece | None:
    if not keep.any():
        return None
    used, inv = np.unique(p.faces[keep], return_inverse=True)
    return Piece(name, p.vertices[used], inv.reshape(-1, 3), p.uv[used], p.image)


# ---------------------------------------------------------------- DSM + ortho sides


def _xmp_bounds(info: dict) -> tuple[float, float, float, float]:
    """Vexcel XMP <Vexcel:geometry> polygon -> (lon_min, lon_max, lat_min, lat_max)."""
    xmp = info.get("xmp", b"")
    xmp = xmp.decode("utf-8", "replace") if isinstance(xmp, bytes) else str(xmp)
    m = re.search(r"<Vexcel:geometry>(POLYGON[^<]+)</Vexcel:geometry>", xmp)
    if not m:
        raise ValueError("no Vexcel:geometry in XMP")
    b = shapely_wkt.loads(m.group(1)).bounds
    return b[0], b[2], b[1], b[3]


def _read_tiled_float_tiff(path: Path, tags: dict) -> np.ndarray:
    """Uncompressed or deflate, tiled, single-band float32 TIFF, no predictor (what dsm/extract returns)."""
    raw = path.read_bytes()
    order = ">" if raw[:2] == b"MM" else "<"
    if tags.get(317, 1) != 1 or tags.get(277, 1) != 1:
        raise ValueError("unsupported TIFF layout (predictor or several bands)")
    w, h, tw, th = tags[256], tags[257], tags[322], tags[323]
    comp = tags.get(259, 1)
    out = np.empty((math.ceil(h / th) * th, math.ceil(w / tw) * tw), np.float32)
    per_row = math.ceil(w / tw)
    for k, (o, n) in enumerate(zip(tags[324], tags[325], strict=True)):
        buf = raw[o : o + n]
        buf = zlib.decompress(buf) if comp in (8, 32946) else buf
        r, c = divmod(k, per_row)
        out[r * th : (r + 1) * th, c * tw : (c + 1) * tw] = np.frombuffer(buf, f"{order}f4").reshape(th, tw)
    return out[:h, :w].copy()


class Dsm:
    """Vexcel DSM GeoTIFF (EPSG:3857, float32 metres over EGM2008, nodata -9999)."""

    def __init__(self, path: Path) -> None:
        im = Image.open(path)
        tags = dict(im.tag_v2)  # header only: Pillow mis-decodes this big-endian tiled float32 TIFF's pixels
        self.h = _read_tiled_float_tiff(path, tags)
        i0, j0, _, x0, y0, _ = tags[33922]
        self.sx, self.sy = tags[33550][0], tags[33550][1]
        self.x0, self.y0, self.i0, self.j0 = x0, y0, i0, j0  # PixelIsArea: (i0, j0) is a pixel corner
        valid = self.h > -1000
        if not valid.all():
            self.h[~valid] = np.median(self.h[valid])

    def sample(self, lon: np.ndarray, lat: np.ndarray, grid: np.ndarray | None = None) -> np.ndarray:
        mx, my = merc(lon, lat)
        col = self.i0 + (mx - self.x0) / self.sx - 0.5
        row = self.j0 + (self.y0 - my) / self.sy - 0.5
        return ndimage.map_coordinates(self.h if grid is None else grid, [row, col], order=1, mode="nearest")


def footprints_xz() -> list[Polygon]:
    d = json.loads(STRUCTURES.read_text())
    fc = json.loads(d["content"][0]["text"]) if "content" in d else d
    out = []
    for ft in fc["features"]:
        g = ft["geometry"]
        geom = shapely_wkt.loads(g) if isinstance(g, str) else shapely.geometry.shape(g)
        polys = geom.geoms if isinstance(geom, MultiPolygon) else [geom]
        for poly in polys:
            xs, ys = poly.exterior.xy
            out.append(Polygon(lonlat_to_xz(np.array(xs), np.array(ys))))
    return out


class Surface:
    """DSM + ortho sampled on regular block-frame grids: ground, side strips and canopy sheets."""

    def __init__(self, dsm: Dsm, dsm_ground0: float, fps: list[Polygon]) -> None:
        self.dsm, self.g0 = dsm, dsm_ground0
        self.ortho = Image.open(ORTHO)
        self.ortho.load()
        lon_min, lon_max, lat_min, lat_max = _xmp_bounds(self.ortho.info)
        self.m_min, self.m_max = merc(lon_min, lat_min), merc(lon_max, lat_max)
        # Bare ground: an opening of the DSM over ~25 m, smoothed (no DTM on this account: dtm/extract 404s).
        px = max(3, int(round(25.0 / (dsm.sx * math.cos(math.radians(LAT0))))))
        self.ground = ndimage.uniform_filter(ndimage.grey_opening(dsm.h, size=(px, px)), size=px)
        self.smooth = ndimage.median_filter(dsm.h, size=3)
        self.fp_union = unary_union([f.buffer(0.4) for f in fps])
        self.ortho_ground = self._ground_ortho(fps)

    def _ground_ortho(self, fps: list[Polygon]) -> Image.Image:
        """The ortho with tree crowns painted out (filled from the surrounding ground), so ground under cut
        or floating canopies reads as lawn and pavement instead of a flattened tree photo."""
        W, H = self.ortho.size
        f = 4  # work at 1/4 resolution for the mask and the fill, add the original detail back at full res
        w, h = W // f, H // f
        cols, rows = np.meshgrid((np.arange(w) + 0.5) * f, (np.arange(h) + 0.5) * f)
        mx = self.m_min[0] + cols / W * (self.m_max[0] - self.m_min[0])
        my = self.m_max[1] - rows / H * (self.m_max[1] - self.m_min[1])
        dc = self.dsm.i0 + (mx - self.dsm.x0) / self.dsm.sx - 0.5
        dr = self.dsm.j0 + (self.dsm.y0 - my) / self.dsm.sy - 0.5
        hag = ndimage.map_coordinates(self.smooth - self.ground, [dr, dc], order=1, mode="nearest")
        lon = np.degrees(mx / _A)
        lat = np.degrees(2 * np.arctan(np.exp(my / _A)) - np.pi / 2)
        xz = lonlat_to_xz(lon.ravel(), lat.ravel())
        inside = shapely.contains_xy(self.fp_union, xz[:, 0], xz[:, 1]).reshape(h, w)
        tree = ndimage.binary_dilation((hag > 1.8) & ~inside, iterations=2)
        small = np.asarray(self.ortho.resize((w, h), Image.BILINEAR), np.float32)
        valid = (~tree).astype(np.float32)
        fill = np.zeros_like(small)
        for sigma in (40, 15, 5):  # px at 1/4 res (~3 m, 1.1 m, 0.4 m): coarse to fine normalised convolution
            num = np.stack([ndimage.gaussian_filter(small[..., k] * valid, sigma) for k in range(3)], -1)
            den = ndimage.gaussian_filter(valid, sigma)[..., None]
            est = num / np.maximum(den, 1e-4)
            fill = np.where(den > 0.05, est, fill) if sigma == 40 else np.where(den > 0.3, est, fill)
        full = np.asarray(self.ortho, np.float32)
        mask_full = (
            np.asarray(
                Image.fromarray((tree * 255).astype(np.uint8)).resize((W, H), Image.BILINEAR), np.float32
            )[..., None]
            / 255.0
        )
        fill_full = np.stack(
            [np.asarray(Image.fromarray(fill[..., k]).resize((W, H), Image.BILINEAR)) for k in range(3)], -1
        )
        detail = full - np.stack([ndimage.uniform_filter(full[..., k], 9) for k in range(3)], -1)
        out = full * (1 - mask_full) + (fill_full + 0.35 * detail) * mask_full
        return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))

    def ground_y(self, x: np.ndarray, z: np.ndarray) -> np.ndarray:
        return self.dsm.sample(*xz_to_lonlat(x, z), self.ground) - self.g0

    def trunks(
        self, half_m: float, canopy_xz: np.ndarray | None = None, core_x: tuple[float, float] | None = None
    ) -> trimesh.Trimesh | None:
        """Plain bark-coloured trunks under tree crowns (DSM local maxima, outside buildings), so cut
        crowns read as trees from the street rather than floating rocks. Not photo-textured."""
        hag = self.smooth - self.ground
        px = max(3, int(round(5.0 / (self.dsm.sx * math.cos(math.radians(LAT0))))))
        peak = (hag == ndimage.maximum_filter(hag, size=px)) & (hag > 4.5)
        rows, cols = np.nonzero(peak)
        mx = self.dsm.x0 + (cols + 0.5 - self.dsm.i0) * self.dsm.sx
        my = self.dsm.y0 - (rows + 0.5 - self.dsm.j0) * self.dsm.sy
        lon = np.degrees(mx / _A)
        lat = np.degrees(2 * np.arctan(np.exp(my / _A)) - np.pi / 2)
        xz = lonlat_to_xz(lon, lat)
        ok = (np.abs(xz[:, 0]) <= half_m) & (np.abs(xz[:, 1]) <= half_m)
        ok &= ~shapely.contains_xy(self.fp_union.buffer(1.0), xz[:, 0], xz[:, 1])
        if canopy_xz is not None and core_x is not None and len(canopy_xz):
            # In the core, the crowns are the 2023 mesh's: only plant a trunk under an actual mesh crown.
            from scipy.spatial import cKDTree

            n = np.array([len(i) for i in cKDTree(canopy_xz).query_ball_point(xz, r=2.5)])
            in_core = (xz[:, 0] >= core_x[0]) & (xz[:, 0] <= core_x[1])
            ok &= ~in_core | (n >= 6)
        parts = []
        for (x, z), h in zip(xz[ok], hag[rows, cols][ok], strict=True):
            gy = float(self.ground_y(np.array([x]), np.array([z]))[0])
            height = float(min(TREE_CUT_M + 1.5, 0.55 * h))
            r = float(np.clip(0.022 * h, 0.1, 0.28))
            t = trimesh.creation.cylinder(radius=r, height=height, sections=10)
            t.apply_transform(trimesh.transformations.rotation_matrix(-math.pi / 2, [1, 0, 0]))
            t.apply_translation([x, gy + height / 2, z])
            parts.append(t)
        if not parts:
            return None
        m = trimesh.util.concatenate(parts)
        # The whole file is unlit, so bake a soft sun term into the colour to keep trunks round, not posts.
        sun = np.array([-0.5, 0.6, 0.6]) / np.linalg.norm([-0.5, 0.6, 0.6])
        shade = 0.6 + 0.5 * np.clip(m.vertex_normals @ sun, 0, 1)
        # A 16 px bark ramp indexed by u = shade (an explicit non-metal material: glTF's default is metallic).
        ramp = np.linspace(0.55, 1.1, 16)[:, None] * np.array([118, 104, 88], float)[None, :]
        img = Image.fromarray(np.clip(ramp, 0, 255).astype(np.uint8)[None, :, :].repeat(2, axis=0))
        uv = np.stack([(shade - 0.55) / 0.55 * 15 / 16 + 1 / 32, np.full(len(shade), 0.5)], 1)
        mat = trimesh.visual.material.PBRMaterial(
            name="tree_trunk", baseColorTexture=img, metallicFactor=0.0, roughnessFactor=1.0
        )
        m.visual = trimesh.visual.TextureVisuals(uv=uv, material=mat)
        print(f"trunks: {len(parts)}")
        return m

    def grid(
        self,
        name: str,
        xr: tuple[float, float],
        zr: tuple[float, float],
        step: float,
        mode: str,
        tex_px: int = 4096,
        drop: float = 0.0,
    ) -> Piece | None:
        """mode: 'ground' (bare ground everywhere), 'side' (buildings inside footprints, ground elsewhere),
        'canopy' (only cells that are tall and outside footprints, at DSM height; open edges)."""
        (xa, xb), (za, zb) = xr, zr
        nx, nz = int(math.ceil((xb - xa) / step)) + 1, int(math.ceil((zb - za) / step)) + 1
        gx, gz = np.meshgrid(np.linspace(xa, xb, nx), np.linspace(za, zb, nz))
        lon, lat = xz_to_lonlat(gx.ravel(), gz.ravel())
        h = self.dsm.sample(lon, lat, self.smooth) - self.g0
        g = self.dsm.sample(lon, lat, self.ground) - self.g0
        inside = shapely.contains_xy(self.fp_union, gx.ravel(), gz.ravel())
        tree = ~inside & ((h - g) >= 2.2)
        y = {"ground": g, "side": np.where(inside, h, g), "canopy": h}[mode] - drop
        verts = np.stack([gx.ravel(), y, gz.ravel()], axis=1)
        idx = np.arange(nx * nz).reshape(nz, nx)
        a, b, c, d = idx[:-1, :-1].ravel(), idx[:-1, 1:].ravel(), idx[1:, :-1].ravel(), idx[1:, 1:].ravel()
        faces = np.concatenate([np.stack([a, c, b], 1), np.stack([b, c, d], 1)])  # CCW seen from +Y
        if mode == "canopy":
            t = tree[faces].all(axis=1)
            if not t.any():
                return None
            faces = faces[t]
        mx, my = merc(lon, lat)
        u = (mx - self.m_min[0]) / (self.m_max[0] - self.m_min[0])
        vtop = (self.m_max[1] - my) / (self.m_max[1] - self.m_min[1])
        W, H = self.ortho.size
        c0, c1 = int(max(0, math.floor(u.min() * W) - 2)), int(min(W, math.ceil(u.max() * W) + 2))
        r0, r1 = int(max(0, math.floor(vtop.min() * H) - 2)), int(min(H, math.ceil(vtop.max() * H) + 2))
        crop = (self.ortho if mode == "canopy" else self.ortho_ground).crop((c0, r0, c1, r1))
        scale = min(1.0, tex_px / max(crop.size))
        if scale < 1:
            crop = crop.resize(
                (max(1, int(crop.size[0] * scale)), max(1, int(crop.size[1] * scale))), Image.LANCZOS
            )
        uv = np.stack([(u * W - c0) / (c1 - c0), 1.0 - (vtop * H - r0) / (r1 - r0)], 1).astype(np.float32)
        p = Piece(name, verts, faces, uv, crop)
        return submesh(p, np.ones(len(faces), bool), name) if mode == "canopy" else p


# ---------------------------------------------------------------- assembly


def hero_footprints(fps: list[Polygon]) -> dict[str, Polygon]:
    out = {}
    for lid, (lat, lon) in HEROES.items():
        p = shapely.geometry.Point(*lonlat_to_xz(lon, lat)[0])
        best = min(fps, key=lambda f: f.distance(p))
        if best.distance(p) < 8.0:
            out[lid] = best
    return out


def to_trimesh(p: Piece) -> trimesh.Trimesh:
    # Double-sided: cut canopies are open shells seen from below.
    mat = trimesh.visual.material.PBRMaterial(
        name=p.name, baseColorTexture=p.image, metallicFactor=0.0, roughnessFactor=1.0, doubleSided=True
    )
    return trimesh.Trimesh(
        p.vertices, p.faces, visual=trimesh.visual.TextureVisuals(uv=p.uv, material=mat), process=False
    )


def collision(fps: list[Polygon], dsm: Dsm, dsm_ground0: float, half_m: float) -> trimesh.Trimesh:
    """Ground slab + one prism per structure footprint, height = 85th percentile of DSM inside it."""
    parts = [
        trimesh.creation.box(
            extents=[2 * half_m, 0.5, 2 * half_m],
            transform=trimesh.transformations.translation_matrix([0, -0.25, 0]),
        )
    ]
    for fp in fps:
        if not fp.is_valid or fp.area < 6 or not fp.intersects(shapely.box(-half_m, -half_m, half_m, half_m)):
            continue
        simple = fp.simplify(0.4, preserve_topology=True)
        x0, z0, x1, z1 = fp.bounds
        gx, gz = np.meshgrid(np.arange(x0, x1, 0.5), np.arange(z0, z1, 0.5))
        ins = shapely.contains_xy(fp, gx.ravel(), gz.ravel())
        if ins.sum() < 4:
            continue
        lon, lat = xz_to_lonlat(gx.ravel()[ins], gz.ravel()[ins])
        top = float(np.percentile(dsm.sample(lon, lat), 85)) - dsm_ground0
        if top < 1.5:
            continue
        # extrude_polygon builds along +Z from a polygon in XY: map (x, z_block) -> XY, rotate Z-up -> Y-up.
        poly = Polygon([(x, -z) for x, z in simple.exterior.coords])
        prism = trimesh.creation.extrude_polygon(poly, height=top)
        prism.apply_transform(trimesh.transformations.rotation_matrix(-math.pi / 2, [1, 0, 0]))
        parts.append(prism)
    m = trimesh.util.concatenate(parts)
    m.visual = trimesh.visual.TextureVisuals(
        material=trimesh.visual.material.PBRMaterial(
            name="collision", baseColorFactor=[128, 128, 128, 255], metallicFactor=0.0, roughnessFactor=1.0
        )
    )
    return m


def build(
    budget: int,
    half_m: float,
    side_step: float,
    texture_size: int,
    cut_trees: bool = True,
    ground_mesh: bool = False,
    trunks: bool = True,
) -> dict:
    t0 = time.time()
    core = [t for t in tiles_covering(CORE_ZOOM, half_m) if t[0] in CORE_COLS]
    have = fetch_tiles(CORE_ZOOM, core, budget)
    print(f"core: {len(have)}/{len(core)} z{CORE_ZOOM} tiles")
    decoded = decode([tile_path(CORE_ZOOM, *t) for t in have])
    tiles = [
        p
        for p in (load_tile(d, tile_rect_xz(CORE_ZOOM, *t)) for d, t in zip(decoded, have, strict=True))
        if p
    ]

    # Height datum: the street at the origin is y = 0 in both datasets (3D mesh: ellipsoid; DSM: EGM2008).
    v = np.concatenate([p.vertices for p in tiles])
    near = np.hypot(v[:, 0], v[:, 2]) < 15.0
    mesh_ground0 = float(np.percentile(v[near, 1], 10))
    for p in tiles:
        p.vertices[:, 1] -= mesh_ground0
    dsm = Dsm(DSM)
    gx, gz = np.meshgrid(np.linspace(-15, 15, 31), np.linspace(-15, 15, 31))
    disk = np.hypot(gx, gz).ravel() < 15
    dsm_ground0 = float(np.percentile(dsm.sample(*xz_to_lonlat(gx.ravel()[disk], gz.ravel()[disk])), 10))
    print(f"ground at origin: mesh {mesh_ground0:.2f} m ellipsoidal, DSM {dsm_ground0:.2f} m EGM2008")

    # Crop core tiles to the square; side strips fill x outside the core columns.
    core_x0 = min(tile_rect_xz(CORE_ZOOM, x, y)[0] for x, y in have)
    core_x1 = max(tile_rect_xz(CORE_ZOOM, x, y)[1] for x, y in have)
    fps = footprints_xz()
    surf = Surface(dsm, dsm_ground0, fps)
    heroes = hero_footprints(fps)
    hero_zone = {lid: fp.buffer(1.5) for lid, fp in heroes.items()}
    bldg_zone = unary_union([f.buffer(0.5) for f in fps])
    core_pieces: list[Piece] = []
    hero_pieces: dict[str, list[Piece]] = {lid: [] for lid in heroes}
    cut_total = 0
    for p in tiles:
        fv = p.vertices[p.faces]
        c = fv.mean(axis=1)
        keep = (np.abs(c[:, 0]) <= half_m) & (np.abs(c[:, 2]) <= half_m)
        # Photogrammetry trees (and cars, fences) come down to the ground as solid blobs. Outside buildings,
        # drop every face that reaches below TREE_CUT_M: crowns stay up, the street opens up,
        # and the ortho-draped ground layer below shows through.
        if cut_trees:
            hag = fv[:, :, 1] - surf.ground_y(fv[:, :, 0].ravel(), fv[:, :, 2].ravel()).reshape(-1, 3)
            # ground_mesh=False also drops the mesh's own ground outside buildings: the 2026 ortho layer is
            # sharper and seamless, and mixing both gives a patchwork of shards.
            low = hag.min(axis=1) < TREE_CUT_M
            band = low if not ground_mesh else low & (hag.max(axis=1) > GROUND_TOL_M)
            out_bldg = ~shapely.contains_xy(bldg_zone, c[:, 0], c[:, 2])
            cut = band & out_bldg
            cut_total += int((keep & cut).sum())
            keep &= ~cut
        taken = np.zeros(len(c), bool)
        for lid, zone in hero_zone.items():
            m = keep & shapely.contains_xy(zone, c[:, 0], c[:, 2]) & (c[:, 1] > 0.25)
            if (sp := submesh(p, m, f"{p.name}_{lid}")) is not None:
                hero_pieces[lid].append(sp)
            taken |= m
        if (sp := submesh(p, keep & ~taken, p.name)) is not None:
            core_pieces.append(sp)
    print(f"tree/fence skirt faces cut: {cut_total}")

    sides: list[Piece] = []
    ranges = [r for r in ((-half_m, core_x0), (core_x1, half_m)) if r[1] - r[0] > 0.5]
    for k, xr in enumerate(ranges):
        sides.append(surf.grid(f"dsm_ortho_side_{k}", xr, (-half_m, half_m), side_step, "side"))
        if cp := surf.grid(f"dsm_canopy_side_{k}", xr, (-half_m, half_m), side_step, "canopy", tex_px=2048):
            sides.append(cp)
    # Ground under the core mesh (shows where tree skirts were cut), a few cm below the mesh's own ground.
    sides.append(
        surf.grid(
            "ortho_ground_core",
            (core_x0, core_x1),
            (-half_m, half_m),
            1.0,
            "ground",
            drop=0.05 if ground_mesh else 0.0,
        )
    )
    canopy = []
    for p in core_pieces:
        c = p.vertices[p.faces].mean(axis=1)
        hi = (c[:, 1] > TREE_CUT_M) & ~shapely.contains_xy(bldg_zone, c[:, 0], c[:, 2])
        canopy.append(c[hi][:, [0, 2]])
    canopy_xz = np.concatenate(canopy) if canopy else np.zeros((0, 2))
    trunk_mesh = surf.trunks(half_m, canopy_xz, (core_x0, core_x1)) if (cut_trees and trunks) else None

    scene = trimesh.Scene()
    for p in core_pieces + sides:
        scene.add_geometry(to_trimesh(p), node_name=p.name, geom_name=p.name)
    for lid, ps in hero_pieces.items():
        for i, p in enumerate(ps):
            scene.add_geometry(
                to_trimesh(p), node_name=f"hero_{lid}" + (f"_{i}" if i else ""), geom_name=p.name
            )
    if trunk_mesh is not None:
        scene.add_geometry(trunk_mesh, node_name="tree_trunks", geom_name="tree_trunks")
    WORK.mkdir(parents=True, exist_ok=True)
    PHOTO.mkdir(parents=True, exist_ok=True)
    raw = WORK / "render_raw.glb"
    scene.export(raw)
    compress(raw, PHOTO / "render.glb", texture_size)

    col = collision(fps, dsm, dsm_ground0, half_m)
    col.export(PHOTO / "collision.glb")

    all_pieces = core_pieces + sides + [p for ps in hero_pieces.values() for p in ps]
    stats = {
        "core_tiles": [f"z{CORE_ZOOM}/{x}/{y}" for x, y in have],
        "core_x_range_m": [round(core_x0, 2), round(core_x1, 2)],
        "side_x_ranges_m": [[round(a, 2), round(b, 2)] for a, b in ranges],
        "triangles": {
            "core_3d_cities": int(sum(len(p.faces) for p in core_pieces)),
            "heroes": {lid: int(sum(len(p.faces) for p in ps)) for lid, ps in hero_pieces.items()},
            "sides_dsm_ortho": int(sum(len(p.faces) for p in sides)),
            "total": int(sum(len(p.faces) for p in all_pieces)),
            "collision": int(len(col.faces)),
        },
        "textures": len(all_pieces),
        "ground_origin": {"mesh_ellipsoid_m": round(mesh_ground0, 2), "dsm_egm2008_m": round(dsm_ground0, 2)},
        "render_glb_bytes": (PHOTO / "render.glb").stat().st_size,
        "collision_glb_bytes": (PHOTO / "collision.glb").stat().st_size,
        "vexcel_calls_photo_ledger": sum(1 for _ in open(LEDGER)) if LEDGER.exists() else 0,
        "build_s": round(time.time() - t0, 1),
    }
    (WORK / "stats.json").write_text(json.dumps(stats, indent=1))
    print(json.dumps(stats, indent=1))
    return stats


def compress(src: Path, dest: Path, texture_size: int) -> None:
    """Unlit (photos carry their own light) + meshopt + WebP like the interiors (house_mesh.compress_glb),
    plus meshopt simplification: the DSM grids are mostly flat, so 6 cm of error drops ~80% of triangles."""
    cli = HERE / "node_modules" / ".bin" / "gltf-transform"
    if not cli.exists():
        raise FileNotFoundError("run `npm i` in worldgen/ to install gltf-transform")
    unlit = src.with_name(src.stem + "_unlit.glb")
    subprocess.run([str(cli), "unlit", str(src), str(unlit)], check=True, capture_output=True)
    subprocess.run(
        [
            str(cli),
            "optimize",
            str(unlit),
            str(dest),
            "--compress",
            "meshopt",
            "--texture-compress",
            "webp",
            "--texture-size",
            str(texture_size),
            "--simplify",
            "true",
            "--simplify-error",
            "0.0002",  # ~6 cm here
            "--join",
            "false",
            "--flatten",
            "false",
            "--instance",
            "false",
            "--palette",
            "false",
        ],
        check=True,
        capture_output=True,
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--budget", type=int, default=19, help="max Vexcel network calls in ledger_photo.jsonl")
    ap.add_argument("--offline", action="store_true", help="use cached tiles only")
    ap.add_argument("--half", type=float, default=HALF_M)
    ap.add_argument("--side-step", type=float, default=0.6, help="DSM grid spacing for the side strips (m)")
    ap.add_argument("--texture-size", type=int, default=4096)
    ap.add_argument("--keep-tree-skirts", action="store_true", help="do not cut tree blobs above the street")
    args = ap.parse_args(argv)
    build(
        0 if args.offline else args.budget,
        args.half,
        args.side_step,
        args.texture_size,
        cut_trees=not args.keep_tree_skirts,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
