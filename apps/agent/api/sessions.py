"""Per-player agent sessions and emit(session_id, event) onto the session's SSE stream."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field


@dataclass
class Session:
    session_id: str
    player_id: str
    block_id: str
    queue: asyncio.Queue[dict] = field(default_factory=asyncio.Queue)


class Sessions:
    def __init__(self) -> None:
        self._by_id: dict[str, Session] = {}

    def create(self, player_id: str, block_id: str) -> Session:
        s = Session(session_id=str(uuid.uuid4()), player_id=player_id, block_id=block_id)
        self._by_id[s.session_id] = s
        return s

    def get(self, session_id: str) -> Session | None:
        return self._by_id.get(session_id)

    async def emit(self, session_id: str, event: dict) -> None:
        """Queue one agent event ({"type", "data"}) for the session's stream."""
        s = self._by_id.get(session_id)
        if s is not None:
            await s.queue.put(event)


sessions = Sessions()
