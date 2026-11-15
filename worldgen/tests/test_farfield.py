"""The far field from the drone splat and the sky by direction (farfield.py)."""

import numpy as np
from shapely.geometry import Point, box

from worldgen import exterior as ex
from worldgen import farfield as fa
from worldgen.panos import Raycaster

from .test_rebake import pano_set
from .test_splat import disks, join


def scene(rng):
    xy = np.stack(np.meshgrid(np.arange(-15, 15, 0.06), np.arange(-15, 15, 0.06)), -1).reshape(-1, 2)
    house = (xy[:, 0] > 6) & (xy[:, 0] < 11) & (xy[:, 1] > 5) & (xy[:, 1] < 10)
    ground = disks(np.c_[xy[~house], np.zeros((~house).sum())], (0.5, 0.5, 0.5), size=0.05)
    roof = disks(np.c_[xy[house], np.full(house.sum(), 3.0)], (0.5, 0.2, 0.2), size=0.05)
    r, a = 2.0 * np.sqrt(rng.uniform(0, 1, 6000)), rng.uniform(0, 2 * np.pi, 6000)
    crown = disks(
        np.c_[-7 + r * np.cos(a), -6 + r * np.sin(a), rng.uniform(2.5, 6.0, 6000)], (0.1, 0.5, 0.1), size=0.08
    )
    return join(ground, roof, crown)


def test_far_field_keeps_a_house_drops_a_tree_to_the_ground_and_leaves_the_lot_open():
    far = fa.build(
        scene(np.random.default_rng(0)), box(-3, -3, 3, 3), box(-2, -2, 2, 2), (0.0, 0.0), np.ones(3), 13.0
    )
    tris = np.concatenate([np.asarray(m.vertices)[m.faces] for _, m in far.meshes])
    rays = Raycaster(tris)
    at = np.array([[8.5, 7.5, 20.0], [-7.0, -6.0, 20.0], [0.0, 0.0, 20.0], [-10.0, 8.0, 20.0]])
    t, prim = rays.cast(at, np.tile([0, 0, -1.0], (4, 1)))
    assert abs(20 - t[0] - 3.0) < 0.15  # the neighbour's roof
    assert abs(20 - t[1]) < 0.3  # under the crown: ground
    assert prim[2] < 0  # nothing inside the lot
    assert abs(20 - t[3]) < 0.1
    assert len(far.trees) == 1
    tree = far.trees[0]
    assert abs(tree.x + 7) < 0.5 and abs(tree.y + 6) < 0.5 and tree.far and not tree.in_lot
    assert 5.0 < tree.top < 6.5 and 1.5 < tree.r_major < 2.8
    assert tree.colour[1] > tree.colour[0] and tree.colour[1] > tree.colour[2]
    # The skyline keeps the crown: the sky may not be read through it.
    t, _ = Raycaster(far.skyline).cast(at[1:2], np.array([[0, 0, -1.0]]))
    assert 20 - t[0] > 4.5
    # Every face has a texture and texture coordinates inside it.
    for _, m in far.meshes:
        assert m.visual.uv.min() > -0.01 and m.visual.uv.max() < 1.01


def test_sky_takes_open_sky_only_and_continues_it_behind_what_is_in_the_way(tmp_path):
    # Fake panoramas: face 0 (up) is red, the side faces other colours; "sky" here = red.
    panos = pano_set(tmp_path, [(0.0, 0.0, 1.5), (4.0, 0.0, 1.5)])
    lid = np.array([[[1.0, -30, 8], [60, -30, 8], [60, 30, 8]], [[1.0, -30, 8], [60, 30, 8], [1.0, 30, 8]]])
    colour, report = fa.sky(panos, Raycaster(lid), lambda rgb: (rgb[..., 0] > 200) & (rgb[..., 1] < 60))
    up = colour(np.array([[0.0, 0.0, 1.0], [-0.3, 0.1, 1.0], [0.5, 0.0, 1.0], [1.0, 0.0, 0.05]]))
    assert (up[:, 0] > 200).all() and (up[:, 1] < 60).all()  # red everywhere, also under the lid and low down
    assert report["cameras"] and 0.0 < report["covered"] < 0.6


def test_the_dome_is_sky_above_the_skyline_and_the_disk_leaves_the_far_field_out():
    g = ex.Ground(-1.0, -1.0, np.zeros((100, 100)), box(-1, -1, 13, 13), 0.0)
    wall = np.array([[[30.0, -40, 0], [30, 40, 0], [30, 40, 6]], [[30.0, -40, 0], [30, 40, 6], [30, -40, 6]]])
    rays = Raycaster(wall)
    eye = np.array([6.0, 6.0, 1.5])
    d = np.array([[1.0, 0, 0.1], [1.0, 0, 0.5], [-1.0, 0, 0.1]])
    d /= np.linalg.norm(d, axis=1)[:, None]
    assert fa.clear(rays, eye, d).tolist() == [False, True, True]  # behind the wall; over it; nothing there
    covered = Point(6, 6).buffer(20)
    patches = ex.backdrop_patches(
        g, np.array([6.0, 6.0]), 40.0, box(4, 4, 8, 8), 0.0, sky=(lambda v: v * 0 + 9, rays), skip=covered
    )
    disk = [p for p in patches if "sky" not in p.meta]
    assert disk and len(patches) - len(disk) == 5
    for p in disk:  # no disk texel inside the far field
        xy = p.pos(np.array([p.w / 2]), np.array([p.h / 2]))[0, :2]
        assert np.hypot(*(xy - 6)) > 15


