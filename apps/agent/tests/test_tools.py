"""agent/tools.py: each tool on the fixture room, then one real Deep Agents run with a scripted model."""

import json
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

import settings
from agent import game_api, tools
from agent.context import AgentContext
from data import packs
from tests.test_layout import ASSETS

ROOM = "hou_000123.r1"
CTX = AgentContext(session_id="s1", player_id="p1", block_id="hou_navidad_01")
RT = SimpleNamespace(context=CTX)


class DictFiles:
    def __init__(self) -> None:
        self.files: dict[str, Any] = {}

    def read_json(self, path):
        return json.loads(self.files[path]) if path in self.files else None

    def write_json(self, path, obj):
        self.files[path] = json.dumps(obj)


@pytest.fixture(autouse=True)
def fixture_world(monkeypatch):
    monkeypatch.setattr(settings, "USE_FIXTURES", True)
    monkeypatch.setattr(settings, "FAKE_PLACEMENT", False)
    packs.clear_cache()
    monkeypatch.setattr(packs, "catalog", lambda: list(ASSETS.values()))
    monkeypatch.setattr(packs, "get_asset", lambda asset_id: ASSETS.get(asset_id))
    yield
    # Tests may still have catalog/get_asset/listings patched here (monkeypatch restores them afterwards),
    # so only clear the caches that are really cached functions.
    for f in (packs._read_pack, packs.listings):
        getattr(f, "cache_clear", lambda: None)()


@pytest.fixture
def files(monkeypatch):
    f = DictFiles()
    monkeypatch.setattr(tools, "FILES", f)
    return f


@pytest.fixture
def game(monkeypatch):
    calls = []

    async def post_plan(block_id, player_id, plan):
        calls.append(("plan", block_id, player_id, plan))
        return {"placed": len(plan["objects"])}

    async def post_focus(block_id, player_id, listing_id):
        calls.append(("focus", block_id, player_id, listing_id))
        return {"ok": True}

    async def get_player(block_id, player_id):
        return {"id": player_id, "zone": "hou_000123", "x": 1.0, "y": 0.0, "z": 2.0}

    monkeypatch.setattr(game_api, "post_plan", post_plan)
    monkeypatch.setattr(game_api, "post_focus", post_focus)
    monkeypatch.setattr(game_api, "get_player", get_player)
    return calls


def call(t, **kw):
    return json.loads(t.func(**kw))


async def acall(t, **kw):
    return json.loads(await t.coroutine(**kw))


# --- reads ---------------------------------------------------------------------------------------


def test_reads_on_fixtures():
    assert call(tools.get_listing, listing_id="hou_000123")["id"] == "hou_000123"
    assert "error" in call(tools.get_listing, listing_id="nope")
    rooms = call(tools.get_rooms, listing_id="hou_000123")
    assert rooms[0] == {
        "id": ROOM,
        "label": "living_room",
        "floor": 0,
        "area_m2": 25.2,
        "doors": 2,
        "windows": 1,
    }
    assert call(tools.get_house_graph, listing_id="hou_000123")["nodes"]
    assert call(tools.get_audit, listing_id="hou_000123")["listing_id"] == "hou_000123"


def test_search_assets_filters_and_ranks():
    out = call(tools.search_assets, query="movie night seating", category="sofa")
    assert [a["id"] for a in out["results"]] == ["sofa_abo_B072C8Q9RF"]
    assert set(out["results"][0]) == {
        "id",
        "name",
        "category",
        "style",
        "color",
        "size_m",
        "cover",
        "placement",
    }
    assert call(tools.search_assets, query="table", max_w=0.6)["results"][0]["id"] in {"side", "lamp"}
    assert "categories:" in call(tools.search_assets, query="x", category="spaceship")["error"]


def test_room_geometry_is_saved(files):
    out = call(tools.get_room_geometry, room_id=ROOM, runtime=RT)
    assert out["area_m2"] == 25.2 and files.read_json(f"/rooms/{ROOM}.json") == out
    assert "error" in call(tools.get_room_geometry, room_id="hou_000123.r9", runtime=RT)
    assert "error" in call(tools.get_room_geometry, room_id="garbage", runtime=RT)


# --- furnish loop ----------------------------------------------------------------------------------


