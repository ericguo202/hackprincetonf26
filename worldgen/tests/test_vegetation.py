"""Trees and shrubs from the scan (worldgen/vegetation.py): the measuring steps on small synthetic scenes."""

import math

import numpy as np
import trimesh
from shapely.geometry import Point, box

from worldgen import vegetation as vg


def _blob(rng, centre, radii, n):
    """n points scattered in an ellipsoid, with random normals (foliage)."""
    d = rng.normal(size=(n, 3))
    d /= np.linalg.norm(d, axis=1)[:, None]
    p = np.asarray(centre) + d * rng.uniform(0, 1, (n, 1)) ** (1 / 3) * radii
    nm = rng.normal(size=(n, 3))
    return p, nm / np.linalg.norm(nm, axis=1)[:, None]


def _column(x, y, r, z0, z1, n=600, seed=1):
    """Points on an upright cylinder, normals pointing out."""
    rng = np.random.default_rng(seed)
    a = rng.uniform(0, 2 * math.pi, n)
    z = rng.uniform(z0, z1, n)
    p = np.stack([x + r * np.cos(a), y + r * np.sin(a), z], 1)
    return p, np.stack([np.cos(a), np.sin(a), np.zeros(n)], 1)


def test_planarity_tells_a_fence_from_foliage():
    rng = np.random.default_rng(0)
    fence = np.c_[rng.uniform(0, 6, 3000), np.zeros(3000), rng.uniform(0, 2, 3000)]
    fn = np.tile([0.0, 1.0, 0.0], (3000, 1))
    fn[::2] *= -1  # both sides of the sheet
    leaves, ln = _blob(rng, (20, 0, 5), (1.5, 1.5, 1.5), 3000)
    flat = vg.planarity(np.r_[fence, leaves], np.r_[fn, ln], np.full(6000, 0.01))
    assert np.median(flat[:3000]) > 0.95
    assert np.median(flat[3000:]) < 0.5 < vg.LEAFY_MAX + 0.2
    assert (flat[3000:] < vg.LEAFY_MAX).mean() > 0.95


def test_find_trunks_keeps_the_round_column_under_foliage():
    trunk, tn = _column(2.0, 3.0, 0.15, 0.0, 2.6)
    post, pn = _column(8.0, 3.0, 0.05, 0.0, 2.6, seed=2)  # nothing hangs over it
    rng = np.random.default_rng(3)
    fence = np.c_[rng.uniform(0, 6, 2000), np.full(2000, 6.0), rng.uniform(0, 1.8, 2000)]
    fn = np.tile([0.0, 1.0, 0.0], (2000, 1))
    stub, sn = _column(5.0, 1.0, 0.1, 0.0, 0.9, seed=4)  # too short
    c, n = np.r_[trunk, post, fence, stub], np.r_[tn, pn, fn, sn]
    trunks, members = vg.find_trunks(c, n, c[:, 2], covered=lambda x, y: x < 4)
    assert len(trunks) == 1
    t = trunks[0]
    assert abs(t.x - 2.0) < 0.1 and abs(t.y - 3.0) < 0.1
    assert 0.1 < t.radius < 0.2
    assert set(members[0]) <= set(range(len(trunk)))
    # without the cover test the post counts too; the fence (long) and the stub (short) never do
    assert len(vg.find_trunks(c, n, c[:, 2])[0]) == 2


def test_crowns_are_shared_out_between_trunks_and_a_trunkless_one_is_kept():
    rng = np.random.default_rng(5)
    a, _ = _blob(rng, (0, 0, 5), (2.5, 2.5, 1.5), 6000)
    b, _ = _blob(rng, (12, 0, 9), (3.0, 3.0, 2.0), 6000)
    scrap, _ = _blob(rng, (25, 0, 4), (0.4, 0.4, 0.3), 200)
    pts = np.r_[a, b, scrap]
    grid = vg.canopy_grid(pts, pts[:, 2])
    crowns = vg.segment_crowns(grid, [vg.Trunk(0.3, 0.2, 0.2)])
    assert len(crowns) == 2  # the scrap is under CROWN_MIN_AREA
    with_trunk = next(c for c in crowns if c.trunk is not None)
    without = next(c for c in crowns if c.trunk is None)
    assert abs(with_trunk.cells[:, 0].mean()) < 0.5 and 15 < with_trunk.area < 25
    assert abs(without.cells[:, 0].mean() - 12) < 0.5
    assert 6.0 < with_trunk.top < 7.2 and 3.3 < with_trunk.bottom < 4.5
    assert without.top > with_trunk.top
    # a thin trunk does not reach a crown 3 m wide: beyond trunk_reach the foliage is somebody else's
    thin = vg.Trunk(0.0, 0.0, 0.02)
    assert vg.trunk_reach(thin) == 3.0
    assert vg.trunk_reach(vg.Trunk(0, 0, 0.3)) == 13.5


