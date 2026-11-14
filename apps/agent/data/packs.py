"""Lane A: read-only access to the listings index and world-pack JSON (never meshes).

USE_FIXTURES=1 serves contracts/fixtures/; otherwise reads PACK_DIR (worldgen/out).
Tool wrappers in agent/tools.py call these behind the AGENTS.md section 10 signatures.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path

import settings


def _read(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


@cache
def listings() -> list[dict]:
    if settings.USE_FIXTURES:
        return [_read(settings.FIXTURES_DIR / "listing.json")]
    return _read(settings.PACK_DIR / "listings" / "index.json")


def get_listing(listing_id: str) -> dict | None:
    return next((x for x in listings() if x["id"] == listing_id), None)


def _house_file(listing_id: str, name: str) -> Path:
    listing = get_listing(listing_id)
    if listing is None or not listing.get("block_id"):
        raise KeyError(f"{listing_id} is not on a built block")
    packs = sorted((settings.PACK_DIR / "packs" / listing["block_id"]).glob("v*"))
    if not packs:
        raise KeyError(f"no pack for block {listing['block_id']}")
    return packs[-1] / "houses" / listing_id / name


def get_rooms(listing_id: str) -> list[dict]:
    if settings.USE_FIXTURES:
        return [_read(settings.FIXTURES_DIR / "room.json")]
    return _read(_house_file(listing_id, "rooms.json"))


def get_house_graph(listing_id: str) -> dict:
    if settings.USE_FIXTURES:
        return _read(settings.FIXTURES_DIR / "graph.json")
    return _read(_house_file(listing_id, "graph.json"))


def get_audit(listing_id: str) -> dict:
    if settings.USE_FIXTURES:
        return _read(settings.FIXTURES_DIR / "audit.json")
    return _read(_house_file(listing_id, "audit.json"))


@cache
def catalog() -> list[dict]:
    if settings.USE_FIXTURES:
        return [_read(settings.FIXTURES_DIR / "asset.json")]
    packs = sorted((settings.PACK_DIR / "packs").glob("*/v*/furniture/catalog.json"))
    return _read(packs[-1]) if packs else []
