"""place_picks: one-pass layout from the picks, regenerate (new layout, new furniture), overrides."""

import json
import math

import pytest

from agent import tools
from data import packs
from tests.test_layout import ASSETS
from tests.test_tools import ROOM, RT, files, fixture_world  # noqa: F401  (pytest fixtures)

TV, SOFA = "tv_stand_abo_B07GFFPKBG", "sofa_abo_B072C8Q9RF"
EXTRA = {
    "sofa_alt": {
        **ASSETS[SOFA],
        "id": "sofa_alt",
        "name": "Grey sofa",
        "size_m": {"w": 2.0, "d": 0.9, "h": 0.8},
    },
}
PICKS = [
    {"asset_id": TV, "role": "TV on the long wall", "priority": 1, "rule": "against_wall"},
    {
        "asset_id": SOFA,
        "role": "Main seating facing the TV",
        "priority": 1,
        "rule": "facing",
        "relative_to": TV,
        "alternates": ["sofa_alt"],
    },
    {
        "asset_id": "coffee",
        "role": "Drinks within reach",
        "priority": 1,
        "rule": "center",
        "relative_to": SOFA,
    },
    {"asset_id": "rug", "role": "Warms the seating area", "priority": 2, "rule": "center"},
]


@pytest.fixture(autouse=True)
def more_assets(monkeypatch):
    every = {**ASSETS, **EXTRA}
    monkeypatch.setattr(packs, "get_asset", lambda asset_id: every.get(asset_id))


def call(t, **kw):
    out = json.loads(t.func(**kw))
    assert "error" not in out, out
    return out


def save():
    return call(tools.save_picks, room_id=ROOM, items=PICKS)


def test_one_pass_places_everything_in_order(files):  # noqa: F811
    save()
    out = call(tools.place_picks, room_id=ROOM, runtime=RT)
    assert out["status"] == "validated" and out["dropped"] == [] and out["layout_number"] == 1
    by_asset = {p["asset_id"]: p for p in out["placed"]}
    assert by_asset[SOFA]["relative_to"] == by_asset[TV]["instance_id"] == "o1"
    assert by_asset["coffee"]["relative_to"] == by_asset[SOFA]["instance_id"]
    plan = files.read_json(tools.plan_path(ROOM))
    assert [o["reason"] for o in plan["objects"]][:2] == ["TV on the long wall", "Main seating facing the TV"]
    assert by_asset[TV]["alternatives"]  # the planner can review and switch


def test_new_layout_moves_the_anchor(files):  # noqa: F811
    save()
    first = call(tools.place_picks, room_id=ROOM, runtime=RT)
    a = files.read_json(tools.plan_path(ROOM))["objects"][0]
    second = call(tools.place_picks, room_id=ROOM, runtime=RT, new_layout=True)
    b = files.read_json(tools.plan_path(ROOM))["objects"][0]
    assert second["plan_id"] != first["plan_id"] and second["layout_number"] == 2
    turned = abs((a["rot_deg"] - b["rot_deg"] + 180) % 360 - 180)
    assert math.dist((a["x"], a["z"]), (b["x"], b["z"])) >= 1.0 or turned >= 45
    assert second["status"] == "validated"


def test_new_furniture_swaps_unused_alternates(files):  # noqa: F811
    save()
    call(tools.place_picks, room_id=ROOM, runtime=RT)
    out = call(tools.place_picks, room_id=ROOM, runtime=RT, new_furniture=True)
    assert {"from": SOFA, "to": "sofa_alt"} in out["swapped"]
    assets = [o["asset_id"] for o in files.read_json(tools.plan_path(ROOM))["objects"]]
    assert "sofa_alt" in assets and SOFA not in assets
    # The coffee table still follows the sofa that is actually there.
    coffee = next(p for p in out["placed"] if p["asset_id"] == "coffee")
    assert coffee["relative_to"] == next(
        p["instance_id"] for p in out["placed"] if p["asset_id"] == "sofa_alt"
    )


def test_switch_a_piece_to_an_alternative(files):  # noqa: F811
    save()
    out = call(tools.place_picks, room_id=ROOM, runtime=RT)
    rug = next(p for p in out["placed"] if p["asset_id"] == "rug")
    alt = rug["alternatives"][0]["pose_id"]
    moved = call(
        tools.place_asset,
        room_id=ROOM,
        asset_id="rug",
        pose_id=alt,
        runtime=RT,
        instance_id=rug["instance_id"],
    )
    assert moved["placed"] == rug["instance_id"]
    assert call(tools.validate_layout, room_id=ROOM, runtime=RT)["status"] == "validated"


def test_save_picks_checks_relative_to_and_alternates(files):  # noqa: F811
    bad_rel = [{**PICKS[1], "relative_to": "not_a_pick"}]
    assert "not one of the picks" in json.loads(tools.save_picks.func(room_id=ROOM, items=bad_rel))["error"]
    bad_alt = [{**PICKS[0], "alternates": ["huge"]}]
    assert "7.00 m wide" in json.loads(tools.save_picks.func(room_id=ROOM, items=bad_alt))["error"]
    files.files.clear()  # no picks saved: place_picks says so instead of placing nothing
    assert "no picks" in json.loads(tools.place_picks.func(room_id=ROOM, runtime=RT))["error"]
