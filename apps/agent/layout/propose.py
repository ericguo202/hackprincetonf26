"""propose_poses: up to 5 valid poses for one asset by a placement rule (AGENTS.md section 10).

The model picks the intent (rule + what it relates to); this module computes every coordinate.

  rule          meaning                                  relative_to
  against_wall  back to a free wall, facing into room    ignored
  corner        tucked into a convex corner              ignored
  center        open floor; with relative_to: in front   optional (coffee table in front of the sofa;
                of that piece, facing it                 for on_surface assets: on top of it)
  facing        front pointed at another piece           required (sofa facing the TV)
  beside        next to another piece, backs aligned     required (nightstand beside the bed)

Every candidate passes the same checks as validate_layout against the room and the pieces already
placed (both clearance directions), so a returned pose never adds a conflict. Scores are deterministic.
"""

from __future__ import annotations

import math
from collections.abc import Iterator

from shapely.geometry import Point

from layout.geometry import (
    RoomGeometry,
    WallSegment,
    footprint,
    front_dir,
    right_dir,
    rot_facing,
)
from layout.validate import WINDOW_MAX_H_M, object_conflicts

RULES = ("against_wall", "corner", "center", "facing", "beside")
DEFAULT_RULE = {  # catalog `placement` -> the rule to try first
    "wall": "against_wall",
    "corner": "corner",
    "center": "center",
    "beside": "beside",
    "free": "center",
    "on_surface": "center",
}
WALL_GAP_M = 0.02
BESIDE_GAP_M = 0.05
IN_FRONT_GAP_M = 0.45  # seat edge to coffee table
STEP_M = 0.2
GRID_M = 0.25
AXIS_CLUSTER_DEG = 3.0  # wall angles this close are one direction (scan noise)
AXIS_BONUS = 0.3  # center score bonus x share of wall length along the pose's direction
# Preferred centre-to-centre distance (min, max) for `facing`, by (asset category, target category).
FACING_RANGE = {
    ("sofa", "tv_stand"): (2.2, 3.6),
    ("sectional", "tv_stand"): (2.2, 3.6),
    ("loveseat", "tv_stand"): (2.0, 3.4),
    ("armchair", "tv_stand"): (2.0, 3.4),
    ("tv_stand", "sofa"): (2.2, 3.6),
    ("tv_stand", "sectional"): (2.2, 3.6),
}
DEFAULT_FACING_RANGE = (1.2, 3.0)
TARGET_NEEDED = ("facing", "beside")
MIN_SPREAD_M = 0.4  # returned poses with the same rotation are at least this far apart


def _cand(x: float, z: float, rot: float, score: float, reason: str) -> dict:
    return {"x": x, "z": z, "rot_deg": rot % 360, "score": score, "reason": reason}


def _wall_runs(geo: RoomGeometry, allow_windows: bool) -> list[WallSegment]:
    """Free wall pieces; low pieces may run under windows, so join window pieces back into the wall."""
    runs: list[WallSegment] = []
    for s in geo.walls:
        if s.under_window and not allow_windows:
            continue
        last = runs[-1] if runs else None
        if last and last.wall == s.wall and math.dist(last.b, s.a) < 1e-6:
            runs[-1] = WallSegment(s.wall, last.a, s.b, s.inward, last.under_window or s.under_window)
        else:
            runs.append(WallSegment(s.wall, s.a, s.b, s.inward, s.under_window))
    return runs


