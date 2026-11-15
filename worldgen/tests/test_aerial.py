"""Aerial layers in a house's plan frame (no network: synthetic rasters)."""

import json
import math
import struct
import zlib

import numpy as np
from PIL import Image
from shapely.geometry import Polygon, box

from worldgen import aerial as ae
from worldgen import geo
from worldgen.sources.vexcel import VexcelClient

LAT0, LON0 = 29.70113, -95.670841


def write_tiled_tiff(path, data: np.ndarray, x0: float, y0: float, px: float, tile: int = 16) -> None:
    """A big-endian, tiled, deflate float32 GeoTIFF like Vexcel's dsm/extract answer."""
    h, w = data.shape
    tiles = []
    for r in range(0, h, tile):
        for c in range(0, w, tile):
            t = np.zeros((tile, tile), ">f4")
            part = data[r : r + tile, c : c + tile]
            t[: part.shape[0], : part.shape[1]] = part
            tiles.append(zlib.compress(t.tobytes()))
    n = len(tiles)
    assert n > 1  # single-tile files keep offsets inline; not needed here
    entries = 12
    after_ifd = 8 + 2 + entries * 12 + 4
    scale = struct.pack(">3d", px, px, 0.0)
    tie = struct.pack(">6d", 0.0, 0.0, 0.0, x0, y0, 0.0)
    counts = struct.pack(f">{n}I", *[len(t) for t in tiles])
    scale_at = after_ifd
    tie_at = scale_at + len(scale)
    counts_at = tie_at + len(tie)
    offs_at = counts_at + len(counts)
    first = offs_at + 4 * n
    offs = struct.pack(f">{n}I", *(first + sum(len(t) for t in tiles[:k]) for k in range(n)))

    def short(tag, value):
        return struct.pack(">HHIHH", tag, 3, 1, value, 0)

    def long(tag, value):
        return struct.pack(">HHII", tag, 4, 1, value)

    def ref(tag, typ, count, where):
        return struct.pack(">HHII", tag, typ, count, where)

    ifd = struct.pack(">H", entries)
    ifd += long(256, w) + long(257, h) + short(258, 32) + short(259, 8) + short(277, 1)
    ifd += long(322, tile) + long(323, tile) + ref(324, 4, n, offs_at) + ref(325, 4, n, counts_at)
    ifd += short(339, 3) + ref(33550, 12, 3, scale_at) + ref(33922, 12, 6, tie_at)
    ifd += struct.pack(">I", 0)
    path.write_bytes(b"MM" + struct.pack(">HI", 42, 8) + ifd + scale + tie + counts + offs + b"".join(tiles))


def test_height_tiff_round_trip(tmp_path):
    rng = np.random.default_rng(0)
    data = rng.normal(30, 2, (40, 50)).astype(np.float32)
    data[3, 4] = -32767.0  # no data
    write_tiled_tiff(tmp_path / "d.tif", data, 1000.0, 2000.0, 0.15)
    out, x0, y0, px = ae.read_height_tiff(tmp_path / "d.tif")
    assert out.shape == (40, 50) and (x0, y0, px) == (1000.0, 2000.0, 0.15)
    assert np.isnan(out[3, 4])
    ok = np.isfinite(out)
    assert np.allclose(out[ok], data[ok])


def synthetic(heading: float = 20.0, position=(12.0, -30.0)) -> ae.Aerial:
    """An Aerial whose DSM is 100 + 0.01 * (metres east of the block origin) and whose ortho's red channel
    grows eastward, green southward."""
    m, n = geo._radii(LAT0)
    half = 80.0
    lat = LAT0 + np.degrees(np.array([half, -half]) / m)
    lon = LON0 + np.degrees(np.array([-half, half]) / (n * math.cos(math.radians(LAT0))))
    (mx0, mx1), (my0, my1) = ae.merc(lat, lon)[0], ae.merc(lat, lon)[1]
    px = 0.2
    cols, rows = int((mx1 - mx0) / px), int((my0 - my1) / px)
    east_m = (np.arange(cols) + 0.5) * px * math.cos(math.radians(LAT0)) - half  # mercator scale
    dsm = np.tile(100 + 0.01 * east_m, (rows, 1)).astype(np.float32)
    ortho = np.zeros((200, 200, 3), np.uint8)
    ortho[..., 0] = np.linspace(0, 255, 200)[None, :]
    ortho[..., 1] = np.linspace(0, 255, 200)[:, None]
    return ae.Aerial(LAT0, LON0, position, heading, dsm, mx0, my0, px, ortho, (mx0, my0, mx1, my1))


