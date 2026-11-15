"""Step 4-5: Matterport model -> houses/<listing_id>/{interior.glb, collision.glb, rooms.json, graph.json}.

uv run python -m worldgen.build_house --block hou_navidad_01 --listing hou_237901269 --version N  (not live)

Inputs: the model's room vectors (`boundary.edges`, Property Intelligence) and its defurnished OBJ, both read
through worldgen/sources/matterport.py and cached under out/cache and out/raw. Hand fixes for a model live in
worldgen/overrides/<model_id>.json. Every area is computed from a polygon; nothing is typed in by hand.

House-local frame: Y up, meters, X = Matterport x, Z = -Matterport y, origin = the Matterport model origin
(floor level). build_block places the house on the block (Listing.world)."""

from __future__ import annotations

import argparse
import json
import math
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import shapely
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import polygonize, split, unary_union

from worldgen import aerial as ae
from worldgen import exterior as ex
from worldgen import house_mesh as hm
from worldgen import rebake as rb
from worldgen import splat
from worldgen.pack import load_block, pack_dir, update_manifest
from worldgen.panos import PanoSet
from worldgen.sources import matterport as mp

HERE = Path(__file__).resolve().parent
OVERRIDES = HERE / "overrides"

# Matterport classification label -> Room.label. Outdoor labels are dropped (the block covers outside).
LABELS = {
    "living room": "living_room",
    "family room": "living_room",
    "great room": "living_room",
    "den": "living_room",
    "bedroom": "bedroom",
    "primary bedroom": "primary_bedroom",
    "master bedroom": "primary_bedroom",
    "kitchen": "kitchen",
    "dining room": "dining_room",
    "bathroom": "bathroom",
    "half bath": "bathroom",
    "half bathroom": "bathroom",
    "hallway": "hallway",
    "entryway": "hallway",
    "foyer": "hallway",
    "stairs": "staircase",
    "staircase": "staircase",
    "office": "office",
    "closet": "closet",
    "walk-in closet": "closet",
    "pantry": "closet",
    "garage": "garage",
    "laundry": "utility",
    "laundry room": "utility",
    "utility room": "utility",
}
OUTDOOR = {"patio", "yard", "balcony", "exterior", "outdoor", "porch", "deck", "pool", "driveway", "terrace"}
VOID = "void"

DOOR_Z = (0.3, 1.8)  # wall band (m above floor) searched for door gaps
BIN = 0.05


@dataclass
class Edge:
    id: str
    type: str  # wall | invisible
    a: tuple[float, float]
    b: tuple[float, float]
    thickness: float
    openings: list[dict]

    @property
    def length(self) -> float:
        return math.dist(self.a, self.b)

    def line(self) -> LineString:
        return LineString([self.a, self.b])


@dataclass
class RoomPart:
    mp_id: str
    raw_label: str
    label: str
    confidence: float
    floor: int
    height: float
    poly: Polygon  # plan coords
    edges: list[Edge]
    keep: bool = True
    id: str = ""
    floor_z: float = 0.0
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- rooms


def chain_polygon(edges: list[Edge]) -> Polygon | None:
    """Closed room outline from its boundary edges (dangling edges, like a half wall, are ignored)."""
    lines = [e.line() for e in edges if e.length > 1e-6]
    polys = list(polygonize(unary_union(lines))) if lines else []
    if not polys:
        return None
    merged = unary_union(polys)
    if merged.geom_type == "MultiPolygon":
        merged = max(merged.geoms, key=lambda p: p.area)
    return Polygon(merged.exterior)


def map_label(raw: str) -> str | None:
    """Room.label for a Matterport label; None for outdoor and void spaces."""
    key = raw.strip().lower()
    if key in OUTDOOR or not key or key == VOID:
        return None
    return LABELS.get(key, "other")


def read_rooms(model: dict) -> list[RoomPart]:
    parts: list[RoomPart] = []
    for floor_idx, floor in enumerate(model.get("floors") or []):
        for r in floor.get("rooms") or []:
            cls = (r.get("classifications") or [{}])[0]
            raw = cls.get("label") or r.get("label") or ""
            edges = [
                Edge(
                    id=e["id"],
                    type=e.get("type") or "wall",
                    a=(e["vertices"][0]["position"]["x"], e["vertices"][0]["position"]["y"]),
                    b=(e["vertices"][1]["position"]["x"], e["vertices"][1]["position"]["y"]),
                    thickness=float(e.get("thickness") or 0.1),
                    openings=e.get("openings") or [],
                )
                for e in ((r.get("boundary") or {}).get("edges") or [])
                if len(e.get("vertices") or []) == 2
            ]
            poly = chain_polygon(edges)
            if poly is None or poly.area < 0.2:
                continue
            label = map_label(raw)
            has_door = any(o.get("type") == "doorway" for e in edges for o in e.openings)
            keep = True
            if label is None:
                if raw.strip().lower() == VOID and has_door and poly.area < 4.0:
                    label = "closet"  # a small unscanned space behind a door
                else:
                    keep, label = False, "other"
            parts.append(
                RoomPart(
                    mp_id=r["id"],
                    raw_label=raw or "(none)",
                    label=label,
                    confidence=float(cls.get("confidence") or 0.5) * (0.5 if raw.lower() == VOID else 1.0),
                    floor=floor_idx,
                    height=float((r.get("dimensionEstimates") or {}).get("height") or 2.4),
                    poly=poly,
                    edges=edges,
                    keep=keep,
                )
            )
    return parts


