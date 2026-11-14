"""Env-driven settings shared by all server lanes (AGENTS.md section 9)."""

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

CONTRACTS_DIR = REPO_ROOT / "contracts"
FIXTURES_DIR = CONTRACTS_DIR / "fixtures"
DATA_DIR = REPO_ROOT / "data"
ASSETS_DIR = REPO_ROOT / "assets"
