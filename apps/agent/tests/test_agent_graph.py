"""The real deep agent (concierge + subagents) on fixtures, driven by a scripted model, through run_turn.

Checks the filesystem handoffs (brief -> room file -> picks -> plan), the permission rules, the game calls,
parallel auditors, and the SSE events from contracts/agent-events.md.
"""

import json

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from agent import runner, tools
from agent.context import AgentContext
from agent.graph import build_agent
from data import packs
from tests.helpers import RoleModel, call, turn
from tests.test_tools import fixture_world, game  # noqa: F401  (pytest fixtures)

LISTING = "hou_000123"
ROOM = "hou_000123.r1"
TV, SOFA = "tv_stand_abo_B07GFFPKBG", "sofa_abo_B072C8Q9RF"
CTX = AgentContext(session_id="s-graph", player_id="p1", block_id="hou_navidad_01")
TYPES = {"agent.todos", "agent.subagent", "agent.tool", "agent.message", "listings.results", "audit.findings"}


@pytest.fixture(autouse=True)
def state_files(monkeypatch):
    monkeypatch.setattr(tools, "FILES", tools.StateFiles())  # the real StateBackend, not test_tools' dict


async def run(scripts, text, ctx=CTX):
    agent = build_agent(model=RoleModel(scripts=scripts), checkpointer=InMemorySaver())
    events: list[dict] = []

    async def emit(e):
        events.append(e)

    await runner.run_turn(ctx, text, emit, agent=agent)
    state = await agent.aget_state({"configurable": {"thread_id": ctx.session_id}})
    assert not [e for e in events if e["type"] == "error"], events
    assert {e["type"] for e in events} <= TYPES
    return events, state.values


def tool_events(events, tool):
    return [e["data"] for e in events if e["type"] == "agent.tool" and e["data"]["tool"] == tool]


FURNISH = {
    "concierge": [
        turn(call("write_todos", todos=[{"content": "Find the room", "status": "in_progress"}])),
        turn(call("get_player_context")),
        turn(call("write_file", file_path=f"/briefs/{LISTING}.md", content="Movie nights, family of four.")),
        turn(
            call(
                "task",
                subagent_type="room-scout",
                description=f"Listing {LISTING}. Brief: /briefs/{LISTING}.md",
            )
        ),
        turn(
            call(
                "task", subagent_type="asset-curator", description=f"Room {ROOM}. Brief: /briefs/{LISTING}.md"
            )
        ),
        turn(call("task", subagent_type="layout-planner", description=f"Room {ROOM}. Start a new plan.")),
        turn(
            call("write_file", 1, file_path=f"/plans/{ROOM}.json", content="{}"),
            call("write_file", 2, file_path="/skills/furnish-living-room/SKILL.md", content="x"),
        ),
        turn(text="Placed a TV stand and a sofa facing it, 3.5 m apart."),
    ],
    "room-scout": [
        turn(call("get_room_geometry", room_id=ROOM)),
        turn(
            call(
                "RoomChoice",
                room_id=ROOM,
                label="living_room",
                why="Only living room",
                usable_area_m2=25.2,
                constraints=["longest free wall 6.0 m"],
            )
        ),
    ],
    "asset-curator": [
        turn(call("read_file", file_path=f"/rooms/{ROOM}.json")),
        turn(call("search_assets", query="movie night", category="sofa")),
        turn(
            call(
                "save_picks",
                room_id=ROOM,
                items=[
                    {"asset_id": TV, "role": "TV on the long wall", "priority": 1},
                    {"asset_id": SOFA, "role": "main seating, faces the TV", "priority": 1},
                ],
            )
        ),
        turn(
            call(
                "Picks",
                picks_path=f"/plans/{ROOM}.picks.json",
                items=[
                    {"asset_id": TV, "count": 1, "role": "TV", "priority": 1},
                    {"asset_id": SOFA, "count": 1, "role": "seating", "priority": 1},
                ],
            )
        ),
    ],
    "layout-planner": [
        turn(call("read_file", file_path=f"/plans/{ROOM}.picks.json")),
        turn(call("start_plan", room_id=ROOM)),
        turn(call("propose_poses", room_id=ROOM, asset_id=TV)),
        turn(call("place_asset", room_id=ROOM, asset_id=TV, pose_id="p1")),
        turn(call("propose_poses", room_id=ROOM, asset_id=SOFA, rule="facing", relative_to="o1")),
        turn(call("place_asset", room_id=ROOM, asset_id=SOFA, pose_id="p1")),
        turn(call("validate_layout", room_id=ROOM)),
        turn(call("commit_placement", room_id=ROOM)),
        turn(
            call(
                "PlanReport",
                plan_id="p_x",
                room_id=ROOM,
                committed=True,
                placed=["o1 TV stand", "o2 Sofa"],
                dropped=[],
                notes="Sofa faces the TV across the room.",
            )
        ),
    ],
}


