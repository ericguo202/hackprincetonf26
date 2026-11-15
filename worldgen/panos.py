"""Matterport panoramas (skybox cube faces + camera poses) and projective shading from them.

Convention, found by matching every candidate against the scan mesh's colours on 7014 Navidad Rd
(2026-11-14; the right one scored 22 mean abs error, the other 15 scored 35+):
- `rotation` is camera -> world; the camera frame is Z-up with forward = +X and right = -Y.
- Faces: 0 = up (image top points backward), 1 = forward, 2 = right, 3 = back, 4 = left,
  5 = down (image top points forward). The centre of face 5 is the blurred tripod.

Everything is in Matterport Z-up model coordinates."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from embreex import rtcore_scene
from embreex.mesh_construction import TriangleMesh
from PIL import Image

NADIR_BLUR = 0.62  # tan of the half-angle around straight down that the tripod blur covers (about 32 deg)


def quat_matrix(q: dict) -> np.ndarray:
    x, y, z, w = q["x"], q["y"], q["z"], q["w"]
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ]
    )


@dataclass
class Pano:
    id: str
    pos: np.ndarray  # (3,)
    rot: np.ndarray  # (3, 3) camera -> world
    paths: list[Path]


class PanoSet:
    def __init__(self, model: dict, skybox_dir: Path, cache: int = 12) -> None:
        self.panos: list[Pano] = []
        for p in model.get("panoLocations") or []:
            paths = [skybox_dir / f"{p['id']}_{i}.jpg" for i in range(6)]
            if all(f.exists() for f in paths) and p.get("rotation"):
                pos = np.array([p["position"][k] for k in "xyz"], dtype=float)
                self.panos.append(Pano(p["id"], pos, quat_matrix(p["rotation"]), paths))
        self.positions = np.array([p.pos for p in self.panos]) if self.panos else np.zeros((0, 3))
        self._cache: OrderedDict[int, list[np.ndarray]] = OrderedDict()
        self._cache_size = cache

    def __len__(self) -> int:
        return len(self.panos)

    def subset(self, keep: list[int]) -> PanoSet:
        """A PanoSet with only the given panoramas (e.g. the ones taken indoors)."""
        out = PanoSet.__new__(PanoSet)
        out.panos = [self.panos[i] for i in keep]
        out.positions = self.positions[keep] if keep else np.zeros((0, 3))
        out._cache = OrderedDict()
        out._cache_size = self._cache_size
        return out

    def faces(self, i: int) -> list[np.ndarray]:
        if i in self._cache:
            self._cache.move_to_end(i)
            return self._cache[i]
        faces = [np.asarray(Image.open(f).convert("RGB")) for f in self.panos[i].paths]
        self._cache[i] = faces
        if len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)
        return faces

    def sample(self, i: int, pts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Colour of world points as seen from pano i (bilinear), and a 0-1 trust factor that is 0 inside
        the tripod blur and ramps to 1 just outside it (so floors hand over between cameras smoothly)."""
        p = self.panos[i]
        dc = (pts - p.pos) @ p.rot  # world -> camera
        df, dr, du = dc[:, 0], -dc[:, 1], dc[:, 2]
        af, ar, au = np.abs(df), np.abs(dr), np.abs(du)
        face = np.empty(len(pts), dtype=np.int8)
        x = np.empty(len(pts))
        y = np.empty(len(pts))
        up = (au >= af) & (au >= ar)
        fwd = ~up & (af >= ar)
        side = ~up & ~fwd
        with np.errstate(divide="ignore", invalid="ignore"):
            s = up & (du > 0)
            face[s], x[s], y[s] = 0, dr[s] / au[s], df[s] / au[s]
            s = up & (du <= 0)
            face[s], x[s], y[s] = 5, dr[s] / au[s], -df[s] / au[s]
            s = fwd & (df > 0)
            face[s], x[s], y[s] = 1, dr[s] / af[s], -du[s] / af[s]
            s = fwd & (df <= 0)
            face[s], x[s], y[s] = 3, -dr[s] / af[s], -du[s] / af[s]
            s = side & (dr > 0)
            face[s], x[s], y[s] = 2, -df[s] / ar[s], -du[s] / ar[s]
            s = side & (dr <= 0)
            face[s], x[s], y[s] = 4, df[s] / ar[s], -du[s] / ar[s]
        trust = np.ones(len(pts))
        down = face == 5
        trust[down] = np.clip((np.hypot(x[down], y[down]) - NADIR_BLUR) / 0.4, 0, 1)
        out = np.zeros((len(pts), 3))
        imgs = self.faces(i)
        for f in range(6):
            s = face == f
            if not s.any():
                continue
            img = imgs[f]
            h, w = img.shape[:2]
            px = np.clip((x[s] + 1) / 2 * w - 0.5, 0, w - 1.001)
            py = np.clip((y[s] + 1) / 2 * h - 0.5, 0, h - 1.001)
            x0, y0 = px.astype(int), py.astype(int)
            fx, fy = (px - x0)[:, None], (py - y0)[:, None]
            c = (
                img[y0, x0] * (1 - fx) * (1 - fy)
                + img[y0, x0 + 1] * fx * (1 - fy)
                + img[y0 + 1, x0] * (1 - fx) * fy
                + img[y0 + 1, x0 + 1] * fx * fy
            )
            out[s] = c
        return out, trust


