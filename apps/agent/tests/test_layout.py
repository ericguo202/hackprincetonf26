"""layout/: room geometry, validate_layout and propose_poses against the fixture room and an L-shaped room."""

import json
import math

import pytest

import settings
from layout import propose_poses, room_geometry, validate_layout
from layout.geometry import footprint

# Sizes copied from furniture/v1/catalog.json (worldgen/out is not in git).
ASSETS = {
    "sofa_abo_B072C8Q9RF": {
        "id": "sofa_abo_B072C8Q9RF",
        "name": "Sofa",
        "category": "sofa",
        "size_m": {"w": 2.312, "d": 0.969, "h": 0.792},
        "placement": "wall",
        "clearance_m": {"front": 0.75},
    },
    "tv_stand_abo_B07GFFPKBG": {
        "id": "tv_stand_abo_B07GFFPKBG",
        "name": "TV stand",
        "category": "tv_stand",
        "size_m": {"w": 1.6, "d": 0.418, "h": 0.52},
        "placement": "wall",
        "clearance_m": {"front": 0.9},
    },
    "coffee": {
        "id": "coffee",
        "name": "Coffee table",
        "category": "coffee_table",
        "size_m": {"w": 1.1, "d": 0.6, "h": 0.45},
        "placement": "center",
        "clearance_m": {"front": 0.0},
    },
    "side": {
        "id": "side",
        "name": "Side table",
        "category": "side_table",
        "size_m": {"w": 0.5, "d": 0.5, "h": 0.55},
        "placement": "beside",
        "clearance_m": {"front": 0.0},
    },
    "shelf": {
        "id": "shelf",
        "name": "Bookshelf",
        "category": "bookshelf",
        "size_m": {"w": 0.8, "d": 0.3, "h": 1.8},
        "placement": "wall",
        "clearance_m": {"front": 0.6},
    },
    "rug": {
        "id": "rug",
        "name": "Rug",
        "category": "rug",
        "size_m": {"w": 2.4, "d": 1.7, "h": 0.01},
        "placement": "center",
        "clearance_m": {"front": 0.0},
    },
    "lamp": {
        "id": "lamp",
        "name": "Table lamp",
        "category": "table_lamp",
        "size_m": {"w": 0.3, "d": 0.3, "h": 0.5},
        "placement": "on_surface",
        "clearance_m": {"front": 0.0},
    },
    "huge": {
        "id": "huge",
        "name": "Huge sectional",
        "category": "sectional",
        "size_m": {"w": 7.0, "d": 1.0, "h": 0.8},
        "placement": "corner",
        "clearance_m": {"front": 0.75},
    },
}

L_ROOM = {  # 5 x 4 with the 2 x 2 top-right corner cut out; door on the bottom wall
    "id": "hou_test.r2",
    "label": "living_room",
    "label_confidence": 1.0,
    "floor": 0,
    "floor_y": 0.0,
    "height_m": 2.6,
    "area_m2": 16.0,
    "polygon": [[0, 0], [5, 0], [5, 2], [3, 2], [3, 4], [0, 4]],
    "openings": [{"type": "door", "a": [1.0, 0], "b": [1.9, 0], "to_room": "hou_test.r1"}],
}


@pytest.fixture
def room():
    return json.loads((settings.FIXTURES_DIR / "room.json").read_text(encoding="utf-8"))


@pytest.fixture
def plan():
    return json.loads((settings.FIXTURES_DIR / "placement_plan.json").read_text(encoding="utf-8"))


def obj(iid, asset_id, x, z, rot=0.0):
    return {
        "instance_id": iid,
        "asset_id": asset_id,
        "x": x,
        "y": 0.0,
        "z": z,
        "rot_deg": rot,
        "reason": "test",
    }


def types(result):
    return sorted({c["type"] for c in result["conflicts"]})


# --- geometry -----------------------------------------------------------------------------------


def test_summary_of_fixture_room(room):
    s = room_geometry(room).summary()
    assert s["area_m2"] == pytest.approx(25.2)
    assert {o["wall"] for o in s["openings"]} == {1, 3}  # east and west walls
    longest = s["free_walls"][0]
    assert longest["length_m"] == pytest.approx(6.0)
    # Doors are cut out of the walls (plus margin); the window piece stays but is flagged.
    west = [w for w in s["free_walls"] if w["wall"] == 3]
    assert sum(w["length_m"] for w in west) == pytest.approx(4.2 - 0.9 - 0.2)
    assert any(w["under_window"] for w in west)


