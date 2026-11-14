"""Summarize our Vexcel spend from the local call ledger (out/scout/ledger/vexcel_calls.jsonl).

uv run python -m worldgen.scout.vexcel_usage [--snapshot <label>]

The account's own usage endpoints are forbidden for our role (REST /usage/query -> 403, MCP
usage_query -> "Forbidden by role permissions", verified 2026-11-14), so the ledger is the only
record. It counts every network request (cache hits are never recorded). `--snapshot` appends the
summary to ledger/vexcel_usage_snapshots.jsonl so PROGRESS.md can cite it per phase.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter

from worldgen.scout.common import LEDGER, append_jsonl, now_iso, read_jsonl

CALLS = LEDGER / "vexcel_calls.jsonl"
SNAPSHOTS = LEDGER / "vexcel_usage_snapshots.jsonl"

# Extract endpoints that return imagery; the brief caps these at 40 small crops.
IMAGE_PATHS = ("ortho/extract", "oriented/extract")


def summarize() -> dict:
    rest = Counter()
    rest_ok = Counter()
    mcp = Counter()
    retries = 0
    for r in read_jsonl(CALLS):
        if r.get("kind") == "rest":
            rest[r["path"]] += 1
            if r.get("retry"):
                retries += 1
            if r.get("status") == 200:
                rest_ok[r["path"]] += 1
        elif r.get("kind") == "mcp":
            mcp[r["tool"]] += 1
    return {
        "at": now_iso(),
        "rest_calls": sum(rest.values()),
        "rest_calls_200": sum(rest_ok.values()),
        "rest_retries": retries,
        "rest_by_path": dict(rest),
        "image_extracts": sum(v for k, v in rest_ok.items() if k.strip("/") in IMAGE_PATHS),
        "mcp_tool_calls": sum(mcp.values()),
        "mcp_by_tool": dict(mcp),
        "total_calls": sum(rest.values()) + sum(mcp.values()),
        "units_visible": False,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--snapshot", default=None, help="label, e.g. 'end of phase 0'")
    args = p.parse_args(argv)
    s = summarize()
    if args.snapshot:
        s["label"] = args.snapshot
        append_jsonl(SNAPSHOTS, [s])
    print(json.dumps(s, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