def test_a_pergola_is_a_slab_and_a_crown_is_not():
    rng = np.random.default_rng(6)
    lattice = np.c_[rng.uniform(0, 4, 8000), rng.uniform(0, 3, 8000), rng.uniform(2.5, 2.62, 8000)]
    crown, _ = _blob(rng, (12, 1.5, 5), (2.5, 2.5, 2.0), 8000)
    pts = np.r_[lattice, crown]
    grid = vg.canopy_grid(pts, pts[:, 2])
    slab = vg.slab_cells(grid)
    i, j = grid.cell(lattice[:, 0], lattice[:, 1])
    assert slab[i, j].mean() > 0.9
    i, j = grid.cell(crown[:, 0], crown[:, 1])
    assert slab[i, j].mean() < 0.05


def test_crown_axes_measures_an_ellipse():
    a = np.radians(30)
    xs, ys = np.meshgrid(np.arange(-8, 8, 0.25), np.arange(-8, 8, 0.25))
    u = xs * math.cos(a) + ys * math.sin(a)
    v = -xs * math.sin(a) + ys * math.cos(a)
    inside = (u / 5.0) ** 2 + (v / 3.0) ** 2 < 1
    cells = np.c_[xs[inside], ys[inside]] + [10.0, 20.0]
    centre, r1, r2, ang = vg.crown_axes(cells)
    assert np.allclose(centre, [10, 20], atol=0.2)
    assert abs(r1 - 5.0) < 0.5 and abs(r2 - 3.0) < 0.4
    assert abs((ang - a + math.pi / 2) % math.pi - math.pi / 2) < 0.1
    # about another centre the radii reach the far side
    _, far1, _, _ = vg.crown_axes(cells, centre + [5 * math.cos(a), 5 * math.sin(a)])
    assert far1 > 8.0


def _variant():
    """A stick tree: a trunk to 0.3, leaf cards from 0.3 to 1.0 in a ball of radius 0.5."""
    rng = np.random.default_rng(7)
    ring = np.array([[0.03, 0, 0], [0, 0, 0.03], [-0.03, 0, 0], [0, 0, -0.03]])
    bark = np.concatenate([ring + [0, y, 0] for y in np.linspace(0, 0.6, 7)])
    idx = np.array([[k, k + 1, k + 4] for k in range(len(bark) - 5)])
    mid = np.c_[rng.uniform(-0.3, 0.3, 200), rng.uniform(0.35, 0.95, 200), rng.uniform(-0.3, 0.3, 200)]
    quad = np.array([[-0.05, -0.05, 0], [0.05, -0.05, 0], [0.05, 0.05, 0], [-0.05, 0.05, 0]])
    cards = mid[:, None, :] + quad[None]
    cards[0, 0, 1], cards[1, 2, 1] = 0.3, 1.0
    uv = np.tile(vg.CARD_UV, (200, 1, 1))
    axis = np.zeros((2, 2))
    return vg.Variant("stick", bark, np.zeros((len(bark), 2)), idx, cards, uv, 0.3, 0.5, 0.03, axis)


