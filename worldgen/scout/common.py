"""Shared paths, env loading, caching, JSONL helpers and polite HTTP for the scout steps."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import random
import tempfile
import time
from collections.abc import Callable, Iterable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar

import httpx
from dotenv import load_dotenv

# Inputs come from the main checkout (some are not committed); outputs stay in this worktree.
MAIN_CHECKOUT = Path(os.environ.get("SCOUT_MAIN_CHECKOUT", "/Users/pfu/hackprincetonf26"))
KIT = MAIN_CHECKOUT / "costar-hackathon-kit"
SPLAT_TSV = MAIN_CHECKOUT / "docs/prompts/data/splat-models.tsv"
ENV_FILE = MAIN_CHECKOUT / ".env"

WORLDGEN = Path(__file__).resolve().parents[1]
OUT = WORLDGEN / "out" / "scout"
CACHE = OUT / "cache"
DOWNLOADS = OUT / "downloads"
LEDGER = OUT / "ledger"

PORTAL = "https://beta-app.matterport.com/2026-princeton-hackathon"

MAX_CONCURRENCY = 4

# httpx logs full request URLs at INFO; Vexcel URLs carry the API key. Keep it quiet.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

T = TypeVar("T")


def load_env() -> None:
    """Load API keys from the main checkout's .env without overriding the real environment."""
    load_dotenv(ENV_FILE, override=False)


def now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def ensure_out() -> None:
    for d in (OUT, CACHE, DOWNLOADS, LEDGER):
        d.mkdir(parents=True, exist_ok=True)


# ---------- files ----------


def write_json(path: Path, obj: Any, indent: int | None = 2) -> None:
    """Atomic JSON write (tmp file + rename) so an interrupted run never leaves half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump(obj, f, indent=indent, sort_keys=False, default=_json_default)
        f.write("\n")
    os.replace(tmp, path)


def read_json(path: Path) -> Any:
    with open(path) as f:
        return json.load(f)


def read_jsonl(path: Path) -> Iterator[dict]:
    if not path.exists():
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def append_jsonl(path: Path, rows: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        for r in rows:
            f.write(json.dumps(r, default=_json_default) + "\n")


def write_jsonl(path: Path, rows: Iterable[dict]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    n = 0
    with os.fdopen(fd, "w") as f:
        for r in rows:
            f.write(json.dumps(r, default=_json_default) + "\n")
            n += 1
    os.replace(tmp, path)
    return n


def _json_default(o: Any) -> Any:
    if isinstance(o, float) and (math.isnan(o) or math.isinf(o)):
        return None
    if isinstance(o, set):
        return sorted(o)
    if isinstance(o, Path):
        return str(o)
    if hasattr(o, "item"):  # numpy scalars
        return o.item()
    raise TypeError(f"not JSON serializable: {type(o).__name__}")


# ---------- cache ----------


class Cache:
    """One JSON file per key under out/scout/cache/<namespace>/. Restarts never refetch."""

    def __init__(self, namespace: str) -> None:
        self.dir = CACHE / namespace
        self.dir.mkdir(parents=True, exist_ok=True)

    def path(self, key: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in key)
        if len(safe) > 120:
            safe = safe[:80] + "_" + hashlib.sha1(key.encode()).hexdigest()[:16]
        return self.dir / f"{safe}.json"

    def get(self, key: str) -> Any | None:
        p = self.path(key)
        if p.exists():
            return read_json(p)
        return None

    def has(self, key: str) -> bool:
        return self.path(key).exists()

    def put(self, key: str, value: Any) -> None:
        write_json(self.path(key), value, indent=None)


def stable_hash(obj: Any) -> str:
    return hashlib.sha1(json.dumps(obj, sort_keys=True).encode()).hexdigest()[:20]


# ---------- polite retries ----------

RETRY_STATUS = {429, 500, 502, 503, 504}


def with_backoff(fn: Callable[[], T], *, tries: int = 6, base: float = 1.0, what: str = "request") -> T:
    """Call fn(); on 429/5xx (httpx.HTTPStatusError) or transport errors, retry with exponential
    backoff and jitter. Error messages are re-raised without URLs (they can carry keys)."""
    for attempt in range(tries):
        try:
            return fn()
        except httpx.HTTPStatusError as e:
            code = e.response.status_code
            if code not in RETRY_STATUS or attempt == tries - 1:
                raise RuntimeError(f"{what}: HTTP {code}") from None
            retry_after = e.response.headers.get("retry-after")
            delay = float(retry_after) if retry_after and retry_after.isdigit() else base * 2**attempt
        except httpx.TransportError as e:
            if attempt == tries - 1:
                raise RuntimeError(f"{what}: transport error {type(e).__name__}") from None
            delay = base * 2**attempt
        time.sleep(delay + random.uniform(0, 0.5))
    raise AssertionError("unreachable")


# ---------- geo ----------

EARTH_R = 6_371_008.8  # mean Earth radius, meters


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R * math.asin(math.sqrt(a))


SQFT_TO_M2 = 0.09290304
ACRE_TO_M2 = 4046.8564224
