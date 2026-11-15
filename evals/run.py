"""Eval runner for the concierge agent (Lane B runner, Lane A cases). AGENTS.md sections 10 and 13.

Runs each case in evals/cases/*.json through the real deep agent (the same runner.run_turn the API uses),
with the game service faked so commits and beacons are captured instead of sent. Every check is
deterministic: the committed plan is re-validated with the layout validator, asset categories and cover
come from the catalog, room labels from rooms.json. The model is never asked to grade itself.

    cd apps/agent
    uv run python ../../evals/run.py                     # every case, real packs (USE_FIXTURES=0)
    uv run python ../../evals/run.py furnish_movie_night play_refurnish -j 2
    uv run python ../../evals/run.py --list
    uv run python ../../evals/run.py --feedback          # also score each turn's LangSmith trace

Needs MODEL and a provider key (from .env). Writes evals/out/<timestamp>.json and exits 1 if a case fails.

Case file:
    {"id": "...", "description": "...", "zone": "<listing_id> | block",
     "turns": [{"prompt": "...", "expect": {<check>: <value>, ...}}, ...]}
Turns run in one session, so follow-ups see earlier turns. Checks (all optional, per turn):
    no_error: true            no `error` event (default true)
    planned: true             the concierge streamed a todo list
    subagents: [name]         each subagent was started
    tools: [name]             each tool finished at least once
    committed: true|false     a plan was (or was not) sent to the game this turn
    room_label: "living_room" the committed plan's room has this label
    categories_all: [cat]     the committed plan has every category
    categories_any: [cat]     ...at least one of them
    min_objects: n            the committed plan has at least n pieces
    min_cover: n              ...at least n with cover "half" or "full"
    valid: true               the committed plan re-validates with zero conflicts
    replaces_previous: true   same room as the previous turn's plan, different pieces or poses
    focus: "<listing_id>"     the beacon went on this listing
    answer_mentions: ["a|b"]  the final answer contains each item (any "|" alternative), case-insensitive
    max_words: n              the final answer is at most n words (judges read it on screen)
    max_seconds: s            the turn finished within s seconds (also a hard timeout, default 300)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

EVALS = Path(__file__).resolve().parent
AGENT_DIR = EVALS.parent / "apps" / "agent"
DEFAULT_TIMEOUT_S = 300
CHECKS = {
    "no_error", "planned", "subagents", "tools", "committed", "room_label", "categories_all",
    "categories_any", "min_objects", "min_cover", "valid", "replaces_previous", "focus",
    "answer_mentions", "max_words", "max_seconds",
}  # fmt: skip


# --- results --------------------------------------------------------------------------------------------


@dataclass
class TurnResult:
    prompt: str
    seconds: float = 0.0
    timed_out: bool = False
    events: list[dict] = field(default_factory=list)
    plans: list[dict] = field(default_factory=list)  # plans POSTed to the (fake) game this turn
    focus: list[str] = field(default_factory=list)
    trace: str | None = None
    run_id: str | None = None

    @property
    def answer(self) -> str:
        return "".join(e["data"]["text"] for e in self.events if e["type"] == "agent.message").strip()

    def of(self, kind: str) -> list[dict]:
        return [e["data"] for e in self.events if e["type"] == kind]


@dataclass
class Check:
    name: str
    passed: bool
    detail: str


# --- checks (pure; tests/test_evals.py covers them) -------------------------------------------------------


def _cats(plan: dict, catalog: dict[str, dict]) -> list[str]:
    return [catalog.get(o["asset_id"], {}).get("category", "?") for o in plan["objects"]]


def _layout_key(plan: dict) -> list[tuple]:
    return sorted(
        (o["asset_id"], round(o["x"], 2), round(o["z"], 2), round(o["rot_deg"])) for o in plan["objects"]
    )


def score_turn(
    expect: dict,
    turn: TurnResult,
    previous: TurnResult | None,
    catalog: dict[str, dict],
    label_of: Callable[[str], str],
    revalidate: Callable[[dict], list[dict]],
) -> list[Check]:
    """Deterministic checks of one turn. label_of(room_id) reads rooms.json; revalidate(plan) returns the
    layout validator's conflicts."""
    out: list[Check] = []
    plan = turn.plans[-1] if turn.plans else None

    def add(name: str, passed: bool, detail: str) -> None:
        out.append(Check(name, bool(passed), detail))

    def need_plan(name: str) -> bool:
        if plan is None:
            add(name, False, "nothing committed")
        return plan is not None

    errors = [e["message"] for e in turn.of("error")]
    if expect.get("no_error", True):
        detail = "timed out" if turn.timed_out else "; ".join(errors)[:200]
        add("no_error", not errors and not turn.timed_out, detail or "none")
    if "max_seconds" in expect:
        add("max_seconds", turn.seconds <= expect["max_seconds"], f"{turn.seconds:.0f}s")
    if expect.get("planned"):
        add("planned", bool(turn.of("agent.todos")), f"{len(turn.of('agent.todos'))} todo updates")
    if "subagents" in expect:
        started = {s["name"] for s in turn.of("agent.subagent") if s["status"] == "started"}
        missing = [n for n in expect["subagents"] if n not in started]
        add("subagents", not missing, f"missing {missing}" if missing else f"started {sorted(started)}")
    if "tools" in expect:
        done = {t["tool"] for t in turn.of("agent.tool") if t["status"] == "finished"}
        missing = [n for n in expect["tools"] if n not in done]
        add("tools", not missing, f"missing {missing}" if missing else "all called")
    if "committed" in expect:
        add("committed", bool(turn.plans) == expect["committed"], f"{len(turn.plans)} plans sent")
    if "room_label" in expect and need_plan("room_label"):
        label = label_of(plan["room_id"])
        add("room_label", label == expect["room_label"], f"{plan['room_id']} is {label}")
    if "categories_all" in expect and need_plan("categories_all"):
        cats = set(_cats(plan, catalog))
        missing = [c for c in expect["categories_all"] if c not in cats]
        add("categories_all", not missing, f"missing {missing}" if missing else f"has {sorted(cats)}")
    if "categories_any" in expect and need_plan("categories_any"):
        cats = set(_cats(plan, catalog))
        hit = sorted(cats & set(expect["categories_any"]))
        add("categories_any", bool(hit), f"has {hit}" if hit else f"none of them in {sorted(cats)}")
    if "min_objects" in expect and need_plan("min_objects"):
        n = len(plan["objects"])
        add("min_objects", n >= expect["min_objects"], f"{n} pieces")
    if "min_cover" in expect and need_plan("min_cover"):
        cover = [catalog.get(o["asset_id"], {}).get("cover") for o in plan["objects"]]
        n = sum(c in ("half", "full") for c in cover)
        add("min_cover", n >= expect["min_cover"], f"{n} cover pieces")
    if expect.get("valid") and need_plan("valid"):
        conflicts = revalidate(plan)
        add(
            "valid",
            not conflicts,
            f"{len(conflicts)} conflicts" + (f": {conflicts[:2]}" if conflicts else ""),
        )
    if expect.get("replaces_previous") and need_plan("replaces_previous"):
        before = previous.plans[-1] if previous and previous.plans else None
        if before is None:
            add("replaces_previous", False, "previous turn committed nothing")
        else:
            same_room = before["room_id"] == plan["room_id"]
            changed = _layout_key(before) != _layout_key(plan)
            how = "changed" if changed else "unchanged"
            detail = f"room {before['room_id']} -> {plan['room_id']}, layout {how}"
            add("replaces_previous", same_room and changed, detail)
    if "focus" in expect:
        add("focus", expect["focus"] in turn.focus, f"beacon on {turn.focus or 'nothing'}")
    if "answer_mentions" in expect:
        text = turn.answer.lower()
        missing = [m for m in expect["answer_mentions"] if not any(a.lower() in text for a in m.split("|"))]
        add("answer_mentions", not missing, f"missing {missing}" if missing else "all mentioned")
    if "max_words" in expect:
        words = len(turn.answer.split())
        add("max_words", words <= expect["max_words"], f"{words} words (max {expect['max_words']})")
    return out


