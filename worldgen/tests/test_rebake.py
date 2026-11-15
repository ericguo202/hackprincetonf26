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


def test_a_vaulted_rooms_walls_stop_at_the_scanned_ceiling():
    # 4 x 3 room, ceiling 2.4 m at the south wall (y = 0) rising to 3.2 m at the north wall.
    r = room(flat_ceiling=False, wall_top=3.25)
    gx, gy = np.meshgrid(np.arange(0, 4.01, rb.CEIL_STEP), np.arange(0, 3.01, rb.CEIL_STEP), indexing="ij")
    r.ceiling = (0.0, 0.0, 2.4 + 0.8 * gy / 3.0)
    walls = [p for p in rb.shell_planes([r]) if p.kind == "wall"]
    south = next(p for p in walls if p.n[1] > 0.9)
    north = next(p for p in walls if p.n[1] < -0.9)
    side = next(p for p in walls if abs(p.n[0]) > 0.9)
    top = lambda p: p.shape.bounds[3]  # noqa: E731
    assert 2.4 < top(south) < 2.4 + 0.25  # the low wall no longer rises to the room's highest point
    assert 3.1 < top(north) <= 3.25
    assert top(side) > 3.1 and side.shape.area < side.w * 3.25 - 0.5  # a side wall is a trapezoid
    for p in walls:  # every wall keeps its full width (no slit at its end)
        assert p.shape.bounds[0] == pytest.approx(0.0, abs=1e-9) and p.shape.bounds[2] == pytest.approx(p.w)
    # Without a scanned ceiling the walls keep their full height.
    full = [p for p in rb.shell_planes([room(flat_ceiling=False, wall_top=3.25)]) if p.kind == "wall"]
    assert all(p.shape.bounds[3] == pytest.approx(3.25) for p in full)


def test_ceiling_grid_reads_the_scanned_ceiling_and_fills_holes():
    import trimesh

    from worldgen.panos import Raycaster

    # A sloping ceiling over a 4 x 3 room (2.4 m at y = 0, 3.3 m at y = 3) with a hole in it, and a cabinet
    # top at 2.0 m that is not the ceiling.
    v = np.array([[0, 0, 2.4], [4, 0, 2.4], [4, 3, 3.3], [0, 3, 3.3]], float)
    ceiling = trimesh.Trimesh(v, [[0, 2, 1], [0, 3, 2]], process=False)  # facing down
    keep = ~(ceiling.triangles_center[:, 0] > 9)
    cab = trimesh.creation.box(extents=[1.0, 0.5, 0.6])
    cab.apply_translation([0.6, 2.7, 1.7])
    tris = np.concatenate([ceiling.triangles[keep], cab.triangles])
    cross = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    norm = np.linalg.norm(cross, axis=1)
    scan = rb.Scan(
        tris,
        np.zeros((len(tris), 3, 2)),
        np.zeros(len(tris), int),
        cross / norm[:, None],
        norm / 2,
        [np.zeros((2, 2, 3), np.uint8)],
        Raycaster(tris),
    )
    r = room(flat_ceiling=False, wall_top=3.35)
    r.ceiling = rb._ceiling_grid(scan, r)
    z = rb.ceiling_z(r, np.array([2.0, 2.0, 0.6]), np.array([0.3, 2.7, 2.7]))
    assert abs(z[0] - (2.4 + 0.3 * 0.3)) < 0.05 and abs(z[1] - (2.4 + 0.3 * 2.7)) < 0.05
    assert abs(z[2] - (2.4 + 0.3 * 2.7)) < 0.05  # above the cabinet: the ceiling, not the cabinet's top
    assert np.isfinite(rb.ceiling_z(r, np.array([-0.3]), np.array([-0.3]))).all()  # just outside: nearest


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
        unit = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
        p = rb.Plane(kind, np.zeros(3), np.eye(3)[0], np.eye(3)[2], np.eye(3)[1], 1.0, 1.0, unit, room=room)
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


# ---------------------------------------------------------------- single view, synthesis, ledges, small boxes


