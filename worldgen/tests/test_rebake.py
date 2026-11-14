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


# ---------------------------------------------------------------- relief boxes


def cabinet_scan():
    """A 4 x 3 m room's walls and floor plus a base cabinet (0.6 deep, 0.9 high, x 1..3) on the y = 0 wall,
    with outward-facing triangles like a real scan."""

    def quad(a, b, c, d):
        return [[a, b, c], [a, c, d]]

    tris = []
    tris += quad((0, 0, 0), (4, 0, 0), (4, 3, 0), (0, 3, 0))  # floor, facing up
    tris += quad((0, 0, 0), (0, 0, 2.5), (4, 0, 2.5), (4, 0, 0))  # y = 0 wall, facing +y
    tris += quad((1, 0.6, 0), (1, 0.6, 0.9), (3, 0.6, 0.9), (3, 0.6, 0))  # cabinet front, facing +y
    tris += quad((1, 0, 0.9), (3, 0, 0.9), (3, 0.6, 0.9), (1, 0.6, 0.9))  # cabinet top, facing up
    t = np.array(tris, dtype=float)
    cross = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
    norm = np.linalg.norm(cross, axis=1)
    normals = cross / norm[:, None]
    return rb.Scan(t, np.zeros((len(t), 3, 2)), np.zeros(len(t), int), normals, norm / 2, [], Raycaster(t))


def test_largest_rect():
    m = np.zeros((10, 12), bool)
    m[2:8, 3:11] = True
    m[0:10, 5:7] = True
    assert rb._largest_rect(m) == (3, 11, 2, 8)


def test_relief_rects_finds_counter_and_upper_cabinet_and_ignores_a_slope():
    res = rb.RELIEF_RES
    D = np.zeros((125, 200))  # a 2.5 m x 4 m wall
    D[80:125, 20:120] = 0.62  # base cabinets, top at 0.9 m
    D[84:120, 50:80] = 0.69  # a stove standing proud of them
    D[20:60, 20:120] = 0.31  # upper cabinets
    D[90:100, 60:70] = 0.0  # a hole in the scan
    D[50:125, 150:200] = np.linspace(0.15, 0.9, 50)[None, :]  # a slanted fireplace: no dominant depth
    rects = rb.relief_rects(D, res)
    by_depth = {round(d, 2): (c0, c1, r0, r1) for c0, c1, r0, r1, d in rects}
    assert by_depth[0.62] == (20, 120, 80, 125)  # one piece, running on behind the stove, down to the floor
    assert by_depth[0.31] == (20, 120, 20, 60)
    assert 0.69 in by_depth
    assert all(c1 <= 125 for c0, c1, r0, r1 in by_depth.values())  # nothing fitted to the slope


def test_relief_rects_leaves_a_curtain_alone_and_stands_tall_things_on_the_floor():
    rng = np.random.default_rng(0)
    D = np.zeros((125, 200))
    folds = 0.75 + 0.04 * np.sin(np.arange(60) / 2.0)[None, :] + rng.normal(0, 0.01, (60, 60))
    D[30:90, 20:80] = folds  # a shower curtain: at one depth on average, but wavy
    D[10:110, 120:160] = 0.20  # a wall bump that the scan lost near the floor
    rects = rb.relief_rects(D, rb.RELIEF_RES)
    assert [(c0, c1, r0, r1) for c0, c1, r0, r1, d in rects] == [(120, 160, 10, 125)]


def test_snap_depths_merges_close_levels_only():
    rects = [(0, 10, 0, 10, 0.62), (10, 30, 0, 10, 0.64), (0, 10, 20, 30, 0.30)]
    out = rb._snap_depths(rects)
    assert out[0][4] == out[1][4] == pytest.approx((0.62 * 100 + 0.64 * 200) / 300)
    assert out[2][4] == 0.30


