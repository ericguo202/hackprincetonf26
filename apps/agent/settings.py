"""Env-driven settings for the agent service (AGENTS.md section 12)."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(REPO_ROOT / ".env")


def flag(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


USE_FIXTURES = flag("USE_FIXTURES")
MOCK_AGENT = flag("MOCK_AGENT")
FAKE_PLACEMENT = flag("FAKE_PLACEMENT")
HITL = flag("HITL")
MODEL = os.getenv("MODEL", "")

# World packs: a local folder (worldgen/out) or the published base URL.
PACK_DIR = REPO_ROOT / os.getenv("PACK_DIR", "worldgen/out")  # relative paths are from the repo root
# Published root that mirrors worldgen/out: {PACK_BASE_URL}/listings/index.json,
# {PACK_BASE_URL}/packs/<block_id>/v<N>/... Used for any file not found under PACK_DIR.
PACK_BASE_URL = os.getenv("PACK_BASE_URL", "").rstrip("/")
# Pack version to read. Over HTTP there is no folder listing, so set it when PACK_BASE_URL is used;
# locally it defaults to the newest v<N> folder in PACK_DIR.
PACK_VERSION = int(os.environ["PACK_VERSION"]) if os.getenv("PACK_VERSION") else None
# Block whose furniture catalog the agent uses (one block for the hackathon).
BLOCK_ID = os.getenv("BLOCK_ID", "hou_heights_01")

# Game service internal API (contracts/messages.md).
# On Render, GAME_HOSTPORT comes from `fromService` (private network, "host:port"); GAME_URL overrides it.
GAME_URL = os.getenv("GAME_URL") or (
    f"http://{os.environ['GAME_HOSTPORT']}" if os.getenv("GAME_HOSTPORT") else "http://localhost:2567"
)
GAME_INTERNAL_SECRET = os.getenv("GAME_INTERNAL_SECRET", "")

CONTRACTS_DIR = REPO_ROOT / "contracts"
FIXTURES_DIR = CONTRACTS_DIR / "fixtures"
SKILLS_DIR = Path(__file__).resolve().parent / "skills"