def _against_wall(geo: RoomGeometry, asset: dict) -> Iterator[dict]:
    w, d, h = asset["size_m"]["w"], asset["size_m"]["d"], asset["size_m"]["h"]
    for seg in _wall_runs(geo, allow_windows=h <= WINDOW_MAX_H_M):
        length = seg.length
        if w > length:
            continue
        ux, uz = (seg.b[0] - seg.a[0]) / length, (seg.b[1] - seg.a[1]) / length
        ts = {length / 2}
        t = w / 2
        while t <= length - w / 2 + 1e-9:
            ts.add(t)
            t += STEP_M
        for t in ts:
            x = seg.a[0] + ux * t + seg.inward[0] * (d / 2 + WALL_GAP_M)
            z = seg.a[1] + uz * t + seg.inward[1] * (d / 2 + WALL_GAP_M)
            off = abs(t - length / 2)
            score = 1.0 - off / max(length, 1e-6) + 0.1 * min(length, 6.0) / 6.0
            where = "centered" if off < 0.05 else f"{off:.1f} m off center"
            yield _cand(x, z, seg.facing_rot_deg, score, f"Against wall {seg.wall}, {where}")


def _corner(geo: RoomGeometry, asset: dict) -> Iterator[dict]:
    w, d = asset["size_m"]["w"], asset["size_m"]["d"]
    c = geo.poly.centroid
    for (vx, vz), na, nb in geo.corners:
        for back, side in ((na, nb), (nb, na)):
            x = vx + back[0] * (d / 2 + WALL_GAP_M) + side[0] * (w / 2 + WALL_GAP_M)
            z = vz + back[1] * (d / 2 + WALL_GAP_M) + side[1] * (w / 2 + WALL_GAP_M)
            rot = rot_facing(back)
            to_c = (c.x - x, c.y - z)
            n = math.hypot(*to_c) or 1.0
            f = front_dir(rot)
            score = (f[0] * to_c[0] + f[1] * to_c[1]) / n  # prefer facing into the room
            yield _cand(x, z, rot, score, f"In the corner at ({vx:.1f}, {vz:.1f})")


def _room_axes(geo: RoomGeometry) -> list[tuple[float, float]]:
    """The room's wall directions as (angle in [0, 90), share of wall length), main direction first.

    Scanned walls are a degree or so off each other, so angles within AXIS_CLUSTER_DEG are one
    direction (their length-weighted mean). A 90 degree turn is the same direction.
    """
    ring = list(geo.poly.exterior.coords)
    clusters: list[list[float]] = []  # [angle, total length]
    total = 0.0
    edges = sorted(zip(ring, ring[1:], strict=False), key=lambda e: -math.dist(*e))
    for a, b in edges:
        length = math.dist(a, b)
        if length < 1e-6:
            continue
        total += length
        ang = rot_facing((b[0] - a[0], b[1] - a[1])) % 90
        for c in clusters:
            delta = (ang - c[0] + 45) % 90 - 45  # signed difference on the 90 degree circle
            if abs(delta) <= AXIS_CLUSTER_DEG:
                c[0] = (c[0] + delta * length / (c[1] + length)) % 90
                c[1] += length
                break
        else:
            clusters.append([ang, length])
    if not clusters:
        return [(0.0, 1.0)]
    clusters.sort(key=lambda c: -c[1])
    return [(round(c[0], 1), c[1] / total) for c in clusters]


def _grid(geo: RoomGeometry) -> Iterator[tuple[float, float]]:
    minx, minz, maxx, maxz = geo.poly.bounds
    x = minx + GRID_M / 2
    while x < maxx:
        z = minz + GRID_M / 2
        while z < maxz:
            if geo.poly.contains(Point(x, z)):
                yield x, z
            z += GRID_M
        x += GRID_M


def _center(geo: RoomGeometry, asset: dict) -> Iterator[dict]:
    c = geo.poly.centroid
    span = math.sqrt(geo.poly.area) or 1.0
    axes = _room_axes(geo)
    for x, z in _grid(geo):
        dist = math.hypot(x - c.x, z - c.y)
        for base, share in axes:
            # Lined up with the main walls beats a slightly more central spot on a minor axis.
            score = 1.0 - dist / span + AXIS_BONUS * share
            for k in range(4):
                yield _cand(x, z, base + k * 90, score, f"Open floor, {dist:.1f} m from room center")