# --- running ----------------------------------------------------------------------------------------------


def load_cases(names: list[str]) -> list[dict]:
    cases = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((EVALS / "cases").glob("*.json"))]
    if names:
        unknown = set(names) - {c["id"] for c in cases}
        if unknown:
            sys.exit(f"unknown case ids: {sorted(unknown)}")
        cases = [c for c in cases if c["id"] in names]
    for c in cases:  # a typo in a check name would otherwise silently check nothing
        for t in c["turns"]:
            unknown = set(t.get("expect", {})) - CHECKS
            if unknown:
                sys.exit(f"case {c['id']}: unknown checks {sorted(unknown)}")
    return cases


class FakeGame:
    """Stands in for the game service's internal API; captures what each player's agent sent."""

    def __init__(self) -> None:
        self.zones: dict[str, str] = {}  # player_id -> zone
        self.plans: dict[str, list[dict]] = {}
        self.focus: dict[str, list[str]] = {}

    def install(self, game_api) -> None:
        async def get_player(block_id, player_id):
            return {"id": player_id, "zone": self.zones.get(player_id, "block"), "x": 0.0, "y": 0.0, "z": 0.0}

        async def post_plan(block_id, player_id, plan, room_label=None):
            if plan.get("conflicts"):
                raise ValueError("refusing to commit a plan with conflicts")
            self.plans.setdefault(player_id, []).append(plan)
            return {"placed": len(plan["objects"])}

        async def post_focus(block_id, player_id, listing_id):
            self.focus.setdefault(player_id, []).append(listing_id)
            return {"ok": True}

        game_api.get_player, game_api.post_plan, game_api.post_focus = get_player, post_plan, post_focus


