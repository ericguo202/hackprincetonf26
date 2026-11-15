"""Run one user message through the concierge and emit contract events (used by the API and the CLI).

One thread per session (thread_id = session_id) on the process checkpointer, so follow-ups ("make it a
playroom instead") see the earlier turns and files. One run per session at a time. Each turn's root run
gets a run_id we pick, so its LangSmith link (agent/trace.py) is in TRACES before the turn's final
agent.message; the web client reads it from GET /sessions/{id}/trace.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from agent import trace
from agent.context import AgentContext
from agent.events import EventMapper

log = logging.getLogger("agent")
Emit = Callable[[dict], Awaitable[None]]
RECURSION_LIMIT = 200

_agent: Any = None
_locks: dict[str, asyncio.Lock] = {}
TRACES: dict[str, str] = {}  # session_id -> LangSmith link of its latest turn
RUNS: dict[str, uuid.UUID] = {}  # session_id -> root run id of its latest turn (evals attach feedback)


def get_agent() -> Any:
    global _agent
    if _agent is None:
        from agent.graph import build_agent

        _agent = build_agent()
    return _agent


async def run_turn(ctx: AgentContext, text: str, emit: Emit, agent: Any = None) -> str | None:
    """Run one turn; returns its LangSmith trace link (None when tracing is off)."""
    lock = _locks.setdefault(ctx.session_id, asyncio.Lock())
    if lock.locked():
        await emit({"type": "error", "data": {"message": "The agent is still working on your last message."}})
        return None
    async with lock:
        mapper = EventMapper()
        ok = False
        run_id = RUNS[ctx.session_id] = uuid.uuid4()
        link = asyncio.create_task(trace.trace_url(run_id))  # resolves while the agent runs
        try:
            agent = agent or get_agent()
            async for chunk in agent.astream(
                {"messages": [{"role": "user", "content": text}]},
                config={
                    "configurable": {"thread_id": ctx.session_id},
                    "recursion_limit": RECURSION_LIMIT,
                    "run_id": run_id,
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
            url = await link
            if url:
                TRACES[ctx.session_id] = url
            for event in mapper.finish(completed=ok):
                await emit(event)
        return url
