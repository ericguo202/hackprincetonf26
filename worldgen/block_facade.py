"""Facade openings for the procedural block: a drawn texture atlas (windows, front door, garage door;
no external assets) and their placement on each building's walls. The garage door goes where the
ortho shows a driveway meeting the street-facing wall; the front door on the street side next to it.
"""

from __future__ import annotations

import math

import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from shapely.geometry import Point

from worldgen.block_glb import MeshBuf

ATLAS = 1024
# atlas regions in pixels (x0, y0, x1, y1) and the real size they represent (w, h) in meters
REG = {
    "window": ((0, 0, 256, 384), (0.95, 1.45)),
    "window2": ((256, 0, 640, 384), (1.75, 1.45)),
    "door": ((640, 0, 896, 512), (1.0, 2.15)),
    "garage": ((0, 512, 1024, 992), (4.9, 2.2)),
    "garage1": ((640, 0, 896, 512), (1.0, 2.15)),  # unused placeholder
}


def _window(d: ImageDraw.ImageDraw, x0, y0, x1, y1, panes_x=2) -> None:
    w, h = x1 - x0, y1 - y0
    f = int(w * 0.07) if panes_x == 2 else int(h * 0.07)
    d.rectangle([x0, y0, x1, y1], fill=(236, 234, 228))  # frame
    gx0, gy0, gx1, gy1 = x0 + f, y0 + f, x1 - f, y1 - f
    # glass: sky reflection at the top fading to a dark interior, slight blue
    for yy in range(gy0, gy1):
        t = (yy - gy0) / max(gy1 - gy0, 1)
        c = (int(150 - 110 * t), int(165 - 115 * t), int(180 - 110 * t))
        d.line([(gx0, yy), (gx1, yy)], fill=c)
    # curtain/blind hints
    d.rectangle([gx0, gy0, gx1, gy0 + int((gy1 - gy0) * 0.12)], fill=(205, 200, 188))
    # meeting rail and muntins (6 over 6)
    mid = (gy0 + gy1) // 2
    d.rectangle([gx0, mid - f // 3, gx1, mid + f // 3], fill=(236, 234, 228))
    cols = 3 * panes_x // 2 if panes_x > 2 else 3
    for k in range(1, cols):
        xx = gx0 + (gx1 - gx0) * k // cols
        d.line([(xx, gy0), (xx, gy1)], fill=(232, 230, 224), width=max(2, f // 4))
    for yy in (gy0 + (mid - gy0) // 2, mid + (gy1 - mid) // 2):
        d.line([(gx0, yy), (gx1, yy)], fill=(232, 230, 224), width=max(2, f // 4))
    if panes_x > 2:
        xm = (x0 + x1) // 2
        d.rectangle([xm - f // 2, y0, xm + f // 2, y1], fill=(236, 234, 228))


def facade_atlas() -> Image.Image:
    img = Image.new("RGB", (ATLAS, ATLAS), (200, 196, 188))
    d = ImageDraw.Draw(img)
    (x0, y0, x1, y1), _ = REG["window"]
    _window(d, x0 + 4, y0 + 4, x1 - 4, y1 - 4)
    (x0, y0, x1, y1), _ = REG["window2"]
    _window(d, x0 + 4, y0 + 4, x1 - 4, y1 - 4, panes_x=4)
    # front door: white casing, dark painted six-panel door, brass knob
    (x0, y0, x1, y1), _ = REG["door"]
    d.rectangle([x0, y0, x1, y1], fill=(236, 234, 228))
    dx0, dy0, dx1, dy1 = x0 + 18, y0 + 18, x1 - 18, y1
    d.rectangle([dx0, dy0, dx1, dy1], fill=(92, 44, 36))
    pw = (dx1 - dx0 - 36) // 2
    for r, (py0, py1) in enumerate([(0.06, 0.30), (0.36, 0.62), (0.68, 0.95)]):
        for c in range(2):
            px0 = dx0 + 12 + c * (pw + 12)
            d.rectangle(
                [px0, dy0 + int(py0 * (dy1 - dy0)), px0 + pw, dy0 + int(py1 * (dy1 - dy0))],
                outline=(62, 28, 22),
                width=4,
                fill=(100, 50, 40) if r else (104, 52, 42),
            )
    d.ellipse(
        [dx1 - 34, dy0 + (dy1 - dy0) // 2 - 8, dx1 - 18, dy0 + (dy1 - dy0) // 2 + 8], fill=(190, 160, 90)
    )
    # garage door: white sectional, 4 sections with raised short panels, light grime at the bottom
    (x0, y0, x1, y1), _ = REG["garage"]
    d.rectangle([x0, y0, x1, y1], fill=(226, 224, 218))
    gx0, gy0, gx1, gy1 = x0 + 14, y0 + 14, x1 - 14, y1
    d.rectangle([gx0, gy0, gx1, gy1], fill=(238, 237, 232))
    for s in range(4):
        sy0 = gy0 + (gy1 - gy0) * s // 4
        sy1 = gy0 + (gy1 - gy0) * (s + 1) // 4
        d.line([(gx0, sy0), (gx1, sy0)], fill=(170, 168, 162), width=3)
        for c in range(8):
            px0 = gx0 + 10 + (gx1 - gx0 - 20) * c // 8
            px1 = gx0 + 10 + (gx1 - gx0 - 20) * (c + 1) // 8 - 10
            d.rectangle([px0, sy0 + 14, px1, sy1 - 14], outline=(200, 198, 192), width=3)
    for yy in range(gy1 - 60, gy1):
        t = (yy - (gy1 - 60)) / 60
        d.line([(gx0, yy), (gx1, yy)], fill=(int(238 - 50 * t), int(237 - 52 * t), int(232 - 56 * t)))
    return img.filter(ImageFilter.SMOOTH)


def _uv(key: str) -> np.ndarray:
    (x0, y0, x1, y1), _ = REG[key]
    return np.array([[x0, y1], [x1, y1], [x1, y0], [x0, y0]], float) / ATLAS


def _quad_on_wall(buf: MeshBuf, a, t, nrm, s0, w, y0, h, key) -> None:
    off = nrm * 0.03
    p0 = np.array([a[0] + t[0] * s0, 0, a[1] + t[1] * s0])
    p1 = np.array([a[0] + t[0] * (s0 + w), 0, a[1] + t[1] * (s0 + w)])
    P = [p0 + [0, y0, 0] + off, p1 + [0, y0, 0] + off, p1 + [0, y0 + h, 0] + off, p0 + [0, y0 + h, 0] + off]
    buf.quad(*P, uv=_uv(key), facing=nrm)


def driveway_score(ortho, a, t, nrm2, L) -> np.ndarray:
    """Per 0.25 m along the wall edge: fraction of pale grey (concrete) pixels 1.5..6 m outward."""
    s = np.arange(0, L, 0.25)
    out = np.arange(1.5, 6.0, 0.5)
    S, Off = np.meshgrid(s, out, indexing="ij")
    x = a[0] + t[0] * S + nrm2[0] * Off
    z = a[1] + t[1] * S + nrm2[1] * Off
    c = ortho.sample(x.ravel(), z.ravel()).reshape(*S.shape, 3)
    mx, mn = c.max(-1), c.min(-1)
    sat = (mx - mn) / np.maximum(mx, 1)
    pale = (c.mean(-1) > 125) & (sat < 0.2)
    return pale.mean(1)


def place_openings(
    buf: MeshBuf,
    wall_poly,
    y_ground: float,
    wall_top: float,
    road_lines,
    ortho,
    two_story: bool,
    rng,
    door_at=None,
    garage_at=None,
) -> dict:
    """Windows, front door and garage door on the outer walls of one building.
    Returns {"garage": {"p", "n", "w"}, "door": {"p", "n"}, "windows": n}: centre (x, z) on the wall,
    outward normal (x, z), width. `door_at` (x, z) puts the front door on the wall nearest that point."""
    ring = np.asarray(wall_poly.exterior.coords)
    # orientation of the ring in x-z: positive shoelace -> outward = (t_z, -t_x)
    x, z = ring[:, 0], ring[:, 1]
    ccw = float(np.sum(x[:-1] * z[1:] - x[1:] * z[:-1])) > 0
    c = wall_poly.centroid
    near = min(road_lines, key=lambda ln: ln.distance(c)) if road_lines else None
    to_road = None
    if near is not None:
        q = near.interpolate(near.project(c))
        v = np.array([q.x - c.x, q.y - c.y])
        to_road = v / max(np.linalg.norm(v), 1e-6)
    edges = []
    for i in range(len(ring) - 1):
        a, b = ring[i], ring[i + 1]
        L = float(np.hypot(*(b - a)))
        if L < 0.8:
            continue
        t = (b - a) / L
        n2 = np.array([t[1], -t[0]]) if ccw else np.array([-t[1], t[0]])
        front = float(n2 @ to_road) if to_road is not None else 0.0
        edges.append({"a": a, "t": t, "n2": n2, "L": L, "front": front, "busy": []})
    wall_h = wall_top - y_ground
    if wall_h < 2.0:
        return {}
    res: dict = {}
    # garage: where a traced driveway meets the house (garage_at = ((x, z), width)), else the best
    # driveway match on a street-facing edge
    best = None
    if garage_at is not None and edges:
        q, gw = np.asarray(garage_at[0], float), float(garage_at[1])

        def _dg(e):
            s_ = float(np.clip((q - e["a"]) @ e["t"], 0, e["L"]))
            return float(np.linalg.norm(e["a"] + e["t"] * s_ - q))

        e = min(edges, key=_dg)
        gw = min(gw, e["L"] - 0.3)
        if gw >= 2.2 and _dg(e) < 2.5:
            s0 = float(np.clip((q - e["a"]) @ e["t"] - gw / 2, 0.15, e["L"] - gw - 0.15))
            _quad_on_wall(
                buf,
                e["a"],
                e["t"],
                np.r_[e["n2"][0], 0, e["n2"][1]],
                s0,
                gw,
                y_ground + 0.02,
                min(2.2, wall_h - 0.25),
                "garage",
            )
            e["busy"].append((s0 - 0.3, s0 + gw + 0.3))
            res["garage"] = {"p": e["a"] + e["t"] * (s0 + gw / 2), "n": e["n2"], "w": gw}
            edges_for_auto = []
        else:
            edges_for_auto = edges
    else:
        edges_for_auto = edges
    for e in edges_for_auto:
        if e["front"] < 0.6 or e["L"] < 5.3:
            continue
        sc = driveway_score(ortho, e["a"], e["t"], e["n2"], e["L"])
        k = int(4.9 / 0.25)
        if len(sc) < k:
            continue
        win = np.convolve(sc, np.ones(k) / k, mode="valid")
        j = int(np.argmax(win))
        if best is None or win[j] > best[0]:
            best = (float(win[j]), e, j * 0.25)
    if best and best[0] > 0.45:
        _, e, s0 = best
        s0 = float(np.clip(s0, 0.15, e["L"] - 4.9 - 0.15))
        _quad_on_wall(
            buf,
            e["a"],
            e["t"],
            np.r_[e["n2"][0], 0, e["n2"][1]],
            s0,
            4.9,
            y_ground + 0.02,
            min(2.2, wall_h - 0.25),
            "garage",
        )
        e["busy"].append((s0 - 0.3, s0 + 5.2))
        res["garage"] = {"p": e["a"] + e["t"] * (s0 + 2.45), "n": e["n2"], "w": 4.9}
    # front door: longest street-facing edge with room left (or the wall nearest door_at)
    fronts = sorted([e for e in edges if e["front"] > 0.5], key=lambda e: -e["L"])
    if door_at is not None and edges:
        q = np.asarray(door_at, float)

        def _d(e):
            s_ = float(np.clip((q - e["a"]) @ e["t"], 0, e["L"]))
            return float(np.linalg.norm(e["a"] + e["t"] * s_ - q))

        e = min(edges, key=_d)
        s0 = float(np.clip((q - e["a"]) @ e["t"] - 0.5, 0.1, max(e["L"] - 1.1, 0.1)))
        _quad_on_wall(
            buf,
            e["a"],
            e["t"],
            np.r_[e["n2"][0], 0, e["n2"][1]],
            s0,
            1.0,
            y_ground + 0.02,
            min(2.15, wall_h - 0.15),
            "door",
        )
        e["busy"].append((s0 - 0.5, s0 + 1.5))
        res["door"] = {"p": e["a"] + e["t"] * (s0 + 0.5), "n": e["n2"]}
        fronts = []
    for e in fronts:
        free = _free_spans(e)
        free = [f for f in free if f[1] - f[0] >= 1.6]
        if free:
            f0, f1 = max(free, key=lambda f: f[1] - f[0])
            s0 = (f0 + f1) / 2 - 0.5
            _quad_on_wall(
                buf,
                e["a"],
                e["t"],
                np.r_[e["n2"][0], 0, e["n2"][1]],
                s0,
                1.0,
                y_ground + 0.02,
                min(2.15, wall_h - 0.15),
                "door",
            )
            e["busy"].append((s0 - 0.5, s0 + 1.5))
            res["door"] = {"p": e["a"] + e["t"] * (s0 + 0.5), "n": e["n2"]}
            break
    # windows
    nwin = 0
    for e in edges:
        if e["L"] < 1.6:
            continue
        n3 = np.r_[e["n2"][0], 0, e["n2"][1]]
        rows = [0.95] + ([3.85] if two_story and wall_h > 5.0 else [])
        count = max(1, int(e["L"] / 3.1))
        wide = e["front"] > 0.5 and e["L"] > 4.5
        for r in rows:
            for k in range(count):
                key = "window2" if (wide and k % 2 == 0) else "window"
                w = 1.75 if key == "window2" else 0.95
                s0 = (k + 0.5) * e["L"] / count - w / 2
                if s0 < 0.3 or s0 + w > e["L"] - 0.3:
                    continue
                if r < 2 and any(s0 < b1 and s0 + w > b0 for b0, b1 in e["busy"]):
                    continue
                wh = min(1.45, wall_h - 0.2 - r)
                if wh < 0.9:
                    continue
                _quad_on_wall(buf, e["a"], e["t"], n3, s0, w, y_ground + r, wh, key)
                nwin += 1
    res["windows"] = nwin
    return res


def _free_spans(e) -> list[tuple[float, float]]:
    spans = [(0.4, e["L"] - 0.4)]
    for b0, b1 in e["busy"]:
        nxt = []
        for f0, f1 in spans:
            if b1 <= f0 or b0 >= f1:
                nxt.append((f0, f1))
            else:
                if b0 > f0:
                    nxt.append((f0, b0))
                if b1 < f1:
                    nxt.append((b1, f1))
        spans = nxt
    return spans


__all__ = ["Point", "facade_atlas", "math", "place_openings"]


def fence_texture(size: int = 512, seed: int = 5) -> Image.Image:
    """Weathered cedar privacy fence (1.8 m x 1.8 m tile): vertical pickets with dark gaps and grain."""
    rng = np.random.default_rng(seed)
    a = np.zeros((size, size, 3), np.float32)
    bw = size / 13.0
    for i in range(13):
        x0, x1 = int(i * bw), int((i + 1) * bw)
        base = np.array([150, 128, 104], np.float32) * rng.uniform(0.82, 1.08)
        grain = rng.normal(0, 1, (size, 1)).repeat(x1 - x0, 1)
        grain = np.cumsum(grain, 0) * 0.15
        cols = base[None, None, :] + (grain[..., None] + rng.normal(0, 6, (size, x1 - x0, 1)))
        a[:, x0:x1] = cols
        a[:, max(x0, x1 - 3) : x1] *= 0.45  # gap shadow
    # weathering: greyer and darker toward the bottom, rail shadows
    y = np.linspace(0, 1, size)[:, None, None]
    a = a * (1.0 - 0.25 * y**3)
    for ry in (0.12, 0.55, 0.9):
        r = int(ry * size)
        a[r : r + 5] *= 0.8
    grey = a.mean(-1, keepdims=True)
    a = 0.75 * a + 0.25 * grey
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))