def furnish_tv_and_sofa():
    call(tools.start_plan, room_id=ROOM, runtime=RT)
    tv = call(tools.propose_poses, room_id=ROOM, asset_id="tv_stand_abo_B07GFFPKBG", runtime=RT)
    assert tv["rule"] == "against_wall" and "score" not in tv["poses"][0]
    call(tools.place_asset, room_id=ROOM, asset_id="tv_stand_abo_B07GFFPKBG", pose_id="p1", runtime=RT)
    sofa = call(
        tools.propose_poses,
        room_id=ROOM,
        asset_id="sofa_abo_B072C8Q9RF",
        rule="facing",
        relative_to="o1",
        runtime=RT,
    )
    return call(
        tools.place_asset,
        room_id=ROOM,
        asset_id="sofa_abo_B072C8Q9RF",
        pose_id=sofa["poses"][0]["pose_id"],
        reason="Faces the TV across the room for movie night together",
        runtime=RT,
    )


def test_plan_loop(files):
    out = furnish_tv_and_sofa()
    assert out["placed"] == "o2"
    plan = files.read_json(f"/plans/{ROOM}.json")
    assert [o["instance_id"] for o in plan["objects"]] == ["o1", "o2"]
    assert plan["objects"][1]["reason"] == "Faces the TV across the room for movie night together"
    v = call(tools.validate_layout, room_id=ROOM, runtime=RT)
    assert v == {"status": "validated", "objects": 2, "conflicts": []}


def test_place_needs_a_proposal_and_rechecks(files):
    call(tools.start_plan, room_id=ROOM, runtime=RT)
    err = call(tools.place_asset, room_id=ROOM, asset_id="side", pose_id="p1", runtime=RT)
    assert "propose_poses" in err["error"]
    # Propose two side tables' poses, place one, then try the same pose again: it now overlaps.
    call(tools.propose_poses, room_id=ROOM, asset_id="side", rule="corner", runtime=RT)
    call(tools.place_asset, room_id=ROOM, asset_id="side", pose_id="p1", runtime=RT)
    again = call(tools.place_asset, room_id=ROOM, asset_id="side", pose_id="p1", runtime=RT)
    assert "no longer fits" in again["error"]


def test_move_and_remove(files):
    furnish_tv_and_sofa()
    moved = call(
        tools.propose_poses,
        room_id=ROOM,
        asset_id="tv_stand_abo_B07GFFPKBG",
        rule="against_wall",
        moving="o1",
        runtime=RT,
    )
    pose = moved["poses"][-1]
    out = call(
        tools.place_asset,
        room_id=ROOM,
        asset_id="tv_stand_abo_B07GFFPKBG",
        pose_id=pose["pose_id"],
        instance_id="o1",
        runtime=RT,
    )
    o1 = next(o for o in out["plan"]["objects"] if o["instance_id"] == "o1")
    assert (o1["x"], o1["z"]) == (pose["x"], pose["z"]) and len(out["plan"]["objects"]) == 2
    assert (
        call(tools.remove_asset, room_id=ROOM, instance_id="o2")["plan"]["objects"][0]["instance_id"] == "o1"
    )
    assert "error" in call(tools.remove_asset, room_id=ROOM, instance_id="o2")


def test_place_on_blocked_pose_moves_blockers_out(files, monkeypatch):
    from tests.test_layout import BENCH

    monkeypatch.setitem(ASSETS, "bench", BENCH)
    furnish_tv_and_sofa()
    out = call(tools.propose_poses, room_id=ROOM, asset_id="bench", rule="against_wall", runtime=RT)
    assert out["poses"] == [] and "score" not in out["blocked"][0]
    b1 = out["blocked"][0]
    placed = call(tools.place_asset, room_id=ROOM, asset_id="bench", pose_id="b1", runtime=RT)
    assert [r["instance_id"] for r in placed["removed"]] == b1["blocked_by"]
    ids = {o["instance_id"] for o in placed["plan"]["objects"]}
    assert placed["placed"] in ids and not ids & set(b1["blocked_by"])
    assert call(tools.validate_layout, room_id=ROOM, runtime=RT)["status"] == "validated"


def test_reason_is_cut_to_12_words(files):
    call(tools.start_plan, room_id=ROOM, runtime=RT)
    call(tools.propose_poses, room_id=ROOM, asset_id="side", rule="corner", runtime=RT)
    out = call(
        tools.place_asset,
        room_id=ROOM,
        asset_id="side",
        pose_id="p1",
        reason=" ".join(f"w{i}" for i in range(20)),
        runtime=RT,
    )
    assert len(out["plan"]["objects"][0]["reason"].split()) == 12


