"""Client for the game service internal API (contracts/messages.md). Lane B calls Lane D's API."""

from __future__ import annotations

import httpx

import settings


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=settings.GAME_URL,
        headers={"X-Internal-Secret": settings.GAME_INTERNAL_SECRET},
        timeout=10,
    )


async def post_plan(block_id: str, player_id: str, plan: dict) -> dict:
    if plan.get("conflicts"):
        raise ValueError("refusing to commit a plan with conflicts")
    async with _client() as c:
        r = await c.post(
            f"/internal/rooms/{block_id}/houses/{plan['listing_id']}/plan",
            json={"player_id": player_id, "plan": plan},
        )
        r.raise_for_status()
        return r.json()


async def post_focus(block_id: str, player_id: str, listing_id: str) -> dict:
    async with _client() as c:
        r = await c.post(
            f"/internal/rooms/{block_id}/focus", json={"player_id": player_id, "listing_id": listing_id}
        )
        r.raise_for_status()
        return r.json()


async def get_player(block_id: str, player_id: str) -> dict:
    async with _client() as c:
        r = await c.get(f"/internal/rooms/{block_id}/players/{player_id}")
        r.raise_for_status()
        return r.json()