def apply_splits(parts: list[RoomPart], overrides: dict) -> list[RoomPart]:
    """Cut one Matterport room into several (e.g. an open-plan 'Kitchen' into kitchen + living room)."""
    out = list(parts)
    for sp in overrides.get("splits") or []:
        target = [p for p in out if p.keep and p.raw_label == sp["room_label"]]
        if "at" in sp:  # several rooms share the label (an earlier split): take the one holding this point
            target = [p for p in target if p.poly.contains(Point(sp["at"]))]
        if len(target) != 1:
            raise ValueError(f"split: expected one room labelled {sp['room_label']!r}, found {len(target)}")
        parent = target[0]
        pieces = list(split(parent.poly, LineString(sp["line"])).geoms)
        new: list[RoomPart] = []
        for spec in sp["parts"]:
            hit = [g for g in pieces if g.contains(Point(spec["point"]))]
            if len(hit) != 1:
                raise ValueError(f"split {sp['room_label']}: point {spec['point']} is in {len(hit)} pieces")
            poly = hit[0]
            border = poly.boundary.buffer(0.03)
            # An edge that crosses the split line (e.g. an outer wall) belongs to both parts.
            edges = [e for e in parent.edges if border.intersection(e.line()).length > 0.05]
            new.append(
                RoomPart(
                    mp_id=parent.mp_id,
                    raw_label=parent.raw_label,
                    label=spec["label"],
                    confidence=float(spec.get("confidence", 0.8)),
                    floor=parent.floor,
                    height=parent.height,
                    poly=poly,
                    edges=edges,
                    notes=[f"split from Matterport '{parent.raw_label}'"],
                )
            )
        i = out.index(parent)
        out[i : i + 1] = new
    return out


def infer_primary(parts: list[RoomPart]) -> None:
    """No room is classified as a primary bedroom: the largest bedroom becomes it (lower confidence)."""
    kept = [p for p in parts if p.keep]
    if any(p.label == "primary_bedroom" for p in kept):
        return
    beds = sorted((p for p in kept if p.label == "bedroom"), key=lambda p: p.poly.area, reverse=True)
    if beds:
        beds[0].label = "primary_bedroom"
        beds[0].confidence = min(beds[0].confidence, 0.7)
        beds[0].notes.append("largest bedroom, inferred primary")


# ---------------------------------------------------------------- openings


def _empty_runs(occupied: np.ndarray) -> list[tuple[int, int]]:
    runs, start = [], None
    for i, occ in enumerate(occupied):
        if not occ and start is None:
            start = i
        elif occ and start is not None:
            runs.append((start, i))
            start = None
    if start is not None:
        runs.append((start, len(occupied)))
    return runs