def test_place_tree_fills_the_measured_crown():
    v = _variant()
    t = vg.Tree(10.0, 5.0, 0.4, top=9.0, bottom=2.5, r_major=4.0, r_minor=2.0, angle=math.pi / 2,
                lean=(1.0, 0.0), colour=vg.LEAF_GREEN, trunk_r=0.2, in_lot=True)  # fmt: skip
    bark, cards, light = vg.place_tree(v, t)
    z = cards[..., 2] - t.z
    assert abs(z.min() - 2.5) < 0.05 and abs(z.max() - 9.0) < 0.05  # leaves from `bottom` to `top`
    assert abs(bark[:, 2].min() - t.z) < 1e-6  # the trunk stands on the ground
    mid = cards.mean(1)
    # the major axis points along plan y (angle 90 degrees), and the crown's middle leans 1 m in x
    assert np.ptp(mid[:, 1]) > 1.5 * np.ptp(mid[:, 0])
    assert abs(mid[:, 0].mean() - 11.0) < 0.5
    # the trunk is about as thick as measured (0.45 to 1.6 times the model's is allowed)
    low = bark[bark[:, 2] - t.z < 1.5]
    r = np.hypot(low[:, 0] - low[:, 0].mean(), low[:, 1] - low[:, 1].mean()).max()
    assert 0.1 < r < 0.3
    assert light.min() >= 0 and light.max() < 1 and light.std() > 0.05
    again = vg.place_tree(v, t)
    assert np.array_equal(again[2], light)  # the noise is seeded by the position


def test_pick_variant_matches_crown_shape():
    wide, tall = _variant(), _variant()
    wide.crown_radius, tall.crown_radius = 1.0, 0.3
    assert vg.pick_variant([wide, tall], 1.3) == 0
    assert vg.pick_variant([wide, tall], 0.5) == 1


def test_leaf_page_has_a_slot_per_tint_and_level():
    atlas = np.zeros((8, 8, 4), np.uint8)
    atlas[2:6, 2:6] = (100, 150, 80, 255)
    tints = [np.array([60.0, 90.0, 40.0]), np.array([120.0, 120.0, 110.0])]
    page = vg.leaf_page(atlas, tints)
    assert page.shape == (16, 8 * len(vg.LEVELS), 4)
    assert (page[..., 3] > 0).sum() == 16 * 2 * len(vg.LEVELS)
    dark, light = page[3, 3, :3].astype(int), page[3, 8 * 3 + 3, :3].astype(int)
    assert light.sum() > 2 * dark.sum()
    # the second tint's row, third level, is that tint times the level (and its colour shift)
    want = tints[1] * vg.LEVELS[2] * vg.LEVEL_TINT[2]
    assert np.allclose(page[8 + 3, 8 * 2 + 3, :3], want, atol=1.5)
    uv = vg.slot_uv(np.tile(vg.CARD_UV, (2, 1, 1)), np.array([0, 1]), np.array([3, 0]), 2)
    assert uv[0, :, 0].min() >= 0.75 and uv[0, :, 1].min() >= 0.5  # tint 0 is the top row (v up)
    assert uv[1, :, 0].max() <= 0.25 and uv[1, :, 1].max() <= 0.5


def test_merge_tints_joins_the_nearest_colours():
    cols = [
        np.array(c, float)
        for c in ((90, 110, 60), (92, 112, 61), (130, 130, 120), (50, 60, 30), (200, 180, 60))
    ]
    tints, index = vg.merge_tints(cols, limit=4)
    assert len(tints) == 4 and index[0] == index[1] and len(set(index)) == 4
    assert np.allclose(tints[index[0]], [91, 111, 60.5])
    assert vg.merge_tints(cols[:2], limit=4)[1] == [0, 1]


def test_leaf_colour_and_plant_colours():
    grey = vg.leaf_colour(np.tile([136.0, 135.0, 137.0], (100, 1)))
    assert grey[1] > grey[0] and grey[1] > grey[2]  # pulled toward green
    assert np.allclose(vg.leaf_colour(np.zeros((3, 3))), vg.leaf_colour(np.tile(vg.LEAF_GREEN, (50, 1))))
    black = vg.leaf_colour(np.tile([17.0, 16.0, 10.0], (100, 1)))
    assert black @ [0.299, 0.587, 0.114] >= 54.9  # backlit leaves are lifted to a leaf's brightness
    yes = np.array([[60, 90, 40], [119, 114, 68], [59, 53, 11], [34, 52, 27]])
    no = np.array([[129, 137, 143], [223, 221, 215], [154, 38, 27], [140, 111, 68], [156, 148, 135]])
    assert vg.plant_coloured(yes).all() and not vg.plant_coloured(no).any()


