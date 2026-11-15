"""The deep agent: concierge plus four subagents (AGENTS.md section 10).

Subagents hand work to each other through the shared filesystem (/briefs, /rooms, /plans, /search), not
through task text. Plans and rooms live per thread in the StateBackend; skills are read from apps/agent/
skills on disk through a CompositeBackend route. Tools write plans; built-in writes to /plans and /skills
are denied.
"""

from __future__ import annotations

from deepagents import FilesystemPermission, create_deep_agent
from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend
from langchain.agents.middleware import TodoListMiddleware
from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.memory import InMemorySaver

import settings
from agent import prompts, schemas, tools
from agent.context import AgentContext

SKILLS_DIR = settings.REPO_ROOT / "apps" / "agent" / "skills"
SKILLS = ["/skills/"]
PERMISSIONS = [
    *tools.PLAN_PERMISSIONS,
    FilesystemPermission(operations=["write"], paths=["/skills/**"], mode="deny"),
]

# One checkpointer for the process; one thread per session (thread_id = session_id).
CHECKPOINTER = InMemorySaver()


def backend() -> CompositeBackend:
    return CompositeBackend(
        default=StateBackend(),
        routes={"/skills/": FilesystemBackend(root_dir=SKILLS_DIR, virtual_mode=True)},
    )


SUBAGENTS = [
    {
        "name": "room-scout",
        "description": "Picks the room to furnish in a house and returns its geometry constraints. Writes "
        "/rooms/<room_id>.json. Give it the listing id and the brief path.",
        "system_prompt": prompts.ROOM_SCOUT,
        "tools": tools.ROOM_SCOUT_TOOLS,
        "response_format": schemas.RoomChoice,
    },
    {
        "name": "asset-curator",
        "description": "Picks real catalog furniture for one room and purpose, sized to it, and saves "
        "/plans/<room_id>.picks.json. Give it the room id, brief path and room file path.",
        "system_prompt": prompts.ASSET_CURATOR,
        "tools": tools.CURATOR_TOOLS,
        "response_format": schemas.Picks,
    },
    {
        "name": "layout-planner",
        "description": "Places the picks in the room: proposes poses, validates, fixes, and commits to the "
        "shared world. Also edits an existing plan. Give it the room id, brief and picks paths.",
        "system_prompt": prompts.LAYOUT_PLANNER,
        "tools": tools.LAYOUT_TOOLS,
        "response_format": schemas.PlanReport,
    },
    {
        "name": "listing-auditor",
        "description": "Checks one home against the buyer's needs using its rooms, graph and data audit, "
        "and flags contradictions. Run one per candidate, all in the same turn.",
        "system_prompt": prompts.LISTING_AUDITOR,
        "tools": tools.AUDITOR_TOOLS,
        "response_format": schemas.Audit,
    },
]


def chat_model(spec: str) -> BaseChatModel:
    """The model from MODEL ("provider:model") with the latency knobs from settings applied."""
    provider = spec.split(":", 1)[0]
    kwargs: dict = {}
    effort = settings.MODEL_REASONING_EFFORT
    if effort and provider == "openrouter":
        kwargs["reasoning"] = {"effort": effort}
    elif effort and provider == "openai":
        kwargs["reasoning_effort"] = effort
    if settings.MODEL_PROVIDER_SORT and provider == "openrouter":
        kwargs["openrouter_provider"] = {"sort": settings.MODEL_PROVIDER_SORT}
    return init_chat_model(spec, **kwargs)


def build_agent(model: str | BaseChatModel | None = None, checkpointer=CHECKPOINTER):
    model = model or settings.MODEL
    if not model:
        raise RuntimeError("MODEL is not set (provider:model, e.g. anthropic:claude-sonnet-5-5)")
    if isinstance(model, str):
        model = chat_model(model)
    return create_deep_agent(
        model=model,
        tools=tools.CONCIERGE_TOOLS,
        system_prompt=prompts.CONCIERGE,
        subagents=[{**s, "skills": SKILLS} for s in SUBAGENTS],  # custom subagents do not inherit skills
        skills=SKILLS,
        permissions=PERMISSIONS,
        backend=backend(),
        context_schema=AgentContext,
        checkpointer=checkpointer,
        # deepagents 0.7 no longer adds write_todos by default; the concierge's plan streams as agent.todos.
        middleware=[TodoListMiddleware(system_prompt=prompts.TODOS)],
        name="concierge",
    )
