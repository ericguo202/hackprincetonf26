"""Unit tests for the procedural block prototype's geometry helpers (no network, no data files)."""

import json
import struct

import numpy as np
from shapely import contains_xy
from shapely.geometry import LineString, Polygon

from worldgen.block_buildings import Building, decompose, fit_roof, roof_dist
from worldgen.block_data import to_latlon, to_xz
from worldgen.block_glb import Material, MeshBuf, Node, write_glb
from worldgen.block_ground import fence_lines


def test_frame_round_trip():
    lat, lon = to_latlon(25.0, -40.0)
    x, z = to_xz(lat, lon)
    assert abs(x - 25.0) < 1e-6 and abs(z + 40.0) < 1e-6
    # +Z is south: a point south of the origin has positive z
    assert to_xz(29.70, -95.670841)[1] > 0


def l_shape(theta: float = 0.3) -> Polygon:
    pts = np.array([(0, 0), (16, 0), (16, 8), (8, 8), (8, 14), (0, 14)], float)
    c, s = np.cos(theta), np.sin(theta)
    return Polygon(pts @ np.array([[c, s], [-s, c]]) + [100, -50])


def test_l_shape_decomposes_into_two_rectangles():
    b = Building(poly=l_shape(), primary=True)
    iou = decompose(b)
    assert iou > 0.97
    assert len(b.rects) == 2
    dims = sorted(sorted([round(r.w, 1), round(r.d, 1)]) for r in b.rects)
    assert dims == [[8.0, 14.0], [8.0, 16.0]]


def test_roof_fit_recovers_pitch_and_storey():
    b = Building(poly=l_shape(), primary=True)
    decompose(b)
    rng = np.random.default_rng(0)
    x0, z0, x1, z1 = b.poly.bounds
    pts = rng.uniform([x0, z0], [x1, z1], (6000, 2))
    inside = contains_xy(b.poly, pts[:, 0], pts[:, 1])
    x, z = pts[inside, 0], pts[inside, 1]
    u, v = b.to_local(x, z)
    nd = 2.8 + 0.5 * roof_dist(b, u, v, "hip")  # true roof: edge 2.8 m, 0.5 pitch
    fit_roof(b, x, z, nd, None)
    assert b.kind == "hip"
    assert abs(b.eave - 2.8) < 0.25
    assert abs(b.pitch - 0.5) < 0.08


def test_glb_writer_names_and_unlit(tmp_path):
    buf = MeshBuf()
    buf.quad([0, 0, 0], [1, 0, 0], [1, 0, 1], [0, 0, 1], facing=np.array([0, 1, 0]))
    out = tmp_path / "t.glb"
    st = write_glb(
        out,
        [Node("hero_hou_1", mesh=[(buf, "m")], extras={"listing_id": "hou_1"})],
        {"m": Material("m", unlit=True)},
        {},
    )
    assert st["triangles"] == 2
    data = out.read_bytes()
    n = struct.unpack("<I", data[12:16])[0]
    gl = json.loads(data[20 : 20 + n])
    assert gl["nodes"][0]["name"] == "hero_hou_1"
    assert "KHR_materials_unlit" in gl["extensionsUsed"]
    # the quad faces up
    assert gl["accessors"][1]["count"] == 4


def test_fences_skip_the_street_side():
    road = LineString([(-50, 0), (50, 0)]).buffer(4.3)
    parcel = Polygon([(0, 6), (15, 6), (15, 40), (0, 40)])
    house = Building(poly=Polygon([(3, 12), (12, 12), (12, 24), (3, 24)]), primary=True, kind="hip")
    lines = fence_lines([{"poly": parcel}], [house], road, [LineString([(-50, 0), (50, 0)])])
    assert lines
    for ln in lines:
        for _, z in ln.coords:
            assert z >= 17.9  # nothing in front of the middle of the house
