"""Room geometry for the layout planner (AGENTS.md section 10, Lane B). Pure functions, no I/O.

Frame (AGENTS.md section 9): house-local, Y up. Room polygons and openings are [x, z] on the floor.
An object at rot_deg = 0 faces +Z; rotation is counterclockwise seen from above, so its front
direction is (sin θ, cos θ) and its right side (local +X) is (cos θ, -sin θ). This matches
apps/web/src/world/geometry.ts rectCorners, so a footprint here is exactly what the client draws.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.polygon import orient

Vec = tuple[float, float]

ON_EDGE_M = 0.05  # an opening endpoint this close to a wall line lies on that wall
DOOR_MARGIN_M = 0.10  # keep furniture this far from the door jambs along the wall
DOOR_SWING_MIN_M = 0.80  # keep-clear depth inside the room in front of a door (its swing)
DOOR_SWING_MAX_M = 1.00  # ...and at most this deep, however wide the opening
MIN_WALL_M = 0.30  # shorter free wall pieces are useless for placement


def front_dir(rot_deg: float) -> Vec:
    t = math.radians(rot_deg)
    return (math.sin(t), math.cos(t))


def right_dir(rot_deg: float) -> Vec:
    """Local +X: the object's right as seen by someone standing in front of it, looking at it."""
    t = math.radians(rot_deg)
    return (math.cos(t), -math.sin(t))


def rot_facing(direction: Vec) -> float:
    """rot_deg whose front points along `direction`, in [0, 360)."""
    return math.degrees(math.atan2(direction[0], direction[1])) % 360


def box(
    x: float, z: float, w: float, d: float, rot_deg: float, off_front: float = 0.0, off_right: float = 0.0
) -> Polygon:
    """Rotated w x d rectangle centred at (x, z) shifted by off_front/off_right in its own frame."""
    fx, fz = front_dir(rot_deg)
    rx, rz = right_dir(rot_deg)
    cx = x + fx * off_front + rx * off_right
    cz = z + fz * off_front + rz * off_right
    hw, hd = w / 2, d / 2
    return Polygon(
        [
            (cx + rx * sx * hw + fx * sz * hd, cz + rz * sx * hw + fz * sz * hd)
            for sx, sz in ((-1, -1), (1, -1), (1, 1), (-1, 1))
        ]
    )


def footprint(x: float, z: float, size: dict, rot_deg: float) -> Polygon:
    return box(x, z, size["w"], size["d"], rot_deg)


def clearance_zones(
    x: float, z: float, size: dict, rot_deg: float, clearance: dict | None
) -> dict[str, Polygon]:
    """Keep-clear rectangles on each side that has a clearance > 0 (front, back, left, right)."""
    w, d = size["w"], size["d"]
    zones: dict[str, Polygon] = {}
    for side, c in (clearance or {}).items():
        if not c or c <= 0:
            continue
        if side == "front":
            zones[side] = box(x, z, w, c, rot_deg, off_front=d / 2 + c / 2)
        elif side == "back":
            zones[side] = box(x, z, w, c, rot_deg, off_front=-(d / 2 + c / 2))
        elif side == "right":
            zones[side] = box(x, z, c, d, rot_deg, off_right=w / 2 + c / 2)
        elif side == "left":
            zones[side] = box(x, z, c, d, rot_deg, off_right=-(w / 2 + c / 2))
    return zones


@dataclass
class OpeningInfo:
    type: str  # door | window
    a: Vec
    b: Vec
    to_room: str | None
    wall: int | None  # index of the polygon edge it sits on, None if it is on no edge
    inward: Vec  # unit normal pointing into the room
    width_m: float
    keep_clear: Polygon  # doors: swing zone inside the room; windows: thin strip along the wall


@dataclass
class WallSegment:
    wall: int
    a: Vec
    b: Vec
    inward: Vec
    under_window: bool

    @property
    def length(self) -> float:
        return math.dist(self.a, self.b)

    @property
    def facing_rot_deg(self) -> float:
        """rot_deg of an object standing with its back to this wall."""
        return rot_facing(self.inward)


@dataclass
class RoomGeometry:
    room: dict
    poly: Polygon
    openings: list[OpeningInfo]
    walls: list[WallSegment]  # free pieces of wall: openings cut out, windows kept but flagged
    corners: list[tuple[Vec, Vec, Vec]] = field(default_factory=list)  # (vertex, inward dir A, inward dir B)

    @property
    def id(self) -> str:
        return self.room["id"]

    def summary(self) -> dict:
        """What get_room_geometry returns to the model: compact, every number computed here."""
        minx, minz, maxx, maxz = self.poly.bounds
        return {
            "room_id": self.id,
            "label": self.room["label"],
            "area_m2": round(self.poly.area, 2),
            "height_m": self.room["height_m"],
            "floor_y": self.room["floor_y"],
            "bbox": {"min": [round(minx, 2), round(minz, 2)], "max": [round(maxx, 2), round(maxz, 2)]},
            "polygon": [[round(x, 3), round(z, 3)] for x, z in self.poly.exterior.coords[:-1]],
            "openings": [
                {
                    "type": o.type,
                    "a": _r(o.a),
                    "b": _r(o.b),
                    "width_m": round(o.width_m, 2),
                    "to_room": o.to_room,
                    "wall": o.wall,
                }
                for o in self.openings
            ],
            "free_walls": [
                {
                    "wall": s.wall,
                    "a": _r(s.a),
                    "b": _r(s.b),
                    "length_m": round(s.length, 2),
                    "facing_rot_deg": round(s.facing_rot_deg, 1),
                    "under_window": s.under_window,
                }
                for s in sorted(self.walls, key=lambda s: -s.length)
            ],
        }


