"""Step 8: publish the LIVE pack version packs/<block_id>/v<N> (worldgen/blocks/<block>.json
live_pack_version, see worldgen/versions.py), listings/index.json and the furniture library its manifest
names (furniture/v<N>/, contracts v2.3.0); print PACK_BASE_URL. Skip any furniture/v<N> already published.
Only the live version is ever published: to ship another version, pin it first.

Packs are served by the web static site (no bucket; HANDOFF 2026-11-14 16:30, AGENTS.md section 5 allows it
under ~100 MB). Publishing writes apps/web/public/packs/ so that it holds exactly the published set and
nothing else (stale versions and local copies are removed), laid out like the published root:
  listings/index.json, packs/<block>/v<N>/..., furniture/v<N>/...
The web build copies public/ into dist/, so the site serves it at /packs/. Commit the folder to deploy.

uv run python -m worldgen.publish hou_navidad_01             # validate, then write apps/web/public/packs
uv run python -m worldgen.publish hou_navidad_01 --local     # same, without validation (fast, for dev)"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from worldgen import versions

HERE = Path(__file__).resolve().parent
WEB_PUBLIC = HERE.parent / "apps" / "web" / "public" / "packs"  # committed: the static site serves it
VALIDATE = HERE.parent / "contracts" / "validate.py"
SIZE_LIMIT_MB = 95  # the whole published set must stay under ~100 MB on the static site


def live_pack(out: Path, block: str, version: int | None = None) -> Path:
    """The live pack folder. A given `version` must be the live one (guards against shipping a test build)."""
    live = versions.live_version(block)
    if version is not None and version != live:
        raise SystemExit(
            f"v{version} is not the live version (v{live}, worldgen/blocks/{block}.json). "
            f"Pin it first: uv run python -m worldgen.versions pin {block} --version {version} ..."
        )
    src = versions.pack_dir(block, live, out)
    issues = versions.problems(block, live, out)
    if issues:
        raise SystemExit(f"live pack {src} is not complete:\n  " + "\n  ".join(issues))
    return src


def furniture_dir(out: Path, pack: Path) -> Path | None:
    """The furniture library folder the pack's manifest names (out/furniture/v<N>), if it was built."""
    manifest = pack / "manifest.json"
    if not manifest.exists():
        return None
    rel = json.loads(manifest.read_text(encoding="utf-8")).get("furniture_catalog")
    if not rel:
        return None
    folder = (out / rel).parent
    return folder if (folder / "catalog.json").is_file() else None


def _validate(path: Path) -> None:
    r = subprocess.run([sys.executable, str(VALIDATE), str(path)], capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"contracts/validate.py failed on {path}:\n{(r.stdout + r.stderr).strip()}")


def _size_mb(folder: Path) -> float:
    return sum(f.stat().st_size for f in folder.rglob("*") if f.is_file()) / 1e6


def publish_local(
    block: str, out: Path, dest: Path = WEB_PUBLIC, version: int | None = None, validate: bool = False
) -> str:
    """Write the live pack version, the listings index and the furniture library its manifest names into
    `dest`, which afterwards holds exactly that set (anything else there is removed). Returns the value for
    VITE_PACK_BASE_URL."""
    src = live_pack(out, block, version)
    furniture = furniture_dir(out, src)
    index = out / "listings" / "index.json"
    if furniture is None:
        raise SystemExit(f"{src}/manifest.json names a furniture library that is not built under {out}")
    if not index.is_file():
        raise SystemExit(f"{index} is missing; run worldgen.costar first")
    if validate:
        _validate(src)
        _validate(furniture.parent)
    staging = dest.parent / f".{dest.name}.staging"
    if staging.exists():
        shutil.rmtree(staging)
    shutil.copytree(src, staging / "packs" / block / src.name)
    shutil.copytree(furniture, staging / "furniture" / furniture.name)
    (staging / "listings").mkdir(parents=True)
    shutil.copy2(index, staging / "listings" / "index.json")
    size = _size_mb(staging)
    if size > SIZE_LIMIT_MB:
        shutil.rmtree(staging)
        raise SystemExit(f"published set is {size:.1f} MB, over the {SIZE_LIMIT_MB} MB static-site budget")
    if dest.exists():
        shutil.rmtree(dest)
    staging.rename(dest)
    return f"/packs/packs/{block}/{src.name}"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("block")
    p.add_argument(
        "--local", action="store_true", help="skip validation (dev only; do not commit the result)"
    )
    p.add_argument("--out", type=Path, default=HERE / "out")
    p.add_argument("--version", type=int, help="optional check: must equal the live version")
    args = p.parse_args(argv)
    base = publish_local(args.block, args.out, version=args.version, validate=not args.local)
    print(f"wrote {WEB_PUBLIC} ({_size_mb(WEB_PUBLIC):.1f} MB)")
    print(f"web:   VITE_PACK_BASE_URL={base}")
    print(f"agent: PACK_BASE_URL=https://<web static site>/packs  PACK_VERSION={base.rsplit('/v', 1)[1]}")
    if not args.local:
        print("deploy: git add apps/web/public/packs && git commit (a web deploy closes game WebSockets)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
