"""Which pack version is live, and the only way to change it.

Every block has exactly one LIVE pack version, recorded in git in worldgen/blocks/<block>.json as
`live_pack_version` (with a `pack_history` of every change and why). Nothing picks "the newest folder":
publish, audit, costar and the agent all read the live version, and builders refuse to write into it.
Experiments build into any other v<N>; a version only becomes live through `pin`, which is a reviewed,
committed change. So the house map shown anywhere is always the one named in git, and a stray test build
can never be shipped or read by accident.

  uv run python -m worldgen.versions status hou_navidad_01
  uv run python -m worldgen.versions pin hou_navidad_01 --version 2 \\
      --compared-to-live "7014 score 14.07 -> 13.2, 6915 14.76 -> 14.1; walked both; no regressions" \\
      --reason "interior: sharper wall seams (lane-a/house-quality) + scanned lot (lane-a/outdoor)"

Promotion rule (docs/house-pipeline-handoff.md, "Pack versions"): a candidate replaces the live version only
when it builds on the live one: metrics mostly better, nothing clearly worse, no earlier gain lost.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BLOCKS = HERE / "blocks"
OUT = HERE / "out"
REPO = HERE.parent

HOUSE_FILES = ("interior.glb", "collision.glb", "rooms.json", "graph.json")


def block_config_path(block_id: str, blocks: Path | None = None) -> Path:
    return (blocks or BLOCKS) / f"{block_id}.json"


def block_config(block_id: str, blocks: Path | None = None) -> dict:
    path = block_config_path(block_id, blocks)
    if not path.is_file():
        raise SystemExit(f"no block config {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def live_version(block_id: str, blocks: Path | None = None) -> int:
    """The live pack version from worldgen/blocks/<block>.json. Never guesses."""
    v = block_config(block_id, blocks).get("live_pack_version")
    if not isinstance(v, int):
        raise SystemExit(
            f"{block_config_path(block_id, blocks)} has no live_pack_version; "
            f"pin one: uv run python -m worldgen.versions pin {block_id} --version N ..."
        )
    return v


def live_version_or_none(block_id: str, blocks: Path | None = None) -> int | None:
    path = block_config_path(block_id, blocks)
    if not path.is_file():
        return None
    v = json.loads(path.read_text(encoding="utf-8")).get("live_pack_version")
    return v if isinstance(v, int) else None


def pack_dir(block_id: str, version: int, out: Path = OUT) -> Path:
    return out / "packs" / block_id / f"v{version}"


def live_pack_dir(block_id: str, out: Path = OUT, blocks: Path | None = None) -> Path:
    d = pack_dir(block_id, live_version(block_id, blocks), out)
    if not d.is_dir():
        raise SystemExit(
            f"live pack {d} is missing on this machine (pinned in {block_config_path(block_id, blocks)})"
        )
    return d


def refuse_live(block_id: str, version: int, what: str, blocks: Path | None = None) -> None:
    """Builders call this before writing packs/<block>/v<version>: the live version is never rebuilt."""
    live = live_version_or_none(block_id, blocks)
    if live is not None and version == live:
        raise SystemExit(
            f"refusing to {what} into packs/{block_id}/v{version}: it is the LIVE version "
            f"(worldgen/blocks/{block_id}.json). Build into a new version, compare it with v{live}, "
            f"then `python -m worldgen.versions pin`."
        )


def live_furniture_versions(out: Path = OUT, blocks: Path | None = None) -> set[int]:
    """Furniture library versions named by any block's live pack manifest (these must never be rebuilt)."""
    used: set[int] = set()
    for cfg in (blocks or BLOCKS).glob("*.json"):
        block_id = cfg.stem
        live = live_version_or_none(block_id, blocks)
        if live is None:
            continue
        manifest = pack_dir(block_id, live, out) / "manifest.json"
        if manifest.is_file():
            rel = json.loads(manifest.read_text(encoding="utf-8")).get("furniture_catalog") or ""
            parts = rel.split("/")
            if len(parts) >= 2 and parts[0] == "furniture" and parts[1][1:].isdigit():
                used.add(int(parts[1][1:]))
    return used


def refuse_live_furniture(version: int, out: Path = OUT, blocks: Path | None = None) -> None:
    if version in live_furniture_versions(out, blocks):
        raise SystemExit(
            f"refusing to write furniture/v{version}: a live pack manifest uses it. "
            f"Build furniture/v<N+1> instead and point a new pack version at it."
        )


# ---------------------------------------------------------------- checks


