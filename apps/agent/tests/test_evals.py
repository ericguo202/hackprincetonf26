"""The eval runner's checks (evals/run.py) on hand-made turns, and the case files' shape."""

import importlib.util
import sys

import settings

spec = importlib.util.spec_from_file_location("evals_run", settings.REPO_ROOT / "evals" / "run.py")
ev = importlib.util.module_from_spec(spec)
sys.modules["evals_run"] = ev  # dataclasses look their module up here
spec.loader.exec_module(ev)

CATALOG = {
    "sofa_1": {"category": "sofa", "cover": "half"},
    "tv_1": {"category": "tv_stand", "cover": "half"},
    "rug_1": {"category": "rug", "cover": "none"},
    "mat_1": {"category": "play_mat", "cover": "none"},
}
LABELS = {"h.r3": "living_room"}


def obj(asset_id, x=0.0, z=0.0, rot=0):
    return {"instance_id": asset_id, "asset_id": asset_id, "x": x, "y": 0.0, "z": z, "rot_deg": rot}


def plan(*objects, room="h.r3"):
    return {"plan_id": "p", "listing_id": "h", "room_id": room, "objects": list(objects), "conflicts": []}


def turn(*plans, events=(), focus=(), seconds=10.0):
    return ev.TurnResult(
        prompt="x", seconds=seconds, events=list(events), plans=list(plans), focus=list(focus)
    )


def score(expect, t, previous=None, conflicts=()):
    checks = ev.score_turn(
        expect, t, previous, CATALOG, lambda r: LABELS.get(r, "?"), lambda p: list(conflicts)
    )
    return {c.name: c.passed for c in checks}


def test_furnish_checks_pass_on_a_good_plan():
    t = turn(
        plan(obj("tv_1"), obj("sofa_1", z=3), obj("rug_1", z=1.5)),
        events=[
            {"type": "agent.todos", "data": {"todos": []}},
            {"type": "agent.subagent", "data": {"name": "room-scout", "status": "started", "summary": ""}},
            {"type": "agent.tool", "data": {"agent": "a", "tool": "commit_placement", "status": "finished"}},
            {"type": "agent.message", "data": {"text": "Placed a TV", "final": False}},
            {"type": "agent.message", "data": {"text": " and a sofa.", "final": True}},
        ],
    )
    expect = {
        "planned": True,
        "subagents": ["room-scout"],
        "tools": ["commit_placement"],
        "committed": True,
        "room_label": "living_room",
        "categories_all": ["tv_stand", "sofa"],
        "categories_any": ["sectional", "sofa"],
        "min_objects": 3,
        "min_cover": 2,
        "valid": True,
        "answer_mentions": ["tv|television", "SOFA"],
        "max_seconds": 60,
    }
    result = score(expect, t)
    assert result == {k: True for k in [*expect, "no_error"]}


def test_failures_are_reported():
    t = turn(
        plan(obj("rug_1")),
        events=[{"type": "error", "data": {"message": "boom"}}],
        seconds=400,
    )
    result = score(
        {"categories_all": ["tv_stand"], "min_cover": 1, "valid": True, "max_seconds": 300, "focus": "h"},
        t,
        conflicts=[{"type": "overlap"}],
    )
    assert result == {
        "no_error": False,
        "max_seconds": False,
        "categories_all": False,
        "min_cover": False,
        "valid": False,
        "focus": False,
    }
    assert score({"committed": True, "room_label": "living_room"}, turn()) == {
        "no_error": True,
        "committed": False,
        "room_label": False,
    }


def test_replaces_previous_needs_same_room_and_a_changed_layout():
    before = turn(plan(obj("sofa_1"), obj("tv_1", z=3)))
    moved = turn(plan(obj("sofa_1", x=2), obj("tv_1", z=3)))
    assert score({"replaces_previous": True}, moved, before)["replaces_previous"]
    assert not score({"replaces_previous": True}, before, before)["replaces_previous"]
    other_room = turn(plan(obj("mat_1"), room="h.r9"))
    assert not score({"replaces_previous": True}, other_room, before)["replaces_previous"]
    assert not score({"replaces_previous": True}, moved, turn())["replaces_previous"]


def test_case_files_are_well_formed():
    cases = ev.load_cases([])  # exits on an unknown check name
    assert len({c["id"] for c in cases}) == len(cases) >= 5
    for c in cases:
        assert c["turns"] and c.get("zone") and all(t["prompt"] for t in c["turns"])
