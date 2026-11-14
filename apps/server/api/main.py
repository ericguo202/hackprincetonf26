"""FastAPI app: one WebSocket per session, static data and assets. Lane C owns this file."""

from __future__ import annotations

import asyncio
import json

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

import settings
from api.sessions import sessions

app = FastAPI(title="Built World Sandbox")

if settings.DATA_DIR.exists():
    app.mount("/data", StaticFiles(directory=settings.DATA_DIR), name="data")
if settings.ASSETS_DIR.exists():
    app.mount("/assets", StaticFiles(directory=settings.ASSETS_DIR), name="assets")


@app.get("/health")
def health() -> dict:
    return {"ok": True, "use_fixtures": settings.USE_FIXTURES, "mock_agent": settings.MOCK_AGENT}


async def replay_fixture_transcript(session_id: str) -> None:
    """MOCK_AGENT=1: replay the server -> web events from the fixture transcript."""
    path = settings.FIXTURES_DIR / "events.session.jsonl"
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        if event["type"].startswith("user."):
            continue
        await sessions.emit(session_id, event)
        await asyncio.sleep(0.4)


@app.websocket("/ws/{session_id}")
async def ws(websocket: WebSocket, session_id: str) -> None:
    await websocket.accept()
    sessions.attach(session_id, websocket)
    try:
        while True:
            event = await websocket.receive_json()
            if event.get("type") == "user.message":
                if settings.MOCK_AGENT:
                    asyncio.create_task(replay_fixture_transcript(session_id))
                else:
                    await sessions.emit(
                        session_id, {"type": "error", "data": {"message": "agent not wired yet"}}
                    )
    except WebSocketDisconnect:
        sessions.detach(session_id)
