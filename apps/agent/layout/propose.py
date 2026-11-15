"""propose_poses: up to 5 valid poses for one asset by a placement rule (AGENTS.md section 10).

The model picks the intent (rule + what it relates to); this module computes every coordinate.

  rule          meaning                                  relative_to
  against_wall  back to a free wall, facing into room    ignored
  corner        tucked into a convex corner              ignored
  center        open floor; with relative_to: in front   optional (coffee table in front of the sofa;
                of that piece, facing it                 for on_surface assets: on top of it)
  facing        front pointed at another piece           required (sofa facing the TV; a chair facing its
                                                         table is pulled up to a free side of it)
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
from layout.validate import covers_window, object_conflicts, sits_at

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
SEAT_GAP_M = 0.05  # chair front edge to the table edge it is pulled up to
SEAT_SPACING_M = 0.10  # free space between two chairs on the same side of a table
ONE_SEAT_TABLES = {"desk"}  # one chair, centred on the front side; other tables seat every side
MIN_SPREAD_M = 0.4  # returned poses with the same rotation are at least this far apart
BLOCKED_LIMIT = 3  # blocked poses offered when no pose fits
BLOCKED_SCAN = 60  # room-valid blocked candidates examined, best scores first


def mount_y(asset: dict) -> float:
    """Wall-mounted assets (catalog mount_y_m, contracts v2.5.0: a wall TV) hang this far above the floor."""
    return float(asset.get("mount_y_m") or 0.0)


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
    w, d = asset["size_m"]["w"], asset["size_m"]["d"]
    for seg in _wall_runs(geo, allow_windows=not covers_window(asset)):
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


def _at_table(asset: dict, target: dict, target_asset: dict) -> Iterator[dict]:
    """A chair pulled up to its table: on a free side, square to it, facing it.

    The long sides fill first, alternating front and back so chairs sit across from each other; the ends
    come after. A table whose sides are about equal takes one chair per side. A desk takes one chair,
    centred on its front. Sides against a wall drop out in the conflict check, like any candidate.
    """
    w, d = asset["size_m"]["w"], asset["size_m"]["d"]
    tw, td = target_asset["size_m"]["w"], target_asset["size_m"]["d"]
    f, r = front_dir(target["rot_deg"]), right_dir(target["rot_deg"])
    one_seat = target_asset["category"] in ONE_SEAT_TABLES
    # (label, outward normal, direction along the side, side length, table extent across the side)
    sides = [("front", f, r, tw, td)]
    if not one_seat:
        sides += [
            ("back", (-f[0], -f[1]), r, tw, td),
            ("right end", r, f, td, tw),
            ("left end", (-r[0], -r[1]), f, td, tw),
        ]
    longest = max(s[3] for s in sides)
    slots: list[tuple[int, int, int, dict]] = []
    for order, (label, n, along, length, across) in enumerate(sides):
        if length < 0.8 * w:
            continue  # the chair is wider than this side
        count = 1 if one_seat else max(int(length // (w + SEAT_SPACING_M)), 1)
        short = 0 if length >= 0.9 * longest else 1
        for i in range(count):
            off = (i + 0.5) * length / count - length / 2
            out = across / 2 + SEAT_GAP_M + d / 2
            x = target["x"] + n[0] * out + along[0] * off
            z = target["z"] + n[1] * out + along[1] * off
            c = _cand(x, z, rot_facing((-n[0], -n[1])), 0.0, f"At {target_asset['name']}, {label} side")
            slots.append((short, i, order, c))
    for rank, (_, _, _, c) in enumerate(sorted(slots, key=lambda s: s[:3])):
        c["score"] = 3.0 - 0.01 * rank
        yield c


def _facing(geo: RoomGeometry, asset: dict, target: dict, target_asset: dict) -> Iterator[dict]:
    if sits_at(asset, target_asset):
        yield from _at_table(asset, target, target_asset)
        return
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
    Each pose: {pose_id, x, y, z, rot_deg, reason, score}. When none fit, poses is empty and note says why;
    if some fit the room but not the placed pieces, "blocked" lists up to 3 of them with blocked_by ids.
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
            "y": round(c.get("y", floor_y + mount_y(asset)), 3),
            "z": round(c["z"], 3),
            "rot_deg": round(c["rot_deg"], 1),
            "reason": c["reason"],
            "score": round(c["score"], 3),
        }
        for i, c in enumerate(chosen)
    ]
    out = {"poses": poses}
    if poses:
        note = f"{len(poses)} valid pose{'s' if len(poses) > 1 else ''} by {rule}"
    elif tried == 0:
        note = _why_none(geo, asset, rule)
    else:
        note = f"all {tried} {rule} candidates conflict with the room or placed pieces"
        blocked = _blocked(geo, asset, ranked, others, target, floor_y)
        if blocked:
            out["blocked"] = blocked
            note += "; blocked poses fit once the pieces in blocked_by move out"
    out["note"] = note
    return out


def _blocked(
    geo: RoomGeometry,
    asset: dict,
    ranked: list[dict],
    others: list[tuple[dict, dict]],
    target: dict | None,
    floor_y: float,
    limit: int = BLOCKED_LIMIT,
) -> list[dict]:
    """Poses that fit the room but conflict with placed pieces, fewest blockers first, then by score.

    Each: {pose_id: b1.., x, y, z, rot_deg, reason, score, blocked_by: [instance_id]}. A candidate blocked
    by the relative_to piece is never offered (moving that piece would break the relation).
    """
    found: list[tuple[int, int, dict, list[str]]] = []
    for i, c in enumerate(ranked):
        if len(found) >= BLOCKED_SCAN:
            break
        obj = {"instance_id": "o0", "x": c["x"], "z": c["z"], "rot_deg": c["rot_deg"]}
        if object_conflicts(geo, obj, asset, [], check_reverse=True):
            continue  # does not fit the room itself
        by = [
            o["instance_id"]
            for o, a in others
            if object_conflicts(geo, obj, asset, [(o, a)], check_reverse=True)
        ]
        if not by or (target is not None and target["instance_id"] in by):
            continue
        found.append((len(by), i, c, by))
    chosen: list[tuple[dict, list[str]]] = []
    for _, _, c, by in sorted(found, key=lambda f: (f[0], f[1])):
        if any(
            math.dist((c["x"], c["z"]), (k["x"], k["z"])) < MIN_SPREAD_M and set(by) == set(kb)
            for k, kb in chosen
        ):
            continue
        chosen.append((c, by))
        if len(chosen) == limit:
            break
    return [
        {
            "pose_id": f"b{i + 1}",
            "x": round(c["x"], 3),
            "y": round(c.get("y", floor_y + mount_y(asset)), 3),
            "z": round(c["z"], 3),
            "rot_deg": round(c["rot_deg"], 1),
            "reason": c["reason"],
            "score": round(c["score"], 3),
            "blocked_by": by,
        }
        for i, (c, by) in enumerate(chosen)
    ]


def _why_none(geo: RoomGeometry, asset: dict, rule: str) -> str:
    w = asset["size_m"]["w"]
    if rule == "against_wall":
        longest = max((s.length for s in _wall_runs(geo, not covers_window(asset))), default=0.0)
        return f"no free wall segment fits {w:.2f} m (longest is {longest:.2f} m)"
    return f"no {rule} candidates in this room"