@pytest.mark.asyncio
async def test_commit_refuses_conflicts_then_places(files, game):
    furnish_tv_and_sofa()
    plan = files.read_json(f"/plans/{ROOM}.json")
    plan["objects"].append(
        {
            "instance_id": "o3",
            "asset_id": "side",
            "x": 0.6,
            "y": 0.0,
            "z": 1.45,
            "rot_deg": 0,
            "reason": "hand edit in the door swing",
        }
    )
    files.write_json(f"/plans/{ROOM}.json", plan)
    refused = await acall(tools.commit_placement, room_id=ROOM, runtime=RT)
    assert "conflicts" in refused["error"] and game == []

    call(tools.remove_asset, room_id=ROOM, instance_id="o3")
    out = await acall(tools.commit_placement, room_id=ROOM, runtime=RT)
    assert out["placed"] == 2
    kind, block, player, sent = game[0]
    assert (kind, block, player) == ("plan", "hou_navidad_01", "p1")
    assert sent["status"] == "placed" and sent["conflicts"] == [] and sent["listing_id"] == "hou_000123"
    assert files.read_json(f"/plans/{ROOM}.json")["status"] == "placed"


@pytest.mark.asyncio
async def test_game_tools(game):
    assert (await acall(tools.focus_listing, listing_id="hou_000123", runtime=RT)) == {
        "focused": "hou_000123"
    }
    assert game[-1] == ("focus", "hou_navidad_01", "p1", "hou_000123")
    assert "error" in await acall(tools.focus_listing, listing_id="nope", runtime=RT)
    ctx = await acall(tools.get_player_context, runtime=RT)
    assert ctx["in_house"] == "hou_000123" and ctx["block_id"] == "hou_navidad_01"


@pytest.mark.asyncio
async def test_game_down_is_an_error_not_a_crash(files, monkeypatch):
    furnish_tv_and_sofa()
    monkeypatch.setattr(settings, "GAME_URL", "http://127.0.0.1:9")
    out = await acall(tools.commit_placement, room_id=ROOM, runtime=RT)
    assert "game service" in out["error"]


# --- one real Deep Agents run ---------------------------------------------------------------------


class ScriptedModel(BaseChatModel):
    """Replays fixed AI turns; tool results are checked by the test after the run."""

    script: list[AIMessage]
    step: int = 0

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        msg = self.script[min(self.step, len(self.script) - 1)]
        self.step += 1
        return ChatResult(generations=[ChatGeneration(message=msg)])


def tool_turn(i: int, name: str, **args) -> AIMessage:
    return AIMessage(
        content="", tool_calls=[{"name": name, "args": args, "id": f"c{i}", "type": "tool_call"}]
    )


@pytest.mark.asyncio
async def test_deep_agent_run_uses_state_files(game):
    from deepagents import create_deep_agent
    from langgraph.checkpoint.memory import InMemorySaver

    script = [
        tool_turn(1, "start_plan", room_id=ROOM),
        tool_turn(2, "propose_poses", room_id=ROOM, asset_id="tv_stand_abo_B07GFFPKBG"),
        tool_turn(3, "place_asset", room_id=ROOM, asset_id="tv_stand_abo_B07GFFPKBG", pose_id="p1"),
        tool_turn(
            4, "propose_poses", room_id=ROOM, asset_id="sofa_abo_B072C8Q9RF", rule="facing", relative_to="o1"
        ),
        tool_turn(5, "place_asset", room_id=ROOM, asset_id="sofa_abo_B072C8Q9RF", pose_id="p1"),
        tool_turn(6, "write_file", file_path=f"/plans/{ROOM}.json", content="{}"),  # must be denied
        tool_turn(7, "validate_layout", room_id=ROOM),
        tool_turn(8, "commit_placement", room_id=ROOM),
        AIMessage(content="Placed a TV stand and a sofa facing it."),
    ]
    agent = create_deep_agent(
        model=ScriptedModel(script=script),
        tools=tools.LAYOUT_TOOLS,
        permissions=tools.PLAN_PERMISSIONS,
        context_schema=AgentContext,
        checkpointer=InMemorySaver(),
    )
    result = await agent.ainvoke(
        {"messages": [{"role": "user", "content": "Furnish the living room for movie night"}]},
        config={"configurable": {"thread_id": "t1"}},
        context=CTX,
    )
    by_id = {m.tool_call_id: m for m in result["messages"] if isinstance(m, ToolMessage)}
    for i in (1, 2, 3, 4, 5, 7, 8):
        assert "error" not in json.loads(by_id[f"c{i}"].content), by_id[f"c{i}"].content
    assert by_id["c6"].status == "error"  # built-in write_file cannot touch /plans/**

    plan = json.loads(result["files"][f"/plans/{ROOM}.json"]["content"])
    assert plan["status"] == "placed" and [o["asset_id"] for o in plan["objects"]] == [
        "tv_stand_abo_B07GFFPKBG",
        "sofa_abo_B072C8Q9RF",
    ]
    assert f"/plans/{ROOM}.poses.json" in result["files"]
    assert game[0][0] == "plan" and len(game[0][3]["objects"]) == 2