def test_voxel_share():
    pts = np.array([[0.1, 0.1, 0.1], [0.2, 0.1, 0.1], [0.1, 0.2, 0.2], [3.0, 3.0, 3.0]])
    share = vg.voxel_share(pts, np.array([True, True, False, False]), 0.5)
    assert np.allclose(share, [2 / 3, 2 / 3, 2 / 3, 0.0])


def test_find_shrubs_wants_foliage_standing_on_the_ground():
    rng = np.random.default_rng(8)
    bush, _ = _blob(rng, (3, 3, 0.6), (0.8, 0.7, 0.55), 1500)
    hanging, _ = _blob(rng, (9, 3, 1.3), (0.6, 0.6, 0.25), 800)
    weed, _ = _blob(rng, (15, 3, 0.35), (0.15, 0.15, 0.1), 300)
    fence = np.c_[rng.uniform(18, 24, 4000), np.full(4000, 3.0), rng.uniform(0.3, 1.8, 4000)]
    ivy, _ = _blob(rng, (21, 3.1, 0.7), (0.8, 0.08, 0.4), 400)  # a few leaves on the fence
    pts = np.r_[bush, hanging, weed, fence, ivy]
    foliage = np.r_[np.ones(2600, bool), np.zeros(4000, bool), np.ones(400, bool)]
    why: list = []
    found = vg.find_shrubs(pts, pts[:, 2], np.full(len(pts), 0.002), foliage, why)
    assert len(found) == 1 and found[0].max() < 1500
    reasons = " | ".join(r[3] for r in why)
    assert "hangs" in reasons and "foliage is" in reasons
    assert all(r[2] >= vg.SHRUB_MIN_FACES for r in why)


def test_bush_cards_fill_the_shrub_and_repeat():
    s = vg.Shrub(4.0, 5.0, 0.2, 0.9, 0.6, 1.1, vg.LEAF_GREEN)
    cards, light, cluster = vg.bush_cards(s)
    assert len(cards) == len(light) == len(cluster) >= 14
    assert cards[..., 2].min() >= 0.2 and cards[..., 2].max() < 0.2 + 1.1 + 0.6
    assert np.abs(cards[..., 0] - 4.0).max() < 0.9 + 0.6 and np.abs(cards[..., 1] - 5.0).max() < 0.6 + 0.6
    assert set(cluster) <= {0, 1, 2, 3}
    assert np.array_equal(vg.bush_cards(s)[0], cards)


def test_prism_and_outside_point():
    tris = vg.prism(1.0, 2.0, 0.2, 0.0, 2.5)
    assert tris.shape == (24, 3, 3)
    assert np.isclose(tris[..., 2].min(), 0.0) and np.isclose(tris[..., 2].max(), 2.5)
    assert np.hypot(tris[..., 0] - 1.0, tris[..., 1] - 2.0).max() < 0.2 + 1e-9
    lot = box(0, 0, 20, 10)
    p = vg.outside_point(lot, np.array([8.0, 8.5]))  # a crown hanging over the north fence
    assert not lot.contains(Point(*p)) and abs(lot.exterior.distance(Point(*p)) - vg.OUTSIDE) < 1e-6
    assert p[1] > 10 and abs(p[0] - 8.0) < 1e-6
    q = vg.outside_point(lot, np.array([8.0, 10.6]))  # already outside: further out, not back in
    assert q[1] > 10.6


def test_without_the_library_nothing_is_replaced(tmp_path, monkeypatch):
    monkeypatch.setattr(vg, "LIBRARY", tmp_path / "missing")
    assert vg.load_library() is None
    report: dict = {}
    faces = np.arange(5)
    taken, solids = vg.replace_vegetation(
        trimesh.Scene(), None, None, faces, np.zeros(5, bool), np.ones(5, bool), None, box(0, 0, 1, 1), report
    )
    assert not taken.any() and solids.shape == (0, 3, 3)
    assert report["vegetation"]["replaced"] is False and "library" in report["vegetation"]["why"]
