"""Read the CoStar kit (costar-hackathon-kit/data/*.jsonl, *.csv). Raw records only;
mapping lives in worldgen/costar.py."""

from __future__ import annotations

import csv
import json
from collections.abc import Iterator
from pathlib import Path


def iter_residential_listings(kit: Path) -> Iterator[dict]:
    """Raw records of data/residential-listings.jsonl."""
    with open(kit / "data" / "residential-listings.jsonl", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def iter_residential_listing_features(kit: Path) -> Iterator[dict]:
    """Raw records of data/residential-listing-features.jsonl (one per listing id; 221 listings have none)."""
    with open(kit / "data" / "residential-listing-features.jsonl", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def matterport_csv(kit: Path) -> dict[int, list[str]]:
    """ListingId -> Matterport model ids, in residential-listings.csv row order.

    This CSV is CoStar's own Matterport list, so it is the join: no address or distance matching.
    98 listings have more than one model; one listing has none (empty MatterportModelId)."""
    out: dict[int, list[str]] = {}
    with open(kit / "residential-listings.csv", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            ids = out.setdefault(int(row["ListingId"]), [])
            model = row["MatterportModelId"].strip()
            if model and model not in ids:
                ids.append(model)
    return out
