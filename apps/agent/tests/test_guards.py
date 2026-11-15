"""Code guards behind the prompts: required pieces per skill, uncommitted-plan safety net, wide openings."""

import asyncio
import math

from agent import runner, tools
from agent.context import AgentContext
from data import packs
from layout import room_geometry

CATALOG = [
    {"id": "stand", "category": "tv_stand", "placement": "wall", "size_m": {"w": 1.6, "d": 0.4, "h": 0.5}},
    {"id": "tv", "category": "tv", "placement": "on_surface", "size_m": {"w": 1.2, "d": 0.2, "h": 0.8}},
    {"id": "wall_tv", "category": "tv", "placement": "wall", "size_m": {"w": 1.2, "d": 0.1, "h": 0.7}},
    {"id": "sofa", "category": "sofa", "placement": "wall", "size_m": {"w": 2.2, "d": 0.9, "h": 0.8}},
    {
        "id": "coffee",
        "category": "coffee_table",
        "placement": "free",
        "size_m": {"w": 1.2, "d": 0.6, "h": 0.4},
    },
]
BY_ID = {a["id"]: a for a in CATALOG}


def missing(skill, ids, fits=lambda a: True, monkeypatch=None):
    monkeypatch.setattr(packs, "catalog", lambda: CATALOG)
    return tools.missing_required(skill, [BY_ID[i] for i in ids], fits)


def test_living_room_without_tv_is_refused(monkeypatch):
    assert missing("furnish-living-room", ["stand", "sofa", "coffee"], monkeypatch=monkeypatch) == ["tv"]
    assert missing("furnish-living-room", ["stand", "tv", "sofa", "coffee"], monkeypatch=monkeypatch) == []


def test_wall_tv_replaces_the_stand(monkeypatch):
    assert missing("furnish-living-room", ["wall_tv", "sofa", "coffee"], monkeypatch=monkeypatch) == []


def test_required_piece_waived_when_nothing_in_the_catalog_fits(monkeypatch):
    def fits(a):
        return a["category"] != "tv"

    assert missing("furnish-living-room", ["stand", "sofa", "coffee"], fits, monkeypatch=monkeypatch) == []


def test_play_is_met_by_either_list(monkeypatch):
    monkeypatch.setattr(packs, "catalog", lambda: CATALOG)  # no play_mat or bookshelf: both waived
    assert tools.missing_required("furnish-for-play", [], lambda a: True) == []


def test_uncommitted_plan_is_tracked_and_committed(monkeypatch):
    monkeypatch.setattr(tools, "_thread_id", lambda: "s1")
    tools.UNCOMMITTED.clear()
    plan = {"plan_id": "p_1", "room_id": "h.r3", "status": "validated", "objects": [{"instance_id": "o1"}]}
    tools._track_uncommitted(plan)
    assert tools.UNCOMMITTED["s1"]["h.r3"]["plan_id"] == "p_1"

    sent = []

    async def send_plan(ctx, p):
        sent.append(p["plan_id"])
        return {"plan_id": p["plan_id"], "placed": 1}

    monkeypatch.setattr(tools, "send_plan", send_plan)
    events = []

    async def emit(e):
        events.append(e)

    asyncio.run(runner.commit_leftovers(AgentContext("s1", "p", "b"), emit))
    assert sent == ["p_1"]
    assert events[0]["data"]["tool"] == "commit_placement" and events[0]["data"]["status"] == "finished"
    assert "s1" not in tools.UNCOMMITTED


def test_committed_plan_is_not_tracked(monkeypatch):
    monkeypatch.setattr(tools, "_thread_id", lambda: "s2")
    tools.UNCOMMITTED.clear()
    plan = {"plan_id": "p_2", "room_id": "h.r3", "status": "validated", "objects": [{"instance_id": "o1"}]}
    tools._track_uncommitted(plan)
    tools._track_uncommitted({**plan, "status": "placed"})
    assert tools.UNCOMMITTED["s2"] == {}


def test_wide_opening_keeps_a_walkway_not_a_deep_zone():
    room = {
        "id": "h.r1",
        "polygon": [[0, 0], [6, 0], [6, 5], [0, 5]],
        "openings": [{"type": "door", "a": [0, 1], "b": [0, 4.4], "to_room": "h.r2"}],
    }
    keep = room_geometry(room).openings[0].keep_clear
    minx, _, maxx, _ = keep.bounds
    assert maxx - minx < 1.2  # 1.0 m deep plus the jamb margin, not 3.4 m


ROOM_SMALL = {"id": "h.r4", "polygon": [[0, 0], [4.4, 0], [4.4, 3.46], [0, 3.46]], "openings": []}
SEATS = {
    "desk": {
        "id": "desk",
        "name": "Desk",
        "category": "desk",
        "placement": "wall",
        "size_m": {"w": 1.1, "d": 0.56, "h": 0.73},
        "clearance_m": {"front": 0.9},
    },
    "huge_desk": {
        "id": "huge_desk",
        "name": "Huge desk",
        "category": "desk",
        "placement": "wall",
        "size_m": {"w": 9.0, "d": 0.56, "h": 0.73},
    },
    "chair": {
        "id": "chair",
        "name": "Chair",
        "category": "office_chair",
        "placement": "free",
        "size_m": {"w": 0.6, "d": 0.6, "h": 1.0},
    },
}


def test_chair_is_dropped_with_its_desk_not_floated():
    from layout.auto import auto_place_all

    picks = [
        {"asset_id": "huge_desk", "priority": 1, "rule": "against_wall"},
        {"asset_id": "chair", "priority": 1, "rule": "facing", "relative_to": "huge_desk"},
    ]
    r = auto_place_all(room_geometry(ROOM_SMALL), picks, SEATS.get)
    assert r["objects"] == []
    assert {d["asset_id"] for d in r["dropped"]} == {"huge_desk", "chair"}


def test_desk_gets_a_spot_where_its_chair_fits():
    from layout.auto import auto_place_all

    picks = [
        {"asset_id": "desk", "priority": 1, "rule": "against_wall"},
        {"asset_id": "chair", "priority": 1, "rule": "facing", "relative_to": "desk"},
    ]
    r = auto_place_all(room_geometry(ROOM_SMALL), picks, SEATS.get)
    desk, chair = r["objects"]
    assert r["dropped"] == []
    assert math.dist((desk["x"], desk["z"]), (chair["x"], chair["z"])) < 1.0  # pulled up, not elsewhere
