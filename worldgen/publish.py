"""Step 8: publish the LIVE pack version packs/<block_id>/v<N> (worldgen/blocks/<block>.json
live_pack_version, see worldgen/versions.py), listings/index.json and the furniture library its manifest
names (furniture/v<N>/, contracts v2.3.0); print PACK_BASE_URL. Skip any furniture/v<N> already published.
Only the live version is ever published: to ship another version, pin it first.

uv run python -m worldgen.publish hou_navidad_01 --local     # copy into apps/web/public/packs for local dev
uv run python -m worldgen.publish hou_navidad_01             # upload to the bucket (not implemented yet)"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from worldgen import versions

HERE = Path(__file__).resolve().parent
WEB_PUBLIC = HERE.parent / "apps" / "web" / "public" / "packs"  # gitignored

TODO = "validate with contracts/validate.py, then boto3 upload with public-read and CORS"


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


def publish_local(block: str, out: Path, dest: Path = WEB_PUBLIC, version: int | None = None) -> str:
    """Copy the live pack version, the listings index (if built) and the furniture library the manifest
    names (if built) under apps/web/public/packs, laid out like the bucket (packs/<block>/v<N> next to
    listings/ and furniture/v<N>/), so the Vite dev server serves it. Returns the value for
    VITE_PACK_BASE_URL."""
    src = live_pack(out, block, version)
    target = dest / "packs" / block / src.name
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(src, target)
    index = out / "listings" / "index.json"
    if index.exists():
        (dest / "listings").mkdir(parents=True, exist_ok=True)
        shutil.copy2(index, dest / "listings" / "index.json")
    furniture = furniture_dir(out, src)
    if furniture is not None:
        f_target = dest / "furniture" / furniture.name
        if f_target.exists():
            shutil.rmtree(f_target)
        shutil.copytree(furniture, f_target)
    return f"/packs/packs/{block}/{src.name}"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("block")
    p.add_argument("--local", action="store_true", help="copy into apps/web/public/packs for the dev server")
    p.add_argument("--out", type=Path, default=HERE / "out")
    p.add_argument("--version", type=int, help="optional check: must equal the live version")
    p.add_argument("--bucket", default=None, help="defaults to $PACK_BUCKET")
    args = p.parse_args(argv)
    if args.local:
        base = publish_local(args.block, args.out, version=args.version)
        print(f"copied to {WEB_PUBLIC}\nVITE_PACK_BASE_URL={base}")
        return 0
    raise SystemExit(f"not implemented yet: {TODO} ({vars(args)})")


if __name__ == "__main__":
    raise SystemExit(main())
