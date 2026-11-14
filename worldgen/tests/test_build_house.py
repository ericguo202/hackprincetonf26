import numpy as np
import pytest

from worldgen import build_house as bh
from worldgen.panos import Raycaster


def edge(eid, a, b, openings=(), etype="wall"):
    return {
        "id": eid,
        "type": etype,
        "thickness": 0.1,
        "vertices": [
            {"id": f"{eid}a", "position": {"x": a[0], "y": a[1]}},
            {"id": f"{eid}b", "position": {"x": b[0], "y": b[1]}},
        ],
        "openings": list(openings),
    }


def room(rid, label, edges, height=2.5):
    return {
        "id": rid,
        "classifications": [{"label": label, "confidence": 1.0}],
        "dimensionEstimates": {"height": height},
        "boundary": {"edges": edges},
    }


DOOR = {"type": "doorway", "width": 0.9}
WINDOW = {"type": "window", "width": 1.0}

# Two rooms side by side sharing edge "s" (x = 4) with a door; an open-plan "Kitchen" with a half wall.
MODEL = {
    "floors": [
        {
            "rooms": [
                room(
                    "A",
                    "Bedroom",
                    [
                        edge("a1", (0, 0), (4, 0)),
                        edge("a2", (0, 0), (0, 3), [WINDOW]),
                        edge("a3", (0, 3), (4, 3)),
                        edge("s", (4, 0), (4, 3), [DOOR]),
                    ],
                ),
                room(
                    "B",
                    "Bedroom",
                    [
                        edge("b1", (4, 0), (6, 0)),
                        edge("b2", (6, 0), (6, 3)),
                        edge("b3", (4, 3), (6, 3)),
                        edge("s", (4, 0), (4, 3), [DOOR]),
                    ],
                ),
                room(
                    "K",
                    "Kitchen",
                    [
                        edge("k1", (0, 3), (4, 3)),
                        edge("k2", (4, 3), (4, 9)),
                        edge("k3", (0, 9), (4, 9)),
                        edge("k4", (0, 3), (0, 9)),
                        edge("half", (0, 6), (2.5, 6)),
                    ],
                ),
                room(
                    "V",
                    "Void",
                    [
                        edge("v1", (6, 0), (7, 0)),
                        edge("v2", (7, 0), (7, 1)),
                        edge("v3", (6, 1), (7, 1)),
                        edge("v4", (6, 0), (6, 1)),
                    ],
                ),
            ]
        }
    ]
}
SPLIT = {
    "splits": [
        {
            "room_label": "Kitchen",
            "line": [[-1, 6], [5, 6]],
            "parts": [{"label": "kitchen", "point": [1, 8]}, {"label": "living_room", "point": [1, 4]}],
        }
    ]
}


def wall_points_with_gap(gap=(1.8, 2.7)):
    ys = np.arange(0, 3, 0.01)
    ys = ys[(ys < gap[0]) | (ys > gap[1])]
    return np.array([[4.0, y, z] for y in ys for z in (0.5, 1.0, 1.5)])


def build_parts():
    parts = bh.apply_splits(bh.read_rooms(MODEL), SPLIT)
    bh.infer_primary(parts)
    for i, p in enumerate((q for q in parts if q.keep), 1):
        p.id = f"hou_1.r{i}"
    return parts


def test_rooms_areas_labels_and_split():
    parts = build_parts()
    kept = {p.label: p for p in parts if p.keep}
    assert set(kept) == {"primary_bedroom", "bedroom", "kitchen", "living_room"}
    assert kept["primary_bedroom"].poly.area == pytest.approx(12.0)  # the larger bedroom
    assert kept["kitchen"].poly.area == pytest.approx(12.0)
    assert kept["living_room"].poly.area == pytest.approx(12.0)
    assert not [p for p in parts if p.label == "closet"]  # a void with no door is dropped
    # The outer wall k2 crosses the split line, so both halves keep it.
    assert {"k2", "k4"} <= {e.id for e in kept["kitchen"].edges}
    assert {"k2", "k4"} <= {e.id for e in kept["living_room"].edges}


def test_door_goes_into_the_scanned_wall_gap():
    e = bh.Edge("s", "wall", (4, 0), (4, 3), 0.1, [DOOR])
    runs = bh.runs_from_points(e, wall_points_with_gap(), 0.0)
    ((t0, t1, found),) = bh.place_doors(e, [0.9], runs)
    assert found
    assert (t0 + t1) / 2 == pytest.approx(2.25, abs=0.06)
    assert t1 - t0 == pytest.approx(0.9)


