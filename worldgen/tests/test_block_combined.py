"""Unit tests for the combined block's helpers (no network, no data files)."""

import json
import struct

import numpy as np
from shapely.geometry import LineString, Polygon, box

from worldgen.block_combined import chaikin, near_zone, raster_polys, smooth_polys, solar_elevation
from worldgen.block_glb import Material, MeshBuf, Node, write_glb


def test_chaikin_keeps_ring_closed_and_shape():
    sq = np.array([(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)], float)
    r = chaikin(sq, 3)
    assert np.allclose(r[0], r[-1])
    a = Polygon(r).area
    assert 80 < a < 100  # corners cut, square kept


def test_raster_polys_area_and_smoothing():
    lab = np.zeros((50, 50), np.int8)
    lab[10:30, 5:45] = 1  # 4 m x 8 m at 0.2 m
    p = raster_polys(lab, 1, half=5.0, res=0.2)
    assert abs(p.area - 32.0) < 1e-6
    x0, z0, x1, z1 = p.bounds
    assert abs(x0 - (-5 + 5 * 0.2)) < 1e-9 and abs(z0 - (-5 + 10 * 0.2)) < 1e-9
    s = smooth_polys(p)
    assert s.is_valid and abs(s.area - 32.0) < 1.5


def test_solar_elevation_winter_noon_houston():
    # winter solstice noon at 29.7 N: 90 - 29.7 - 23.2
    assert abs(solar_elevation(180.0) - 37.1) < 0.3
    assert solar_elevation(205.0) < solar_elevation(180.0)


def test_near_zone_takes_fronting_lots_only():
    street = LineString([(0, -100), (0, 100)])
    front = Polygon([(8, -10), (40, -10), (40, 10), (8, 10)])  # fronts the street (8 m from centreline)
    behind = Polygon([(41, -10), (70, -10), (70, 10), (41, 10)])  # next lot back
    m = near_zone([street], [{"poly": front}, {"poly": behind}], half=160)
    assert m.contains(front.buffer(-0.5))
    assert m.intersection(behind).area < 0.05 * behind.area


def test_glb_shared_mesh_instances(tmp_path):
    buf = MeshBuf()
    buf.add(np.array([[0, 0, 0], [1, 0, 0], [0, 0, 1]], float), [[0, 2, 1]])
    m = np.eye(4)
    m[3, 0] = 5.0  # column-major translation x
    nodes = [Node(f"t{i}", mesh=[(buf, "m")], mesh_key="tree", matrix=m.ravel().tolist()) for i in range(3)]
    p = tmp_path / "x.glb"
    write_glb(p, [Node("trees", children=nodes)], {"m": Material("m")}, {})
    data = p.read_bytes()
    jlen = struct.unpack("<I", data[12:16])[0]
    gl = json.loads(data[20 : 20 + jlen])
    assert len(gl["meshes"]) == 1
    assert {n.get("mesh") for n in gl["nodes"] if n["name"] != "trees"} == {0}
    assert all("matrix" in n for n in gl["nodes"] if n["name"] != "trees")


def test_driveway_runs_from_garage_to_curb():
    from worldgen.block_hardscape import driveway

    road = box(-5, -100, 5, 100)  # street along z, curb at x = 5
    lab = np.zeros((100, 100), np.int8)  # 0.2 m raster over +-10 m: no concrete -> door width
    g = {"p": np.array([15.0, 0.0]), "n": np.array([-1.0, 0.0]), "w": 4.9}
    d = driveway(g, road, lab, half=10.0, res=0.2)
    assert d is not None and abs(d["L"] - 10.0) < 0.1
    assert d["v1"] - d["v0"] >= 4.9
    assert d["poly"].intersects(road.buffer(0.01)) and not d["poly"].buffer(-0.05).intersects(road)


def test_gutter_and_line_uv():
    from worldgen.block_hardscape import gutter_band, line_uv

    road = box(-4, -50, 4, 50)
    g = gutter_band(road, 0.45)
    assert abs(g.area - (road.area - box(-3.55, -49.55, 3.55, 49.55).area)) < 1e-6
    uv = line_uv(LineString([(0, -50), (0, 50)]), 1.5)(np.array([5.0]), np.array([0.0]))
    assert np.allclose(uv, [[50 / 1.5, 5 / 1.5]])


def test_fit_walk_follows_an_arc_at_constant_width():
    from worldgen.block_hardscape import fit_walk

    arc = LineString([(np.cos(a) * 6, np.sin(a) * 6) for a in np.linspace(0, 2.5, 40)])
    poly = arc.buffer(0.55)
    band, line, w = fit_walk(poly)
    assert abs(w - 1.1) < 0.15
    assert abs(line.length - arc.length) < 1.5
    assert band.intersection(poly).area / band.union(poly).area > 0.85
    assert fit_walk(Polygon([(0, 0), (2, 0), (2, 2), (0, 2)])) is None  # a square bed is not a walk
