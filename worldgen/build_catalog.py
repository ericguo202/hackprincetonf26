"""Step 6: furniture packs -> furniture/*.glb (normalized) + furniture/catalog.json.

uv run python -m worldgen.build_catalog --block hou_heights_01"""

from __future__ import annotations

import argparse

TODO = "Y-up, meters, origin bottom center, front +Z, under 5 MB, license and cover per asset"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--block", required=True)
    p.add_argument("--src", default="out/raw/furniture")
    args = p.parse_args(argv)
    raise SystemExit(f"not implemented yet: {TODO} ({vars(args)})")


if __name__ == "__main__":
    raise SystemExit(main())
