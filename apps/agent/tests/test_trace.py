"""The LangSmith link points at the turn's root run, and the API serves it after the turn."""

import uuid

import pytest
from fastapi.testclient import TestClient
from langchain_core.tracers.context import collect_runs
from langgraph.checkpoint.memory import InMemorySaver

from agent import runner, tools, trace
from agent.context import AgentContext
from agent.graph import build_agent
from tests.helpers import RoleModel, turn
from tests.test_tools import fixture_world, game  # noqa: F401  (pytest fixtures)

PREFIX = "https://smith.example/o/t/projects/p/p1"


@pytest.fixture(autouse=True)
def fake_langsmith(monkeypatch):
    monkeypatch.setattr(tools, "FILES", tools.StateFiles())

    async def url(run_id, timeout=5.0):
        return trace.url_for(run_id, PREFIX)

    monkeypatch.setattr(trace, "trace_url", url)


def test_url_for():
    rid = uuid.UUID(int=1)
    assert trace.url_for(rid, PREFIX) == f"{PREFIX}/r/{rid}?poll=true"


@pytest.mark.asyncio
async def test_turn_link_names_the_root_run(game):  # noqa: F811  (pytest fixture)
    agent = build_agent(
        model=RoleModel(scripts={"concierge": [turn(text="Hi.")]}), checkpointer=InMemorySaver()
    )
    ctx = AgentContext(session_id="s-trace", player_id="p1", block_id="hou_navidad_01")
    events: list[dict] = []
    at_final: list[str | None] = []

    async def emit(e):
        events.append(e)
        if e["data"].get("final"):  # the link is ready when the bubble closes
            at_final.append(runner.TRACES.get(ctx.session_id))

    with collect_runs() as cb:
        url = await runner.run_turn(ctx, "hello", emit, agent=agent)
    root = next(r for r in cb.traced_runs if r.name == "concierge")  # the graph's root run
    assert url == trace.url_for(root.id, PREFIX) == at_final[0]
    assert events[-1]["data"] == {"text": "", "final": True}


def test_trace_endpoint(monkeypatch):
    monkeypatch.setattr("settings.MOCK_AGENT", True)
    from api.main import app

    c = TestClient(app)
    sid = c.post("/sessions", json={"player_id": "p1", "block_id": "hou_navidad_01"}).json()["session_id"]
    assert c.get(f"/sessions/{sid}/trace").json() == {"url": None}
    runner.TRACES[sid] = f"{PREFIX}/r/x?poll=true"
    assert c.get(f"/sessions/{sid}/trace").json() == {"url": f"{PREFIX}/r/x?poll=true"}
    assert c.get("/sessions/nope/trace").status_code == 404
