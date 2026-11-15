import numpy as np
import shapely
import trimesh
from shapely.geometry import LineString, Polygon, box

from worldgen import exterior as ex
from worldgen import rebake as rb
from worldgen import roof as rf
from worldgen.panos import Raycaster


def room(cuts=()):
    poly = Polygon([(0, 0), (4, 0), (4, 3), (0, 3)])
    sides = [((0, 0), (4, 0)), ((4, 0), (4, 3)), ((4, 3), (0, 3)), ((0, 3), (0, 0))]
    return rb.ShellRoom(poly, 0.0, 2.5, [(LineString([a, b]), 0.1) for a, b in sides], cuts=list(cuts))


def fake_scan(tris: np.ndarray) -> rb.Scan:
    cross = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    norm = np.linalg.norm(cross, axis=1)
    return rb.Scan(
        tris=tris,
        uvs=np.zeros((len(tris), 3, 2)),
        tex=np.zeros(len(tris), int),
        normals=cross / norm[:, None],
        areas=norm / 2,
        images=[np.zeros((2, 2, 3), np.uint8)],
        rays=Raycaster(tris),
    )


def test_clipped_grid_covers_the_polygon_exactly():
    poly = Polygon([(0, 0), (5.3, 0), (5.3, 2.1), (2.2, 4.7), (0, 4.7)]).difference(box(1, 1, 2, 2))
    v, f = ex.clipped_grid(poly, 0.25)
    tri = v[f]
    e1, e2 = tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]
    area = np.abs(e1[:, 0] * e2[:, 1] - e1[:, 1] * e2[:, 0]).sum() / 2
    assert abs(area - poly.area) < 1e-6
    # The outline stays straight: every vertex is inside (or on) the polygon.
    assert shapely.contains_xy(poly.buffer(1e-6), v[:, 0], v[:, 1]).all()


def test_wall_cut_removes_the_door_from_its_own_wall_only():
    door = ((1.0, 0.0), (1.9, 0.0), 0.0, 2.0)  # on the south wall (y = 0)
    planes = [p for p in rb.shell_planes([room([door])]) if p.kind == "wall"]
    cut = [p for p in planes if p.shape.area < p.w * p.h - 1e-6]
    assert len(cut) == 1
    p = cut[0]
    assert abs(p.n[1] - 1.0) < 1e-9  # the south wall faces north, into the room
    assert abs((p.w * p.h - p.shape.area) - 0.9 * 2.0) < 1e-6  # door width x height, floor up


def test_facade_ring_moves_out_to_the_scanned_wall_face():
    # A 4 x 3 house outline; the scanned outer wall face sits 0.2 m outside it on every side.
    face = trimesh.creation.box(extents=[4.4, 3.4, 3.0])
    face.apply_translation([2.0, 1.5, 1.5])
    scan = fake_scan(face.vertices[face.faces])
    outline = Polygon([(0, 0), (4, 0), (4, 3), (0, 3)])
    corners, normals = ex.facade_ring(outline, scan, 0.0)
    walls = Polygon(corners)
    assert walls.is_valid
    assert abs(walls.area - 4.4 * 3.4) < 0.05
    centre = np.array([2.0, 1.5])
    for k, c in enumerate(corners):  # normals point away from the house
        mid = (c + corners[(k + 1) % len(corners)]) / 2
        assert (mid - centre) @ normals[k] > 0


def test_door_frames_span_the_wall_and_facade_cut_matches():
    corners = np.array([(-0.2, -0.2), (-0.2, 3.2), (4.2, 3.2), (4.2, -0.2)])  # clockwise
    d = corners[[1, 2, 3, 0]] - corners
    d /= np.linalg.norm(d, axis=1)[:, None]
    normals = np.stack([-d[:, 1], d[:, 0]], 1)
    door = ((1.0, 0.0), (1.9, 0.0), 0.0, 2.0)
    frames = ex.door_frames(corners, normals, [door], 0.0)
    assert len(frames) == 4  # two jambs, lintel, threshold
    jambs = [p for p in frames if abs(p.n[2]) < 0.5]
    assert all(abs(p.w - (0.2 + rb.WALL_INSET)) < 1e-6 and abs(p.h - 2.0) < 1e-6 for p in jambs)
    sill = next(p for p in frames if p.kind == "floor")
    assert abs(sill.origin[1]) < 1e-6 and abs(sill.h - 0.2) < 1e-6  # centreline out to the facade

    class FlatGround:
        def height(self, x, y):
            return np.full(np.shape(x), -0.1)

    planes = ex.facade_planes(corners, normals, FlatGround(), 2.7, [door])
    cut = [p for p in planes if p.shape.area < p.w * p.h - 1e-6]
    assert len(cut) == 1 and abs(cut[0].n[1] + 1.0) < 1e-9  # the south facade faces south


