"""Lane A: read-only access to the listings index and world-pack JSON (never meshes).

Where data comes from, in order:
  1. USE_FIXTURES=1 -> contracts/fixtures/
  2. PACK_DIR (worldgen/out) if the file exists there (laptop, after running worldgen)
  3. PACK_BASE_URL over HTTPS (Render: the published bucket, same layout as worldgen/out)

Layout (same under PACK_DIR and PACK_BASE_URL):
  listings/index.json
  packs/<block_id>/v<PACK_VERSION>/houses/<listing_id>/{rooms,graph,audit}.json
  furniture/v<FURNITURE_VERSION>/catalog.json   shared by every block (contracts v2.3.0)

Each file is fetched once and kept in memory (JSON only; the meshes are for the browser).
Tool wrappers in agent/tools.py call these behind the AGENTS.md section 10 signatures.
"""

from __future__ import annotations

import json
import re
from functools import cache
from pathlib import Path

import httpx

import settings


class PackFileMissing(KeyError):
    """A pack file is in neither PACK_DIR nor PACK_BASE_URL."""


def _read(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


@cache
def _read_pack(rel: str) -> object:
    """Read a JSON file by its path under the published root (furniture/v1/catalog.json): local, then HTTP."""
    local = settings.PACK_DIR / rel
    if local.is_file():
        return _read(local)
    if not settings.PACK_BASE_URL:
        raise PackFileMissing(f"{rel}: not in {settings.PACK_DIR} and PACK_BASE_URL is not set")
    url = f"{settings.PACK_BASE_URL}/{rel}"
    r = httpx.get(url, timeout=30, follow_redirects=True)
    if r.status_code in (403, 404):  # S3 answers 403 for missing keys on a public-read bucket
        raise PackFileMissing(f"{rel}: not found at {url} ({r.status_code})")
    r.raise_for_status()
    return r.json()


def clear_cache() -> None:
    """Forget everything read so far (tests, or after a republish)."""
    for f in (_read_pack, listings, catalog, _catalog_index):
        f.cache_clear()


class PackVersionUnknown(RuntimeError):
    """Neither PACK_VERSION nor a pinned live_pack_version says which pack to read."""


def pack_version(block_id: str) -> int:
    """PACK_VERSION if set; else the live version pinned in worldgen/blocks/<block>.json. Never guesses."""
    if settings.PACK_VERSION is not None:
        return settings.PACK_VERSION
    cfg = settings.BLOCKS_DIR / f"{block_id}.json"
    live = _read(cfg).get("live_pack_version") if cfg.is_file() else None
    if not isinstance(live, int):
        raise PackVersionUnknown(
            f"set PACK_VERSION, or pin a live version in {cfg} (uv run python -m worldgen.versions pin ...)"
        )
    return live


def furniture_version() -> int:
    """FURNITURE_VERSION if set; else the version the live pack manifest names (furniture/v<N>/...)."""
    if settings.FURNITURE_VERSION is not None:
        return settings.FURNITURE_VERSION
    manifest = _read_pack(f"{pack_root(settings.BLOCK_ID)}/manifest.json")
    m = re.fullmatch(r"furniture/v(\d+)/catalog\.json", str(manifest.get("furniture_catalog", "")))
    if not m:
        raise PackVersionUnknown(
            f"{pack_root(settings.BLOCK_ID)}/manifest.json names no furniture/v<N>/catalog.json"
        )
    return int(m.group(1))


def pack_root(block_id: str) -> str:
    return f"packs/{block_id}/v{pack_version(block_id)}"


@cache
def listings() -> list[dict]:
    if settings.USE_FIXTURES:
        return [_read(settings.FIXTURES_DIR / "listing.json")]
    return _read_pack("listings/index.json")


def get_listing(listing_id: str) -> dict | None:
    return next((x for x in listings() if x["id"] == listing_id), None)


def _house_file(listing_id: str, name: str) -> object:
    listing = get_listing(listing_id)
    if listing is None or not listing.get("block_id"):
        raise KeyError(f"{listing_id} is not on a built block")
    return _read_pack(f"{pack_root(listing['block_id'])}/houses/{listing_id}/{name}")


def get_rooms(listing_id: str) -> list[dict]:
    if settings.USE_FIXTURES:
        return [_read(settings.FIXTURES_DIR / "room.json")]
    return _house_file(listing_id, "rooms.json")


def get_house_graph(listing_id: str) -> dict:
    if settings.USE_FIXTURES:
        return _read(settings.FIXTURES_DIR / "graph.json")
    return _house_file(listing_id, "graph.json")


def get_audit(listing_id: str) -> dict:
    if settings.USE_FIXTURES:
        return _read(settings.FIXTURES_DIR / "audit.json")
    return _house_file(listing_id, "audit.json")


@cache
def catalog() -> list[dict]:
    """Furniture library entries (Asset contract), the same for every block. Never the .glb files."""
    if settings.USE_FIXTURES:
        return [_read(settings.FIXTURES_DIR / "asset.json")]
    return _read_pack(f"furniture/v{furniture_version()}/catalog.json")


@cache
def _catalog_index() -> dict[str, dict]:
    return {a["id"]: a for a in catalog()}


def get_asset(asset_id: str) -> dict | None:
    return _catalog_index().get(asset_id)