@pytest.mark.asyncio
async def test_furnish_flow_hands_off_through_files(game):  # noqa: F811  (pytest fixture)
    events, state = await run(FURNISH, "Furnish the living room for a family that watches movies together")
    files = state["files"]

    # Each step wrote its file; the planner's plan was committed and sent to the game.
    for path in (f"/briefs/{LISTING}.md", f"/rooms/{ROOM}.json", f"/plans/{ROOM}.picks.json"):
        assert path in files, path
    picks = json.loads(files[f"/plans/{ROOM}.picks.json"]["content"])
    assert [i["asset_id"] for i in picks["items"]] == [TV, SOFA]
    plan = json.loads(files[f"/plans/{ROOM}.json"]["content"])
    assert plan["status"] == "placed" and [o["asset_id"] for o in plan["objects"]] == [TV, SOFA]
    assert [c[0] for c in game] == ["plan"] and len(game[0][3]["objects"]) == 2

    # Built-in writes to /plans and /skills were refused; the plan above is the tools' plan.
    writes = [m for m in state["messages"] if getattr(m, "name", None) == "write_file"]
    assert [m.status for m in writes[-2:]] == ["error", "error"]

    # Events: todos, three subagents in order, tool lines labelled with the agent that ran them.
    assert events[0]["type"] == "agent.todos"
    subs = [(e["data"]["name"], e["data"]["status"]) for e in events if e["type"] == "agent.subagent"]
    assert subs == [
        ("room-scout", "started"),
        ("room-scout", "finished"),
        ("asset-curator", "started"),
        ("asset-curator", "finished"),
        ("layout-planner", "started"),
        ("layout-planner", "finished"),
    ]
    assert tool_events(events, "get_room_geometry")[0]["agent"] == "room-scout"
    assert tool_events(events, "save_picks")[0] == {
        "agent": "asset-curator",
        "tool": "save_picks",
        "status": "finished",
        "summary": f"2 picks saved to /plans/{ROOM}.picks.json",
    }
    commit = tool_events(events, "commit_placement")[0]
    assert commit["agent"] == "layout-planner" and commit["status"] == "finished"
    assert not tool_events(events, "RoomChoice") and not tool_events(events, "write_todos")
    texts = [e["data"] for e in events if e["type"] == "agent.message"]
    assert texts[-1] == {"text": "", "final": True}
    assert "".join(t["text"] for t in texts).startswith("Placed a TV stand")


SEARCH = {
    "concierge": [
        turn(
            call(
                "write_file", file_path="/briefs/search.md", content="New parents with an infant, buy < 450k"
            )
        ),
        turn(
            call(
                "search_listings",
                status="for_sale",
                max_price_usd=450000,
                needs=[
                    {"need": "single_story", "weight": "high"},
                    {"need": "small_bedroom_near_primary", "weight": "high"},
                ],
            )
        ),
        turn(  # both auditors in one message: they run in parallel
            call(
                "task",
                "a",
                subagent_type="listing-auditor",
                description=f"Listing {LISTING}. Brief: /briefs/search.md",
            ),
            call(
                "task",
                "b",
                subagent_type="listing-auditor",
                description="Listing hou_000456. Brief: /briefs/search.md",
            ),
        ),
        turn(call("focus_listing", listing_id=LISTING)),
        turn(text="hou_000123: one story, a 9.8 m2 bedroom next to the primary."),
    ],
    "listing-auditor": [
        turn(call("read_file", file_path="/search/results.json")),
        turn(call("get_audit", listing_id=LISTING)),
        turn(
            call(
                "Audit",
                listing_id=LISTING,
                fit=[],
                contradictions=["beds 3 listed, 2 scanned"],
                verdict="Fits.",
            )
        ),
    ],
}


@pytest.mark.asyncio
async def test_home_search_runs_auditors_in_parallel(game, monkeypatch):  # noqa: F811
    base = packs.listings()[0]
    other = {**base, "id": "hou_000456", "price_usd": 999000}  # over budget: excluded by the hard filter
    away = {**base, "id": "hou_000789", "block_id": "hou_heights_01"}  # another block: never searched
    monkeypatch.setattr(packs, "listings", lambda: [base, other, away])

    events, state = await run(SEARCH, "We're new parents with an infant. Find us a home under 450k.")

    results = json.loads(state["files"]["/search/results.json"]["content"])
    assert [m["listing_id"] for m in results["matches"]] == [LISTING]
    assert results["considered"] == 2 and results["excluded"][0]["listing_id"] == "hou_000456"
    listed = [e["data"] for e in events if e["type"] == "listings.results"]
    assert listed[0]["matches"][0]["listing_id"] == LISTING

    subs = [(e["data"]["name"], e["data"]["status"]) for e in events if e["type"] == "agent.subagent"]
    assert (
        subs.count(("listing-auditor", "started")) == 2 and subs.count(("listing-auditor", "finished")) == 2
    )
    # Both started before either finished: one message, run in parallel.
    assert subs[:2] == [("listing-auditor", "started")] * 2
    audits = tool_events(events, "get_audit")
    assert len(audits) == 2 and {a["agent"] for a in audits} == {"listing-auditor"}
    assert sum(e["type"] == "audit.findings" for e in events) == 2
    assert ("focus", "hou_navidad_01", "p1", LISTING) in game
