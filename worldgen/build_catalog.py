"""Step 6: furniture sources -> the shared library out/furniture/v<N>/{<id>.glb, catalog.json}.

uv run python -m worldgen.build_catalog [--version 1] [--force]

The implementation is worldgen/normalize.py (ABO assets from out/abo_selection.json); this entry point
keeps the AGENTS.md command name and passes every argument through.
"""

from __future__ import annotations

from worldgen.normalize import main

if __name__ == "__main__":
    raise SystemExit(main())