def test_relief_boxes_turn_a_scanned_cabinet_into_one_box():
    scan = cabinet_scan()
    boxes = rb.relief_boxes(scan, [room()])
    assert len(boxes) == 1
    b = boxes[0]
    assert b.attached and b.t0 == 0.0 and not b.to_ceiling
    assert b.t1 == pytest.approx(0.9, abs=0.03)
    assert b.depth == pytest.approx(0.6 - rb.WALL_INSET, abs=0.03)  # measured from the inset wall face
    assert b.s1 - b.s0 == pytest.approx(2.0, abs=0.06)
    # The scan's cabinet faces are replaced by the box; the floor far from it is not.
    assert rb.in_boxes(np.array([[2.0, 0.6, 0.5], [2.0, 0.3, 0.9]]), boxes).all()
    assert not rb.in_boxes(np.array([[2.0, 2.0, 0.0]]), boxes).any()
    assert b.triangles().shape == (12, 3, 3)


def test_box_faces_face_outward_and_skip_what_is_hidden():
    o, u, n = np.zeros(3), np.array([1.0, 0, 0]), np.array([0, 1.0, 0])
    counter = rb.Box(o, u, n, 1.0, 3.0, 0.0, 0.9, 0.6)
    planes = rb.box_planes([counter])
    assert len(planes) == 4  # front, two sides, top; no bottom on the floor, no back against a wall
    centre = np.array([2.0, 0.3, 0.45])
    for p in planes:
        mid = p.origin + p.u * p.w / 2 + p.v * p.h / 2
        assert (mid - centre) @ p.n > 0
    # A box in the corner needs no side face on the wall it touches; one that reaches the ceiling, no top.
    corner = rb.Box(o, u, n, 0.0, 1.0, 0.0, 2.5, 0.6, wall_ends=(True, False), to_ceiling=True)
    assert len(rb.box_planes([corner])) == 2
    # A free-standing one (open stretch of the room outline) also gets a back.
    assert len(rb.box_planes([rb.Box(o, u, n, 1.0, 3.0, 0.0, 0.9, 0.6, attached=False)])) == 5
    # A stove standing 8 cm proud of the counter: the counter's front loses the part behind it, and the
    # stove's sides keep only the strip that sticks out.
    stove = rb.Box(o, u, n, 1.5, 2.2, 0.0, 0.9, 0.68)
    both = rb.box_planes([counter, stove])
    front = next(p for p in both if p.box == 0 and abs(p.n @ n) > 0.9)
    assert front.shape.area == pytest.approx(2.0 * 0.9 - 0.7 * 0.9, abs=0.08)  # a 2 cm rim stays, hidden
    sides = [p for p in both if p.box == 1 and abs(p.n @ u) > 0.9]
    assert len(sides) == 2 and all(p.shape.area == pytest.approx(0.08 * 0.9, abs=0.04) for p in sides)


# ---------------------------------------------------------------- hanging objects


def soup_scan(tris):
    t = np.array(tris, dtype=float)
    cross = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
    norm = np.linalg.norm(cross, axis=1)
    normals = cross / norm[:, None]
    return rb.Scan(t, np.zeros((len(t), 3, 2)), np.zeros(len(t), int), normals, norm / 2, [], Raycaster(t))


def test_a_fan_is_under_a_ceiling_but_the_ceiling_and_a_beam_are_not():
    # The ceiling stops at x = 3 where a beam hangs down: the beam body has no scanned surface above it.
    ceiling = [[(0, 0, 3), (3, 3, 3), (3, 0, 3)], [(0, 0, 3), (0, 3, 3), (3, 3, 3)]]  # facing down
    blade_top = [[(1.5, 1.2, 2.6), (2.5, 1.2, 2.6), (2.5, 1.8, 2.6)]]  # facing up
    blade_bottom = [[(1.5, 1.2, 2.58), (2.5, 1.8, 2.58), (2.5, 1.2, 2.58)]]  # facing down
    beam_side = [[(3.0, 0, 2.8), (3.0, 3, 3.0), (3.0, 3, 2.8)]]  # facing -x, into the room
    scan = soup_scan(ceiling + blade_top + blade_bottom + beam_side)
    assert scan.normals[0, 2] < 0 and scan.normals[2, 2] > 0 and scan.normals[3, 2] < 0
    assert scan.normals[4, 0] < 0
    under = rb._under_a_ceiling(scan, np.arange(5))
    assert under.tolist() == [False, False, True, True, False]