def _r(p: Vec) -> list[float]:
    return [round(p[0], 3), round(p[1], 3)]


def _unit(v: Vec) -> Vec:
    n = math.hypot(*v)
    return (v[0] / n, v[1] / n)


def _inward_normal(poly: Polygon, a: Vec, b: Vec) -> Vec:
    """Unit normal of segment a-b pointing into the polygon (robust to winding and concave rooms)."""
    tx, tz = _unit((b[0] - a[0], b[1] - a[1]))
    n1, n2 = (-tz, tx), (tz, -tx)
    mx, mz = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
    probe = 0.05
    if poly.contains(Point(mx + n1[0] * probe, mz + n1[1] * probe)):
        return n1
    if poly.contains(Point(mx + n2[0] * probe, mz + n2[1] * probe)):
        return n2
    # Opening slightly off the wall line: pick the side nearer the centroid.
    c = poly.centroid
    return n1 if (c.x - mx) * n1[0] + (c.y - mz) * n1[1] > 0 else n2


def _param_on_edge(p: Vec, a: Vec, b: Vec) -> float | None:
    """Position t (metres from a) of p projected on edge a-b, or None if p is not on that edge."""
    ex, ez = b[0] - a[0], b[1] - a[1]
    length = math.hypot(ex, ez)
    t = ((p[0] - a[0]) * ex + (p[1] - a[1]) * ez) / length
    if t < -ON_EDGE_M or t > length + ON_EDGE_M:
        return None
    if LineString([a, b]).distance(Point(p)) > ON_EDGE_M:
        return None
    return min(max(t, 0.0), length)


def room_geometry(room: dict) -> RoomGeometry:
    pts = [tuple(map(float, p)) for p in room["polygon"]]
    poly = orient(Polygon(pts), sign=1.0)
    if not poly.is_valid:
        poly = poly.buffer(0)
    ring = list(poly.exterior.coords)[:-1]
    edges = [(ring[i], ring[(i + 1) % len(ring)]) for i in range(len(ring))]

    openings: list[OpeningInfo] = []
    cuts: dict[int, list[tuple[float, float, bool]]] = {i: [] for i in range(len(edges))}  # (t0, t1, window)
    for o in room.get("openings", []):
        a, b = tuple(o["a"]), tuple(o["b"])
        wall = None
        for i, (ea, eb) in enumerate(edges):
            ta, tb = _param_on_edge(a, ea, eb), _param_on_edge(b, ea, eb)
            if ta is not None and tb is not None:
                wall = i
                lo, hi = sorted((ta, tb))
                margin = DOOR_MARGIN_M if o["type"] == "door" else 0.0
                cuts[i].append((lo - margin, hi + margin, o["type"] == "window"))
                break
        inward = _inward_normal(poly, a, b)
        width = math.dist(a, b)
        seg = LineString([a, b])
        if o["type"] == "door":
            # A door leaf swings as deep as it is wide; a wide opening (double door, an open passage to
            # the next room) keeps a walkway clear, not a zone as deep as the opening is wide.
            depth = min(max(width, DOOR_SWING_MIN_M), DOOR_SWING_MAX_M)
            far = [(p[0] + inward[0] * depth, p[1] + inward[1] * depth) for p in (b, a)]
            keep = Polygon([a, b, *far]).buffer(DOOR_MARGIN_M, join_style=2).intersection(poly)
        else:
            keep = seg.buffer(0.15, cap_style=2)
        openings.append(OpeningInfo(o["type"], a, b, o.get("to_room"), wall, inward, width, keep))

    walls: list[WallSegment] = []
    for i, (ea, eb) in enumerate(edges):
        length = math.dist(ea, eb)
        if length < 1e-6:
            continue
        inward = _inward_normal(poly, ea, eb)
        ux, uz = (eb[0] - ea[0]) / length, (eb[1] - ea[1]) / length
        # Sweep the edge, splitting at every opening; door spans are removed, window spans flagged.
        marks = {0.0, length}
        for lo, hi, _ in cuts[i]:
            marks.update((min(max(lo, 0.0), length), min(max(hi, 0.0), length)))
        ts = sorted(marks)
        for t0, t1 in zip(ts, ts[1:], strict=False):
            if t1 - t0 < 1e-6:
                continue
            mid = (t0 + t1) / 2
            covering = [w for lo, hi, w in cuts[i] if lo <= mid <= hi]
            if any(not w for w in covering):  # inside a door span
                continue
            seg = WallSegment(
                i,
                (ea[0] + ux * t0, ea[1] + uz * t0),
                (ea[0] + ux * t1, ea[1] + uz * t1),
                inward,
                under_window=bool(covering),
            )
            if seg.length >= MIN_WALL_M or seg.under_window:
                walls.append(seg)
    walls = _merge(walls)

    corners = []
    n = len(edges)
    for i in range(n):
        prev_a, v = edges[(i - 1) % n]
        _, nxt = edges[i]
        cross = (v[0] - prev_a[0]) * (nxt[1] - v[1]) - (v[1] - prev_a[1]) * (nxt[0] - v[0])
        if cross > 1e-9:  # convex vertex on a counterclockwise ring
            corners.append((v, _inward_normal(poly, prev_a, v), _inward_normal(poly, v, nxt)))
    return RoomGeometry(room, poly, openings, walls, corners)


def _merge(walls: list[WallSegment]) -> list[WallSegment]:
    """Join touching pieces of the same wall with the same window flag."""
    out: list[WallSegment] = []
    for s in walls:
        last = out[-1] if out else None
        if (
            last
            and last.wall == s.wall
            and last.under_window == s.under_window
            and math.dist(last.b, s.a) < 1e-6
        ):
            last.b = s.b
        else:
            out.append(s)
    return [s for s in out if s.length >= MIN_WALL_M]