def _in_front_of(asset: dict, target: dict, target_asset: dict) -> Iterator[dict]:
    d = asset["size_m"]["d"]
    td = target_asset["size_m"]["d"]
    f = front_dir(target["rot_deg"])
    for gap in (0.40, IN_FRONT_GAP_M, 0.50, 0.60):
        off = td / 2 + gap + d / 2
        x, z = target["x"] + f[0] * off, target["z"] + f[1] * off
        yield _cand(
            x,
            z,
            target["rot_deg"] + 180,
            1.0 - abs(gap - IN_FRONT_GAP_M),
            f"{gap:.2f} m in front of {target_asset['name']}",
        )


def _on_top_of(asset: dict, target: dict, target_asset: dict, floor_y: float) -> Iterator[dict]:
    tw, td = target_asset["size_m"]["w"], target_asset["size_m"]["d"]
    w, d = asset["size_m"]["w"], asset["size_m"]["d"]
    if w > tw or d > td:
        return
    r = right_dir(target["rot_deg"])
    y = floor_y + target_asset["size_m"]["h"]
    for side, label in ((0.0, "centered"), (1.0, "right end"), (-1.0, "left end")):
        off = side * max(tw / 2 - w / 2 - 0.05, 0.0)
        c = _cand(
            target["x"] + r[0] * off,
            target["z"] + r[1] * off,
            target["rot_deg"],
            1.0 - abs(side) * 0.2,
            f"On {target_asset['name']}, {label}",
        )
        c["y"] = y
        yield c


def _facing(geo: RoomGeometry, asset: dict, target: dict, target_asset: dict) -> Iterator[dict]:
    lo, hi = FACING_RANGE.get((asset["category"], target_asset["category"]), DEFAULT_FACING_RANGE)
    tf = front_dir(target["rot_deg"])
    tname = target_asset["name"]

    def score(x: float, z: float, rot: float, on_wall: bool) -> tuple[float, float, float]:
        dx, dz = target["x"] - x, target["z"] - z
        dist = math.hypot(dx, dz) or 1e-6
        f = front_dir(rot)
        aim = (f[0] * dx + f[1] * dz) / dist  # 1 = pointing straight at the target
        mutual = -(tf[0] * dx + tf[1] * dz) / dist  # 1 = target faces back
        miss = max(lo - dist, 0.0, dist - hi)
        s = 2.0 * aim + 0.5 * mutual - miss + (0.5 if on_wall else 0.0)
        return s, dist, aim

    if asset.get("placement") == "wall":
        for c in _against_wall(geo, asset):
            s, dist, aim = score(c["x"], c["z"], c["rot_deg"], True)
            if aim > math.cos(math.radians(20)):
                yield _cand(c["x"], c["z"], c["rot_deg"], s, f"Facing {tname}, {dist:.1f} m, back to wall")
    for x, z in _grid(geo):
        dist = math.hypot(target["x"] - x, target["z"] - z)
        if dist < 0.5:
            continue
        rot = rot_facing((target["x"] - x, target["z"] - z))
        s, dist, _ = score(x, z, rot, False)
        yield _cand(x, z, rot, s, f"Facing {tname}, {dist:.1f} m away")


def _beside(asset: dict, target: dict, target_asset: dict) -> Iterator[dict]:
    w, d = asset["size_m"]["w"], asset["size_m"]["d"]
    tw, td = target_asset["size_m"]["w"], target_asset["size_m"]["d"]
    f, r = front_dir(target["rot_deg"]), right_dir(target["rot_deg"])
    back_align = d / 2 - td / 2  # move along the target's front so both backs line up
    for sign, label in ((1, "right"), (-1, "left")):  # as seen facing the target
        off = sign * (tw / 2 + BESIDE_GAP_M + w / 2)
        x = target["x"] + r[0] * off + f[0] * back_align
        z = target["z"] + r[1] * off + f[1] * back_align
        yield _cand(x, z, target["rot_deg"], 1.0, f"Beside {target_asset['name']}, {label} as you face it")