def test_shade_single_takes_a_face_from_one_camera_and_matches_the_next(tmp_path):
    from worldgen.panos import shade_single

    # Two cameras look down at a floor; a wall between them hides each one's far half.
    panos = pano_set(tmp_path, [(1.0, 1.5, 1.5), (3.0, 1.5, 1.5)])
    floor = [[(0, 0, 0), (4, 0, 0), (4, 3, 0)], [(0, 0, 0), (4, 3, 0), (0, 3, 0)]]
    wall = [[(2, 0, 0), (2, 3, 0), (2, 3, 2.5)], [(2, 0, 0), (2, 3, 2.5), (2, 0, 2.5)]]
    rays = Raycaster(np.array(floor + wall, dtype=float))
    xs = np.r_[np.linspace(0.1, 0.4, 60), np.linspace(3.6, 3.9, 60)]
    pts = np.c_[xs, np.full(120, 0.3), np.zeros(120)]
    rgb, valid = shade_single(pts, np.tile([0.0, 0.0, 1.0], (120, 1)), panos, rays)
    assert valid.all()
    assert np.abs(rgb - COLOURS[5]).max() < 12  # every point from a down face, of whichever camera sees it


def test_fill_gives_a_big_hole_the_texture_of_the_seen_part():
    rng = np.random.default_rng(0)
    img = np.clip(150 + rng.normal(0, 12, (300, 300, 3)), 0, 255).astype(np.uint8)  # a popcorn ceiling
    valid = np.ones((300, 300), bool)
    valid[100:200, 100:200] = False  # where the fan was
    smooth = rb._fill(img, valid, detail=False)
    textured = rb._fill(img, valid)
    assert (textured[valid] == img[valid]).all()
    assert smooth[130:170, 130:170].std() < 2  # a smear
    assert textured[130:170, 130:170].std() > 6  # grain like the rest
    assert abs(float(textured[130:170, 130:170].mean()) - 150) < 4  # same colour


def test_lone_small_rectangles_are_not_boxes_but_parts_of_a_big_one_are():
    res = rb.RELIEF_RES
    fridge = (100, 140, 40, 125, 0.86)  # tall
    fridge_bit = (140, 152, 60, 100, 0.84)  # small, touching it, same front
    counter = (10, 90, 80, 125, 0.62)  # big
    bump = (155, 190, 0, 125, 0.21)  # a wall bump from floor to ceiling
    tank = (160, 183, 84, 107, 0.29)  # a toilet tank in front of the bump: small, nothing beside it
    bowl = (165, 179, 99, 125, 0.69)
    kept = rb._drop_lone_small([fridge, fridge_bit, counter, bump, tank, bowl], res)
    assert kept == [fridge, fridge_bit, counter, bump]
    # A microwave 8 cm proud of the wall cabinets on either side of it is built in, and stays a box.
    left, right = (0, 55, 20, 40, 0.32), (70, 130, 20, 40, 0.32)
    microwave = (55, 70, 22, 40, 0.40)
    assert rb._drop_lone_small([left, right, microwave], res) == [left, right, microwave]
    assert rb._drop_lone_small([left, microwave], res) == [left]  # with a cabinet on one side only: not


def test_a_scanned_bar_ledge_becomes_a_slab():
    rng = np.random.default_rng(2)
    n = 9
    gx, gy = np.meshgrid(np.linspace(1.0, 3.0, n), np.linspace(1.2, 1.5, 4))
    top = np.dstack([gx, gy, 1.15 + rng.normal(0, 0.008, gx.shape)])
    under = np.dstack([gx, gy, 1.10 + rng.normal(0, 0.008, gx.shape)])
    tris = []
    for i in range(3):
        for j in range(n - 1):
            a, b, c, d = top[i, j], top[i, j + 1], top[i + 1, j + 1], top[i + 1, j]
            tris += [[a, b, c], [a, c, d]]  # facing up
            a, b, c, d = under[i, j], under[i, j + 1], under[i + 1, j + 1], under[i + 1, j]
            tris += [[a, c, b], [a, d, c]]  # facing down
    for j in range(n - 1):  # the front edge joins the two sheets into one piece
        a, b, c, d = top[0, j], top[0, j + 1], under[0, j + 1], under[0, j]
        tris += [[a, c, b], [a, d, c]]
    scan = soup_scan(tris)
    boxes = rb.ledge_boxes(scan, np.arange(len(scan.tris)), [room()])
    assert len(boxes) == 1
    b = boxes[0]
    assert not b.attached
    assert sorted([round(b.s1 - b.s0, 1), round(b.depth, 1)]) == [0.3, 2.0]
    assert b.t1 == pytest.approx(1.15, abs=0.02) and b.t0 == pytest.approx(1.10, abs=0.02)
    assert rb.in_boxes(np.array([[2.0, 1.35, 1.13]]), boxes).all()
    assert len(rb.box_planes(boxes)) == 6  # floating and free-standing: all six faces


