"""Validate contract fixtures and lane data files against contracts/*.schema.json.

Run from the repo root: uv run python contracts/validate.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

CONTRACTS = Path(__file__).resolve().parent
ROOT = CONTRACTS.parent

EVENT_TYPES = {
    "user.message",
    "user.decision",
    "agent.todos",
    "agent.subagent",
    "agent.tool",
    "agent.message",
    "agent.approval",
    "listings.results",
    "world.focus",
    "placement.plan",
    "error",
}


def load_validator(name: str) -> Draft202012Validator:
    schema = json.loads((CONTRACTS / f"{name}.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def check(validator: Draft202012Validator, doc: object, where: str, errors: list[str]) -> None:
    for err in validator.iter_errors(doc):
        path = "/".join(str(p) for p in err.absolute_path) or "<root>"
        errors.append(f"{where}: {path}: {err.message}")


def check_many(validator: Draft202012Validator, docs: object, where: str, errors: list[str]) -> None:
    if not isinstance(docs, list):
        errors.append(f"{where}: expected a JSON array")
        return
    for i, doc in enumerate(docs):
        check(validator, doc, f"{where}[{i}]", errors)


def main() -> int:
    v = {n: load_validator(n) for n in ("listing", "room", "asset", "placement_plan")}
    errors: list[str] = []
    checked = 0

    fx = CONTRACTS / "fixtures"
    for name in v:
        check(v[name], json.loads((fx / f"{name}.json").read_text(encoding="utf-8")), f"fixtures/{name}.json", errors)
        checked += 1

    transcript = fx / "events.session.jsonl"
    for n, line in enumerate(transcript.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        where = f"fixtures/events.session.jsonl:{n}"
        event = json.loads(line)
        if set(event) != {"type", "data"}:
            errors.append(f"{where}: envelope must have exactly 'type' and 'data'")
            continue
        if event["type"] not in EVENT_TYPES:
            errors.append(f"{where}: unknown event type {event['type']!r}")
        if event["type"] == "placement.plan":
            check(v["placement_plan"], event["data"], where, errors)
        checked += 1

    # Lane D data files, when present.
    listings = ROOT / "data" / "listings.json"
    if listings.exists():
        check_many(v["listing"], json.loads(listings.read_text(encoding="utf-8")), "data/listings.json", errors)
        checked += 1
    for rooms in sorted((ROOT / "data" / "houses").glob("*/rooms.json")):
        check_many(v["room"], json.loads(rooms.read_text(encoding="utf-8")), str(rooms.relative_to(ROOT)), errors)
        checked += 1
    for plan in sorted((ROOT / "data" / "houses").glob("*/plans/*.json")):
        check(v["placement_plan"], json.loads(plan.read_text(encoding="utf-8")), str(plan.relative_to(ROOT)), errors)
        checked += 1
    catalog = ROOT / "assets" / "furniture" / "catalog.json"
    if catalog.exists():
        check_many(v["asset"], json.loads(catalog.read_text(encoding="utf-8")), "assets/furniture/catalog.json", errors)
        checked += 1

    for e in errors:
        print(f"FAIL {e}")
    print(f"contracts v{(CONTRACTS / 'VERSION').read_text().strip()}: {checked} files checked, {len(errors)} errors")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