def test_sky_mask():
    px = np.array(
        [[[120, 170, 230], [235, 235, 240], [60, 110, 40], [240, 240, 235], [140, 100, 70]]], np.uint8
    )
    assert ex._sky(px).tolist() == [[True, True, False, True, False]]


def test_facade_never_moves_inside_the_wall_centreline():
    # Only an inside surface was scanned (0.3 m inside the outline, e.g. a recessed fireplace): the facade
    # must stay outside the outline instead of covering it from the room.
    inner = trimesh.creation.box(extents=[3.4, 2.4, 3.0])
    inner.apply_translation([2.0, 1.5, 1.5])
    scan = fake_scan(inner.vertices[inner.faces])
    outline = Polygon([(0, 0), (4, 0), (4, 3), (0, 3)])
    corners, _ = ex.facade_ring(outline, scan, 0.0)
    assert Polygon(corners).contains(outline)


# ---------------------------------------------------------------- roof from the aerial surface model


def gable(x, y):
    """Eaves at 2.6 m along y = -0.5 and y = 3.5 (0.5 m past a 4 x 3 house), ridge along y = 1.5."""
    return 2.6 + 0.5 * (2.0 - np.abs(np.asarray(y, float) - 1.5)) + 0.0 * np.asarray(x, float)


def test_roof_region_adds_what_the_plan_lacks_and_keeps_a_minimum_overhang():
    walls = Polygon([(0, 0), (4, 0), (4, 3), (0, 3)])
    # Vexcel's outline: a porch to the west, but 0.1 m short of the wall on the east (registration).
    vexcel = Polygon([(-2.5, -0.4), (3.9, -0.4), (3.9, 3.4), (-2.5, 3.4)])
    region = ex.roof_region(walls, vexcel)
    assert region.contains(box(-2.4, 0, 0, 3))  # the porch roof
    assert region.contains(walls.buffer(ex.MIN_OVERHANG - 0.01, join_style="mitre"))
    alone = ex.roof_region(walls, None)
    assert abs(alone.area - (4 + 2 * ex.OVERHANG) * (3 + 2 * ex.OVERHANG)) < 0.05


def test_ceiling_floor_follows_a_vaulted_ceiling_not_the_rooms_highest_point():
    flat = room()
    vaulted = room()
    vaulted.poly = Polygon([(4, 0), (8, 0), (8, 3), (4, 3)])
    vaulted.flat_ceiling, vaulted.wall_top = False, 3.6
    # Scanned ceiling: 2.4 m at the south wall rising to 3.5 m at the north wall; one sample missing.
    gx, gy = np.meshgrid(np.arange(4, 8.01, rb.CEIL_STEP), np.arange(0, 3.01, rb.CEIL_STEP), indexing="ij")
    vaulted.ceiling = (4.0, 0.0, 2.4 + (3.5 - 2.4) * gy / 3.0)
    low = ex.ceiling_floor([flat, vaulted])
    assert abs(low(np.array([2.0]), np.array([1.5]))[0] - (2.5 + ex.ROOF_GAP)) < 1e-6
    south, north = low(np.array([6.0, 6.0]), np.array([0.5, 2.5]))
    assert south < 2.4 + 0.5 and north > 3.2  # follows the slope
    assert north < 3.6  # and is not the room's highest point everywhere
    assert np.isnan(low(np.array([20.0]), np.array([20.0]))[0])


