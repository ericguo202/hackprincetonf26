"""Non-ABO furniture sources (Poly Haven, KayKit, Kenney, FurniMesh, Team): read and fetch them.

    uv run python -m worldgen.sources.furniture fetch [--extra extra_furniture.yaml] [--dest out/raw/extra]

Each item in the YAML names its source file (url or local, sha256, license, credit). Fetch downloads every
distinct file once into <dest>/<file> (or copies it from `local`, a path under worldgen/, for files
committed in worldgen/furniture_src/ that have no stable download URL) and checks its sha256, so a changed
upstream file fails loudly instead of silently changing the library. Normalizing is worldgen.normalize's
job (--extra), not this file's.
Downloaded files are data only: nothing in them is executed.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

import httpx
import yaml

HERE = Path(__file__).resolve().parents[1]  # worldgen/
EXTRA = HERE / "extra_furniture.yaml"
RAW_EXTRA = HERE / "out" / "raw" / "extra"
# contracts/asset.schema.json source.dataset, minus ABO
DATASETS = {"PolyHaven", "KayKit", "Kenney", "FurniMesh", "Team"}
REQUIRED = (
    "id", "category", "dataset", "item_id", "product_name", "sha256", "file", "license", "credit",
    "tags", "placement",
)  # fmt: skip


def load_extra(path: Path = EXTRA, raw_dir: Path = RAW_EXTRA) -> list[dict]:
    """Items of extra_furniture.yaml, each with `raw` set to its local source file."""
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    items = []
    for it in doc.get("items") or []:
        missing = [k for k in REQUIRED if not it.get(k)]
        if missing:
            raise ValueError(f"{path}: {it.get('id', '?')}: missing {missing}")
        if bool(it.get("url")) == bool(it.get("local")):
            raise ValueError(f"{path}: {it['id']}: exactly one of url, local")
        if it["dataset"] not in DATASETS:
            raise ValueError(f"{path}: {it['id']}: dataset {it['dataset']!r} not in {sorted(DATASETS)}")
        items.append({**it, "raw": str(raw_dir / it["file"])})
    ids = [it["id"] for it in items]
    if len(ids) != len(set(ids)):
        raise ValueError(f"{path}: duplicate ids")
    return items


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fetch(items: list[dict]) -> int:
    failed = 0
    seen: set[str] = set()
    for it in items:
        dst = Path(it["raw"])
        if str(dst) in seen:
            continue
        seen.add(str(dst))
        if not (dst.exists() and sha256(dst) == it["sha256"]):
            dst.parent.mkdir(parents=True, exist_ok=True)
            if it.get("local"):
                content = (HERE / it["local"]).read_bytes()
            else:
                r = httpx.get(it["url"], timeout=120, follow_redirects=True)
                r.raise_for_status()
                content = r.content
            tmp = dst.with_suffix(dst.suffix + ".part")
            tmp.write_bytes(content)
            got = sha256(tmp)
            if got != it["sha256"]:
                tmp.unlink()
                print(f"FAILED {it['file']}: sha256 {got} != {it['sha256']} ({it.get('url') or it['local']})")
                failed += 1
                continue
            tmp.replace(dst)
        print(f"ok {dst.stat().st_size / 1e3:8.1f} KB  {it['dataset']:9s} {it['license']:10s} {dst}")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--extra", type=Path, default=EXTRA)
    f.add_argument("--dest", type=Path, default=RAW_EXTRA)
    a = p.parse_args(argv)
    return fetch(load_extra(a.extra, a.dest))


if __name__ == "__main__":
    raise SystemExit(main())