def problems(block_id: str, version: int, out: Path = OUT, blocks: Path | None = None) -> list[str]:
    """What is missing or broken in packs/<block>/v<version> for it to be live (empty list = fine)."""
    d = pack_dir(block_id, version, out)
    if not d.is_dir():
        return [f"{d} does not exist"]
    found = []
    if not (d / "manifest.json").is_file():
        found.append("manifest.json missing")
    for house in block_config(block_id, blocks).get("houses", []):
        lid = house["listing_id"]
        for name in HOUSE_FILES:
            if not (d / "houses" / lid / name).is_file():
                found.append(f"houses/{lid}/{name} missing")
        if not (d / "houses" / lid / "audit.json").is_file():
            found.append(
                f"houses/{lid}/audit.json missing (run worldgen.audit --block {block_id} --version {version})"
            )
    return found


def validate(block_id: str, version: int, out: Path = OUT) -> tuple[bool, str]:
    r = subprocess.run(
        [sys.executable, str(REPO / "contracts" / "validate.py"), str(pack_dir(block_id, version, out))],
        capture_output=True,
        text=True,
        cwd=HERE,
    )
    return r.returncode == 0, (r.stdout + r.stderr).strip()


# ---------------------------------------------------------------- commands


def status(block_id: str, out: Path = OUT, blocks: Path | None = None) -> str:
    live = live_version_or_none(block_id, blocks)
    root = out / "packs" / block_id
    found = sorted(int(p.name[1:]) for p in root.glob("v*") if p.is_dir() and p.name[1:].isdigit())
    lines = [f"{block_id}: live pack version = {f'v{live}' if live is not None else 'NOT PINNED'}"]
    for v in found:
        d = pack_dir(block_id, v, out)
        when = dt.datetime.fromtimestamp(d.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        issues = problems(block_id, v, out, blocks)
        tag = "LIVE " if v == live else "     "
        lines.append(f"  {tag}v{v:<4} modified {when}  {'ok' if not issues else '; '.join(issues)}")
    if live is not None and live not in found:
        lines.append(f"  WARNING: live v{live} is not on this machine")
    for h in block_config(block_id, blocks).get("pack_history", []):
        lines.append(
            f"  history: v{h['version']} pinned {h['date']} (was {h.get('previous')}): {h['reason']}"
        )
    return "\n".join(lines)


def pin(
    block_id: str,
    version: int,
    reason: str,
    compared_to_live: str,
    out: Path = OUT,
    blocks: Path | None = None,
    skip_validate: bool = False,
) -> dict:
    cfg_path = block_config_path(block_id, blocks)
    cfg = block_config(block_id, blocks)
    previous = cfg.get("live_pack_version")
    if previous == version:
        raise SystemExit(f"v{version} is already live")
    issues = problems(block_id, version, out, blocks)
    if issues:
        raise SystemExit(f"cannot pin v{version}:\n  " + "\n  ".join(issues))
    if previous is not None and not compared_to_live.strip():
        raise SystemExit(
            f"say how v{version} compares with the live v{previous} on every house (--compared-to-live). "
            "A candidate should build on the live version: mostly better, nothing clearly worse."
        )
    if not skip_validate:
        ok, log = validate(block_id, version, out)
        if not ok:
            raise SystemExit(f"contracts/validate.py failed on v{version}:\n{log}")
    entry = {
        "version": version,
        "previous": previous,
        "date": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "reason": reason,
        "compared_to_live": compared_to_live,
    }
    cfg["live_pack_version"] = version
    cfg.setdefault("pack_history", []).append(entry)
    cfg_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return entry


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("status", help="list pack versions on this machine and which one is live")
    s.add_argument("block")
    q = sub.add_parser("pin", help="make a version live (edits worldgen/blocks/<block>.json; commit it)")
    q.add_argument("block")
    q.add_argument("--version", type=int, required=True)
    q.add_argument("--reason", required=True, help="what changed in this version")
    q.add_argument(
        "--compared-to-live",
        default="",
        help="how it compares with the live version per house (scores, viewer check); no clear regressions",
    )
    q.add_argument("--skip-validate", action="store_true")
    a = p.parse_args(argv)
    if a.cmd == "status":
        print(status(a.block))
        return 0
    entry = pin(a.block, a.version, a.reason, a.compared_to_live, skip_validate=a.skip_validate)
    print(
        f"{a.block}: v{entry['version']} is now live (was {entry['previous']}). Next:\n"
        f"  1. commit worldgen/blocks/{a.block}.json\n"
        f"  2. uv run python -m worldgen.publish {a.block} (--local for dev)\n"
        f"  3. tell lane D to set PACK_VERSION={entry['version']} on Render, and lane C to use "
        f"VITE_PACK_BASE_URL=.../packs/{a.block}/v{entry['version']} (HANDOFF.md)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
