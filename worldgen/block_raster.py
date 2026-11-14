"""Raster helpers for the procedural block: read Vexcel DSM GeoTIFFs, map rasters to the block frame,
derive a ground model (DTM) from the DSM, and sample the ortho.

Vexcel returns rasters in EPSG:3857 (Web Mercator). The ortho JPEG carries no georeferencing; with
`crop=bounding_box` it spans the requested lon/lat box exactly, on a Mercator grid.
"""

from __future__ import annotations

import math
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

from worldgen.block_data import ORIGIN, to_xz

R_EARTH = 6378137.0


def merc(lat: float, lon: float) -> tuple[float, float]:
    x = math.radians(lon) * R_EARTH
    y = math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) * R_EARTH
    return x, y


def inv_merc(x: float, y: float) -> tuple[float, float]:
    lon = math.degrees(x / R_EARTH)
    lat = math.degrees(2 * math.atan(math.exp(y / R_EARTH)) - math.pi / 2)
    return lat, lon


# ---------------- GeoTIFF (big-endian, tiled, deflate, float32) ----------------


def _read_ifd(buf: bytes) -> tuple[str, dict[int, tuple]]:
    bo = buf[:2]
    e = ">" if bo == b"MM" else "<"
    (ifd_off,) = struct.unpack(e + "I", buf[4:8])
    (n,) = struct.unpack(e + "H", buf[ifd_off : ifd_off + 2])
    sizes = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1, 11: 4, 12: 8, 16: 8}
    fmts = {1: "B", 2: "c", 3: "H", 4: "I", 5: "II", 7: "B", 11: "f", 12: "d", 16: "Q"}
    tags: dict[int, tuple] = {}
    for i in range(n):
        o = ifd_off + 2 + 12 * i
        tag, typ, cnt = struct.unpack(e + "HHI", buf[o : o + 8])
        size = sizes.get(typ, 1) * cnt
        data = buf[o + 8 : o + 12] if size <= 4 else None
        if data is None:
            (p,) = struct.unpack(e + "I", buf[o + 8 : o + 12])
            data = buf[p : p + size]
        if typ == 2:
            tags[tag] = (data.decode("latin1"),)
        elif typ in fmts:
            f = fmts[typ]
            k = len(f)
            vals = struct.unpack(e + f * cnt, data[: struct.calcsize(e + f * cnt)]) if k == 1 else ()
            tags[tag] = vals
    return e, tags


@dataclass
class GeoRaster:
    data: np.ndarray  # float32 (rows, cols); NaN = nodata
    x0: float  # EPSG:3857 x of the left edge of column 0
    y0: float  # EPSG:3857 y of the top edge of row 0
    px: float  # pixel size in EPSG:3857 meters

    def sample_merc(self, mx: np.ndarray, my: np.ndarray, order: int = 1) -> np.ndarray:
        c = (mx - self.x0) / self.px - 0.5
        r = (self.y0 - my) / self.px - 0.5
        return ndimage.map_coordinates(self.data, [r, c], order=order, mode="nearest")


def read_geotiff(path: Path) -> GeoRaster:
    buf = Path(path).read_bytes()
    e, t = _read_ifd(buf)
    w, h = t[256][0], t[257][0]
    tw, th = t[322][0], t[323][0]
    offs, cnts = t[324], t[325]
    comp = t[259][0]
    pred = t.get(317, (1,))[0]
    if comp not in (8, 32946) or pred != 1:
        raise ValueError(f"unsupported tiff compression {comp} predictor {pred}")
    out = np.full((h, w), np.nan, np.float32)
    nx = math.ceil(w / tw)
    for i, (o, c) in enumerate(zip(offs, cnts, strict=True)):
        tile = np.frombuffer(zlib.decompress(buf[o : o + c]), dtype=e + "f4").reshape(th, tw)
        ty, tx = divmod(i, nx)
        r0, c0 = ty * th, tx * tw
        rh, cw = min(th, h - r0), min(tw, w - c0)
        out[r0 : r0 + rh, c0 : c0 + cw] = tile[:rh, :cw]
    nod = t.get(42113)
    if nod:
        out[out == float(nod[0].strip("\x00"))] = np.nan
    out[(out < -1000) | (out > 10000)] = np.nan
    scale = t[33550]
    tie = t[33922]  # (i, j, k, X, Y, Z): raster point (i, j) -> model (X, Y)
    px = scale[0]
    # GeoTIFF raster space is PixelIsArea by default: (i, j) refers to the corner of pixel (i, j).
    x0 = tie[3] - tie[0] * px
    y0 = tie[4] + tie[1] * px
    return GeoRaster(out, x0, y0, px)


# ---------------- frame helpers ----------------