def test_footprint_matches_web_rotation():
    # rot 90: front (+Z) turns to +X, so a 2 x 1 piece becomes 1 wide in x and 2 deep in z.
    minx, minz, maxx, maxz = footprint(0, 0, {"w": 2, "d": 1, "h": 1}, 90).bounds
    assert (maxx - minx, maxz - minz) == (pytest.approx(1), pytest.approx(2))


def test_concave_room_inward_normals_and_corners():
    geo = room_geometry(L_ROOM)
    assert geo.poly.area == pytest.approx(16.0)
    for seg in geo.walls:  # a point just inside each wall is inside the room
        mx, mz = (seg.a[0] + seg.b[0]) / 2, (seg.a[1] + seg.b[1]) / 2
        assert geo.poly.contains(
            footprint(mx + seg.inward[0] * 0.1, mz + seg.inward[1] * 0.1, {"w": 0.01, "d": 0.01}, 0)
        )
    assert len(geo.corners) == 5  # the reflex corner at (3, 2) is not a corner to put things in


# --- validate_layout ----------------------------------------------------------------------------


def test_fixture_plan_is_valid(room, plan):
    out = validate_layout(plan, room, ASSETS)
    assert out["conflicts"] == [] and out["status"] == "validated"
    assert plan["conflicts"] == []  # input not mutated


def test_outside_room(room, plan):
    plan["objects"][0]["z"] = 0.2  # sofa back pokes through the south wall
    assert "outside_room" in types(validate_layout(plan, room, ASSETS))


def test_overlap_reported_once(room, plan):
    plan["objects"].append(obj("o3", "side", 3.0, 0.5))
    out = validate_layout(plan, room, ASSETS)
    overlaps = [c for c in out["conflicts"] if c["type"] == "overlap"]
    assert len(overlaps) == 1 and out["status"] == "draft"


def test_blocks_door(room, plan):
    plan["objects"].append(obj("o3", "side", 0.6, 1.45))  # in the swing of the west door
    out = validate_layout(plan, room, ASSETS)
    assert [c["type"] for c in out["conflicts"] if c["instance_id"] == "o3"] == ["blocks_opening"]


def test_tall_piece_covers_window_low_piece_does_not(room, plan):
    plan["objects"].append(obj("o3", "shelf", 0.17, 3.2, rot=90))
    assert "blocks_opening" in types(validate_layout(plan, room, ASSETS))
    plan["objects"][-1] = obj("o3", "side", 0.27, 3.2, rot=90)
    assert validate_layout(plan, room, ASSETS)["conflicts"] == []


def test_clearance_into_wall_and_blocked(room, plan):
    plan["objects"][1]["rot_deg"] = 0  # TV stand faces the wall it stands against
    assert "clearance" in types(validate_layout(plan, room, ASSETS))
    plan = json.loads((settings.FIXTURES_DIR / "placement_plan.json").read_text(encoding="utf-8"))
    plan["objects"].append(obj("o3", "side", 3.0, 1.3))  # side table in front of the sofa
    assert "clearance" in types(validate_layout(plan, room, ASSETS))


def test_coffee_table_and_rug_allowed_in_front_of_sofa(room, plan):
    plan["objects"].append(obj("o3", "coffee", 3.0, 1.73, rot=180))
    plan["objects"].append(obj("o4", "rug", 3.0, 1.6))
    assert validate_layout(plan, room, ASSETS)["conflicts"] == []


def test_wrong_room_and_unknown_asset(room, plan):
    with pytest.raises(ValueError):
        validate_layout({**plan, "room_id": "hou_000123.r9"}, room, ASSETS)
    plan["objects"][0]["asset_id"] = "nope"
    with pytest.raises(KeyError):
        validate_layout(plan, room, ASSETS)


# --- propose_poses ------------------------------------------------------------------------------


def placed(plan, *ids):
    return [(o, ASSETS[o["asset_id"]]) for o in plan["objects"] if o["instance_id"] in ids]


