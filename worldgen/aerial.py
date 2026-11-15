"""Aerial data (Vexcel) around one house, in the house's Matterport plan frame: the surface height model
(DSM), the ortho photo, the building outline and the roof attributes.

The Matterport scan sees a house from the ground: walls, yard, the underside of the eaves, and of the roof
only what a tripod in the yard can see. The aerial data is the other half: the roof's shape and colour from
above. exterior.py uses it for the roof (roof.fit_roof on the heights, the ortho as the colour of what no
ground camera saw).

Nothing here calls the network. The rasters are the ones the block builders cached for the whole block
(out/block_compare/cache/vexcel: `dsm_extract_urban_bbox<S>_<W>_<N>_<E>_z<zoom>.tif` and
`ortho_extract_urban_bbox..._z<zoom>.jpg`, Vexcel REST `dsm/extract` and `ortho/extract` with layer urban,
srid 4326, crop bounding_box, zoom 20 and 21: about 13 cm and 6.5 cm on the ground here). The outline and
attributes are the `property/extract` answer worldgen.costar cached for the listing (out/cache/vexcel). A
house whose block has no cached rasters gets `load(...) = None` and exterior.py falls back to the scan.

Frames: Vexcel rasters are EPSG:3857 (Web Mercator). Plan (x, y) = Matterport model x, y; house-local is
(x, z = -y); the block frame is position + rotY(heading) * house-local (Listing.world, worldgen/costar.py).
"""

from __future__ import annotations

import json
import math
import re
import zlib
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage
from shapely import wkt
from shapely.geometry import Polygon

from worldgen import geo

R_MERC = 6378137.0
BLOCK_CACHE = Path("block_compare") / "cache" / "vexcel"  # under out/: whole-block rasters
LISTING_CACHE = Path("cache") / "vexcel"  # under out/: per-listing property/extract (worldgen.costar)
_BBOX = re.compile(r"bbox(-?\d+\.\d+)_(-?\d+\.\d+)_(-?\d+\.\d+)_(-?\d+\.\d+)_z(\d+)")
# Roof attributes copied into the build report when Vexcel has them (property/extract, per structure).
ATTRIBUTES = (
    "roof-characteristics.material.class",
    "roof-characteristics.shape.value",
    "roof-characteristics.tree-cover.percent",
    "roof-characteristics.condition",
    "roof-objects.chimney.count",
    "roof-objects.roof-vent.count",
    "ground-elevation.value",
)