def test_two_cabinet_fronts_at_one_depth_meet_without_a_slit():
    o, u, n = np.zeros(3), np.array([1.0, 0, 0]), np.array([0, 1.0, 0])
    upper = rb.Box(o, u, n, 0.0, 2.0, 1.6, 2.2, 0.3)
    lower = rb.Box(o, u, n, 0.5, 1.5, 1.4, 1.8, 0.3)  # overlaps the upper one between z 1.6 and 1.8
    fronts = [p for p in rb.box_planes([upper, lower]) if abs(p.n @ n) > 0.9 and p.n @ n > 0]
    area = sum(p.shape.area for p in fronts)
    assert area == pytest.approx(2.0 * 0.6 + 1.0 * 0.2, abs=1e-6)  # the union exactly: no gap, no overlap


def test_fixtures_at_the_far_end_of_a_long_house_still_find_their_camera(tmp_path):
    # 14 cameras in a row, 15 m apart, over one long floor. shade() looks at the 12 nearest to the centre
    # of the points it is given, so points at both ends together lose their own cameras.
    panos = pano_set(tmp_path, [(15.0 * i, 1.5, 1.5) for i in range(14)])
    floor = [[(-5, 0, 0), (200, 0, 0), (200, 3, 0)], [(-5, 0, 0), (200, 3, 0), (-5, 3, 0)]]
    rays = Raycaster(np.array(floor, dtype=float))
    pts = np.array([[1.5, 1.5, 0.0], [196.5, 1.5, 0.0]])
    up = np.tile([0.0, 0.0, 1.0], (2, 1))
    _, together = shade(pts, up, panos, rays, snap=False)
    assert not together.any()
    rgb, valid = rb._shade_local(pts, up, panos, rays)
    assert valid.all() and np.abs(rgb - COLOURS[5]).max() < 12


def test_the_sample_patch_avoids_a_ceiling_light():
    rng = np.random.default_rng(3)
    img = np.clip(150 + rng.normal(0, 10, (200, 400, 3)), 0, 255).astype(np.uint8)
    img[60:100, 40:80] = 250  # a light
    patches = rb._plain_patches(img, np.ones((200, 400), bool), 96)
    assert patches and all(p.shape == (96, 96, 3) and p.max() < 215 for p in patches)  # grain only
    tile = rb._detail_tile(img, np.ones((200, 400), bool), 300, 300)
    assert tile is not None and 5 < tile.std() < 15
    # When every seen square holds a feature (here thin dark lines, closer than the smallest sample
    # square), nothing is copied at all.
    lined = img[:, 200:].copy()
    lined[::16] = 60
    assert rb._detail_tile(lined, np.ones((200, 200), bool), 300, 300) is None


# ---------------------------------------------------------------- walls at open edges, pillars, risers


def test_wall_faces_of_two_rooms_meet_at_an_open_edge_and_a_free_wall_end_gets_a_cap():
    # One 8 x 3 m space split at x = 4 by a line with no wall on it, except a 1 m stub from the north wall.
    outer = [((0, 0), (8, 0)), ((8, 0), (8, 3)), ((8, 3), (0, 3)), ((0, 3), (0, 0))]
    stub = ((4, 3), (4, 2))
    walls = [(LineString([a, b]), 0.1) for a, b in [*outer, stub]]
    west = rb.ShellRoom(Polygon([(0, 0), (4, 0), (4, 3), (0, 3)]), 0.0, 2.5, walls)
    east = rb.ShellRoom(Polygon([(4, 0), (8, 0), (8, 3), (4, 3)]), 0.0, 2.5, walls)
    planes = [p for p in rb.shell_planes([west, east]) if p.kind == "wall"]
    # South wall (facing +y): the two rooms' faces together cover x = 0.05 .. 7.95 without a gap at x = 4.
    south = [p for p in planes if p.n[1] > 0.9 and p.w > 1]
    spans = sorted((p.origin[0] + p.shape.bounds[0], p.origin[0] + p.shape.bounds[2]) for p in south)
    assert len(spans) == 2 and spans[0][1] == pytest.approx(4.0, abs=0.005)
    assert spans[1][0] == pytest.approx(4.0, abs=0.005)
    # The stub's two faces end at y = 2 in open space: each room closes half of its end.
    caps = [p for p in planes if p.n[1] < -0.9 and p.w == pytest.approx(rb.WALL_INSET)]
    assert len(caps) == 2 and all(p.origin[1] == pytest.approx(2.0, abs=0.01) for p in caps)
    assert sorted(round(float(p.origin[0] + p.u[0] * p.w), 2) for p in caps) == [4.0, 4.0]


