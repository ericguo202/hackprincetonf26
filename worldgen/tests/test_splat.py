"""The drone splat as an offline source (splat.py), the snapped roof fit (roof.py) and railings.py."""

import numpy as np
from shapely.geometry import Polygon, box

from worldgen import railings
from worldgen import roof as rf
from worldgen import splat as sp


def disks(xyz: np.ndarray, rgb, normal=(0.0, 0.0, 1.0), size=0.03, alpha=0.9) -> sp.Splat:
    """Flat opaque Gaussians (thin along `normal`) at the given points."""
    n = np.asarray(normal, float) / np.linalg.norm(normal)
    cov = (np.eye(3) - np.outer(n, n)) * size**2 + np.outer(n, n) * 0.003**2
    k = len(xyz)
    return sp.Splat(
        xyz.astype(np.float32),
        np.tile(cov.astype(np.float32), (k, 1, 1)),
        np.tile(np.asarray(rgb, np.float32), (k, 1)) if np.ndim(rgb) == 1 else np.asarray(rgb, np.float32),
        np.full(k, alpha, np.float32),
        np.full(k, size, np.float32),
    )


def join(*parts: sp.Splat) -> sp.Splat:
    return sp.Splat(
        *(np.concatenate([getattr(p, f) for p in parts]) for f in ("xyz", "cov", "rgb", "alpha", "size"))
    )


def gable(rng, ridge_x=5.0, eave_z=2.5, slope=0.4, w=10.0, d=8.0, step=0.04) -> np.ndarray:
    x, y = np.meshgrid(np.arange(0, w, step), np.arange(0, d, step))
    x, y = x.ravel() + rng.normal(0, 0.005, x.size), y.ravel()
    return np.c_[x, y, eave_z + slope * (ridge_x - np.abs(x - ridge_x))]


def test_flatten_sees_only_the_slab_and_orders_front_to_back():
    xy = np.stack(np.meshgrid(np.arange(0.02, 1, 0.04), np.arange(0.02, 1, 0.04)), -1).reshape(-1, 2)
    red = disks(np.c_[xy, np.full(len(xy), 1.0)], (1, 0, 0))
    blue = disks(np.c_[xy, np.full(len(xy), 2.0)], (0, 0, 1))
    s = join(red, blue)
    down = dict(origin=(0, 1, 5.0), u=(1, 0, 0), v=(0, -1, 0), w=1.0, h=1.0, res=50, look=(0, 0, -1))
    rgb, al, dep = s.flatten(**down, near=0.0, far=10.0)
    mid = (slice(10, 40), slice(10, 40))
    assert al[mid].min() > 0.95
    assert rgb[mid][..., 2].mean() > 230 and rgb[mid][..., 0].mean() < 25  # the upper sheet hides the lower
    assert abs(float(np.nanmedian(dep[mid])) - 3.0) < 0.05
    rgb, al, _ = s.flatten(**down, near=3.5, far=4.5)  # the slab around z = 1 only
    assert rgb[mid][..., 0].mean() > 230 and rgb[mid][..., 2].mean() < 25
    _, al, _ = s.flatten(**down, near=5.5, far=9.0)
    assert al.max() < 0.01


def test_roof_sheet_is_the_dense_layer_under_a_canopy():
    rng = np.random.default_rng(0)
    roof = disks(gable(rng), (0.4, 0.3, 0.3))
    leaves = disks(
        np.c_[rng.uniform(0, 10, 4000), rng.uniform(0, 8, 4000), rng.uniform(5.5, 8.5, 4000)], (0, 1, 0)
    )
    sheet = join(roof, leaves).roof_sheet((0, 0, 10, 8), 1.9, 9.0)
    x, y = np.meshgrid(np.arange(0.5, 10, 0.5), np.arange(0.5, 8, 0.5))
    z = sheet(x.ravel(), y.ravel())
    want = 2.5 + 0.4 * (5.0 - np.abs(x.ravel() - 5.0))
    assert np.isfinite(z).mean() > 0.98
    assert np.nanmedian(np.abs(z - want)) < 0.05 and np.nanpercentile(np.abs(z - want), 95) < 0.1


def test_snapped_fit_meets_at_the_ridge_and_stays_continuous():
    rng = np.random.default_rng(1)
    s = disks(gable(rng), (0.4, 0.3, 0.3))
    region = box(0.1, 0.1, 9.9, 7.9)
    sheet = s.roof_sheet((0, 0, 10, 8), 1.9, 9.0)
    roof = rf.fit_roof(sheet, region, step=sp.SHEET_CELL, snap=True, min_area=1.0)
    assert roof is not None and len(roof.planes) == 2 and roof.kind is not None
    x = np.arange(0.5, 9.5, 0.02)
    z = roof.surface(x, np.full(len(x), 4.0))
    assert np.abs(np.diff(z)).max() < 0.03  # no step where the two planes' cells meet
    assert abs(float(z.max()) - (2.5 + 0.4 * 5.0)) < 0.08  # the ridge is at its true height
    assert abs(float(x[z.argmax()]) - 5.0) < 0.15


