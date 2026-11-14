"""Run one user message through the concierge and emit contract events (used by the API and the CLI).

One thread per session (thread_id = session_id) on the process checkpointer, so follow-ups ("make it a
playroom instead") see the earlier turns and files. One run per session at a time.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from agent.context import AgentContext
from agent.events import EventMapper

log = logging.getLogger("agent")
Emit = Callable[[dict], Awaitable[None]]
RECURSION_LIMIT = 200

_agent: Any = None
_locks: dict[str, asyncio.Lock] = {}


def get_agent() -> Any:
    global _agent
    if _agent is None:
        from agent.graph import build_agent

        _agent = build_agent()
    return _agent


async def run_turn(ctx: AgentContext, text: str, emit: Emit, agent: Any = None) -> None:
    lock = _locks.setdefault(ctx.session_id, asyncio.Lock())
    if lock.locked():
        await emit({"type": "error", "data": {"message": "The agent is still working on your last message."}})
        return
    async with lock:
        mapper = EventMapper()
        ok = False
        try:
            agent = agent or get_agent()
            async for chunk in agent.astream(
                {"messages": [{"role": "user", "content": text}]},
                config={
                    "configurable": {"thread_id": ctx.session_id},
                    "recursion_limit": RECURSION_LIMIT,
                    "metadata": {"session_id": ctx.session_id, "player_id": ctx.player_id},
                },
                context=ctx,
                stream_mode=["updates", "messages"],
                subgraphs=True,
                version="v2",
            ):
                for event in mapper.handle(chunk):
                    await emit(event)
            ok = True
        except Exception as e:  # the stream must end cleanly for the player
            log.exception("agent run failed")
            await emit({"type": "error", "data": {"message": f"agent failed: {type(e).__name__}: {e}"[:300]}})
        finally:
            for event in mapper.finish(completed=ok):
                await emit(event)