async def run_case(case: dict, game: FakeGame, block_id: str, env: dict[str, Any]) -> dict:
    from agent import runner
    from agent.context import AgentContext

    player = f"eval-{case['id']}"
    game.zones[player] = case.get("zone", "block")
    ctx = AgentContext(
        session_id=f"eval-{case['id']}-{int(time.time())}", player_id=player, block_id=block_id
    )
    turns: list[dict] = []
    previous: TurnResult | None = None
    for spec in case["turns"]:
        expect = spec.get("expect", {})
        result = TurnResult(prompt=spec["prompt"])
        sent_before = len(game.plans.get(player, []))
        focus_before = len(game.focus.get(player, []))

        async def emit(e: dict, result: TurnResult = result) -> None:
            result.events.append(e)

        t0 = time.monotonic()
        try:
            result.trace = await asyncio.wait_for(
                runner.run_turn(ctx, spec["prompt"], emit), expect.get("max_seconds", DEFAULT_TIMEOUT_S) * 1.5
            )
        except TimeoutError:
            result.timed_out = True
        result.seconds = time.monotonic() - t0
        result.plans = game.plans.get(player, [])[sent_before:]
        result.focus = game.focus.get(player, [])[focus_before:]
        run_id = runner.RUNS.get(ctx.session_id)
        result.run_id = str(run_id) if run_id else None
        checks = score_turn(expect, result, previous, env["catalog"], env["label_of"], env["revalidate"])
        turns.append(
            {
                "prompt": result.prompt,
                "passed": all(c.passed for c in checks),
                "seconds": round(result.seconds, 1),
                "checks": [c.__dict__ for c in checks],
                "answer": result.answer,
                "plan": result.plans[-1] if result.plans else None,
                "focus": result.focus,
                "tools": Counter(t["tool"] for t in result.of("agent.tool")),
                "trace": result.trace,
                "run_id": result.run_id,
            }
        )
        previous = result
    return {
        "id": case["id"],
        "description": case.get("description", ""),
        "passed": all(t["passed"] for t in turns),
        "turns": turns,
    }


def eval_env() -> dict[str, Any]:
    from agent import tools
    from data import packs
    from layout import validate_layout

    catalog = {a["id"]: a for a in packs.catalog()}

    def label_of(room_id: str) -> str:
        try:
            return tools.load_room(room_id)["label"]
        except Exception:
            return "?"

    def revalidate(plan: dict) -> list[dict]:
        return validate_layout(plan, tools.load_room(plan["room_id"]), tools.asset)["conflicts"]

    return {"catalog": catalog, "label_of": label_of, "revalidate": revalidate}


