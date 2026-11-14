"""Step 6: furniture packs -> furniture/*.glb (normalized) + furniture/catalog.json.

uv run python -m worldgen.build_catalog --block hou_heights_01 [--version 1] [--force]

The implementation is worldgen/normalize.py (ABO assets from out/abo_selection.json); this entry point
keeps the AGENTS.md command name and passes every argument through.
"""

from __future__ import annotations

from worldgen.normalize import main

if __name__ == "__main__":
    raise SystemExit(main())
