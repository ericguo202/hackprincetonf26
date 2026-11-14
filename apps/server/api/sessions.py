"""Session registry and emit(session_id, event). Lane C owns this file."""

from __future__ import annotations

from fastapi import WebSocket


class Sessions:
    def __init__(self) -> None:
        self._sockets: dict[str, WebSocket] = {}

    def attach(self, session_id: str, ws: WebSocket) -> None:
        self._sockets[session_id] = ws

    def detach(self, session_id: str) -> None:
        self._sockets.pop(session_id, None)

    async def emit(self, session_id: str, event: dict) -> None:
        ws = self._sockets.get(session_id)
        if ws is not None:
            await ws.send_json(event)


sessions = Sessions()