def test_facade_under_a_gable_is_a_pentagon_up_to_the_roof():
    corners = np.array([(0.0, 0.0), (0.0, 3.0), (4.0, 3.0), (4.0, 0.0)])  # clockwise
    d = corners[[1, 2, 3, 0]] - corners
    d /= np.linalg.norm(d, axis=1)[:, None]
    normals = np.stack([-d[:, 1], d[:, 0]], 1)
    roof = rf.fit_roof(gable, box(-0.5, -0.5, 4.5, 3.5))
    assert roof is not None
    ground = ex.Ground(-2.0, -2.0, np.zeros((60, 60)), box(-2, -2, 6, 5).difference(box(0, 0, 4, 3)), 0.0)
    planes = ex.facade_planes(corners, normals, ground, 2.4, roof=roof)
    assert len(planes) == 4
    end = planes[0]  # the west wall (x = 0), under the gable
    eave = next(p for p in planes if abs(p.n[1]) > 0.9)  # a wall under an eave
    under_eave = (
        2.6 + 0.5 * 0.5 - ex.SOFFIT + 0.15
    )  # roof at the wall line, less the soffit, plus below ground
    assert abs(eave.h - under_eave) < 0.05 and abs(eave.shape.area - eave.w * eave.h) < 0.05
    ridge = 2.6 + 0.5 * 2.0 - ex.SOFFIT + 0.15
    assert abs(end.h - ridge) < 0.05
    assert abs(end.shape.area - 3.0 * (under_eave + ridge) / 2) < 0.1  # a pentagon, not a rectangle


def test_scan_faces_the_roof_replaces_are_dropped_and_canopy_above_it_is_kept():
    walls = Polygon([(0, 0), (4, 0), (4, 3), (0, 3)])
    roof = rf.fit_roof(gable, box(-0.5, -0.5, 4.5, 3.5))
    ground = ex.Ground(-3.0, -3.0, np.zeros((80, 80)), box(-3, -3, 7, 6).difference(walls), 0.0)

    def tri(x, y, z):  # a small upward-facing triangle
        return [[x, y, z], [x + 0.2, y, z], [x, y + 0.2, z]]

    on_roof = tri(2.0, 1.4, float(gable(2.0, 1.4)) + 0.05)  # scanned shingles
    gutter = tri(2.0, -0.45, 2.55)  # at the eave, just under the roof's edge
    canopy = tri(2.0, 1.4, float(gable(2.0, 1.4)) + 2.5)  # a tree over the house
    porch_floor = tri(2.0, -0.3, 0.4)  # something low under the eave: stays
    scan = fake_scan(np.array([on_roof, gutter, canopy, porch_floor], float))
    kept = ex.outdoor_faces(scan, walls, walls, ground, 2.5, roof.region, 2.4, roof)
    assert set(kept.tolist()) == {2, 3}


def test_eave_underside_is_the_scanned_porch_ceiling_and_never_above_the_roof():
    walls = Polygon([(0, 0), (4, 0), (4, 3), (0, 3)])
    region = box(-3.0, -0.5, 4.5, 3.5)  # the roof runs 3 m past the west wall: a porch
    roof = rf.fit_roof(lambda x, y: np.full(np.shape(x), 3.2), region)
    ground = ex.Ground(-4.0, -2.0, np.zeros((70, 50)), region.buffer(1).difference(walls), 0.0)
    # A scanned porch ceiling at 2.45 m over x in [-3, -0.5], facing down, a little crumpled.
    rng = np.random.default_rng(5)
    xs, ys = np.meshgrid(np.arange(-3.0, -0.5, 0.25), np.arange(-0.5, 3.5, 0.25))
    tris = []
    for x, y in zip(xs.ravel(), ys.ravel(), strict=True):
        z = 2.45 + rng.normal(0, 0.02)
        tris.append([[x, y, z], [x, y + 0.25, z], [x + 0.25, y, z]])  # normal -z
        tris.append([[x + 0.25, y, z], [x, y + 0.25, z], [x + 0.25, y + 0.25, z]])
    scan = fake_scan(np.array(tris, float))
    assert (scan.normals[:, 2] < -0.9).all()
    under = ex.eave_underside(scan, roof, walls, ground, 0.0)
    assert abs(under(np.array([-2.0]), np.array([1.5]))[0] - 2.45) < 0.03  # the porch ceiling, flattened
    assert abs(under(np.array([2.0]), np.array([3.3]))[0] - (3.2 - ex.SOFFIT)) < 1e-6  # no scan: the guess
    assert 0.2 < under.measured < 0.9
    # The ceiling's scan faces are replaced by the plane.
    kept = ex.outdoor_faces(scan, walls, walls, ground, 2.5, roof.region, 2.4, roof, under)
    assert len(kept) == 0


