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

# LangSmith tracing is automatic for LangChain/Deep Agents once these env vars are set (load_dotenv
# above runs before any langchain import). Traces go to the agent + environment (agent-based workspace).
LANGSMITH_AGENT_ID = os.getenv("LANGSMITH_AGENT_ID", "")
LANGSMITH_AGENT_ENVIRONMENT = os.getenv("LANGSMITH_AGENT_ENVIRONMENT", "")
if (LANGSMITH_AGENT_ID or LANGSMITH_AGENT_ENVIRONMENT) and any(
    os.getenv(k) for k in ("LANGSMITH_PROJECT", "LANGCHAIN_PROJECT", "LANGCHAIN_SESSION")
):
    # The SDK refuses to trace when it has two destinations; fail loudly instead of silently.
    raise RuntimeError("Unset LANGSMITH_PROJECT: it conflicts with LANGSMITH_AGENT_ID/ENVIRONMENT")
if bool(LANGSMITH_AGENT_ID) != bool(LANGSMITH_AGENT_ENVIRONMENT):
    raise RuntimeError("Set both LANGSMITH_AGENT_ID and LANGSMITH_AGENT_ENVIRONMENT, or neither")

# World packs: a local folder (worldgen/out) or the published base URL.
PACK_DIR = REPO_ROOT / os.getenv("PACK_DIR", "worldgen/out")  # relative paths are from the repo root
# Published root that mirrors worldgen/out: {PACK_BASE_URL}/listings/index.json,
# {PACK_BASE_URL}/packs/<block_id>/v<N>/..., {PACK_BASE_URL}/furniture/v<N>/catalog.json.
# Used for any file not found under PACK_DIR.
PACK_BASE_URL = os.getenv("PACK_BASE_URL", "").rstrip("/")
BLOCKS_DIR = REPO_ROOT / "worldgen" / "blocks"  # committed block configs holding live_pack_version
# Versions to read. Set PACK_VERSION on Render. When it is unset, the pack version is the block's LIVE
# version pinned in git (worldgen/blocks/<block>.json `live_pack_version`, see worldgen/versions.py), never
# "the newest folder", so a test build in worldgen/out can't be read by accident. FURNITURE_VERSION, when
# unset, comes from that pack's manifest (`furniture_catalog`). The library is shared by every block.
PACK_VERSION = int(os.environ["PACK_VERSION"]) if os.getenv("PACK_VERSION") else None
FURNITURE_VERSION = int(os.environ["FURNITURE_VERSION"]) if os.getenv("FURNITURE_VERSION") else None
# The block the game runs (one block for the hackathon).
BLOCK_ID = os.getenv("BLOCK_ID", "hou_navidad_01")

# Game service internal API (contracts/messages.md).
# On Render, GAME_HOSTPORT comes from `fromService` (private network, "host:port"); GAME_URL overrides it.
GAME_URL = os.getenv("GAME_URL") or (
    f"http://{os.environ['GAME_HOSTPORT']}" if os.getenv("GAME_HOSTPORT") else "http://localhost:2567"
)
GAME_INTERNAL_SECRET = os.getenv("GAME_INTERNAL_SECRET", "")

CONTRACTS_DIR = REPO_ROOT / "contracts"
FIXTURES_DIR = CONTRACTS_DIR / "fixtures"
SKILLS_DIR = Path(__file__).resolve().parent / "skills"