def propose_poses(
    geo: RoomGeometry,
    asset: dict,
    rule: str,
    relative_to: str | None = None,
    placed: list[tuple[dict, dict]] | None = None,
    limit: int = 5,
) -> dict:
    """Up to `limit` conflict-free poses, best first: {"poses": [...], "note": str}.

    placed: [(plan object, its asset)] already in the plan (the piece being moved must be left out).
    Each pose: {pose_id, x, y, z, rot_deg, reason, score}. When none fit, poses is empty and note says why.
    """
    placed = placed or []
    if rule not in RULES:
        raise ValueError(f"rule must be one of {', '.join(RULES)}")
    target = target_asset = None
    if relative_to is not None:
        match = [(o, a) for o, a in placed if o["instance_id"] == relative_to]
        if not match:
            raise ValueError(f"relative_to {relative_to!r} is not in the plan")
        target, target_asset = match[0]
    if rule in TARGET_NEEDED and target is None:
        raise ValueError(f"rule {rule!r} needs relative_to (an instance_id already in the plan)")

    floor_y = float(geo.room.get("floor_y", 0.0))
    on_surface = asset.get("placement") == "on_surface"
    if rule == "against_wall":
        cands = _against_wall(geo, asset)
    elif rule == "corner":
        cands = _corner(geo, asset)
    elif rule == "center" and target is not None:
        cands = (
            _on_top_of(asset, target, target_asset, floor_y)
            if on_surface
            else _in_front_of(asset, target, target_asset)
        )
    elif rule == "center":
        cands = _center(geo, asset)
    elif rule == "facing":
        cands = _facing(geo, asset, target, target_asset)
    else:
        cands = _beside(asset, target, target_asset)

    others = [(o, a) for o, a in placed if not (on_surface and target is not None and o is target)]
    ranked = sorted(cands, key=lambda c: -c["score"])
    chosen: list[dict] = []
    tried = 0
    for c in ranked:
        if any(
            math.dist((c["x"], c["z"]), (k["x"], k["z"])) < MIN_SPREAD_M
            and abs((c["rot_deg"] - k["rot_deg"] + 180) % 360 - 180) < 5
            for k in chosen
        ):
            continue
        tried += 1
        obj = {"instance_id": "o0", "x": c["x"], "z": c["z"], "rot_deg": c["rot_deg"]}
        if on_surface and target is not None:
            fp = footprint(c["x"], c["z"], asset["size_m"], c["rot_deg"])
            tfp = footprint(target["x"], target["z"], target_asset["size_m"], target["rot_deg"])
            if not tfp.buffer(0.01).contains(fp):
                continue
        if object_conflicts(geo, obj, asset, others, check_reverse=True):
            continue
        chosen.append(c)
        if len(chosen) == limit:
            break

    poses = [
        {
            "pose_id": f"p{i + 1}",
            "x": round(c["x"], 3),
            "y": round(c.get("y", floor_y), 3),
            "z": round(c["z"], 3),
            "rot_deg": round(c["rot_deg"], 1),
            "reason": c["reason"],
            "score": round(c["score"], 3),
        }
        for i, c in enumerate(chosen)
    ]
    if poses:
        note = f"{len(poses)} valid pose{'s' if len(poses) > 1 else ''} by {rule}"
    elif tried == 0:
        note = _why_none(geo, asset, rule)
    else:
        note = f"all {tried} {rule} candidates conflict with the room or placed pieces"
    return {"poses": poses, "note": note}


def _why_none(geo: RoomGeometry, asset: dict, rule: str) -> str:
    w = asset["size_m"]["w"]
    if rule == "against_wall":
        longest = max(
            (s.length for s in _wall_runs(geo, asset["size_m"]["h"] <= WINDOW_MAX_H_M)), default=0.0
        )
        return f"no free wall segment fits {w:.2f} m (longest is {longest:.2f} m)"
    return f"no {rule} candidates in this room"