def _frame(edge: Edge, pts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    a, b = np.asarray(edge.a), np.asarray(edge.b)
    u = (b - a) / edge.length
    n = np.array([-u[1], u[0]])
    rel = pts[:, :2] - a
    return rel @ u, rel @ n


def _clamp_span(c: float, w: float, length: float) -> tuple[float, float]:
    half = min(w, length) / 2
    c = min(max(c, half), length - half)
    return (c - half, c + half)


def runs_from_points(edge: Edge, wall_pts: np.ndarray, floor_z: float) -> list[tuple[float, float]]:
    """Gaps along an edge from samples of a semantic mesh's wall surface (0.3-1.8 m above the floor)."""
    length = edge.length
    if not len(wall_pts):
        return []
    t, d = _frame(edge, wall_pts)
    z = wall_pts[:, 2] - floor_z
    sel = (abs(d) < edge.thickness / 2 + 0.12) & (t >= 0) & (t <= length) & (z > DOOR_Z[0]) & (z < DOOR_Z[1])
    nbins = max(1, math.ceil(length / BIN))
    occupied = np.zeros(nbins, dtype=bool)
    occupied[np.minimum((t[sel] / BIN).astype(int), nbins - 1)] = True
    return [(s * BIN, min(e * BIN, length)) for s, e in _empty_runs(occupied)]


def runs_from_rays(edge: Edge, rays, floor_z: float) -> list[tuple[float, float]]:
    """Gaps along an edge found by shooting rays across the wall: at each step along the edge and five
    heights (0.3-1.5 m), is there any scanned surface within 0.25 m either side of the centreline? A doorway
    is where at least four of the five heights find nothing. Works on any scan mesh (no semantic labels)."""
    length = edge.length
    nbins = max(1, math.ceil(length / BIN))
    a, b = np.asarray(edge.a), np.asarray(edge.b)
    u = (b - a) / length
    n = np.array([-u[1], u[0], 0.0])
    ts = (np.arange(nbins) + 0.5) * BIN
    heights = np.array([0.3, 0.6, 0.9, 1.2, 1.5])
    base = np.c_[a[0] + ts * u[0], a[1] + ts * u[1]]
    pts = np.c_[np.repeat(base, len(heights), axis=0), np.tile(floor_z + heights, nbins)]
    reach = 0.25
    t1, _ = rays.cast(pts + n * reach, np.tile(-n, (len(pts), 1)))
    t2, _ = rays.cast(pts - n * reach, np.tile(n, (len(pts), 1)))
    present = ((t1 < 2 * reach) | (t2 < 2 * reach)).reshape(nbins, len(heights))
    occupied = present.sum(1) >= 2
    return [(s * BIN, min(e * BIN, length)) for s, e in _empty_runs(occupied)]


def place_doors(
    edge: Edge, widths: list[float], runs: list[tuple[float, float]]
) -> list[tuple[float, float, bool]]:
    """Where each door sits along an edge, as (t0, t1, found) with distances from edge.a.

    Matterport gives door widths but not positions. Each door goes to the unused wall gap (`runs`) closest
    to its width; found is then True. A door with no matching gap (closed when scanned, or the gap was not
    scanned) is spread evenly along the edge with found False."""
    length = edge.length
    used: set[int] = set()
    order = sorted(range(len(widths)), key=lambda i: -widths[i])
    placed: dict[int, tuple[float, float, bool]] = {}
    for rank, i in enumerate(order):
        w = widths[i]
        best, best_score = None, None
        for k, (s, e) in enumerate(runs):
            gap = e - s
            if k in used or gap < 0.5 * w or gap > max(1.8 * w, w + 0.4):
                continue
            score = abs(gap - w)
            if best_score is None or score < best_score:
                best, best_score = k, score
        if best is not None:
            used.add(best)
            c = (runs[best][0] + runs[best][1]) / 2
        else:
            c = length * (rank + 1) / (len(widths) + 1)
        placed[i] = (*_clamp_span(c, w, length), best is not None)
    return [placed[i] for i in range(len(widths))]


def place_windows(edge: Edge, widths: list[float], win_pts: np.ndarray) -> list[tuple[float, float]]:
    """Windows go on the clusters of scanned window surface along the edge; else spread evenly."""
    length = edge.length
    centers: list[float] = []
    if len(win_pts):
        t, d = _frame(edge, win_pts)
        ts = np.sort(t[(abs(d) < edge.thickness / 2 + 0.2) & (t >= 0) & (t <= length)])
        if len(ts):
            clusters, cur = [], [ts[0]]
            for x in ts[1:]:
                if x - cur[-1] > 0.25:
                    clusters.append(cur)
                    cur = []
                cur.append(x)
            clusters.append(cur)
            clusters = sorted(clusters, key=lambda c: c[-1] - c[0], reverse=True)[: len(widths)]
            centers = sorted((c[0] + c[-1]) / 2 for c in clusters)
    out = []
    for i, w in enumerate(widths):
        c = centers[i] if len(centers) == len(widths) else length * (i + 1) / (len(widths) + 1)
        out.append(_clamp_span(c, w, length))
    return out


def opening_spans(parts: list[RoomPart], door_runs, win_pts: np.ndarray) -> dict[str, list[dict]]:
    """edge id -> [{type, width, t0, t1, ...}], computed once per edge so both rooms sharing it agree.
    door_runs(edge, floor_z) returns the wall gaps along an edge. A door span has `open`: True when it was
    matched to a real gap in the scan."""
    edges: dict[str, tuple[Edge, float]] = {}
    for p in parts:
        for e in p.edges:
            edges.setdefault(e.id, (e, p.floor_z))
    spans: dict[str, list[dict]] = {}
    for eid, (e, floor_z) in edges.items():
        if not e.openings or e.length < 1e-6:
            continue
        doors = [o for o in e.openings if o.get("type") == "doorway"]
        wins = [o for o in e.openings if o.get("type") == "window"]
        out = []
        placed = place_doors(e, [float(o["width"]) for o in doors], door_runs(e, floor_z) if doors else [])
        for o, (t0, t1, found) in zip(doors, placed, strict=True):
            height = float(o.get("height") or 2.03)
            out.append(
                {
                    "type": "door",
                    "width": float(o["width"]),
                    "height": height,
                    "t0": t0,
                    "t1": t1,
                    "open": found,
                }
            )
        win_spans = place_windows(e, [float(o["width"]) for o in wins], win_pts)
        for o, (t0, t1) in zip(wins, win_spans, strict=True):
            out.append({"type": "window", "width": float(o["width"]), "t0": t0, "t1": t1})
        spans[eid] = out
    return spans


def _at(e: Edge, t: float) -> tuple[float, float]:
    a, b = np.asarray(e.a), np.asarray(e.b)
    p = a + (b - a) * (t / e.length)
    return (float(p[0]), float(p[1]))


# ---------------------------------------------------------------- outputs


def local(pt: tuple[float, float]) -> list[float]:
    """Plan (x, y) -> house-local [x, z]."""
    return [round(pt[0], 3), round(-pt[1], 3)]


def local_polygon(poly: Polygon) -> list[list[float]]:
    """House-local [x, z] ring, counter-clockwise seen from above (+Y)."""
    pts = [local(c) for c in list(poly.exterior.coords)[:-1]]
    n = len(pts)
    # In [x, z] with +Y up, counter-clockwise from above has a negative shoelace sum.
    area2 = sum(pts[i][0] * pts[(i + 1) % n][1] - pts[(i + 1) % n][0] * pts[i][1] for i in range(n))
    return pts if area2 < 0 else pts[::-1]


def edge_rooms(parts: list[RoomPart]) -> dict[str, list[RoomPart]]:
    by_edge: dict[str, list[RoomPart]] = defaultdict(list)
    for p in parts:
        if p.keep:
            for e in p.edges:
                by_edge[e.id].append(p)
    return by_edge


def span_rooms(e: Edge, s: dict, by_edge: dict[str, list[RoomPart]]) -> list[RoomPart]:
    """Kept rooms whose outline passes through the middle of an opening (an edge can be longer than the
    room's side when the room was split)."""
    mid = Point(_at(e, (s["t0"] + s["t1"]) / 2))
    return [q for q in by_edge[e.id] if q.poly.boundary.distance(mid) < 0.05]


def rooms_json(parts: list[RoomPart], spans: dict[str, list[dict]]) -> list[dict]:
    by_edge = edge_rooms(parts)
    rooms = []
    for p in (q for q in parts if q.keep):
        openings = []
        for e in p.edges:
            for s in spans.get(e.id, []):
                at = span_rooms(e, s, by_edge)
                if p not in at:
                    continue
                others = [q for q in at if q is not p]
                openings.append(
                    {
                        "type": s["type"],
                        "a": local(_at(e, s["t0"])),
                        "b": local(_at(e, s["t1"])),
                        "to_room": others[0].id if others and s["type"] == "door" else None,
                    }
                )
        rooms.append(
            {
                "id": p.id,
                "label": p.label,
                "label_confidence": round(min(max(p.confidence, 0.0), 1.0), 2),
                "floor": p.floor,
                "floor_y": round(p.floor_z, 3),
                "height_m": round(p.height, 2),
                "area_m2": round(p.poly.area, 1),
                "polygon": local_polygon(p.poly),
                "openings": openings,
            }
        )
    return rooms


def split_open_width(a: RoomPart, b: RoomPart) -> float:
    """Width of the open passage between two parts of a split room: shared border minus walls on it."""
    shared = a.poly.boundary.intersection(b.poly.boundary)
    near = shared.buffer(0.03)
    # Only walls that run along the border count (not the end of a crossing outer wall).
    on_border = [
        e.line() for e in a.edges + b.edges if e.type == "wall" and near.intersection(e.line()).length > 0.1
    ]
    if not on_border:
        return float(shared.length)
    walls = unary_union([ln.buffer(0.03, cap_style="flat") for ln in on_border])
    return float(shared.difference(walls).length)


RANK = {"door": 1, "open": 2, "stairs": 3}


def graph_json(parts: list[RoomPart], spans: dict[str, list[dict]]) -> dict:
    kept = [p for p in parts if p.keep]
    by_edge = edge_rooms(parts)
    pair: dict[tuple[str, str], dict] = {}

    def add(a: RoomPart, b: RoomPart, kind: str, width: float | None) -> None:
        key = tuple(sorted((a.id, b.id)))
        cur = pair.get(key)
        better = (
            cur is None
            or RANK[kind] > RANK[cur["kind"]]
            or (kind == cur["kind"] and (width or 0) > (cur["width_m"] or 0))
        )
        if better:
            pair[key] = {
                "a": key[0],
                "b": key[1],
                "kind": kind,
                "width_m": None if width is None else round(width, 2),
            }

    for eid, rooms in by_edge.items():
        if len(rooms) < 2:
            continue
        e = next(x for x in rooms[0].edges if x.id == eid)
        if e.type == "invisible" and rooms[0].mp_id != rooms[1].mp_id:
            add(rooms[0], rooms[1], "open", e.length)
        for s in spans.get(eid, []):
            at = span_rooms(e, s, by_edge)
            if s["type"] == "door" and len(at) >= 2:
                add(at[0], at[1], "door", s["width"])
    by_mp: dict[str, list[RoomPart]] = defaultdict(list)
    for p in kept:
        by_mp[p.mp_id].append(p)
    for siblings in by_mp.values():
        for i in range(len(siblings)):
            for j in range(i + 1, len(siblings)):
                w = split_open_width(siblings[i], siblings[j])
                if w > 0.3:
                    add(siblings[i], siblings[j], "open", w)
    has_window = {
        p.id: any(
            s["type"] == "window" and p in span_rooms(e, s, by_edge)
            for e in p.edges
            for s in spans.get(e.id, [])
        )
        for p in kept
    }
    return {
        "nodes": [
            {
                "id": p.id,
                "label": p.label,
                "area_m2": round(p.poly.area, 1),
                "floor": p.floor,
                "has_window": has_window[p.id],
            }
            for p in kept
        ],
        "edges": sorted(pair.values(), key=lambda x: (x["a"], x["b"])),
    }


def collision_walls(parts: list[RoomPart], spans: dict[str, list[dict]]) -> list[hm.WallSegment]:
    """Box runs per wall edge (every room, kept or not: a closet's walls are still walls). A door is cut out
    only when the scan shows a real gap there and it joins two rooms. Outside doors stay solid (players
    enter and leave a house by zone switch), and so do doors that were closed when scanned and windows.
    Exception: outside doors marked `play_open` (see play_doors) are cut so players can walk into the yard."""
    by_edge = edge_rooms(parts)
    seen: dict[str, tuple[Edge, RoomPart]] = {}
    height: dict[str, float] = defaultdict(float)
    for p in parts:
        for e in p.edges:
            if e.type != "wall":
                continue
            seen.setdefault(e.id, (e, p))
            height[e.id] = max(height[e.id], p.height)
    segs = []
    for eid, (e, p) in seen.items():
        cuts = sorted(
            (s["t0"], s["t1"])
            for s in spans.get(eid, [])
            if s["type"] == "door"
            and (s.get("play_open") or (s.get("open", True) and len(span_rooms(e, s, by_edge)) >= 2))
        )
        t = 0.0
        for t0, t1 in [*cuts, (e.length, e.length)]:
            if t0 > t:
                segs.append(hm.WallSegment(_at(e, t), _at(e, t0), e.thickness, p.floor_z, height[eid]))
            t = max(t, t1)
    return segs


def listing_entry(out: Path, listing_id: str) -> dict:
    """The listing's entry in out/listings/index.json (lat, lon, world pose), or {} when there is none."""
    path = out / "listings" / "index.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data if isinstance(data, list) else data.get("listings") or []
    return next((row for row in rows if row.get("id") == listing_id), {})


def shell_rooms(parts: list[RoomPart], spans: dict[str, list[dict]] | None = None) -> list[rb.ShellRoom]:
    """Kept rooms as rebake inputs: centreline polygon, wall edges and the outside doors opened for play."""
    out = []
    for p in parts:
        if not p.keep:
            continue
        cuts = [
            (_at(e, s["t0"]), _at(e, s["t1"]), p.floor_z, p.floor_z + s["height"])
            for e in p.edges
            for s in (spans or {}).get(e.id, [])
            if s.get("play_open")
        ]
        walls = [(e.line(), e.thickness) for e in p.edges if e.type == "wall"]
        out.append(rb.ShellRoom(p.poly, p.floor_z, p.height, walls, cuts=cuts))
    return out


def void_polys(parts: list[RoomPart], overrides: dict | None = None) -> list[Polygon]:
    """Spaces inside the house that are no room (its outline, not yard): dropped Matterport "Void" rooms
    (closets, chases), and plan polygons listed by hand in overrides/<model>.json as
    "inside": [{"polygon": [[x, y], ...], "why": ...}] (masonry the plan leaves out as a notch: a corner
    fireplace)."""
    voids = [p.poly for p in parts if not p.keep and p.raw_label.strip().lower() == VOID]
    return voids + [Polygon(o["polygon"]) for o in (overrides or {}).get("inside") or []]


def play_doors(parts: list[RoomPart], spans: dict[str, list[dict]], footprint: Polygon) -> list[ex.Door]:
    """Mark the outside doors players may walk through (`play_open`) and return them as (a, b, z0, z1).

    Chosen by the user on 2026-11-14: the front and back doors, i.e. doors on the house outline that the scan
    saw open (so their position is measured, not guessed), not in a garage. Doors into closets the plan left
    out (dropped Void rooms, unmodelled spaces) also have no room on the other side but are not on the
    outline of `footprint` (exterior.house_footprint)."""
    outline = footprint.exterior
    by_edge = edge_rooms(parts)
    out = []
    for p in parts:
        if not p.keep or p.label == "garage":
            continue
        for e in p.edges:
            for s in spans.get(e.id, []):
                if s["type"] != "door" or not s.get("open") or len(span_rooms(e, s, by_edge)) >= 2:
                    continue
                a, b = _at(e, s["t0"]), _at(e, s["t1"])
                if outline.distance(Point(a)) > 0.25 or outline.distance(Point(b)) > 0.25:
                    continue
                if not s.get("play_open"):
                    s["play_open"] = True
                    out.append((a, b, p.floor_z, p.floor_z + s["height"]))
    return out


def scan_floor_z(scan: rb.Scan, parts: list[RoomPart]) -> dict[int, float]:
    """floor_y per Matterport floor index: the height where most upward-facing scan surface inside that
    floor's rooms lies (2 cm histogram mode)."""
    c = scan.tris.mean(1)
    up = scan.normals[:, 2] > 0.9
    out: dict[int, float] = {}
    for f in sorted({p.floor for p in parts}):
        region = unary_union([p.poly.buffer(-0.2) for p in parts if p.floor == f and p.keep])
        sel = up & shapely.contains_xy(region, c[:, 0], c[:, 1])
        if not sel.any():
            out[f] = 0.0
            continue
        z, a = c[sel, 2], scan.areas[sel]
        bins = np.round(z / 0.02).astype(int)
        hist = np.bincount(bins - bins.min(), weights=a)
        mode = (np.argmax(hist) + bins.min()) * 0.02
        near = np.abs(z - mode) < 0.03
        out[f] = float(np.average(z[near], weights=a[near]))
    return out


# ---------------------------------------------------------------- main


def extract_matterpak(model_id: str, raw: Path) -> Path:
    """Download (once) and unpack the MatterPak's textured OBJ + MTL + JPGs; the point cloud is skipped."""
    dest = raw / "matterpak"
    objs = sorted(dest.glob("*.obj"))
    if objs:
        return objs[0]
    zpath = mp.download_bundle_asset(model_id, "mp:matterpak", raw / "matterpak.zip")
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zpath) as z:
        for name in z.namelist():
            safe = Path(name).name  # flat archive; never trust member paths
            if safe and Path(safe).suffix.lower() in {".obj", ".mtl", ".jpg", ".jpeg", ".png"}:
                (dest / safe).write_bytes(z.read(name))
    objs = sorted(dest.glob("*.obj"))
    if not objs:
        raise FileNotFoundError(f"{model_id}: MatterPak has no OBJ")
    return objs[0]