def test_door_without_a_gap_is_centred_and_not_open():
    e = bh.Edge("s", "wall", (4, 0), (4, 3), 0.1, [DOOR])
    ((t0, t1, found),) = bh.place_doors(e, [0.9], [])
    assert not found
    assert (t0 + t1) / 2 == pytest.approx(1.5)


def test_rays_find_the_doorway_in_a_scanned_wall():
    # A wall at x = 4 from y = 0 to 3, 2.5 m tall, with a doorway from y = 1.8 to 2.7.
    def quad(y0, y1):
        a, b, c, d = (4, y0, 0), (4, y1, 0), (4, y1, 2.5), (4, y0, 2.5)
        return [[a, b, c], [a, c, d]]

    rays = Raycaster(np.array(quad(0, 1.8) + quad(2.7, 3), dtype=float))
    e = bh.Edge("s", "wall", (4, 0), (4, 3), 0.1, [DOOR])
    runs = bh.runs_from_rays(e, rays, 0.0)
    assert len(runs) == 1
    assert runs[0][0] == pytest.approx(1.8, abs=0.06) and runs[0][1] == pytest.approx(2.7, abs=0.06)


def spans_for(parts):
    return bh.opening_spans(
        parts, lambda e, fz: bh.runs_from_points(e, wall_points_with_gap(), fz), np.zeros((0, 3))
    )


def test_rooms_graph_and_collision_agree():
    parts = build_parts()
    spans = spans_for(parts)
    rooms = {r["label"]: r for r in bh.rooms_json(parts, spans)}
    door = next(o for o in rooms["primary_bedroom"]["openings"] if o["type"] == "door")
    assert door["to_room"] == rooms["bedroom"]["id"]
    assert rooms["bedroom"]["openings"][0]["to_room"] == rooms["primary_bedroom"]["id"]
    assert rooms["kitchen"]["area_m2"] == 12.0

    graph = bh.graph_json(parts, spans)
    kinds = {(e["kind"], e["width_m"]) for e in graph["edges"]}
    assert ("door", 0.9) in kinds
    assert ("open", 1.5) in kinds  # 4 m shared border minus the 2.5 m half wall
    nodes = {n["label"]: n for n in graph["nodes"]}
    assert nodes["primary_bedroom"]["has_window"] is True  # the window is on the larger bedroom (edge a2)
    assert nodes["kitchen"]["has_window"] is False

    walls = bh.collision_walls(parts, spans)
    shared = [
        w
        for w in walls
        if w.a[0] == pytest.approx(4)
        and w.b[0] == pytest.approx(4)
        and min(w.a[1], w.b[1]) < 3
        and max(w.a[1], w.b[1]) <= 3
    ]
    covered = sum(abs(w.b[1] - w.a[1]) for w in shared)
    assert covered == pytest.approx(3 - 0.9, abs=0.01)  # the door is cut out of the shared wall


def test_polygon_is_counter_clockwise_from_above():
    parts = build_parts()
    pts = bh.local_polygon(next(p for p in parts if p.label == "kitchen").poly)
    n = len(pts)
    shoelace = sum(pts[i][0] * pts[(i + 1) % n][1] - pts[(i + 1) % n][0] * pts[i][1] for i in range(n))
    assert shoelace < 0  # [x, z] with +Y up: negative shoelace = counter-clockwise seen from above


def test_a_closed_or_outside_door_stays_solid_in_collision():
    parts = build_parts()
    spans = spans_for(parts)
    spans["s"][0]["open"] = False  # the door was closed when scanned
    walls = bh.collision_walls(parts, spans)
    shared = [
        w
        for w in walls
        if w.a[0] == pytest.approx(4) and w.b[0] == pytest.approx(4) and max(w.a[1], w.b[1]) <= 3
    ]
    assert sum(abs(w.b[1] - w.a[1]) for w in shared) == pytest.approx(3.0, abs=0.01)


def test_second_split_picks_the_room_by_point():
    two = {
        "splits": [
            *SPLIT["splits"],
            {
                "room_label": "Kitchen",
                "at": [1, 4],
                "line": [[2, -1], [2, 10]],
                "parts": [
                    {"label": "dining_room", "point": [1, 4]},
                    {"label": "living_room", "point": [3, 4]},
                ],
            },
        ]
    }
    parts = bh.apply_splits(bh.read_rooms(MODEL), two)
    areas = {p.label: round(p.poly.area, 1) for p in parts if p.keep and p.raw_label == "Kitchen"}
    assert areas == {"kitchen": 12.0, "dining_room": 6.0, "living_room": 6.0}
