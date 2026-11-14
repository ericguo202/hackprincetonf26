"""Validate contract fixtures, and optionally a world pack, against contracts/*.schema.json.

Fixtures only (from the repo root):   uv run python contracts/validate.py
A pack and its listings index:        uv run python contracts/validate.py worldgen/out/packs/hou_heights_01

A pack path may be packs/<block_id> (every v<N> inside is checked) or packs/<block_id>/v<N>.
The listings index is looked up at <out>/listings/index.json next to packs/.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

CONTRACTS = Path(__file__).resolve().parent
SCHEMAS = ("listing", "room", "graph", "audit", "asset", "placement_plan", "manifest")

AGENT_EVENTS = {
    "agent.todos",
    "agent.subagent",
    "agent.tool",
    "agent.message",
    "listings.results",
    "audit.findings",
    "error",
}


def load_validator(name: str) -> Draft202012Validator:
    schema = json.loads((CONTRACTS / f"{name}.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def read(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


class Checker:
    def __init__(self) -> None:
        self.v = {n: load_validator(n) for n in SCHEMAS}
        self.errors: list[str] = []
        self.checked = 0

    def one(self, schema: str, doc: object, where: str) -> None:
        self.checked += 1
        for err in self.v[schema].iter_errors(doc):
            path = "/".join(str(p) for p in err.absolute_path) or "<root>"
            self.errors.append(f"{where}: {path}: {err.message}")

    def many(self, schema: str, docs: object, where: str) -> None:
        if not isinstance(docs, list):
            self.checked += 1
            self.errors.append(f"{where}: expected a JSON array")
            return
        for i, doc in enumerate(docs):
            self.one(schema, doc, f"{where}[{i}]")

    def fixtures(self) -> None:
        fx = CONTRACTS / "fixtures"
        for name in SCHEMAS:
            self.one(name, read(fx / f"{name}.json"), f"fixtures/{name}.json")
        for n, line in enumerate((fx / "agent.session.jsonl").read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            where = f"fixtures/agent.session.jsonl:{n}"
            event = json.loads(line)
            self.checked += 1
            if set(event) != {"type", "data"}:
                self.errors.append(f"{where}: envelope must have exactly 'type' and 'data'")
                continue
            if event["type"] not in AGENT_EVENTS:
                self.errors.append(f"{where}: unknown event type {event['type']!r}")
            if event["type"] == "audit.findings":
                self.one("audit", event["data"], where)

    def pack(self, root: Path) -> None:
        versions = sorted(p for p in root.glob("v*") if p.is_dir()) if not root.name.startswith("v") else [root]
        if not versions:
            self.errors.append(f"{root}: no v<N> folders")
        for vdir in versions:
            manifest = vdir / "manifest.json"
            if manifest.exists():
                self.one("manifest", read(manifest), str(manifest))
            else:
                self.errors.append(f"{manifest}: missing")
            for house in sorted((vdir / "houses").glob("*")):
                for name, schema, is_list in (("rooms.json", "room", True), ("graph.json", "graph", False),
                                              ("audit.json", "audit", False)):
                    f = house / name
                    if f.exists():
                        (self.many if is_list else self.one)(schema, read(f), str(f))
                for plan in sorted((house / "plans").glob("*.json")):
                    self.one("placement_plan", read(plan), str(plan))
            catalog = vdir / "furniture" / "catalog.json"
            if catalog.exists():
                self.many("asset", read(catalog), str(catalog))
        # out/packs/<block_id>[/v<N>] -> out/listings/index.json
        out = root.parents[2] if root.name.startswith("v") else root.parents[1]
        index = out / "listings" / "index.json"
        if index.exists():
            self.many("listing", read(index), str(index))


def main(argv: list[str]) -> int:
    c = Checker()
    c.fixtures()
    for arg in argv:
        c.pack(Path(arg).resolve())
    for e in c.errors:
        print(f"FAIL {e}")
    version = (CONTRACTS / "VERSION").read_text().strip()
    print(f"contracts v{version}: {c.checked} documents checked, {len(c.errors)} errors")
    return 1 if c.errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