class Raycaster:
    """Embree scene over a triangle soup (n, 3, 3)."""

    def __init__(self, tris: np.ndarray) -> None:
        self._scene = rtcore_scene.EmbreeScene()
        self._mesh = TriangleMesh(self._scene, np.ascontiguousarray(tris, dtype=np.float32))

    def cast(self, origins: np.ndarray, dirs: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """(distance, triangle index) of the first hit per ray; distance inf and index -1 on a miss."""
        if len(origins) == 0:
            return np.zeros(0), np.zeros(0, dtype=np.int32)
        r = self._scene.run(
            np.ascontiguousarray(origins, dtype=np.float32),
            np.ascontiguousarray(dirs, dtype=np.float32),
            output=True,
        )
        t = r["tfar"].astype(float)
        t[r["primID"] < 0] = np.inf
        return t, r["primID"]


SNAP = 0.15  # look this far either side of a shell point for the real scanned surface


def _look(
    panos: PanoSet, rays: Raycaster, p: np.ndarray, geo: np.ndarray, order: list, k: int, rows: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Which of points p[rows] pano order[k] really sees (mask), and the colours and weights of those."""
    c = panos.positions[order[k]]
    v = p[rows] - c
    dist = np.linalg.norm(v, axis=1)
    t, _ = rays.cast(np.tile(c, (len(rows), 1)), v / dist[:, None])
    cos = geo[rows, k] * dist**2
    vis = t >= dist - (0.05 + 0.012 / np.maximum(cos, 0.12))
    if not vis.any():
        return vis, np.zeros((0, 3)), np.zeros(0)
    col, trust = panos.sample(order[k], p[rows[vis]])
    return vis, col, geo[rows[vis], k] * trust


def shade(
    pts: np.ndarray,
    normals: np.ndarray,
    panos: PanoSet,
    rays: Raycaster,
    snap: bool = True,
    max_dist: float = 12.0,
    max_candidates: int = 12,
    views: int = 5,
    chunk: int = 300_000,
    min_cos: float = 0.12,
) -> tuple[np.ndarray, np.ndarray]:
    """Colour each surface point from the panoramas that see it.

    For shell points (snap=True) the point is first moved along its normal onto the scanned surface, so
    every panorama is sampled at the true 3D position (no ghosting when the plane is a few cm off).
    A panorama counts if the point faces it and the scan does not occlude it. Per point the best `views`
    panoramas (by cos / distance^2) are sampled; when three or more see it, views that disagree with the
    per-channel median are thrown out (photo-consistency: doors that were open in one sweep and closed in
    another, the photographer, reflections). The rest are blended with weight^3: nearly "best view wins",
    with soft seams. `min_cos`: a camera counts when it sees the surface less obliquely than this (cosine
    of the angle to the normal; the far field asks for more than the default, a street seen from 1.5 m up is
    streaks beyond a few metres). Returns (rgb float (n, 3), valid (n,))."""
    n = len(pts)
    rgb = np.zeros((n, 3))
    valid = np.zeros(n, dtype=bool)
    for a in range(0, n, chunk):
        b = min(a + chunk, n)
        p, nm = pts[a:b].astype(float), normals[a:b].astype(float)
        m = b - a
        if snap:
            t, _ = rays.cast(p + nm * SNAP, -nm)
            hit = t < 2 * SNAP
            p = p.copy()
            p[hit] = p[hit] + nm[hit] * (SNAP - t[hit, None])
        centre = p.mean(0)
        reach = float(np.linalg.norm(p - centre, axis=1).max()) + max_dist
        order = np.argsort(np.linalg.norm(panos.positions - centre, axis=1))[:max_candidates]
        order = [j for j in order if np.linalg.norm(panos.positions[j] - centre) <= reach]
        if not order:
            continue
        geo = np.zeros((m, len(order)))  # geometric weight of each candidate view
        for k, j in enumerate(order):
            v = panos.positions[j] - p
            dist = np.linalg.norm(v, axis=1)
            cos = np.einsum("ij,ij->i", v, nm) / np.maximum(dist, 1e-6)
            ok = (cos > min_cos) & (dist < max_dist) & (dist > 0.25)
            geo[ok, k] = cos[ok] / dist[ok] ** 2
        kk = min(views, len(order))
        top = (
            np.argpartition(-geo, kk - 1, axis=1)[:, :kk]
            if len(order) > kk
            else np.tile(np.arange(kk), (m, 1))
        )
        cols = np.zeros((m, kk, 3))
        wts = np.zeros((m, kk))

        for k in range(len(order)):
            rows, slots = np.nonzero(top == k)
            use = geo[rows, k] > 0
            rows, slots = rows[use], slots[use]
            if not len(rows):
                continue
            vis, col, w = _look(panos, rays, p, geo, order, k, rows)
            cols[rows[vis], slots[vis]] = col
            wts[rows[vis], slots[vis]] = w
        # Points none of their best views could see: try the remaining candidates, nearest first.
        for k in range(len(order)):
            left = np.nonzero((wts.sum(1) == 0) & (geo[:, k] > 0))[0]
            if not len(left):
                continue
            vis, col, w = _look(panos, rays, p, geo, order, k, left)
            cols[left[vis], 0] = col
            wts[left[vis], 0] = w
        # Photo-consistency: with 3+ views, drop the ones far from the median colour.
        seen = wts > 0
        many = seen.sum(1) >= 3
        if many.any():
            sm = seen[many]
            srt = np.sort(np.where(sm[..., None], cols[many], np.inf), axis=1)  # unseen views sort last
            cnt = sm.sum(1)
            lo = np.take_along_axis(srt, ((cnt - 1) // 2)[:, None, None].repeat(3, 2), axis=1)[:, 0]
            hi = np.take_along_axis(srt, (cnt // 2)[:, None, None].repeat(3, 2), axis=1)[:, 0]
            med = (lo + hi) / 2
            dev = np.abs(cols[many] - med[:, None, :]).max(axis=2)
            bad = (dev > 45) & seen[many]
            bad[bad.sum(1) == seen[many].sum(1)] = False  # never drop every view
            w_many = wts[many]
            w_many[bad] = 0
            wts[many] = w_many
        w3 = wts**3
        wsum = w3.sum(1)
        got = wsum > 0
        rgb[a:b][got] = (cols[got] * w3[got][..., None]).sum(1) / wsum[got, None]
        valid[a:b] = got
    return rgb, valid


def see(
    panos: PanoSet,
    rays: Raycaster,
    j: int,
    pts: np.ndarray,
    normals: np.ndarray,
    max_dist: float = 12.0,
    min_cos: float = 0.12,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """What panorama j sees of surface points: (visible mask (n,), colours and weights of the visible
    ones). Same tests as `shade`: the point faces the camera (cosine above min_cos), is 0.25 to max_dist
    away, and nothing in `rays` is in front of it. The weight is cos / distance^2 times the tripod trust."""
    c = panos.positions[j]
    v = c - pts
    dist = np.linalg.norm(v, axis=1)
    cos = np.einsum("ij,ij->i", v, normals) / np.maximum(dist, 1e-6)
    vis = np.zeros(len(pts), dtype=bool)
    idx = np.nonzero((cos > min_cos) & (dist < max_dist) & (dist > 0.25))[0]
    if len(idx):
        t, _ = rays.cast(np.tile(c, (len(idx), 1)), -v[idx] / dist[idx, None])
        vis[idx] = t >= dist[idx] - (0.05 + 0.012 / cos[idx])
    col, trust = panos.sample(j, pts[vis])
    return vis, col, cos[vis] / dist[vis] ** 2 * trust


def shade_single(
    pts: np.ndarray,
    normals: np.ndarray,
    panos: PanoSet,
    rays: Raycaster,
    max_dist: float = 12.0,
    max_candidates: int = 12,
    min_cos: float = 0.25,
) -> tuple[np.ndarray, np.ndarray]:
    """Colour one small surface (a cabinet front, a fridge door) from as few panoramas as possible.

    `shade` picks and blends views per point, which is right for a wall but turns a shiny or detailed
    object into a patchwork: every camera sees a different reflection, and the surface is a few cm off the
    box face, so views do not line up. Here the whole surface goes to the single panorama that sees most
    of it (largest sum of weights); what that one cannot see goes to the next best, and so on. A later
    view's colours are scaled per channel so that they match the earlier ones where both see the surface
    (exposure differs between panoramas). Views more than about 75 degrees off the surface normal
    (min_cos) do not count: at that angle a lacquered bar top or a steel door shows the window it
    reflects, not itself; the surface is then left to the fill. Returns (rgb float (n, 3), valid (n,))."""
    n = len(pts)
    rgb = np.zeros((n, 3))
    valid = np.zeros(n, dtype=bool)
    if not n or not len(panos):
        return rgb, valid
    centre = pts.mean(0)
    reach = float(np.linalg.norm(pts - centre, axis=1).max()) + max_dist
    d = np.linalg.norm(panos.positions - centre, axis=1)
    order = [int(j) for j in np.argsort(d)[:max_candidates] if d[j] <= reach]
    if not order:
        return rgb, valid
    cols = np.zeros((n, len(order), 3), dtype=np.float32)
    wts = np.zeros((n, len(order)), dtype=np.float32)
    for k, j in enumerate(order):
        vis, col, w = see(panos, rays, j, pts, normals, max_dist, min_cos)
        cols[vis, k] = col
        wts[vis, k] = w
    left = wts.max(1) > 0
    while left.any():
        k = int(np.argmax(wts[left].sum(0)))
        take = left & (wts[:, k] > 0)
        if not take.any():
            break
        col = cols[take, k].astype(float)
        both = valid & (wts[:, k] > 0)
        if both.sum() >= 50:  # match this view's exposure to what is already there
            gain = (np.median(rgb[both], axis=0) + 1.0) / (np.median(cols[both, k], axis=0) + 1.0)
            col *= np.clip(gain, 0.6, 1.6)
        rgb[take] = col
        valid |= take
        left &= ~take
    return rgb, valid
