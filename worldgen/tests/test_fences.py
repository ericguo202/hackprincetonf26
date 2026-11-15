import numpy as np
from shapely.geometry import Polygon, box

from worldgen import fences as fn
from worldgen import rebake as rb
from worldgen.panos import Raycaster


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


class FlatGround:
    """Stand-in for exterior.Ground: a level lot."""

    def __init__(self, lot: Polygon, z: float = 0.0) -> None:
        self.lot, self.z = lot, z

    def height(self, x, y):
        return np.full(np.shape(x), self.z, float)


def sheet(a, b, z0, z1, ripple=0.0, step=0.1, seed=0) -> np.ndarray:
    """A vertical sheet from plan point a to b between heights z0 and z1 as small triangles, pushed
    sideways by up to `ripple` (the way the MatterPak scans a board fence)."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    length = float(np.linalg.norm(b - a))
    u = (b - a) / length
    n = np.array([-u[1], u[0]])
    rng = np.random.default_rng(seed)
    ns, nz = max(1, round(length / step)), max(1, round((z1 - z0) / step))
    s = np.linspace(0, length, ns + 1)
    z = np.linspace(z0, z1, nz + 1)
    off = rng.uniform(-ripple, ripple, (ns + 1, nz + 1))
    p = np.zeros((ns + 1, nz + 1, 3))
    p[..., :2] = a + s[:, None, None] * u + off[..., None] * n
    p[..., 2] = z[None, :]
    q00, q10, q11, q01 = p[:-1, :-1], p[1:, :-1], p[1:, 1:], p[:-1, 1:]
    return np.concatenate([np.stack([q00, q10, q11], -2), np.stack([q00, q11, q01], -2)]).reshape(-1, 3, 3)


def hedge(centre, size, n=2500, seed=1) -> np.ndarray:
    """Small triangles scattered through a box: leaves."""
    rng = np.random.default_rng(seed)
    c = np.asarray(centre) + rng.uniform(-0.5, 0.5, (n, 3)) * np.asarray(size)
    d = rng.normal(size=(n, 2, 3)) * 0.06
    return np.stack([c, c + d[:, 0], c + d[:, 1]], 1)


def yard():
    lot = box(-1, -1, 13, 9)
    fence = sheet((0, 8), (10, 8), 0.0, 1.8, ripple=0.03)  # a board fence along the back
    rail = sheet((1, 2), (5, 2), 0.0, 0.95, ripple=0.01)  # a deck railing: too low
    bush = hedge((8, 3, 0.9), (0.24, 3.0, 1.8))  # a hedge: tall and long, but not a flat sheet
    wall = sheet((11, 0), (11, 6), 0.0, 2.3)  # a garage wall ...
    soffit = np.array(  # ... under its eaves
        [[[10.5, 0, 2.3], [10.5, 6, 2.3], [11.5, 6, 2.3]], [[10.5, 0, 2.3], [11.5, 6, 2.3], [11.5, 0, 2.3]]]
    )
    return lot, np.concatenate([fence, rail, bush, wall, soffit])


def test_find_runs_takes_the_fence_and_nothing_else():
    lot, tris = yard()
    rejected = []
    runs = fn.find_runs(fake_scan(tris), FlatGround(lot), box(20, 20, 21, 21), rejected=rejected)
    assert len(runs) == 1
    r = runs[0]
    assert abs(r.length - 10.0) < 0.3
    assert abs(abs(r.u[0]) - 1.0) < 1e-3  # along x
    assert abs(r.a[1] - 8.0) < 0.03 and abs(r.b[1] - 8.0) < 0.03  # on the fence's line despite the ripple
    assert np.all(np.abs(r.top - 1.8) < 0.06) and np.allclose(r.ground, 0.0)
    why = {w for _, w in rejected}
    assert any("railing" in w for w in why)
    assert any("wall" in w for w in why)
    # The hedge either never forms a solid stretch or is thrown out as not flat; it is never a run.
    assert all(abs(x.a[0] - 8.0) > 0.5 or abs(abs(x.u[0]) - 1.0) < 1e-3 for x in runs)


def test_runs_meet_at_a_corner():
    lot = box(-1, -1, 9, 9)
    tris = np.concatenate([sheet((0, 8.1), (8, 8.1), 0, 1.8), sheet((8.1, 0), (8.1, 8), 0, 1.8)])
    runs = fn.find_runs(fake_scan(tris), FlatGround(lot), box(20, 20, 21, 21))
    assert len(runs) == 2
    ends = np.array([p for r in runs for p in (r.a, r.b)])
    d = np.linalg.norm(ends[:, None] - ends[None], axis=-1)
    d[np.eye(4, dtype=bool)] = 9.0
    assert d.min() < 1e-6  # one end of each run was moved onto the corner


def test_top_line_drops_bumps_fills_dips_and_keeps_steps():
    st = (np.arange(200) + 0.5) * fn.S_BIN  # 20 m
    tz = np.where(st < 12, 1.8, 2.0)  # a step down the slope stays
    tz[20:22] += 0.15  # a post standing above the boards
    tz[50:62] += 0.5  # a neighbour's shrub hanging over 1.2 m of fence
    tz[90:115] -= 0.8  # the scanner did not see the top behind a tree (2.5 m)
    tz[0:9] += 0.3  # a shrub at the very end of the run
    s, top = fn._smooth_top(st, tz.astype(float))
    at = lambda x: float(np.interp(x, s, top))  # noqa: E731
    assert abs(at(2.1) - 1.8) < 0.04 and abs(at(5.6) - 1.8) < 0.04
    assert abs(at(10.0) - 1.8) < 0.04  # the dip is filled
    assert abs(at(0.4) - 1.8) < 0.04  # the bump at the end is gone too
    assert abs(at(6.0) - 1.8) < 0.04 and abs(at(16.0) - 2.0) < 0.04  # both levels of the step
    assert len(s) <= 6  # a few straight pieces, not a staircase


def test_panel_follows_top_and_ground():
    r = fn.Run(
        a=np.array([0.0, 0.0]),
        u=np.array([1.0, 0.0]),
        length=6.0,
        s=np.array([0.0, 3.0, 3.0001, 6.0]),
        top=np.array([1.8, 1.8, 2.1, 2.1]),
        ground=np.array([0.0, 0.0, 0.3, 0.3]),
    )
    (p,) = fn.run_planes(r)
    assert p.w == 6.0 and np.allclose(p.n, [0, 1, 0]) and np.allclose(p.u, [1, 0, 0])
    z0 = p.origin[2]
    assert abs(z0 - (0.0 - fn.GROUND_SINK - 0.02)) < 1e-9
    assert p.shape.contains(Polygon([(0.5, 0.2 - z0), (2.5, 0.2 - z0), (2.5, 1.7 - z0), (0.5, 1.7 - z0)]))
    assert not p.shape.intersects(box(0.5, 1.85 - z0, 2.5, 2.0 - z0))  # above the lower stretch's top
    assert p.shape.contains(box(3.5, 1.9 - z0, 5.5, 2.05 - z0))  # inside the higher stretch
    both = fn.run_planes(r, (1.0, -1.0), gap=0.04)
    assert len(both) == 2 and np.allclose(both[0].n, -both[1].n)
    assert abs((both[0].origin - both[1].origin) @ both[0].n - 0.04) < 1e-9
    # Collision: thin boxes from the ground to the top; scan points on the run are taken, others are not.
    solids = fn.collision_boxes([r])
    assert solids.shape[1:] == (3, 3) and len(solids) >= 24
    assert abs(solids[..., 2].max() - 2.1) < 1e-6 and abs(np.abs(solids[..., 1]).max() - 0.04) < 1e-6
    pts = np.array([[1.0, 0.05, 1.0], [1.0, 0.4, 1.0], [1.0, 0.0, 2.2], [7.0, 0.0, 1.0], [5.0, -0.1, 2.0]])
    assert fn.on_runs(pts, [r]).tolist() == [True, False, False, False, True]


def boards(H=120, W=600, period=14) -> np.ndarray:
    """A fence picture: boards `period` texels wide with a dark gap, each with its own tone, and a rail."""
    rng = np.random.default_rng(3)
    tone = np.repeat(rng.uniform(110, 170, W // period + 1), period)[:W]
    img = np.tile(tone, (H, 1))
    img[:, ::period] = 40
    img[30:36] -= 30
    return np.clip(np.dstack([img, img * 0.9, img * 0.8]), 0, 255).astype(np.uint8)


def test_board_period_finds_the_board_width():
    img = boards()
    valid = np.ones(img.shape[:2], bool)
    assert abs(fn.board_period(img, valid, 96.0) - 14) <= 1
    flat = np.full_like(img, 128)
    assert fn.board_period(flat, valid, 96.0) == fn.BOARD_DEFAULT * 96.0


def test_fill_along_continues_the_boards():
    img = boards()
    H, W = img.shape[:2]
    mask = np.ones((H, W), bool)
    sharp = mask.copy()
    sharp[20:100, 250:350] = False  # a shrub hid this part
    holed = img.copy()
    holed[~sharp] = (0, 255, 0)  # whatever was there must not survive
    out = fn.fill_along(holed, sharp, sharp, mask, np.zeros(W), 14.0)
    assert np.array_equal(out[sharp], img[sharp])  # seen texels are untouched
    hole = out[20:100, 250:350].astype(float)
    assert hole[..., 1].max() < 200  # no green left
    gaps = hole[:, (-250) % 14 :: 14].mean()  # columns where a gap between boards belongs
    faces = hole[:, ((-250) % 14 + 7) :: 14].mean()
    assert gaps < faces - 25  # the gaps are there, at the right phase: boards, not a blur
    assert hole[10:16].mean() < hole[30:60].mean() - 10  # and the rail runs on through the hole (rows 30-36)
    # A soft area keeps its own broad colour and gets the boards.
    soft_img = img.copy()
    region = (slice(0, H), slice(400, 520))
    soft_img[region] = 200  # a distant, washed-out photo
    sharp2 = mask.copy()
    sharp2[region] = False
    out2 = fn.fill_along(soft_img, sharp2, mask, mask, np.zeros(W), 14.0).astype(float)
    area = out2[region]
    assert area.mean() > 170  # still bright
    assert area[:, (-400) % 14 :: 14].mean() < area[:, ((-400) % 14 + 7) :: 14].mean() - 15  # with gaps


def test_fill_along_lines_rows_up_at_the_top():
    # The fence steps down by 20 texels half way: the rail steps with it, and a hole across the step
    # must get the rail at the stepped height on each side.
    H, W = 140, 600
    img = np.zeros((H, W, 3), np.uint8)
    top_row = np.where(np.arange(W) < 300, 0.0, 20.0)
    for half, r0 in ((slice(0, 300), 0), (slice(300, W), 20)):
        img[r0 : r0 + 120, half] = boards(120, 300)
    mask = np.zeros((H, W), bool)
    mask[0:120, :300] = True
    mask[20:140, 300:] = True
    sharp = mask.copy()
    sharp[:, 240:360] = False
    holed = img.copy()
    holed[~sharp] = 0
    out = fn.fill_along(holed, sharp, sharp, mask, top_row, 14.0).astype(float)
    left, right = out[:, 250:290, 0], out[:, 310:350, 0]
    assert left[30:36].mean() < left[60:90].mean() - 10  # rail 30 texels below the top on the left ...
    assert right[50:56].mean() < right[80:110].mean() - 10  # ... and on the right, 20 texels lower


def test_a_pallet_leaning_on_the_fence_is_not_cut_in_half():
    fence = sheet((0, 0), (8, 0), 0.0, 1.8, ripple=0.02)
    # A board leaning against the fence between x = 3 and 4: foot 0.4 m out, top touching the boards.
    lean = sheet((3, 0), (4, 0), 0.0, 1.2, step=0.1)
    lean[..., 1] = -0.4 * (1 - lean[..., 2] / 1.2) - 0.01
    scan = fake_scan(np.concatenate([fence, lean]))
    r = fn.Run(
        np.array([0.0, 0.0]),
        np.array([1.0, 0.0]),
        8.0,
        np.array([0.0, 8.0]),
        np.array([1.8, 1.8]),
        np.zeros(2),
    )
    faces = np.arange(len(scan.tris))
    taken = fn.taken_faces(scan, faces, [r])
    is_fence = faces < len(fence)
    assert taken[is_fence].mean() > 0.95  # the fence's sheet goes everywhere, also behind the board
    assert taken[~is_fence].mean() < 0.25  # the board stays, but for the part lying on the fence
    # Without the board the same faces would all be within reach of the panel.
    assert fn.on_runs(scan.tris[~is_fence].mean(1), [r]).mean() > 0.3
