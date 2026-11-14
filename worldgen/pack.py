"""Shared pack layout helpers: block configs and manifest.json (contracts/manifest.schema.json, DRAFT)."""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
BLOCKS = HERE / "blocks"

# Shared furniture library the pack uses (contracts v2.3.0): relative to the published root, not the pack.
FURNITURE_VERSION = 1
FURNITURE_CATALOG = f"furniture/v{FURNITURE_VERSION}/catalog.json"


def load_block(block_id: str) -> dict:
    """worldgen/blocks/<block_id>.json: block centre and the houses on it (listing id -> Matterport model)."""
    return json.loads((BLOCKS / f"{block_id}.json").read_text(encoding="utf-8"))


def pack_dir(out: Path, block_id: str, version: int) -> Path:
    return out / "packs" / block_id / f"v{version}"


def house_entry(listing_id: str) -> dict:
    base = f"houses/{listing_id}"
    return {
        "listing_id": listing_id,
        "interior": f"{base}/interior.glb",
        "collision": f"{base}/collision.glb",
        "rooms": f"{base}/rooms.json",
        "graph": f"{base}/graph.json",
        "audit": f"{base}/audit.json",
    }


def update_manifest(
    out: Path, block_id: str, version: int, center: dict, listing_id: str | None = None
) -> Path:
    """Create or update the pack manifest; adds/replaces one house entry. Block paths are the fixed layout
    from AGENTS.md section 7 (build_block writes those files)."""
    path = pack_dir(out, block_id, version) / "manifest.json"
    if path.exists():
        manifest = json.loads(path.read_text(encoding="utf-8"))
    else:
        manifest = {
            "block_id": block_id,
            "version": version,
            "origin": {"lat": center["lat"], "lon": center["lon"]},
            "block": {"render": "block/render.glb", "collision": "block/collision.glb"},
            "houses": [],
            "spawns": [],
        }
    # Always (re)set: manifests written before v2.3.0 said furniture/catalog.json inside the pack.
    manifest["furniture_catalog"] = FURNITURE_CATALOG
    if listing_id:
        houses = [h for h in manifest["houses"] if h["listing_id"] != listing_id]
        manifest["houses"] = sorted([*houses, house_entry(listing_id)], key=lambda h: h["listing_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return path