def send_feedback(results: list[dict]) -> None:
    """Score each turn's LangSmith trace with its checks, so the trace page shows what passed."""
    from langsmith import Client

    client = Client()
    for case in results:
        for t in case["turns"]:
            if not t["run_id"]:
                continue
            for c in t["checks"]:
                client.create_feedback(
                    t["run_id"], key=f"eval.{c['name']}", score=int(c["passed"]), comment=c["detail"]
                )
            client.create_feedback(t["run_id"], key="eval.passed", score=int(t["passed"]), comment=case["id"])
    client.flush()


def print_report(results: list[dict]) -> None:
    for case in results:
        print(f"\n{'PASS' if case['passed'] else 'FAIL'}  {case['id']}  {case['description']}")
        for t in case["turns"]:
            print(f"   > {t['prompt']}  ({t['seconds']}s)")
            for c in t["checks"]:
                print(f"     {'ok ' if c['passed'] else 'BAD'} {c['name']}: {c['detail']}")
            if t["trace"]:
                print(f"     trace: {t['trace']}")
    n = sum(c["passed"] for c in results)
    checks = [c for case in results for t in case["turns"] for c in t["checks"]]
    print(f"\n{n}/{len(results)} cases passed, {sum(c['passed'] for c in checks)}/{len(checks)} checks")


async def main_async(args: argparse.Namespace) -> int:
    import settings
    from agent import game_api

    if not settings.MODEL:
        sys.exit("MODEL is not set (provider:model); the evals run the real agent")
    cases = load_cases(args.cases)
    game = FakeGame()
    game.install(game_api)
    env = eval_env()
    block_id = args.block or settings.BLOCK_ID
    sem = asyncio.Semaphore(args.jobs)

    async def one(case: dict) -> dict:
        async with sem:
            print(f"running {case['id']} ...", flush=True)
            return await run_case(case, game, block_id, env)

    results = await asyncio.gather(*(one(c) for c in cases))
    print_report(results)
    secs = [t["seconds"] for c in results for t in c["turns"]]
    print(f"turn seconds: {secs}, total {sum(secs):.0f}s")

    out_dir = EVALS / "out"
    out_dir.mkdir(exist_ok=True)
    tag = f"-{args.tag}" if args.tag else ""
    out = out_dir / f"{datetime.now():%Y%m%d-%H%M%S}{tag}.json"
    meta = {
        "model": settings.MODEL,
        "reasoning_effort": settings.MODEL_REASONING_EFFORT or None,
        "provider_sort": settings.MODEL_PROVIDER_SORT or None,
        "tag": args.tag,
        "block_id": block_id,
        "use_fixtures": settings.USE_FIXTURES,
    }
    out.write_text(json.dumps({**meta, "cases": results}, indent=2, default=str), encoding="utf-8")
    print(f"wrote {out}")
    if args.feedback:
        send_feedback(results)
        print("feedback sent to LangSmith")
    return 0 if all(c["passed"] for c in results) else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the agent eval cases in evals/cases")
    parser.add_argument("cases", nargs="*", help="case ids (default: all)")
    parser.add_argument("-j", "--jobs", type=int, default=1, help="cases to run at once")
    parser.add_argument("--block", default=None, help="block_id (default: settings.BLOCK_ID)")
    parser.add_argument("--fixtures", action="store_true", help="read contracts/fixtures (USE_FIXTURES=1)")
    parser.add_argument("--feedback", action="store_true", help="score each turn's LangSmith trace")
    parser.add_argument("--tag", default="", help="suffix for the results file (A/B runs)")
    parser.add_argument("--list", action="store_true", help="list the cases and exit")
    args = parser.parse_args()
    if args.list:
        for c in load_cases(args.cases):
            print(f"{c['id']:28} {c.get('description', '')}")
        return
    # Before settings is imported: the evals run on the real packs unless asked otherwise.
    os.environ["USE_FIXTURES"] = "1" if args.fixtures else "0"
    sys.path.insert(0, str(AGENT_DIR))
    os.chdir(AGENT_DIR)
    sys.exit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