def test_snapped_fit_keeps_a_real_step():
    rng = np.random.default_rng(2)
    x, y = np.meshgrid(np.arange(0, 10, 0.04), np.arange(0, 8, 0.04))
    x, y = x.ravel(), y.ravel()
    z = np.where(x < 5, 4.0, 2.6) + rng.normal(0, 0.004, x.size)  # a porch roof beside a higher flat roof
    s = disks(np.c_[x, y, z], (0.4, 0.3, 0.3))
    roof = rf.fit_roof(s.roof_sheet((0, 0, 10, 8), 1.9, 9.0), box(0.1, 0.1, 9.9, 7.9), step=0.1, snap=True)
    assert roof is not None and len(roof.planes) == 2
    assert abs(roof.surface(np.array([4.0]), np.array([4.0]))[0] - 4.0) < 0.06
    assert abs(roof.surface(np.array([6.0]), np.array([4.0]))[0] - 2.6) < 0.06


def test_roof_outline_follows_the_sheet_past_the_walls():
    rng = np.random.default_rng(3)
    s = disks(gable(rng), (0.4, 0.3, 0.3))  # the roof covers 0..10 x 0..8
    walls = box(1.0, 1.0, 9.0, 7.0)  # the walls stand a metre inside its edge
    limit = walls.buffer(sp.EDGE_REACH, join_style="mitre")
    sheet = s.roof_sheet(limit.buffer(0.5).bounds, 1.9, 9.0)
    roof = rf.fit_roof(sheet, limit, step=sp.SHEET_CELL, min_area=1.0)
    edge = s.roof_outline(sheet, roof.surface, walls, limit)
    assert isinstance(edge, Polygon)
    assert np.allclose(edge.bounds, (0.0, 0.0, 10.0, 8.0), atol=0.25)
    assert abs(edge.area - 80.0) < 6.0


def test_wall_fill_only_where_the_splat_covers_enough():
    from worldgen import rebake as rb

    yz = np.stack(np.meshgrid(np.arange(0.02, 4, 0.04), np.arange(0.02, 2.5, 0.04)), -1).reshape(-1, 2)
    wall = disks(np.c_[yz[:, 0], np.zeros(len(yz)), yz[:, 1]], (0.6, 0.3, 0.2), normal=(0, 1, 0))
    p = rb.Plane(
        "wall",
        np.zeros(3),
        np.array([1.0, 0, 0]),
        np.array([0, 0, 1.0]),
        np.array([0, -1.0, 0]),
        4.0,
        2.5,
        box(0, 0, 4, 2.5),
    )
    res, W, H = 32.0, 128, 80
    mask = np.ones((H, W), bool)
    rgb, valid = np.zeros((H * W, 3)), np.zeros(H * W, bool)
    fill = sp.WallFill(wall)
    got, ok = fill(p, res, W, H, mask, rgb, valid)
    assert ok.mean() > 0.9 and fill.walls[-1]["used"]
    assert np.abs(got[ok].mean(0) - np.array([153, 76.5, 51])).max() < 8
    half = sp.Splat(*(getattr(wall, f)[wall.xyz[:, 0] < 1.6] for f in ("xyz", "cov", "rgb", "alpha", "size")))
    fill = sp.WallFill(half)
    _, ok = fill(p, res, W, H, mask, rgb, valid)
    assert not ok.any() and not fill.walls[-1]["used"]  # 40% covered: under WALL_MIN, the wall is left alone


class FlatGround:
    lot = box(-1, -1, 9, 9)

    @staticmethod
    def height(x, y):
        return np.zeros(np.shape(x))


def railing(x0=1.0, x1=5.0, y=3.0, top=1.0) -> sp.Splat:
    pts = []
    for z in (0.12, top - 0.03):  # two rails
        s = np.arange(x0, x1, 0.01)
        pts.append(np.c_[s, np.full(len(s), y), np.full(len(s), z)])
    for bx in np.arange(x0, x1 + 0.01, 0.15):  # balusters
        z = np.arange(0.12, top, 0.01)
        pts.append(np.c_[np.full(len(z), bx), np.full(len(z), y), z])
    return disks(np.concatenate(pts), (0.6, 0.1, 0.1), normal=(0, 1, 0), size=0.012)


def test_railing_found_and_panel_has_holes():
    walls = box(20, 20, 30, 30)
    runs = railings.find_rails(railing(), FlatGround, walls)
    assert len(runs) == 1
    r = runs[0]
    assert (
        abs(r.length - 4.0) < 0.25 and abs(abs(r.u[0]) - 1.0) < 0.01 and abs(r.meta["height_m"] - 1.0) < 0.08
    )
    p = railings.rail_plane(railing(), r)
    assert p is not None and p.image.shape[2] == 4
    alpha = p.image[..., 3] > 0
    assert 0.12 < alpha.mean() < 0.8  # see-through between the balusters
    assert alpha[: int(0.2 * railings.RAIL_RES)].mean(1).max() > 0.9  # with a top rail from end to end


def test_fence_and_shrub_are_not_railings():
    rng = np.random.default_rng(4)
    yz = np.stack(np.meshgrid(np.arange(1, 5, 0.02), np.arange(0.0, 1.8, 0.02)), -1).reshape(-1, 2)
    fence = disks(
        np.c_[yz[:, 0], np.full(len(yz), 3.0), yz[:, 1]], (0.5, 0.4, 0.3), normal=(0, 1, 0), size=0.012
    )
    shrub = disks(
        np.c_[rng.uniform(1, 5, 20000), rng.normal(6.0, 0.25, 20000), rng.uniform(0.2, 1.0, 20000)],
        (0, 0.5, 0),
        size=0.012,
    )
    walls = box(20, 20, 30, 30)
    built = [
        r
        for r in railings.find_rails(join(fence, shrub), FlatGround, walls)
        if railings.rail_plane(join(fence, shrub), r)
    ]
    assert built == []
