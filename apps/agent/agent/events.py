"""Map the Deep Agents stream to the SSE events in contracts/agent-events.md.

Input: chunks from `agent.astream(..., stream_mode=["updates", "messages"], subgraphs=True, version="v2")`,
each {"type", "ns", "data"}. The namespace `ns` is () for the concierge and ("tools:<id>",) inside a
subagent; every "messages" chunk carries metadata["lc_agent_name"], so the mapper learns ns -> agent name
from those and uses it for the "updates" chunks, where tool calls and results arrive whole.

  todos update (concierge)          -> agent.todos
  task tool call / its result       -> agent.subagent started / finished
  any other tool result             -> agent.tool finished | error (one line per call)
  search_listings / get_audit result -> also listings.results / audit.findings
  concierge text                    -> agent.message {final: false} deltas; finish() -> {"", final: true}
  end of a clean turn               -> agent.todos with every step completed (if any were left open)
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

MAIN = "concierge"
SILENT_TOOLS = {"write_todos", "RoomChoice", "Picks", "PlanReport", "Audit"}  # structured-output tools too
Event = dict[str, Any]


def _text(msg: BaseMessage) -> str:
    c = msg.content
    if isinstance(c, str):
        return c
    return "".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")


def _json(content: Any) -> Any:
    try:
        return json.loads(content) if isinstance(content, str) else content
    except (TypeError, ValueError):
        return None


def _clip(s: str, n: int = 140) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1] + "…"


# --- one-line summaries per tool result ----------------------------------------------------------------


def _geometry(r: dict) -> str:
    doors = sum(o["type"] == "door" for o in r["openings"])
    windows = len(r["openings"]) - doors
    return f"{r['room_id']}: {r['area_m2']} m2, {doors} doors, {windows} windows"


SUMMARIES: dict[str, Callable[[Any], str]] = {
    "get_room_geometry": _geometry,
    "get_rooms": lambda r: f"{len(r)} rooms: " + ", ".join(f"{x['id']} {x['label']}" for x in r[:4]),
    "get_listing": lambda r: f"{r['id']}: {r.get('address')}, ${r.get('price_usd')}, {r.get('beds')} beds",
    "get_house_graph": lambda r: f"{len(r['nodes'])} rooms, {len(r['edges'])} connections",
    "get_audit": lambda r: (
        f"{r['listing_id']}: "
        + ", ".join(
            f"{sum(f['severity'] == s for f in r['findings'])} {s}" for s in ("conflict", "warn", "info")
        )
    ),
    "search_assets": lambda r: (
        f"{len(r['results'])} matches: " + ", ".join(a["id"] for a in r["results"][:3])
    ),
    "save_picks": lambda r: f"{r['items']} picks saved to {r['path']}",
    "start_plan": lambda r: f"new plan {r['plan_id']}",
    "place_picks": lambda r: (
        f"layout {r['layout_number']}: placed {len(r['placed'])}, dropped {len(r['dropped'])}"
        + (f", swapped {len([s for s in r['swapped'] if 'to' in s])}" if r["swapped"] else "")
        + f", {r['status']}"
    ),
    "propose_poses": lambda r: f"{r['asset_id']}: {len(r['poses'])} poses {r['rule']}",
    "place_asset": lambda r: f"placed {r['placed']} ({len(r['plan']['objects'])} pieces)",
    "remove_asset": lambda r: f"removed {r['removed']}",
    "validate_layout": lambda r: f"{r['status']}, {r['objects']} pieces, {len(r['conflicts'])} conflicts",
    "commit_placement": lambda r: f"{r['placed']} pieces placed in the world ({r['plan_id']})",
    "search_listings": lambda r: (
        (
            f"{r['market']['passed_hard']} of {r['market']['listings']} listings fit; "
            if r.get("market")
            else ""
        )
        + f"{len(r['matches'])} matches of {r['considered']} walkable: "
        + ", ".join(f"{m['listing_id']} {m['score']}" for m in r["matches"])
    ),
    "focus_listing": lambda r: f"Beacon on {r['focused']}",
    "clear_furniture": lambda r: (
        f"{r['removed']} pieces removed from "
        + ((r.get("label") or "the room").replace("_", " ") if r["scope"] == "room" else "the house")
    ),
    "get_player_context": lambda r: f"in {r.get('in_house') or 'the street'}",
}


def strip_say(audit: dict) -> dict:
    """get_audit's result without the model-only `say` sentences: the event keeps audit.schema.json."""
    return {
        **audit,
        "findings": [{k: v for k, v in f.items() if k != "say"} for f in audit.get("findings", [])],
    }


def summarize(tool: str, content: Any) -> tuple[str, str]:
    """(status, summary) for one tool result."""
    r = _json(content)
    if isinstance(r, dict) and "error" in r:
        return "error", _clip(r["error"])
    if r is None and isinstance(content, str) and content.lower().startswith("error"):
        return "error", _clip(content)
    try:
        return "finished", _clip(SUMMARIES[tool](r)) if tool in SUMMARIES else _clip(str(content))
    except (KeyError, TypeError, IndexError):
        return "finished", _clip(str(content))


