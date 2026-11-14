"""Step 8: upload packs/<block_id>/v<N>, listings/index.json and the furniture library its manifest
names (furniture/v<N>/) to the bucket; print PACK_BASE_URL. Skip any furniture/v<N> already published.

uv run python -m worldgen.publish hou_navidad_01"""

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