def build(
    block_id: str,
    listing_id: str,
    version: int,
    out: Path,
    refresh: bool = False,
    texture_size: int = 2048,
    bake_res: float = 256.0,
    debug_dir: Path | None = None,
    holdout: tuple[str, ...] = (),
    fit_boxes: bool = True,
    exterior: bool = True,
    backdrop: str | None = None,
) -> dict:
    block = load_block(block_id)
    house = next((h for h in block["houses"] if h["listing_id"] == listing_id), None)
    if house is None:
        raise SystemExit(f"{listing_id} is not in worldgen/blocks/{block_id}.json")
    model_id = house["matterport_model_id"]
    model = mp.fetch_model(model_id, out / "cache" / "matterport", refresh=refresh)
    ov_path = OVERRIDES / f"{model_id}.json"
    overrides = json.loads(ov_path.read_text(encoding="utf-8")) if ov_path.exists() else {}

    parts = apply_splits(read_rooms(model), overrides)
    infer_primary(parts)
    for i, p in enumerate((q for q in parts if q.keep), 1):
        p.id = f"{listing_id}.r{i}"

    raw = out / "raw" / model_id
    source = house.get("interior_source", "panorama")
    house_dir = pack_dir(out, block_id, version) / "houses" / listing_id
    house_dir.mkdir(parents=True, exist_ok=True)
    scan = mesh = fixture_tris = None
    outdoors: list[ex.Exterior] = []
    # Aerial layers (Vexcel surface model, ortho, roof outline) for the roof; None when the listing has no
    # world pose yet or its block has no cached rasters. `"aerial": false` on the house turns it off.
    aerial = ae.load(out, block, listing_entry(out, listing_id)) if house.get("aerial", True) else None
    if source in ("panorama", "matterpak"):
        scan = rb.load_scan(extract_matterpak(model_id, raw))
        floor_z = scan_floor_z(scan, parts)
        win_pts = np.zeros((0, 3))

        def door_runs(e: Edge, fz: float) -> list[tuple[float, float]]:
            return runs_from_rays(e, scan.rays, fz)
    else:
        mesh = hm.read_obj(mp.download_mesh(model_id, house["interior_mesh"], raw / house["interior_mesh"]))
        floor_pts = hm.sample_points(mesh, ("042",), 20000, seed=1)
        wall_pts = hm.sample_points(mesh, ("041",), 80000, seed=2)
        win_pts = hm.sample_points(mesh, ("032", "024", "033"), 8000, seed=3)
        by_floor: dict[int, list[float]] = defaultdict(list)
        for p in parts:
            by_floor[p.floor].extend(z for x, y, z in floor_pts if p.poly.contains(Point(x, y)))
        floor_z = {f: float(np.median(zs)) if zs else 0.0 for f, zs in by_floor.items()}

        def door_runs(e: Edge, fz: float) -> list[tuple[float, float]]:
            return runs_from_points(e, wall_pts, fz)

    for p in parts:
        p.floor_z = floor_z.get(p.floor, 0.0)
    spans = opening_spans(parts, door_runs, win_pts)
    exterior = exterior and source == "panorama" and house.get("exterior", True)
    # The house outline outdoors (rooms, Void rooms, enclosed notches). Not `footprint` below: that is the
    # MatterPak crop region (rooms buffered 0.35 m), and reusing the name once fed it to the exterior.
    house_outline = (
        ex.house_footprint(scan, shell_rooms(parts), void_polys(parts, overrides))
        if exterior and scan is not None
        else None
    )
    doors_out = play_doors(parts, spans, house_outline) if house_outline is not None else []
    rooms = rooms_json(parts, spans)
    graph = graph_json(parts, spans)
    (house_dir / "rooms.json").write_text(json.dumps(rooms, indent=1) + "\n", encoding="utf-8")
    (house_dir / "graph.json").write_text(json.dumps(graph, indent=1) + "\n", encoding="utf-8")

    footprint = unary_union([p.poly for p in parts]).buffer(0.35)
    top = max(p.floor_z + p.height for p in parts)
    z_min, z_max = min(floor_z.values()) - 0.3, top + 0.3
    if source == "panorama":
        # Clean shell + scan pieces, every texel coloured from the original panoramas (worldgen/rebake.py).
        mp.download_skyboxes(model_id, raw / "skybox_2k", "2k")
        panos = PanoSet(model, raw / "skybox_2k")
        # The uncompressed glb is kept for the preview and scoring scripts. worldgen/out is shared by every
        # worktree, so builds of other pack versions get their own file and cannot overwrite each other.
        suffix = "" if version == 1 else f"_v{version}"
        tmp = out / "cache" / "glb" / f"{model_id}_interior_pano{suffix}.glb"
        if holdout:
            # Quality measurement: bake without these panoramas, then score against them
            # (scripts/preview/render_compare.py --score ... <labels>). Never ship a hold-out build.
            label = {q["id"]: q.get("label") for q in model.get("panoLocations") or []}
            panos = panos.subset([i for i, q in enumerate(panos.panos) if label.get(q.id) not in holdout])
            tmp = tmp.with_name(f"{model_id}_interior_pano_holdout{suffix}.glb")
        rooms_in = shell_rooms(parts, spans)

        def add_outdoors(scene, all_panos) -> None:
            # The scanned lot (yard, facades, roof, backdrop) in the same file: house zone = house + lot.
            hand = [(*d["at"], float(d["radius"])) for d in overrides.get("remove_outdoor") or []]
            outdoors.append(
                ex.add_exterior(
                    scene,
                    scan,
                    rooms_in,
                    all_panos,
                    doors_out,
                    house_outline,
                    aerial=aerial,
                    remove=hand,
                    # The drone splat, when out/raw/splat has one for this model (`"splat": false` on the
                    # house turns it off): the roof's shape and shingles, walls no camera saw (splat.py).
                    splat=splat.load(out, model_id, house_outline.centroid.coords[0])
                    if house.get("splat", True)
                    else None,
                    # What stands past the lot when there is a splat (exterior.add_exterior): "far" unless
                    # the house in blocks/<block>.json or --backdrop says otherwise.
                    backdrop=backdrop or house.get("backdrop", "far"),
                )
            )

        interior_counts, fixture_tris = rb.write_rebaked_interior_glb(
            scan,
            rooms_in,
            panos,
            tmp,
            bake_res,
            debug=debug_dir,
            fit_boxes=fit_boxes,
            drop_boxes=[tuple(d["at"]) for d in overrides.get("drop_boxes") or []],
            remove=[(*d["at"], float(d["radius"])) for d in overrides.get("remove") or []],
            extra=add_outdoors if exterior else None,
        )
        interior_counts["panoramas"] = len(panos)
        hm.compress_glb(tmp, house_dir / "interior.glb", texture_size)
    elif source == "matterpak":
        # The raw photo-textured scan (lumpy geometry, Matterport's low-quality mesh texture).
        tmp = out / "cache" / "glb" / f"{model_id}_interior_raw.glb"
        interior_counts = hm.write_textured_interior_glb(
            extract_matterpak(model_id, raw), footprint, z_min, z_max, top - 0.5, tmp
        )
        hm.compress_glb(tmp, house_dir / "interior.glb", texture_size)
    else:
        # Defurnished scan: no textures exist for it, so flat colours per surface class.
        interior_counts = hm.write_interior_glb(mesh, footprint, z_min, z_max, house_dir / "interior.glb")
    floors = [
        (unary_union([p.poly for p in parts if p.floor == f]).buffer(0.05), z) for f, z in floor_z.items()
    ]
    collision_counts = hm.write_collision_glb(
        collision_walls(parts, spans),
        floors,
        house_dir / "collision.glb",
        fixture_tris,
        ex.collision_meshes(outdoors[0]) if outdoors else None,
    )
    doors = [s for items in spans.values() for s in items if s["type"] == "door"]

    update_manifest(out, block_id, version, block["center"], listing_id)
    return {
        "listing_id": listing_id,
        "model_id": model_id,
        "house_dir": str(house_dir),
        "rooms": [(r["id"], r["label"], r["area_m2"], len(r["openings"])) for r in rooms],
        "dropped": [(p.raw_label, round(p.poly.area, 1)) for p in parts if not p.keep],
        "graph_edges": len(graph["edges"]),
        "doors": {"listed": len(doors), "found_in_scan": sum(s["open"] for s in doors)},
        "interior_faces": interior_counts,
        "exterior": outdoors[0].report if outdoors else None,
        "collision_tris": collision_counts,
        "floor_y": floor_z,
        "sizes_kb": {f.name: round(f.stat().st_size / 1024) for f in sorted(house_dir.glob("*"))},
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--block", required=True, help="block id; worldgen/blocks/<block>.json lists its houses")
    p.add_argument("--listing", required=True)
    p.add_argument(
        "--version",
        type=int,
        required=True,
        help="pack version to write; never the live one (worldgen/blocks/<block>.json, worldgen/versions.py)",
    )
    p.add_argument("--out", type=Path, default=HERE / "out")
    p.add_argument("--refresh", action="store_true", help="re-fetch the Matterport model detail")
    p.add_argument("--bake-res", type=float, default=256.0, help="texels per metre on walls/floors")
    p.add_argument("--no-exterior", action="store_true", help="interior only (no yard, facades, backdrop)")
    p.add_argument(
        "--backdrop",
        choices=("far", "panels", "photo"),
        help="with a drone splat: far = geometry behind the fences (default), panels = picture walls just "
        "outside the lot (an experiment), photo = the photo disk and dome alone",
    )
    p.add_argument(
        "--texture-size",
        type=int,
        default=2048,
        help="max texture edge in interior.glb (1024 halves GPU memory)",
    )
    p.add_argument(
        "--debug-dir",
        type=Path,
        help="write QA images here (wall relief maps with the fitted fixture boxes)",
    )
    p.add_argument(
        "--holdout",
        default="",
        help="comma-separated pano labels to leave out of the bake, for scoring (use with --version 99)",
    )
    p.add_argument(
        "--no-boxes",
        action="store_true",
        help="keep wall-attached fixtures as scan triangles instead of fitting clean boxes (fallback)",
    )
    args = p.parse_args(argv)
    from worldgen import versions

    versions.refuse_live(args.block, args.version, "build a house")
    print(
        json.dumps(
            build(
                args.block,
                args.listing,
                args.version,
                args.out,
                args.refresh,
                args.texture_size,
                args.bake_res,
                args.debug_dir,
                tuple(x for x in args.holdout.split(",") if x),
                not args.no_boxes,
                not args.no_exterior,
                args.backdrop,
            ),
            indent=1,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
