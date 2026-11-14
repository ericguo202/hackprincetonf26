import numpy as np
import pytest
from PIL import Image
from shapely.geometry import LineString, Polygon

from worldgen import rebake as rb
from worldgen.panos import PanoSet, Raycaster, shade

COLOURS = [(250, 0, 0), (0, 250, 0), (0, 0, 250), (250, 250, 0), (0, 250, 250), (250, 0, 250)]  # faces 0-5


def room(**kw):
    poly = Polygon([(0, 0), (4, 0), (4, 3), (0, 3)])
    sides = [((0, 0), (4, 0)), ((4, 0), (4, 3)), ((4, 3), (0, 3)), ((0, 3), (0, 0))]
    return rb.ShellRoom(poly, 0.0, 2.5, [(LineString([a, b]), 0.1) for a, b in sides], **kw)


def pano_set(tmp_path, positions):
    for i, c in enumerate(COLOURS):
        Image.new("RGB", (64, 64), c).save(tmp_path / f"face{i}.png")
    panos = []
    for k, pos in enumerate(positions):
        for i in range(6):
            Image.new("RGB", (64, 64), COLOURS[i]).save(tmp_path / f"p{k}_{i}.jpg", quality=100)
        panos.append(
            {
                "id": f"p{k}",
                "position": dict(zip("xyz", pos, strict=True)),
                "rotation": {"x": 0, "y": 0, "z": 0, "w": 1},
            }
        )
    return PanoSet({"panoLocations": panos}, tmp_path)


def test_shell_planes_face_into_the_room():
    planes = rb.shell_planes([room()])
    kinds = [p.kind for p in planes]
    assert kinds.count("floor") == 1 and kinds.count("ceiling") == 1 and kinds.count("wall") == 4
    centre = np.array([2.0, 1.5, 1.0])
    for p in planes:
        mid = p.origin + p.u * p.w / 2 + p.v * p.h / 2
        assert (centre - mid) @ p.n > 0


def test_vaulted_room_keeps_its_scanned_ceiling_and_gets_taller_walls():
    planes = rb.shell_planes([room(flat_ceiling=False, wall_top=3.2)])
    assert not [p for p in planes if p.kind == "ceiling"]
    assert all(p.h == pytest.approx(3.2) for p in planes if p.kind == "wall")


def test_fill_blends_holes_instead_of_streaking():
    img = np.zeros((40, 40, 3), np.uint8)
    img[:, :20] = 100
    img[:, 20:] = 200
    valid = np.ones((40, 40), bool)
    valid[15:25, 15:25] = False
    out = rb._fill(img, valid)
    assert (out[valid] == img[valid]).all()
    assert 100 < out[20, 20, 0] < 200


def test_openings_are_squared_and_doors_reach_the_floor():
    res = 100.0
    e = np.zeros((250, 400), bool)  # a 2.5 m x 4 m wall
    e[50:240, 100:190] = True  # ragged doorway, stops 10 cm above the floor
    e[50:240:7, 100:104] = False
    e[10:14, 300:304] = True  # a speck
    out, rects = rb._square_openings(e, res)
    assert rects == [(100, 190, 50, 250)]
    assert out[50:250, 100:190].all() and not out[10:14, 300:304].any()
    frames = rb._frame_planes(rb.shell_planes([room()])[2], rects, res)
    assert len(frames) == 3  # two jambs and a lintel; no sill for an opening that reaches the floor


def test_pano_faces_point_the_right_way(tmp_path):
    panos = pano_set(tmp_path, [(0.0, 0.0, 0.0)])
    pts = np.array([[5, 0, 0], [0, -5, 0], [-5, 0, 0], [0, 5, 0], [0, 0, 5], [0, 0, -5], [5, 0, -4]], float)
    col, trust = panos.sample(0, pts)
    want = [1, 2, 3, 4, 0, 5, 1]  # forward +X, right -Y, back, left, up, down
    for c, w in zip(col[:5], want[:5], strict=True):
        assert np.abs(c - COLOURS[w]).max() < 12
    assert trust[5] == 0  # straight down is the tripod blur
    assert trust[6] == 1


def test_shade_uses_only_cameras_that_see_the_point(tmp_path):
    panos = pano_set(tmp_path, [(1.6, 1.5, 1.5), (2.4, 1.5, 1.5)])  # each sees its side only
    floor = [[(0, 0, 0), (4, 0, 0), (4, 3, 0)], [(0, 0, 0), (4, 3, 0), (0, 3, 0)]]
    wall = [[(2, 0, 0), (2, 3, 0), (2, 3, 2.5)], [(2, 0, 0), (2, 3, 2.5), (2, 0, 2.5)]]  # splits the room
    rays = Raycaster(np.array(floor + wall, dtype=float))
    pts = np.array([[0.2, 1.5, 0.0], [3.8, 1.5, 0.0]])
    rgb, valid = shade(pts, np.tile([0.0, 0.0, 1.0], (2, 1)), panos, rays, snap=False)
    assert valid.all()
    assert np.abs(rgb - COLOURS[5]).max() < 12  # both floor points are coloured from a down face
