"""Step 3: Vexcel (or extruded footprints) -> packs/<block_id>/v<N>/block/{render,collision}.glb +
spawns.

uv run python -m worldgen.build_block --block hou_heights_01 --lat 29.79 --lon -95.40"""

from __future__ import annotations

import argparse

TODO = "local ENU origin at block center, Vexcel 3D tiles to glTF or footprint extrusion"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--block", required=True)
    p.add_argument("--lat", type=float, required=True)
    p.add_argument("--lon", type=float, required=True)
    p.add_argument("--radius-m", type=float, default=150.0)
    args = p.parse_args(argv)
    raise SystemExit(f"not implemented yet: {TODO} ({vars(args)})")


if __name__ == "__main__":
    raise SystemExit(main())
