"""CLI runner for the concierge agent. Lane B owns this file.

uv run python -m agent.cli "Furnish the living room for a family that watches movies" --in-house hou_236595179
uv run python -m agent.cli "We're new parents with an infant. Find us a home to buy under 450k."

Prints the SSE events one per line. Several prompts run as one session (follow-ups see earlier turns):
uv run python -m agent.cli "Furnish the living room for movie nights" "Make it a playroom instead" --offline
Without a game server the game calls come back as tool errors. --in-house <listing_id> stands in for the
player's zone; --offline also fakes commits and beacons so a whole flow runs on a laptop.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid

from agent import game_api, runner
from agent.context import AgentContext


def fake_game(zone: str, offline: bool) -> None:
    async def get_player(block_id, player_id):
        return {"id": player_id, "zone": zone, "x": 0.0, "y": 0.0, "z": 0.0}

    game_api.get_player = get_player
    if offline:

        async def post_plan(block_id, player_id, plan, room_label=None):
            return {"placed": len(plan["objects"])}

        async def post_focus(block_id, player_id, listing_id):
            return {"ok": True}

        game_api.post_plan, game_api.post_focus = post_plan, post_focus


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the concierge agent without the web app")
    parser.add_argument("prompts", nargs="+")
    parser.add_argument("--block", default="hou_navidad_01")
    parser.add_argument("--player", default="cli")
    parser.add_argument("--in-house", default=None, help="fake the player's zone (a listing_id)")
    parser.add_argument(
        "--offline", action="store_true", help="fake the game API: commits and beacons succeed"
    )
    args = parser.parse_args()
    if args.in_house or args.offline:
        fake_game(args.in_house or "block", args.offline)

    ctx = AgentContext(session_id=f"cli-{uuid.uuid4().hex[:8]}", player_id=args.player, block_id=args.block)

    async def emit(event: dict) -> None:
        if event["type"] == "agent.message":
            print(event["data"]["text"], end="\n" if event["data"]["final"] else "", flush=True)
        else:
            print(f"[{event['type']}] {json.dumps(event['data'], ensure_ascii=False)[:300]}", flush=True)

    async def run() -> None:
        for prompt in args.prompts:
            print(f"\n> {prompt}")
            await runner.run_turn(ctx, prompt, emit)

    asyncio.run(run())


if __name__ == "__main__":
    main()
