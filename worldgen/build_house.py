"""Step 4-5: Matterport model -> houses/<listing_id>/{interior.glb, collision.glb, rooms.json,
graph.json}.

uv run python -m worldgen.build_house --listing hou_000123"""

from __future__ import annotations

import argparse

TODO = (
    "MatterPak OBJ to glTF, Z-up to Y-up once, decimate, walls from floor-plan vectors, area_m2 from polygons"
)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--listing", required=True)
    p.add_argument("--block", default=None)
    args = p.parse_args(argv)
    raise SystemExit(f"not implemented yet: {TODO} ({vars(args)})")


if __name__ == "__main__":
    raise SystemExit(main())
