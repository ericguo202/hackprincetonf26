"""Step 1-2: CoStar kit -> out/listings/index.json (Listing schema), joined to Matterport and Vexcel
ids.

uv run python -m worldgen.costar --in ../costar-hackathon-kit --out out/listings/index.json"""

from __future__ import annotations

import argparse

TODO = "map residential listings via sources.costar; nulls for missing fields, never fabricate"

ACRE_M2 = 4046.8564224

# CoStar ListingPropertyType (property.propertyType) -> Listing.property_type.
# Codes not in this table (0 None, 32 LotsLands, 128 Commercial) are not homes and stay out of the index.
PROPERTY_TYPES: dict[int, str] = {
    1: "single_family",  # Detached
    2: "townhouse",  # Townhouse
    4: "condo",  # Condo
    256: "condo",  # Coop
    512: "duplex",  # Duplex
    16: "multi_family",  # MultiFamily
    1024: "multi_family",  # Triplex
    2048: "multi_family",  # Quadruplex
    64: "other",  # MobileManufactured
    8: "other",  # Other
}

MATTERPORT_TOUR = 43  # MediaType: Matterport 3D scan


def property_type(record: dict) -> str | None:
    """Listing.property_type for a raw residential listing, or None when it is not a home."""
    code = (record.get("property") or {}).get("propertyType")
    return PROPERTY_TYPES.get(code) if code is not None else None


def lot_area_m2(record: dict) -> float | None:
    """site.lot_area_m2 from property.lotSizeAcresMin; None when the listing has no lot size."""
    acres = (record.get("property") or {}).get("lotSizeAcresMin")
    return None if acres is None else round(acres * ACRE_M2, 1)


def matterport_model_id(record: dict, csv_models: dict[int, list[str]]) -> str | None:
    """The listing's Matterport model from residential-listings.csv (CoStar's Matterport list).

    When a listing has several models, prefer the one on its 3D-tour media item (type 43), the tour
    Homes.com shows; otherwise take the first CSV row. None when the CSV has no model for it."""
    models = csv_models.get(record["id"], [])
    if not models:
        return None
    for media in record.get("media") or []:
        if media.get("type") == MATTERPORT_TOUR and media.get("videoKey") in models:
            return media["videoKey"]
    return models[0]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--in", dest="src", required=True, help="CoStar kit folder")
    p.add_argument("--out", default="out/listings/index.json")
    args = p.parse_args(argv)
    raise SystemExit(f"not implemented yet: {TODO} ({vars(args)})")


if __name__ == "__main__":
    raise SystemExit(main())
