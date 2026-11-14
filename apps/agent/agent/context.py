"""Runtime context for every agent run (AGENTS.md section 10: tools read these, the model never passes them).

Passed as `agent.ainvoke(..., context=AgentContext(...))`; Deep Agents hands the same context to subagents.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentContext:
    session_id: str
    player_id: str
    block_id: str