def xz_to_merc(x: np.ndarray, z: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Vectorised block (x, z) -> EPSG:3857, through lat/lon."""
    from worldgen.block_data import MLAT, MLON

    lat0, lon0 = ORIGIN

    lat = lat0 - np.asarray(z) / MLAT
    lon = lon0 + np.asarray(x) / MLON
    mx = np.radians(lon) * R_EARTH
    my = np.log(np.tan(np.pi / 4 + np.radians(lat) / 2)) * R_EARTH
    return mx, my


@dataclass
class Ortho:
    img: np.ndarray  # uint8 (rows, cols, 3)
    mx0: float
    my0: float  # top
    mx1: float
    my1: float  # bottom

    @classmethod
    def load(cls, path: Path, bbox_ll: tuple[float, float, float, float]) -> Ortho:
        w, s, e, n = bbox_ll
        mx0, my0 = merc(n, w)
        mx1, my1 = merc(s, e)
        return cls(np.asarray(Image.open(path).convert("RGB")), mx0, my0, mx1, my1)

    def uv_px(self, x: np.ndarray, z: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Block (x, z) -> continuous pixel (col, row) in the ortho."""
        mx, my = xz_to_merc(x, z)
        h, w = self.img.shape[:2]
        col = (mx - self.mx0) / (self.mx1 - self.mx0) * w
        row = (self.my0 - my) / (self.my0 - self.my1) * h
        return col, row

    def sample(self, x: np.ndarray, z: np.ndarray) -> np.ndarray:
        col, row = self.uv_px(np.asarray(x, float), np.asarray(z, float))
        if getattr(self, "_f", None) is None:
            self._f = [np.ascontiguousarray(self.img[..., k], dtype=np.float32) for k in range(3)]
        out = np.stack(
            [
                ndimage.map_coordinates(self._f[k], [row - 0.5, col - 0.5], order=1, mode="nearest")
                for k in range(3)
            ],
            axis=-1,
        )
        return out

    def crop_xz(self, x0: float, z0: float, x1: float, z1: float, size: int) -> Image.Image:
        """Resample the block-frame square [x0,x1] x [z0,z1] (z0 north) to a size x size image."""
        xs = x0 + (np.arange(size) + 0.5) / size * (x1 - x0)
        zs = z0 + (np.arange(size) + 0.5) / size * (z1 - z0)
        X, Z = np.meshgrid(xs, zs)
        rgb = self.sample(X.ravel(), Z.ravel()).reshape(size, size, 3)
        return Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8))


def grid_xz(half: float, res: float) -> tuple[np.ndarray, np.ndarray]:
    n = int(round(2 * half / res))
    c = -half + (np.arange(n) + 0.5) * res
    return np.meshgrid(c, c)  # X varies along columns (east), Z along rows (south)


def resample_to_grid(r: GeoRaster, half: float, res: float, order: int = 1) -> np.ndarray:
    X, Z = grid_xz(half, res)
    mx, my = xz_to_merc(X.ravel(), Z.ravel())
    d = np.nan_to_num(r.data, nan=np.nanmedian(r.data))
    return ndimage.map_coordinates(
        d, [(r.y0 - my) / r.px - 0.5, (mx - r.x0) / r.px - 0.5], order=order, mode="nearest"
    ).reshape(X.shape)


def derive_dtm(dsm: np.ndarray, res: float, building_mask: np.ndarray) -> np.ndarray:
    """Ground model from a DSM grid: per-cell low percentile of non-building pixels, cells whose
    ground is hidden (canopy) rejected against a robust plane, holes filled, then smoothed."""
    cell = max(1, int(round(8.0 / res)))
    h, w = dsm.shape
    ny, nx = h // cell, w // cell
    lows = np.full((ny, nx), np.nan)
    for j in range(ny):
        for i in range(nx):
            blk = dsm[j * cell : (j + 1) * cell, i * cell : (i + 1) * cell]
            m = ~building_mask[j * cell : (j + 1) * cell, i * cell : (i + 1) * cell]
            v = blk[m]
            if v.size > cell * cell * 0.15:
                lows[j, i] = np.percentile(v, 5)
    # robust plane fit, then reject cells far above it (canopy over the whole cell)
    jj, ii = np.mgrid[0:ny, 0:nx]
    ok = np.isfinite(lows)
    for _ in range(4):
        A = np.c_[ii[ok], jj[ok], np.ones(ok.sum())]
        coef, *_ = np.linalg.lstsq(A, lows[ok], rcond=None)
        plane = coef[0] * ii + coef[1] * jj + coef[2]
        res_ = lows - plane
        thr = max(0.35, 2.5 * np.nanmedian(np.abs(res_[ok])))
        ok = np.isfinite(lows) & (res_ < thr) & (res_ > -1.5)
    filled = np.where(ok, lows, np.nan)
    # fill holes with nearest valid, then smooth
    idx = ndimage.distance_transform_edt(~ok, return_distances=False, return_indices=True)
    filled = filled[idx[0], idx[1]]
    filled = ndimage.gaussian_filter(filled, 1.0)
    zoom = (h / ny, w / nx)
    full = ndimage.zoom(filled, zoom, order=1)
    out = np.empty_like(dsm)
    out[: full.shape[0], : full.shape[1]] = full[:h, :w]
    if full.shape[0] < h:
        out[full.shape[0] :] = full[-1]
    if full.shape[1] < w:
        out[:, full.shape[1] :] = full[:, -1:]
    return out


__all__ = [
    "GeoRaster",
    "Ortho",
    "derive_dtm",
    "grid_xz",
    "inv_merc",
    "merc",
    "read_geotiff",
    "resample_to_grid",
    "to_xz",
    "xz_to_merc",
]