def test_a_wall_that_meets_the_outline_at_a_right_angle_does_not_cover_it():
    walls = [(LineString([(0, 0), (0, 3)]), 0.1)]  # the west wall only
    runs = rb.wall_runs(rb.ShellRoom(Polygon([(0, 0), (4, 0), (4, 3), (0, 3)]), 0.0, 2.5, walls))
    assert [bool(covered) for _, _, _, covered in runs] == [False, False, False, True]


def test_a_pantry_measured_from_two_walls_keeps_all_its_sides():
    # From the wall along y = 2 (looking -y), turned by 0.6 degrees; and from the line x = 1 (looking +x).
    a = np.radians(0.6)
    u1, n1 = np.array([-np.cos(a), np.sin(a), 0.0]), np.array([-np.sin(a), -np.cos(a), 0.0])
    o1, o2 = np.array([1.8, 2.0, 0.0]), np.array([1.0, 2.0, 0.0])
    u2, n2 = np.array([0, -1.0, 0]), np.array([1.0, 0, 0])
    first = rb.Box(o1, u1, n1, 0.0, 0.8, 0.0, 2.5, 0.78, attached=False, to_ceiling=True)
    second = rb.Box(o2, u2, n2, 0.0, 0.78, 0.9, 2.5, 0.8, attached=False, to_ceiling=True)
    boxes = rb.drop_contained([second, first])
    assert boxes == [first]  # the upper part found again from the other wall is the same pantry
    sides = [p for p in rb.box_planes(boxes) if abs(p.n[2]) < 0.5]
    assert len(sides) == 4 and all(p.shape.area > 0.95 * p.w * p.h for p in sides)
    # Without that step, neither box may lose a face to the other on the side where they nearly coincide.
    both = rb.box_planes([second, first])
    west = [p for p in both if p.n[0] < -0.9]
    assert sum(p.shape.area for p in west) >= 0.78 * 2.5 * 0.95


def test_a_piece_seen_from_one_side_is_kept_whole_and_an_unseen_piece_is_dropped(tmp_path):
    panos = pano_set(tmp_path, [(2.0, 0.5, 1.0)])
    # A "toilet": a front the camera sees and a top-back it does not, joined along an edge.
    a, b, c, d = (1.7, 2.0, 0.0), (2.3, 2.0, 0.0), (2.3, 2.0, 0.8), (1.7, 2.0, 0.8)
    front = [[a, b, c], [a, c, d]]
    back = [[d, (2.3, 2.3, 0.8), c]]  # facing down: never seen from its front
    junk = [[(1.7, 2.6, 0.0), (2.3, 2.6, 0.8), (2.3, 2.6, 0.0)]]  # behind, facing away
    scan = soup_scan(front + back + junk)
    assert scan.normals[0, 1] < 0 and scan.normals[2, 2] < 0 and scan.normals[3, 1] > 0
    assert rb._seen(scan, np.arange(4), panos).tolist() == [0, 1, 2]


def test_the_half_wall_between_a_counter_and_its_ledge_is_filled():
    o, u, n = np.array([0.0, 2.0, 0.0]), np.array([1.0, 0, 0]), np.array([0, -1.0, 0])
    counter = rb.Box(o, u, n, 1.0, 3.0, 0.0, 0.9, 0.6, attached=False, back=0.07)  # y 1.4 .. 2.07
    ledge = rb.Box(np.array([0.9, 1.8, 0.0]), u, -n, 0.0, 2.2, 1.15, 1.2, 0.5, attached=False)  # y 1.8 .. 2.3
    scan = soup_scan([[(9, 9, 9), (9.1, 9, 9), (9, 9.1, 9)]])  # nothing to measure: 12 cm by default
    risers = rb.ledge_risers(scan, [counter, ledge], [ledge])
    assert len(risers) == 1
    r = risers[0]
    assert (r.z0, r.z1) == pytest.approx((0.9, 1.15))
    ring = np.asarray(r.footprint().exterior.coords)
    assert ring[:, 0].min() == pytest.approx(1.0) and ring[:, 0].max() == pytest.approx(3.0)
    assert ring[:, 1].max() == pytest.approx(2.07) and ring[:, 1].min() == pytest.approx(1.95)
    cabinet = rb.Box(o, u, n, 1.0, 3.0, 0.0, 0.9, 0.6)  # attached to a wall: the wall is already there
    assert rb.ledge_risers(scan, [cabinet, ledge], [ledge]) == []