def test_a_crown_is_closed_for_line_of_sight_and_open_under_it():
    mask = np.zeros((12, 12), bool)
    mask[4:8, 4:8] = True  # a crown 2 m across on a 0.5 m grid whose top-left corner is (0, 6)
    under, top = np.full((12, 12), 2.5), np.full((12, 12), 6.0)
    rays = Raycaster(fa._crown_shell(mask, under, top, 0.0, 6.0, 0.5))
    origins = np.array([[-5.0, 3.0, 4.0], [3.0, 3.0, 20.0], [-5.0, 3.0, 1.5], [-5.0, 0.5, 4.0]])
    dirs = np.array([[1.0, 0, 0], [0, 0, -1.0], [1.0, 0, 0], [1.0, 0, 0]])
    t, prim = rays.cast(origins, dirs)
    assert prim[0] >= 0 and abs(-5.0 + t[0] - 2.0) < 0.3  # through the crown from the side: its curtain
    assert prim[1] >= 0 and abs(20 - t[1] - 6.0) < 0.01  # from above: its top
    assert prim[2] < 0  # under the crown: nothing in the way
    assert prim[3] < 0  # beside it


def test_far_field_is_built_only_where_no_camera_sees_the_ground_and_under_buildings(tmp_path):
    panos = pano_set(tmp_path, [(0.0, 0.0, 1.5)])
    # A fence east of the camera hides everything behind it; to the west the ground is open.
    fence = np.array(
        [[[3.2, -20, -1], [3.2, 20, -1], [3.2, 20, 2.2]], [[3.2, -20, -1], [3.2, 20, 2.2], [3.2, -20, 2.2]]]
    )
    s = scene(np.random.default_rng(0))
    west_house = (
        (s.xyz[:, 0] > -11) & (s.xyz[:, 0] < -7) & (s.xyz[:, 1] > 4) & (s.xyz[:, 1] < 9) & (s.xyz[:, 2] < 0.5)
    )
    s.xyz[west_house, 2] = 3.0  # a second house, on the open side
    far = fa.build(
        s, box(-3, -3, 3, 3), box(-2, -2, 2, 2), (0.0, 0.0), np.ones(3), 13.0,
        panos=panos, sight=fence, disk=lambda x, y: np.zeros(np.shape(x)),
    )  # fmt: skip
    rays = Raycaster(np.concatenate([np.asarray(m.vertices)[m.faces] for _, m in far.meshes]))
    at = np.array(
        [[8.5, 7.5, 20.0], [9.0, -8.0, 20.0], [-9.0, -9.5, 20.0], [-9.0, 6.5, 20.0], [-5.0, 0.0, 20.0]]
    )
    t, prim = rays.cast(at, np.tile([0, 0, -1.0], (len(at), 1)))
    assert abs(20 - t[0] - 3.0) < 0.2 and abs(20 - t[1]) < 0.3  # behind the fence: house and ground
    assert prim[2] < 0 and prim[4] < 0  # open ground: left to the photo disk
    assert abs(20 - t[3] - 3.0) < 0.2  # a building on open ground is still geometry
    assert far.covered.contains(Point(9.0, -8.0)) and not far.covered.contains(Point(-5.0, 0.0))
    assert len(far.trees) == 1  # the crown over open ground still gets its model


def test_picture_walls_stand_around_the_lot_and_are_cut_out_where_nothing_is_beyond():
    rng = np.random.default_rng(1)
    yz = np.stack(np.meshgrid(np.arange(-3, 3, 0.05), np.arange(0, 4, 0.05)), -1).reshape(-1, 2)
    # A red house front 12 m east of the lot, 6 m wide and 4 m high; nothing in the other directions.
    front = disks(
        np.c_[np.full(len(yz), 15.0), yz[:, 0], yz[:, 1]], (0.8, 0.1, 0.1), normal=(1, 0, 0), size=0.06
    )
    speck = disks(np.c_[rng.uniform(-40, 40, (30, 2)), rng.uniform(20, 30, 30)], (0, 0, 1))  # far overhead
    walls = fa.wall_panels(join(front, speck), box(-3, -3, 3, 3), 0.0, np.ones(3))
    assert len(walls) == 1  # the three sides with nothing beyond them get no wall
    p = walls[0]
    assert abs(p.origin[0] - (3 + fa.PANEL_OFFSET)) < 0.01 and p.n[0] < -0.99  # east of the lot, facing it
    solid = p.image[..., 3] > 0
    assert 0.03 < solid.mean() < 0.2  # the house front and nothing else
    assert p.image[solid][:, 0].mean() > 180 and p.image[solid][:, 1].mean() < 60
    rows = np.nonzero(solid.any(1))[0]  # row 0 is the wall's top, PANEL_TOP above the ground
    top = fa.PANEL_TOP - rows.min() / fa.PANEL_RES
    assert abs(top - 4.0) < 0.2  # the front keeps its real height on the wall
