"""Step 7: deterministic contradiction checks per listing -> houses/<listing_id>/audit.json.

uv run python -m worldgen.audit --block hou_heights_01"""

from __future__ import annotations

import argparse

TODO = "stories vs floors/staircase, beds vs bedroom rooms, area vs summed rooms (15%), pool vs Vexcel"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--block", required=True)
    args = p.parse_args(argv)
    raise SystemExit(f"not implemented yet: {TODO} ({vars(args)})")


if __name__ == "__main__":
    raise SystemExit(main())