def test_a_low_surface_under_a_narrow_overhang_is_not_the_eave():
    # A 0.5 m overhang all round; under the east one a door head at 2.2 m (the roof is at 3.2 m).
    walls = Polygon([(0, 0), (4, 0), (4, 3), (0, 3)])
    region = box(-0.5, -0.5, 4.5, 3.5)
    roof = rf.fit_roof(lambda x, y: np.full(np.shape(x), 3.2), region)
    ground = ex.Ground(-2.0, -2.0, np.zeros((60, 50)), region.buffer(1).difference(walls), 0.0)
    head = [
        [[4.0, 0.0, 2.2], [4.0, 3.0, 2.2], [4.5, 0.0, 2.2]],
        [[4.5, 0.0, 2.2], [4.0, 3.0, 2.2], [4.5, 3.0, 2.2]],
    ]
    soffit = [
        [[-0.5, 0.0, 2.9], [-0.5, 3.0, 2.9], [0.0, 0.0, 2.9]],
        [[0.0, 0.0, 2.9], [-0.5, 3.0, 2.9], [0.0, 3.0, 2.9]],
    ]
    scan = fake_scan(np.array(head + soffit, float))
    assert (scan.normals[:, 2] < -0.9).all()
    under = ex.eave_underside(scan, roof, walls, ground, 0.0)
    assert abs(under(np.array([4.25]), np.array([1.5]))[0] - (3.2 - ex.SOFFIT)) < 1e-6  # not the door head
    assert abs(under(np.array([-0.25]), np.array([1.5]))[0] - 2.9) < 0.02  # a soffit 0.12 m under the guess


def test_open_rafters_under_a_porch_roof_stay_scan():
    walls = Polygon([(0, 0), (4, 0), (4, 3), (0, 3)])
    region = box(-3.0, -0.5, 4.5, 3.5)
    roof = rf.fit_roof(lambda x, y: np.full(np.shape(x), 3.2), region)
    ground = ex.Ground(-4.0, -2.0, np.zeros((70, 50)), region.buffer(1).difference(walls), 0.0)
    # The west porch: boards at 3.1 m with a rafter 0.25 m deep every 0.6 m (downward-facing strips).
    tris = []
    for x0 in np.arange(-3.0, -0.3, 0.15):
        z = 2.85 if int(round((x0 + 3.0) / 0.15)) % 4 == 0 else 3.1
        tris.append([[x0, -0.5, z], [x0, 3.5, z], [x0 + 0.15, -0.5, z]])
        tris.append([[x0 + 0.15, -0.5, z], [x0, 3.5, z], [x0 + 0.15, 3.5, z]])
    scan = fake_scan(np.array(tris, float))
    assert (scan.normals[:, 2] < -0.9).all()
    under = ex.eave_underside(scan, roof, walls, ground, 0.0)
    assert under.built.contains(box(-2.5, 0.5, -1.0, 2.5))  # the porch is built, not flat
    assert abs(under(np.array([-2.0]), np.array([1.5]))[0] - (3.2 - ex.SOFFIT)) < 1e-6  # no plane height
    kept = ex.outdoor_faces(scan, walls, walls, ground, 2.5, roof.region, 2.4, roof, under)
    c = scan.tris.mean(1)
    inner = (c[:, 0] > -2.5) & (c[:, 0] < -1.0)
    assert np.isin(np.nonzero(inner)[0], kept).all()  # rafters and boards are still there
