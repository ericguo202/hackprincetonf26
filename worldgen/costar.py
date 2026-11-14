"""Step 1-2: CoStar kit -> out/listings/index.json (Listing schema), joined to Matterport and Vexcel
ids.

uv run python -m worldgen.costar --in ../costar-hackathon-kit --out out/listings/index.json"""

from __future__ import annotations

import argparse

TODO = "map residential listings via sources.costar; nulls for missing fields, never fabricate"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--in", dest="src", required=True, help="CoStar kit folder")
    p.add_argument("--out", default="out/listings/index.json")
    args = p.parse_args(argv)
    raise SystemExit(f"not implemented yet: {TODO} ({vars(args)})")


if __name__ == "__main__":
    raise SystemExit(main())