def test_plan_points_land_where_the_house_pose_puts_them():
    a = synthetic()
    # plan (x, y) -> house-local (x, -y) -> block: position + rotY(heading) * local (worldgen/costar.py)
    from worldgen.costar import house_to_block

    for x, y in ((0.0, 0.0), (5.0, 2.0), (-3.0, -7.0)):
        bx, bz = a._block(np.array([x]), np.array([y]))
        ex_, ez_ = house_to_block((x, -y), a.position, a.heading_deg)
        assert abs(bx[0] - ex_) < 1e-9 and abs(bz[0] - ez_) < 1e-9
        back = a.from_block(float(bx[0]), float(bz[0]))
        assert abs(back[0] - x) < 1e-9 and abs(back[1] - y) < 1e-9
        assert abs(a.elevation(np.array([x]), np.array([y]))[0] - (100 + 0.01 * bx[0])) < 0.005
    # The ortho: red grows eastward, green southward.
    c0 = a.colour(np.array([0.0]), np.array([0.0]))[0]
    east = a.from_block(a.position[0] + 20, a.position[1])
    south = a.from_block(a.position[0], a.position[1] + 20)
    assert a.colour(np.array([east[0]]), np.array([east[1]]))[0][0] > c0[0] + 20
    assert a.colour(np.array([south[0]]), np.array([south[1]]))[0][1] > c0[1] + 20


def test_level_uses_open_ground_not_what_stands_on_it():
    a = synthetic(heading=0.0, position=(0.0, 0.0))
    rng = np.random.default_rng(1)
    x, y = rng.uniform(-10, 10, 400), rng.uniform(-10, 10, 400)
    ground = a.elevation(x, y) - 73.2 + rng.normal(0, 0.02, 400)  # the scan's z = 0 is at elevation 73.2
    ground[:120] -= rng.uniform(1.0, 6.0, 120)  # under trees and eaves the DSM is far above the ground
    spread = a.level(x, y, ground)
    assert abs(a.z0 - 73.2) < 0.02 and spread < 0.05
    assert abs(a.surface(np.array([0.0]), np.array([0.0]))[0] - (100 - 73.2)) < 0.03


def test_roof_outline_is_the_structure_over_the_house():
    a = synthetic()
    a.footprints = [box(20, 20, 30, 30), box(-1, -1, 11, 9), box(0, 0, 2, 2)]
    house = box(0, 0, 10, 8)
    assert a.roof_outline(house).equals(box(-1, -1, 11, 9))
    assert a.roof_outline(box(50, 50, 60, 60)) is None  # no structure there


def test_load_needs_a_world_pose_and_rasters_that_cover_the_house(tmp_path):
    block = {"center": {"lat": LAT0, "lon": LON0}}
    lat, lon = geo.latlon(12.0, 30.0, LAT0, LON0)
    listing = {"lat": lat, "lon": lon, "world": {"position": [12.0, 0.0, -30.0], "heading_deg": 20.0}}
    assert ae.load(tmp_path, block, {}) is None
    assert ae.load(tmp_path, block, listing) is None  # nothing cached
    a = synthetic()
    folder = tmp_path / ae.BLOCK_CACHE
    folder.mkdir(parents=True)
    box_name = f"bbox{LAT0 - 0.001:.5f}_{LON0 - 0.001:.5f}_{LAT0 + 0.001:.5f}_{LON0 + 0.001:.5f}"
    write_tiled_tiff(
        folder / f"dsm_extract_urban_{box_name}_z20.tif", a.dsm[:64, :64], a.dsm_x0, a.dsm_y0, a.dsm_px
    )
    Image.fromarray(a.ortho).save(folder / f"ortho_extract_urban_{box_name}_z21.jpg")
    # The listing's cached property/extract answer: one structure, as lon/lat WKT, with roof attributes.
    ring = [geo.latlon(e, nn, LAT0, LON0) for e, nn in ((5, 35), (19, 35), (19, 25), (5, 25))]
    wkt = "POLYGON ((" + ", ".join(f"{lo} {la}" for la, lo in [*ring, ring[0]]) + "))"
    body = {
        "features": [
            {
                "properties": {
                    "structures": [
                        {
                            "footprint.wkt": wkt,
                            "primary-structure": True,
                            "roof-characteristics.shape.value": "gable",
                            "roof-characteristics.material.class": "shingle",
                        }
                    ]
                }
            }
        ]
    }
    cache = tmp_path / ae.LISTING_CACHE
    cache.mkdir(parents=True)
    key = VexcelClient.cache_key("property/extract", {"wkt": f"POINT({lon} {lat})", "srid": 4326})
    (cache / f"{key}.body").write_text(json.dumps(body))
    got = ae.load(tmp_path, block, listing)
    assert got is not None and got.heading_deg == 20.0
    assert got.attributes == {
        "roof-characteristics.material.class": "shingle",
        "roof-characteristics.shape.value": "gable",
    }
    assert len(got.footprints) == 1 and abs(got.footprints[0].area - 14 * 10) < 0.5
    # The structure is 14 x 10 m around the house's position, turned by the heading in plan.
    assert isinstance(got.footprints[0], Polygon) and got.footprints[0].contains(
        Polygon([(-1, -1), (1, -1), (1, 1)])
    )