def merc(lat: np.ndarray, lon: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    return np.radians(lon) * R_MERC, np.log(np.tan(np.pi / 4 + np.radians(lat) / 2)) * R_MERC


def read_height_tiff(path: Path) -> tuple[np.ndarray, float, float, float]:
    """(heights (rows, cols) float32 with NaN for no data, x of the left edge, y of the top edge, pixel
    size), all EPSG:3857 metres, from a tiled, deflate-compressed float32 GeoTIFF (what dsm/extract sends).
    Pillow reads the tags but not these tiles, so the tiles are inflated here."""
    with Image.open(path) as im:
        t = dict(im.tag_v2)
        e = ">" if im.tag_v2.prefix == b"MM" else "<"
        w, h = im.size
    if 322 not in t or t.get(259) not in (8, 32946) or t.get(317, 1) != 1:
        raise ValueError(f"{path.name}: expected a tiled deflate GeoTIFF without predictor")
    tw, th = int(t[322]), int(t[323])
    buf = path.read_bytes()
    out = np.full((h, w), np.nan, np.float32)
    nx = math.ceil(w / tw)
    for i, (o, c) in enumerate(zip(t[324], t[325], strict=True)):
        tile = np.frombuffer(zlib.decompress(buf[o : o + c]), dtype=e + "f4").reshape(th, tw)
        ty, tx = divmod(i, nx)
        r0, c0 = ty * th, tx * tw
        rh, cw = min(th, h - r0), min(tw, w - c0)
        out[r0 : r0 + rh, c0 : c0 + cw] = tile[:rh, :cw]
    out[(out < -1000) | (out > 10000)] = np.nan
    px = float(t[33550][0])
    tie = t[33922]  # raster point (i, j) -> model (X, Y); PixelIsArea: (i, j) is a pixel corner
    return out, float(tie[3] - tie[0] * px), float(tie[4] + tie[1] * px), px


def _nan_filled(a: np.ndarray) -> np.ndarray:
    bad = ~np.isfinite(a)
    if not bad.any():
        return a
    idx = ndimage.distance_transform_edt(bad, return_distances=False, return_indices=True)
    return a[tuple(idx)]


@dataclass
class Aerial:
    lat0: float  # block origin
    lon0: float
    position: tuple[float, float]  # Listing.world.position (x, z), block frame
    heading_deg: float
    dsm: np.ndarray  # (rows, cols) float32 elevations, holes filled from the nearest pixel
    dsm_x0: float
    dsm_y0: float
    dsm_px: float
    ortho: np.ndarray  # (rows, cols, 3) uint8
    ortho_box: tuple[float, float, float, float]  # EPSG:3857 left, top, right, bottom
    footprints: list[Polygon] = field(default_factory=list)  # Vexcel structures on the parcel, plan frame
    attributes: dict = field(default_factory=dict)
    z0: float = 0.0  # DSM elevation of plan z = 0 (set by level)
    source: dict = field(default_factory=dict)

    def _block(self, x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Plan (x, y) -> block (x, z): house-local is (x, -y)."""
        h = math.radians(self.heading_deg)
        c, s = math.cos(h), math.sin(h)
        x, z = np.asarray(x, float), -np.asarray(y, float)
        return self.position[0] + x * c + z * s, self.position[1] - x * s + z * c

    def to_merc(self, x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        bx, bz = self._block(x, y)
        m, n = geo._radii(self.lat0)
        lat = self.lat0 + np.degrees(-bz / m)
        lon = self.lon0 + np.degrees(bx / (n * math.cos(math.radians(self.lat0))))
        return merc(lat, lon)

    def from_block(self, bx: float, bz: float) -> tuple[float, float]:
        """Block (x, z) -> plan (x, y)."""
        h = math.radians(self.heading_deg)
        c, s = math.cos(h), math.sin(h)
        dx, dz = bx - self.position[0], bz - self.position[1]
        return dx * c - dz * s, -(dx * s + dz * c)

    def elevation(self, x: np.ndarray, y: np.ndarray) -> np.ndarray:
        """DSM elevation (its own datum) at plan points, bilinear."""
        mx, my = self.to_merc(x, y)
        col = (mx - self.dsm_x0) / self.dsm_px - 0.5
        row = (self.dsm_y0 - my) / self.dsm_px - 0.5
        return ndimage.map_coordinates(self.dsm, [row, col], order=1, mode="nearest")

    def surface(self, x: np.ndarray, y: np.ndarray) -> np.ndarray:
        """Height of whatever is highest at plan (x, y) (roof, canopy, ground), in plan z (after level)."""
        return self.elevation(x, y) - self.z0

    def colour(self, x: np.ndarray, y: np.ndarray) -> np.ndarray:
        """Ortho colour (n, 3) float at plan points, bilinear."""
        mx, my = self.to_merc(x, y)
        h, w = self.ortho.shape[:2]
        x0, y0, x1, y1 = self.ortho_box
        col = (mx - x0) / (x1 - x0) * w - 0.5
        row = (y0 - my) / (y0 - y1) * h - 0.5
        if getattr(self, "_bands", None) is None:
            self._bands = [np.ascontiguousarray(self.ortho[..., k], dtype=np.float32) for k in range(3)]
        return np.stack(
            [ndimage.map_coordinates(b, [row, col], order=1, mode="nearest") for b in self._bands], axis=-1
        )

    def level(self, x: np.ndarray, y: np.ndarray, z: np.ndarray) -> float:
        """Tie the DSM's datum to the scan's: `z` are scanned ground heights at plan points (x, y). Under
        trees, cars and eaves the DSM is above the ground, so the offset is the median over the samples
        whose difference lies within 0.25 m of the lowest tenth's median (open ground). Returns the spread
        (median absolute deviation) of those, a check on the registration."""
        d = self.elevation(x, y) - np.asarray(z, float)
        d = d[np.isfinite(d)]
        if len(d) < 20:
            raise ValueError("too few ground samples to level the surface model")
        low = np.median(d[d <= np.percentile(d, 10)])
        open_ground = d[np.abs(d - low) < 0.25]
        self.z0 = float(np.median(open_ground))
        return float(np.median(np.abs(open_ground - self.z0)))

    def roof_outline(self, house: Polygon) -> Polygon | None:
        """The Vexcel structure that overlaps `house` (plan) most: Vexcel traces the roof's edge seen from
        above, so it includes eaves, porch roofs and attached garages the floor plan may lack."""
        best = max(self.footprints, key=lambda f: f.intersection(house).area, default=None)
        if best is None or best.intersection(house).area < 0.5 * house.area:
            return None
        return best


def _covering(folder: Path, prefix: str, suffix: str, lat: float, lon: float, margin_deg: float):
    """The cached raster named `<prefix>...bbox<S>_<W>_<N>_<E>_z<zoom><suffix>` that contains (lat, lon)
    with a margin, highest zoom first. Returns (path, (s, w, n, e)) or None."""
    found = []
    for p in sorted(folder.glob(f"{prefix}*{suffix}")):
        m = _BBOX.search(p.name)
        if not m:
            continue
        s, w, n, e = (float(m.group(k)) for k in range(1, 5))
        if s + margin_deg <= lat <= n - margin_deg and w + margin_deg <= lon <= e - margin_deg:
            found.append((int(m.group(5)), p, (s, w, n, e)))
    if not found:
        return None
    _, path, box = max(found, key=lambda f: f[0])
    return path, box


def _property(out: Path, lat: float, lon: float) -> dict | None:
    """The cached property/extract answer for the listing's point (the request worldgen.costar makes),
    read from the cache only."""
    from worldgen.sources.vexcel import VexcelClient

    params = {"wkt": f"POINT({lon} {lat})", "srid": 4326}
    body = out / LISTING_CACHE / f"{VexcelClient.cache_key('property/extract', params)}.body"
    try:
        return json.loads(body.read_text())
    except (OSError, ValueError):
        return None


def load(out: Path, block: dict, listing: dict) -> Aerial | None:
    """Aerial layers for `listing` (an entry of listings/index.json with lat, lon and world), or None when
    the listing has no world pose or no cached rasters cover it."""
    world = listing.get("world") or {}
    if not world.get("position") or listing.get("lat") is None:
        return None
    lat, lon = float(listing["lat"]), float(listing["lon"])
    margin = 40.0 / 111_000.0  # the house and its lot must be inside the raster
    dsm = _covering(out / BLOCK_CACHE, "dsm_extract", ".tif", lat, lon, margin)
    ortho = _covering(out / BLOCK_CACHE, "ortho_extract", ".jpg", lat, lon, margin)
    if dsm is None or ortho is None:
        return None
    heights, x0, y0, px = read_height_tiff(dsm[0])
    s, w, n, e = ortho[1]
    mx0, my0 = merc(n, w)  # top left
    mx1, my1 = merc(s, e)  # bottom right
    c = block["center"]
    a = Aerial(
        lat0=float(c["lat"]),
        lon0=float(c["lon"]),
        position=(float(world["position"][0]), float(world["position"][2])),
        heading_deg=float(world["heading_deg"]),
        dsm=_nan_filled(heights),
        dsm_x0=x0,
        dsm_y0=y0,
        dsm_px=px,
        ortho=np.asarray(Image.open(ortho[0]).convert("RGB")),
        ortho_box=(float(mx0), float(my0), float(mx1), float(my1)),
        source={"dsm": dsm[0].name, "ortho": ortho[0].name},
    )
    prop = _property(out, lat, lon)
    for feature in (prop or {}).get("features") or []:
        for st in (feature.get("properties") or {}).get("structures") or []:
            if not st.get("footprint.wkt"):
                continue
            g = wkt.loads(st["footprint.wkt"])
            g = max(getattr(g, "geoms", [g]), key=lambda q: q.area)
            poly = Polygon(
                [a.from_block(*geo.block_xz(la, lo, a.lat0, a.lon0)) for lo, la in g.exterior.coords]
            )
            if poly.is_valid and poly.area > 4.0:
                a.footprints.append(poly)
                if st.get("primary-structure"):
                    a.attributes = {k: st[k] for k in ATTRIBUTES if st.get(k) is not None}
    return a
