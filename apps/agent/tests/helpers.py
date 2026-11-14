"""A scripted chat model for running the real deep agent in tests without an LLM.

Each agent (concierge or subagent) is recognised from its system prompt. Its next turn comes from its own
script, indexed by how many AI turns that conversation already has, so parallel subagents with the same
script each run it from the start.
"""

from __future__ import annotations

import itertools
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult

ROLES = {
    "You are the concierge": "concierge",
    "You are the room scout": "room-scout",
    "You are the asset curator": "asset-curator",
    "You are the layout planner": "layout-planner",
    "You are a listing auditor": "listing-auditor",
}


def call(name: str, i: int | str = 0, **args: Any) -> dict:
    return {"name": name, "args": args, "id": f"{name}-{i}", "type": "tool_call"}


def turn(*calls: dict, text: str = "") -> AIMessage:
    return AIMessage(content=text, tool_calls=list(calls))


_ids = itertools.count()


class RoleModel(BaseChatModel):
    scripts: dict[str, list[AIMessage]]

    @property
    def _llm_type(self) -> str:
        return "role-scripted"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        system = messages[0].content if messages and isinstance(messages[0], SystemMessage) else ""
        system = system if isinstance(system, str) else str(system)
        role = next((r for marker, r in ROLES.items() if marker in system), None)
        if role is None:
            raise AssertionError(f"unknown agent; system prompt starts {system[:80]!r}")
        step = sum(isinstance(m, AIMessage) for m in messages)
        script = self.scripts[role]
        msg = script[min(step, len(script) - 1)]
        # Fresh tool-call ids on every turn, so parallel subagents never share one.
        n = next(_ids)
        msg = msg.model_copy(update={"tool_calls": [{**c, "id": f"{c['id']}-{n}"} for c in msg.tool_calls]})
        return ChatResult(generations=[ChatGeneration(message=msg)])
