"""The real Navidad pack (worldgen/out, gitignored): skipped when it is not on this machine.

Every room gets geometry with every opening on a wall, and both living rooms take a full
movie-night set from the real catalog with no conflicts.
"""

import json
from types import SimpleNamespace

import pytest

import settings
from agent import tools
from agent.context import AgentContext
from data import packs

BLOCK = "hou_navidad_01"
HOUSES = {"hou_236595179": "hou_236595179.r3", "hou_237901269": "hou_237901269.r4"}
RT = SimpleNamespace(context=AgentContext(session_id="s", player_id="p", block_id=BLOCK))

pytestmark = pytest.mark.skipif(
    not (settings.PACK_DIR / "packs" / BLOCK).exists() or not (settings.PACK_DIR / "furniture").exists(),
    reason="Navidad pack not in worldgen/out",
)


class DictFiles:
    def __init__(self) -> None:
        self.files: dict[str, str] = {}

    def read_json(self, path):
        return json.loads(self.files[path]) if path in self.files else None

    def write_json(self, path, obj):
        self.files[path] = json.dumps(obj)


@pytest.fixture(autouse=True)
def real_pack(monkeypatch):
    monkeypatch.setattr(settings, "USE_FIXTURES", False)
    monkeypatch.setattr(tools, "FILES", DictFiles())
    packs.clear_cache()
    yield
    packs.clear_cache()


def call(t, **kw):
    out = json.loads(t.func(**kw))
    assert "error" not in out, out
    return out


@pytest.mark.parametrize("listing_id", HOUSES)
def test_every_room_has_geometry(listing_id):
    rooms = call(tools.get_rooms, listing_id=listing_id, runtime=RT)
    assert rooms
    for r in rooms:
        g = call(tools.get_room_geometry, room_id=r["id"], runtime=RT)
        assert all(o["wall"] is not None for o in g["openings"]), r["id"]
        assert g["free_walls"], r["id"]


# (category, rule, index of the step it is relative to)
MOVIE_NIGHT = [
    ("tv_stand", "against_wall", None),
    ("sofa", "facing", 0),
    ("coffee_table", "center", 1),
    ("rug", "center", None),
    ("side_table", "beside", 1),
    ("bookshelf", "against_wall", None),
    ("floor_lamp", "corner", None),
]


@pytest.mark.parametrize("room_id", HOUSES.values())
def test_living_room_movie_night(room_id):
    call(tools.start_plan, room_id=room_id, runtime=RT)
    placed: list[str] = []
    for category, rule, rel in MOVIE_NIGHT:
        asset_id = call(tools.search_assets, query=category, category=category)["results"][0]["id"]
        kw = {"rule": rule} | ({"relative_to": placed[rel]} if rel is not None else {})
        poses = call(tools.propose_poses, room_id=room_id, asset_id=asset_id, runtime=RT, **kw)["poses"]
        assert poses, (category, rule)
        out = call(
            tools.place_asset, room_id=room_id, asset_id=asset_id, pose_id=poses[0]["pose_id"], runtime=RT
        )
        placed.append(out["placed"])
    result = call(tools.validate_layout, room_id=room_id, runtime=RT)
    assert result["status"] == "validated" and result["conflicts"] == []
    assert result["objects"] == len(MOVIE_NIGHT)