def test_hanging_solids_cover_the_fan_and_nothing_else():
    blade = [[(1.5, 1.2, 2.3), (2.5, 1.2, 2.3), (2.5, 1.8, 2.3)]]
    crumb = [[(0.2, 0.2, 2.2), (0.25, 0.2, 2.2), (0.25, 0.25, 2.2)]]
    strip = [[(3.5, 0.0, 2.3), (3.7, 0.0, 2.3), (3.7, 3.0, 2.3)]]  # 3 m long: the side of a beam
    tall = [[(0.5, 2.0, 0.2), (1.2, 2.0, 0.2), (1.2, 2.0, 5.2)]]  # centred above 1.9 m, reaches the floor
    scan = soup_scan(blade + crumb + strip + tall)
    solids = rb.hanging_solids(scan, np.arange(4), [room()])
    assert len(solids) == 1  # no solid for the crumb, the long strip, or the thing standing on the floor
    v = solids[0].reshape(-1, 3)
    assert v[:, 0].min() < 1.5 and v[:, 0].max() > 2.5  # grown past the blade
    assert v[:, 2].min() == pytest.approx(2.27) and v[:, 2].max() == pytest.approx(2.33)
    assert rb._in_compact_clumps(scan, np.arange(3)).tolist() == [True, True, False]


def test_a_crumpled_open_door_becomes_a_slab_and_a_round_thing_does_not():
    rng = np.random.default_rng(1)

    def sheet(point, along, width, height, bulge, n=8):
        """A wavy upright sheet as a connected triangle grid."""
        along = np.array(along, float) / np.linalg.norm(along)
        normal = np.array([-along[1], along[0], 0.0])
        gs, gz = np.meshgrid(np.linspace(0, width, n), np.linspace(0.02, height, n))
        off = rng.normal(0, bulge, gs.shape)
        v = np.array(point, float) + gs[..., None] * along + gz[..., None] * [0, 0, 1.0]
        v = v + off[..., None] * normal
        tris = []
        for i in range(n - 1):
            for j in range(n - 1):
                tris += [[v[i, j], v[i, j + 1], v[i + 1, j + 1]], [v[i, j], v[i + 1, j + 1], v[i + 1, j]]]
        return tris

    door = sheet((1.0, 1.0, 0.0), (1.0, 1.0, 0.0), 0.8, 2.0, 0.01)  # open at 45 degrees
    blob = sheet((3.0, 1.0, 0.0), (1.0, 0.0, 0.0), 0.8, 2.0, 0.15)  # same size, far from flat
    scan = soup_scan(door + blob)
    boxes = rb.leaf_boxes(scan, np.arange(len(scan.tris)), [room()])
    assert len(boxes) == 1
    b = boxes[0]
    assert not b.attached and b.s1 == pytest.approx(0.8, abs=0.06) and b.t1 == pytest.approx(2.0, abs=0.06)
    assert 0.04 <= b.depth <= 0.08
    assert abs(abs(b.u @ np.array([1.0, 1.0, 0.0])) / np.sqrt(2) - 1) < 0.01  # lies along the door
    assert rb.in_boxes(np.array([[1.3, 1.3, 1.0]]), boxes).all()
    assert len(rb.box_planes(boxes)) == 5  # two faces, two edges, a top


def test_a_wall_no_camera_saw_takes_its_rooms_wall_colour():
    def plane(kind, room, seen, colour):
        p = rb.Plane(kind, np.zeros(3), np.eye(3)[0], np.eye(3)[2], np.eye(3)[1], 1.0, 1.0, None, room=room)
        p.image = np.full((4, 4, 4), 200, np.uint8)
        p.image[..., :3] = colour
        p.shape_mask = np.ones((4, 4), bool)
        p.unseen = np.zeros((4, 4), bool) if seen else np.ones((4, 4), bool)
        p.seen = 1.0 if seen else 0.0
        return p

    white, tan, grey = (240, 240, 240), (200, 180, 150), (200, 200, 200)
    hidden = plane("wall", 0, False, grey)  # behind a shower curtain, in a white bathroom
    closet = plane("wall", 2, False, grey)  # a closet nobody scanned: no seen wall in its room
    planes = [plane("wall", 0, True, white), hidden, plane("wall", 1, True, tan), plane("wall", 1, True, tan)]
    planes += [closet, plane("floor", 0, True, (90, 60, 30))]
    rb._paint_unseen_planes(planes)
    assert tuple(hidden.image[0, 0, :3]) == white
    assert tuple(closet.image[0, 0, :3]) == tan  # the house's most common wall colour