def assert_all_valid(room, plan, asset_id, poses):
    for p in poses:
        trial = {**plan, "objects": [*plan["objects"], obj("o9", asset_id, p["x"], p["z"], p["rot_deg"])]}
        trial["objects"][-1]["y"] = p["y"]
        assert validate_layout(trial, room, ASSETS)["conflicts"] == [], p


def test_against_wall_faces_into_room(room):
    out = propose_poses(room_geometry(room), ASSETS["tv_stand_abo_B07GFFPKBG"], "against_wall")
    assert 1 <= len(out["poses"]) <= 5
    assert [p["pose_id"] for p in out["poses"]] == [f"p{i + 1}" for i in range(len(out["poses"]))]
    best = out["poses"][0]
    assert best["reason"].endswith("centered")
    assert_all_valid(
        room,
        {
            "plan_id": "t",
            "listing_id": "x",
            "room_id": room["id"],
            "status": "draft",
            "objects": [],
            "conflicts": [],
        },
        "tv_stand_abo_B07GFFPKBG",
        out["poses"],
    )


def test_sofa_facing_tv(room, plan):
    plan["objects"] = plan["objects"][1:]  # only the TV stand is placed
    out = propose_poses(
        room_geometry(room), ASSETS["sofa_abo_B072C8Q9RF"], "facing", "o2", placed(plan, "o2")
    )
    best = out["poses"][0]
    tv = plan["objects"][0]
    assert best["rot_deg"] == 0 and best["z"] < 1.0  # back to the south wall, facing the TV
    assert math.dist((best["x"], best["z"]), (tv["x"], tv["z"])) == pytest.approx(3.5, abs=0.2)
    assert_all_valid(room, plan, "sofa_abo_B072C8Q9RF", out["poses"])


def test_beside_aligns_backs(room, plan):
    out = propose_poses(room_geometry(room), ASSETS["side"], "beside", "o1", placed(plan, "o1", "o2"))
    assert len(out["poses"]) == 2
    sofa = plan["objects"][0]
    for p in out["poses"]:
        assert p["z"] - 0.25 == pytest.approx(sofa["z"] - 0.969 / 2, abs=1e-3)
    assert_all_valid(room, plan, "side", out["poses"])


def test_coffee_table_in_front_of_sofa(room, plan):
    out = propose_poses(room_geometry(room), ASSETS["coffee"], "center", "o1", placed(plan, "o1", "o2"))
    assert out["poses"] and out["poses"][0]["reason"].startswith("0.45 m in front of Sofa")
    assert_all_valid(room, plan, "coffee", out["poses"])


def test_lamp_on_top_of_side_table(room, plan):
    plan["objects"].append(obj("o3", "side", 4.456, 0.256))
    out = propose_poses(room_geometry(room), ASSETS["lamp"], "center", "o3", placed(plan, "o1", "o2", "o3"))
    assert out["poses"] and out["poses"][0]["y"] == pytest.approx(0.55)


def test_corner_and_center_in_l_room():
    geo = room_geometry(L_ROOM)
    plan = {
        "plan_id": "t",
        "listing_id": "hou_test",
        "room_id": L_ROOM["id"],
        "status": "draft",
        "objects": [],
        "conflicts": [],
    }
    for rule, asset_id in (("corner", "side"), ("center", "coffee"), ("against_wall", "shelf")):
        out = propose_poses(geo, ASSETS[asset_id], rule)
        assert out["poses"], (rule, out["note"])
        assert_all_valid(L_ROOM, plan, asset_id, out["poses"])


def test_nothing_fits_says_why(room):
    out = propose_poses(room_geometry(room), ASSETS["huge"], "against_wall")
    assert out["poses"] == [] and "no free wall segment fits 7.00 m" in out["note"]


def test_rule_argument_errors(room, plan):
    geo = room_geometry(room)
    with pytest.raises(ValueError):
        propose_poses(geo, ASSETS["side"], "beside")  # needs relative_to
    with pytest.raises(ValueError):
        propose_poses(geo, ASSETS["side"], "beside", "o7", placed(plan, "o1"))
    with pytest.raises(ValueError):
        propose_poses(geo, ASSETS["side"], "floating")
