import numpy as np
import shapely
import trimesh
from shapely.geometry import LineString, Polygon, box

from worldgen import exterior as ex
from worldgen import rebake as rb
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
