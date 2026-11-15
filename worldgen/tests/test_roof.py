"""Roof planes fitted to a synthetic surface model."""

import numpy as np
from shapely.geometry import box

from worldgen import roof as rf


def _noisy(fn, seed=0, sigma=0.02):
    rng = np.random.default_rng(seed)

    def surface(x, y):
        return fn(np.asarray(x, float), np.asarray(y, float)) + rng.normal(0, sigma, np.shape(x))

    return surface


def test_gable_roof_is_two_planes_meeting_in_a_straight_ridge():
    # 10 x 8 m, eaves at 2.5 m along y = 0 and y = 8, ridge at y = 4, 5/12 pitch.
    def gable(x, y):
        return 2.5 + (5 / 12) * (4 - np.abs(y - 4))

    roof = rf.fit_roof(_noisy(gable), box(0, 0, 10, 8))
    assert roof is not None and len(roof.planes) == 2
    assert abs(np.degrees(roof.pitch) - np.degrees(np.arctan(5 / 12))) < 1.0
    x = np.linspace(0.5, 9.5, 40)
    for y in (0.3, 2.0, 3.9, 4.0, 4.1, 6.0, 7.7):  # eaves (extrapolated), slopes and the ridge
        assert np.abs(roof.height(x, np.full_like(x, y)) - gable(x, y)).max() < 0.05
    # The ridge is a straight line: the same height all along it.
    assert np.ptp(roof.height(x, np.full_like(x, 4.0))) < 0.03


def test_hip_roof_and_normals():
    def hip(x, y):
        return 2.5 + 0.5 * np.minimum(np.minimum(x, 12 - x), np.minimum(y, 8 - y))

    roof = rf.fit_roof(_noisy(hip, 1), box(0, 0, 12, 8))
    assert roof is not None and len(roof.planes) == 4
    xs, ys = np.meshgrid(np.linspace(0.5, 11.5, 30), np.linspace(0.5, 7.5, 20))
    assert np.abs(roof.height(xs.ravel(), ys.ravel()) - hip(xs.ravel(), ys.ravel())).max() < 0.08
    n = roof.normal(np.array([6.0]), np.array([1.0]))[0]  # the south slope faces -y and up
    assert n[1] < -0.3 and n[2] > 0.8 and abs(n[0]) < 0.05


def test_valley_between_two_wings_takes_the_higher_plane():
    # Two gables at right angles (an L): the roof is the higher of the two wings' roofs.
    def wings(x, y):
        a = 2.5 + 0.5 * (3 - np.abs(y - 3))  # wing along x, 6 m deep
        b = 2.5 + 0.5 * (3 - np.abs(x - 3))  # wing along y
        ina, inb = (y <= 6) & (x <= 14), (x <= 6) & (y <= 14)
        return np.where(ina & inb, np.maximum(a, b), np.where(ina, a, np.where(inb, b, 0.0)))

    region = box(0, 0, 14, 6).union(box(0, 0, 6, 14))
    roof = rf.fit_roof(_noisy(wings, 2), region, min_z=1.5)
    assert roof is not None and 4 <= len(roof.planes) <= 6
    pts = np.array([[4.5, 4.0], [4.0, 4.5], [10.0, 2.0], [2.0, 10.0], [3.0, 3.0]])
    assert np.abs(roof.height(pts[:, 0], pts[:, 1]) - wings(pts[:, 0], pts[:, 1])).max() < 0.08


def test_tree_over_the_roof_and_ground_at_the_edge_are_not_roof():
    def gable(x, y):
        return 2.5 + 0.4 * (4 - np.abs(y - 4))

    rng = np.random.default_rng(3)

    def surface(x, y):
        z = gable(x, y) + rng.normal(0, 0.02, np.shape(x))
        tree = (x - 8.5) ** 2 + (y - 6.5) ** 2 < 1.5**2  # a canopy 2 to 4 m above the roof, rough
        z = np.where(tree, z + 3 + rng.normal(0, 0.5, np.shape(x)), z)
        return np.where(x > 10, 0.0, z)  # the region overshoots the house by 1.5 m: ground

    roof = rf.fit_roof(surface, box(0, 0, 11.5, 8), min_z=1.5)
    assert roof is not None and len(roof.planes) == 2
    assert abs(roof.height(np.array([8.5]), np.array([6.5]))[0] - gable(8.5, 6.5)) < 0.06  # under the tree
    assert abs(roof.height(np.array([11.0]), np.array([2.0]))[0] - gable(11.0, 2.0)) < 0.06  # over the ground


def test_floor_lifts_the_roof_and_is_reported():
    def flat(x, y):
        return np.full(np.shape(x), 3.0)

    def floor(x, y):
        return np.where((x > 2) & (x < 4) & (y > 2) & (y < 4), 3.4, np.nan)

    roof = rf.fit_roof(flat, box(0, 0, 8, 8), floor=floor)
    assert roof is not None
    assert abs(roof.height(np.array([3.0]), np.array([3.0]))[0] - 3.4) < 1e-6
    assert abs(roof.height(np.array([6.0]), np.array([6.0]))[0] - 3.0) < 0.01
    frac, most = roof.lifted()
    assert 0.03 < frac < 0.12 and abs(most - 0.4) < 0.01


def test_no_plane_gives_none():
    rng = np.random.default_rng(4)
    assert rf.fit_roof(lambda x, y: rng.normal(0, 2.0, np.shape(x)), box(0, 0, 6, 6)) is None
