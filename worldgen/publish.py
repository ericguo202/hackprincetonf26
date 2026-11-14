"""Step 8: upload packs/<block_id>/v<N> and listings/index.json to the bucket; print PACK_BASE_URL.

uv run python -m worldgen.publish hou_heights_01"""

from __future__ import annotations

import argparse

TODO = "validate with contracts/validate.py, then boto3 upload with public-read and CORS"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("block")
    p.add_argument("--bucket", default=None, help="defaults to $PACK_BUCKET")
    args = p.parse_args(argv)
    raise SystemExit(f"not implemented yet: {TODO} ({vars(args)})")


if __name__ == "__main__":
    raise SystemExit(main())
