"""CLI runner for the concierge agent. Lane B owns this file.

uv run python -m agent.cli "We're new parents with an infant. Find us a home." --listing hou_000123
"""

from __future__ import annotations

import argparse


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the concierge agent without the web app")
    parser.add_argument("prompt")
    parser.add_argument("--listing", default=None)
    parser.add_argument("--room", default=None)
    parser.add_argument("--block", default="hou_heights_01")
    args = parser.parse_args()
    raise SystemExit(f"agent not built yet (prompt={args.prompt!r}); see AGENTS.md section 10, Lane B")


if __name__ == "__main__":
    main()
