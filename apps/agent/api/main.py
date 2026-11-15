"""Agent service: per-player sessions, messages in, SSE events out (contracts/agent-events.md)."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel

import settings
from agent import game_api, runner
from agent.context import AgentContext
from api.sessions import Session, sessions

log = logging.getLogger("agent")

app = FastAPI(title="Built World agent")
# The web client is a separate static site; it only reads the SSE stream and posts messages.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST"], allow_headers=["*"])


class NewSession(BaseModel):
    player_id: str
    block_id: str


class UserMessage(BaseModel):
    text: str


@app.get("/health")
def health() -> dict:
    return {"ok": True, "use_fixtures": settings.USE_FIXTURES, "mock_agent": settings.MOCK_AGENT}


@app.post("/sessions")
def create_session(body: NewSession) -> dict:
    return {"session_id": sessions.create(body.player_id, body.block_id).session_id}


def _session(session_id: str) -> Session:
    s = sessions.get(session_id)
    if s is None:
        raise HTTPException(404, "unknown session")
    return s


async def replay_fixture_transcript(s: Session) -> None:
    """MOCK_AGENT=1: replay the transcript; send its placement and beacon to the game API."""
    plan = json.loads((settings.FIXTURES_DIR / "placement_plan.json").read_text(encoding="utf-8"))
    room = json.loads((settings.FIXTURES_DIR / "room.json").read_text(encoding="utf-8"))
    room_label = room["label"] if room.get("id") == plan["room_id"] else None
    for line in (settings.FIXTURES_DIR / "agent.session.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        await sessions.emit(s.session_id, event)
        tool = event["data"].get("tool") if event["type"] == "agent.tool" else None
        try:
            if tool == "commit_placement":
                await game_api.post_plan(
                    s.block_id, s.player_id, {**plan, "status": "placed"}, room_label=room_label
                )
            elif tool == "focus_listing":
                await game_api.post_focus(s.block_id, s.player_id, plan["listing_id"])
        except Exception as e:  # game server down: the chat still replays
            log.warning("mock agent could not reach the game API: %s", e)
        await asyncio.sleep(0.4)


@app.post("/sessions/{session_id}/messages", status_code=202)
async def post_message(session_id: str, body: UserMessage) -> dict:
    s = _session(session_id)
    if settings.MOCK_AGENT:
        _spawn(replay_fixture_transcript(s))
    elif not settings.MODEL:
        await sessions.emit(s.session_id, {"type": "error", "data": {"message": "MODEL is not set"}})
    else:
        ctx = AgentContext(session_id=s.session_id, player_id=s.player_id, block_id=s.block_id)
        _spawn(runner.run_turn(ctx, body.text, lambda e: sessions.emit(s.session_id, e)))
    return {"accepted": True}


_tasks: set[asyncio.Task] = set()


def _spawn(coro) -> None:
    """Keep a reference so the run is not garbage-collected mid-flight."""
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


@app.get("/sessions/{session_id}/stream", response_class=EventSourceResponse)
async def stream(session_id: str) -> AsyncIterator[ServerSentEvent]:
    s = _session(session_id)
    while True:
        event = await s.queue.get()
        yield ServerSentEvent(event=event["type"], data=event["data"])
