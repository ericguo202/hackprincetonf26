"""Step 6: furniture sources -> the shared library out/furniture/v<N>/{<id>.glb, catalog.json}.

uv run python -m worldgen.build_catalog --version N [--force]   (never a version a live pack uses)
uv run python -m worldgen.build_catalog --version 2 --base 1 --extra extra_furniture.yaml

The implementation is worldgen/normalize.py (ABO assets from out/abo_selection.json, non-ABO items from
extra_furniture.yaml); this entry point keeps the AGENTS.md command name and passes every argument through.
"""

from __future__ import annotations

from worldgen.normalize import main

if __name__ == "__main__":
    raise SystemExit(main())