def _subagent_summary(content: Any) -> str:
    r = _json(content)
    if not isinstance(r, dict):
        return _clip(str(content))
    for key in ("verdict", "notes", "why"):
        if r.get(key):
            head = r.get("listing_id") or r.get("room_id") or ""
            return _clip(f"{head}: {r[key]}" if head else r[key])
    if "items" in r:
        return _clip(f"{len(r['items'])} items: " + ", ".join(i["asset_id"] for i in r["items"]))
    return _clip(str(content))


class EventMapper:
    def __init__(self) -> None:
        self.names: dict[tuple, str] = {(): MAIN}
        self.tasks: dict[str, str] = {}  # task tool_call_id -> subagent name
        self.last_text_id: str | None = None
        self.sent_text = False
        self.todos: list[dict] = []  # last plan sent, so finish() can close it

    def agent(self, ns: tuple) -> str:
        return self.names.get(tuple(ns), "subagent")

    def handle(self, chunk: dict) -> list[Event]:
        ns = tuple(chunk.get("ns") or ())
        if chunk["type"] == "messages":
            return self._message(ns, *chunk["data"])
        if chunk["type"] == "updates":
            return self._updates(ns, chunk["data"] or {})
        return []

    def _message(self, ns: tuple, msg: BaseMessage, meta: dict) -> list[Event]:
        if meta.get("lc_agent_name"):
            self.names.setdefault(ns, meta["lc_agent_name"])
        if ns or meta.get("langgraph_node") != "model" or isinstance(msg, ToolMessage):
            return []
        text = _text(msg)
        if not text:
            return []
        if self.sent_text and msg.id != self.last_text_id:
            text = "\n\n" + text  # a new concierge message in the same turn
        self.last_text_id, self.sent_text = msg.id, True
        return [{"type": "agent.message", "data": {"text": text, "final": False}}]

    def _updates(self, ns: tuple, data: dict) -> list[Event]:
        out: list[Event] = []
        # A node's update is a dict, or a list of dicts when a tool wrote several files (one per write).
        updates = [u for v in data.values() for u in (v if isinstance(v, list) else [v])]
        for update in updates:
            if not isinstance(update, dict):
                continue
            if not ns and update.get("todos") is not None:
                todos = [{"content": t["content"], "status": t["status"]} for t in update["todos"]]
                self.todos = todos
                out.append({"type": "agent.todos", "data": {"todos": todos}})
            msgs = update.get("messages") or []
            for m in msgs if isinstance(msgs, list) else [msgs]:
                if isinstance(m, AIMessage):
                    out += self._calls(ns, m)
                elif isinstance(m, ToolMessage):
                    out += self._result(ns, m)
        return out

    def _calls(self, ns: tuple, m: AIMessage) -> list[Event]:
        out = []
        for call in m.tool_calls or []:
            if call["name"] == "task":
                name = call["args"].get("subagent_type", "subagent")
                self.tasks[call["id"]] = name
                desc = str(call["args"].get("description", "")).strip().splitlines() or [""]
                data = {"name": name, "status": "started", "summary": _clip(desc[0])}
                out.append({"type": "agent.subagent", "data": data})
        return out

    def _result(self, ns: tuple, m: ToolMessage) -> list[Event]:
        if m.tool_call_id in self.tasks:
            name = self.tasks.pop(m.tool_call_id)
            status = "finished" if m.status != "error" else "error"
            data = {"name": name, "status": status, "summary": _subagent_summary(m.content)}
            return [{"type": "agent.subagent", "data": data}]
        tool = m.name or ""
        if tool in SILENT_TOOLS or not tool:
            return []
        status, summary = summarize(tool, m.content)
        if m.status == "error":
            status = "error"
        out: list[Event] = [
            {
                "type": "agent.tool",
                "data": {"agent": self.agent(ns), "tool": tool, "status": status, "summary": summary},
            }
        ]
        r = _json(m.content)
        if status == "finished" and isinstance(r, dict):
            if tool == "search_listings":
                keys = ("listing_id", "address", "price_usd", "score", "evidence")
                matches = [{k: x.get(k) for k in keys} for x in r["matches"]]
                out.append({"type": "listings.results", "data": {"matches": matches}})
            elif tool == "get_audit":
                out.append({"type": "audit.findings", "data": strip_say(r)})
        return out

    def finish(self, completed: bool = True) -> list[Event]:
        """Close the turn: mark the plan done (the concierge does not spend a turn on it) and close the
        chat bubble (the web client appends agent.message text until final: true)."""
        out: list[Event] = []
        if completed and any(t["status"] != "completed" for t in self.todos):
            self.todos = [{**t, "status": "completed"} for t in self.todos]
            out.append({"type": "agent.todos", "data": {"todos": self.todos}})
        self.sent_text, self.last_text_id = False, None
        return [{"type": "agent.message", "data": {"text": "", "final": True}}, *out]  # bubble closes first
