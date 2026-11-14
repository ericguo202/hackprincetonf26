"""Phase 1: CoStar inventory -> out/scout/listings.jsonl, one row per home (propertyId).

uv run python -m worldgen.scout.inventory

Reads (main checkout): costar-hackathon-kit/{residential-listings.csv, commercial-properties.csv,
data/residential-listings.jsonl, data/residential-listing-features.jsonl, data/commercial-property.jsonl,
schemas/enums.json} and docs/prompts/data/splat-models.tsv. Free and deterministic; safe to rerun.

Each row uses the home's best listing (Active, then Contract, then Sold, then the rest; most recent
listDate first) and keeps every listing id and Matterport model id of the home. Values the kit does not
have stay null; `*_source` fields name the field a value came from.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from typing import Any

from worldgen.scout.common import (
    ACRE_TO_M2,
    KIT,
    OUT,
    SPLAT_TSV,
    SQFT_TO_M2,
    ensure_out,
    now_iso,
    read_jsonl,
    write_json,
    write_jsonl,
)

ENUMS = json.loads((KIT / "schemas/enums.json").read_text())


def enum(name: str, code: int | None) -> str | None:
    if code is None:
        return None
    return ENUMS[name].get(str(code), f"code_{code}")


def flags(name: str, code: int | None) -> list[str]:
    """Decode a bit-flag enum value into its names."""
    if not code:
        return []
    return [v for k, v in ENUMS[name].items() if int(k) and code & int(k) == int(k)]


# Status preference for the "best listing": ListingStatus2 1 Active, 2 Contract, 4 Sold, 8, 16.
STATUS_RANK = {1: 0, 2: 1, 4: 2, 8: 3, 16: 4}

# SceneClassificationType codes (enums.json); media and scene codes are their own enums.
SCENE_FRONT, SCENE_REAR, SCENE_SIDE = 131, 132, 133
SCENES_OF_INTEREST = {
    "exterior": 32,
    "backyard": 65,
    "patio": 82,
    "porch": 84,
    "swimming_pool": 19,
    "fenced": 129,
    "wood_fence": 213,
    "chain_link_fence": 214,
    "white_fence": 268,
    "stairs": 123,
    "wall_mounted_tv": 235,
    "media_room": 136,
    "game_room": 76,
    "bedroom": 62,
    "living_room": 64,
    "family_room": 74,
    "open_floorplan": 49,
    "floorplan": 72,
}
MEDIA_PHOTO = 4  # MediaType BuildingPhoto (listing photos)
MEDIA_NAMES = {
    43: "matterport_3d_scan",
    127: "matterport_exterior",
    128: "gaussian_flyover_raw",
    131: "matterport_community",
    132: "exterior_photo",
    133: "matterport_community_thumbnail_video",
    137: "matterport_aerial_plus_interior_xray",
    138: "matterport_aerial_exterior_only",
    4: "photo",
    10: "floor_plan",
    5: "virtual_tour",
    33: "interior_photo",
    100002: "static_map",
}
MATTERPORT_REF = {36, 234}  # ExternalReferenceType4 MatterportScan, MatterportStreamingKey

# Alt-text/caption words: a cheap furnished-vs-vacant hint for Phase 5 (counts only, no verdict).
FURNITURE_WORDS = re.compile(
    r"\b(sofa|couch|sectional|armchair|bed|beds|dining table|coffee table|desk|dresser|nightstand|"
    r"furnished|furniture|staged|decor|rug|television|tv)\b",
    re.I,
)
EMPTY_WORDS = re.compile(r"\b(empty|vacant|unfurnished|no furniture|bare)\b", re.I)

FENCE_LINE = re.compile(r"fenc", re.I)
POOL_PRIVATE_LINE = re.compile(
    r"^(in ground pool|gunite pool|heated in ground pool|pool|pool and spa|heated pool and spa|"
    r"saltwater pool|heated pool|above ground pool|fiberglass pool|gas heated pool|private pool|"
    r"filtered pool|solar heated in ground pool|vinyl pool|lap pool|infinity pool|.*\bprivate pool\b.*)$",
    re.I,
)
POOL_COMMUNITY_LINE = re.compile(r"community pool|association pool|shared pool|neighborhood pool", re.I)
POOL_FENCED_LINE = re.compile(r"fence around pool|pool fenced|fenced pool", re.I)
MAIN_LEVEL_BEDS = re.compile(r"(\d+)\s+Main Level Bedroom", re.I)
STORY_LINE = re.compile(r"(\d+(?:\.\d+)?)-Story Property", re.I)


def load_splats() -> dict[str, dict]:
    out = {}
    with open(SPLAT_TSV) as f:
        for row in csv.DictReader(f, delimiter="\t"):
            out[row["Model"]] = {
                "model_id": row["Model"],
                "name": row["Name"],
                "tour": row["Tour"],
                "segment": row["Segment"],
                "property": row["Property"],
                "drone_photos": int(row["Photos"]) if row["Photos"].isdigit() else None,
                "splat_size": row["Splat"],
                "splat_mb": size_mb(row["Splat"]),
                "total_size": row["Total"],
                "total_mb": size_mb(row["Total"]),
            }
    return out


def size_mb(s: str) -> float | None:
    m = re.match(r"([\d.]+)\s*(KB|MB|GB)", s.strip(), re.I)
    if not m:
        return None
    v, u = float(m.group(1)), m.group(2).upper()
    return round(v * {"KB": 1 / 1024, "MB": 1, "GB": 1024}[u], 1)


def load_csv(path, key: str) -> dict[str, list[str]]:
    """key column -> list of MatterportModelId (non-empty), plus address cols kept separately."""
    out: dict[str, list[str]] = defaultdict(list)
    with open(path) as f:
        for row in csv.DictReader(f):
            mid = (row.get("MatterportModelId") or "").strip()
            out[row[key]]  # touch so keys with no model still appear
            if mid:
                out[row[key]].append(mid)
    return out


def sort_key(rec: dict) -> tuple:
    status_rank = STATUS_RANK.get(rec.get("listingStatus"), 9)
    # most recent first: negate via string compare on ISO date
    date = rec.get("listDate") or rec.get("offMarketDate") or ""
    return (status_rank, _neg(date), rec["id"])


def _neg(s: str) -> str:
    return "".join(chr(0x10FFFF - ord(c)) for c in s)


def feature_facts(feat: dict | None) -> dict[str, Any]:
    """Pull schools, fence, pool, stories and tax facts from a features record (text sections)."""
    out: dict[str, Any] = {
        "features_record": feat is not None,
        "schools": [],
        "fenced_yard": None,
        "fence_lines": [],
        "pool_lines": [],
        "pool_private_text": False,
        "pool_community_text": False,
        "pool_fenced_text": False,
        "main_level_bedrooms": None,
        "stories_text": None,
        "highlights": [],
        "outdoor_lines": [],
        "interior_lines": [],
        "derived": {},
        "hoa_month_usd": None,
        "rental_terms": [],
    }
    if feat is None:
        return out
    dv = feat.get("derivedValues") or {}
    out["derived"] = dv
    out["hoa_month_usd"] = dv.get("totalMonthlyAssociationFees")
    out["highlights"] = [h.get("text") for h in feat.get("highlights") or []][:8]
    for x in feat.get("features") or []:
        if x.get("subcategory1") == "Schools" and x.get("stringValues"):
            level = x["name"].replace(" School", "").lower()
            for name in x["stringValues"]:
                out["schools"].append({"name": name, "level": level, "distance_m": None, "rating": None})
    for s in feat.get("sections") or []:
        sub = s.get("subcategory") or ""
        for line in s.get("lines") or []:
            if FENCE_LINE.search(line):
                out["fence_lines"].append(line)
            if sub == "Pool" or "pool" in line.lower():
                if sub == "Pool":
                    out["pool_lines"].append(line)
                if POOL_PRIVATE_LINE.match(line.strip()) and sub == "Pool":
                    out["pool_private_text"] = True
                if POOL_COMMUNITY_LINE.search(line):
                    out["pool_community_text"] = True
                if POOL_FENCED_LINE.search(line):
                    out["pool_fenced_text"] = True
            m = MAIN_LEVEL_BEDS.search(line)
            if m:
                out["main_level_bedrooms"] = int(m.group(1))
            m = STORY_LINE.search(line)
            if m:
                out["stories_text"] = float(m.group(1))
            if sub == "Outdoor Features":
                out["outdoor_lines"].append(line)
            if sub == "Interior Spaces":
                out["interior_lines"].append(line)
            if sub == "Listing and Financial Details" and re.search(
                r"deposit|lease|available|furnished|application", line, re.I
            ):
                out["rental_terms"].append(line)
    yard_fence = [
        line
        for line in out["fence_lines"]
        if not re.search(r"pool|spa", line, re.I) and not re.search(r"condition", line, re.I)
    ]
    out["fenced_yard"] = True if yard_fence else None
    return out


def media_facts(media: list[dict]) -> dict[str, Any]:
    by_type = Counter()
    scenes = Counter()
    photo_views = Counter()
    alt_furn = alt_empty = 0
    mp_media_keys: list[dict] = []
    for m in media or []:
        t = m.get("type")
        by_type[MEDIA_NAMES.get(t, f"type_{t}")] += 1
        if m.get("videoKey") and (
            m.get("externalReferenceType") in MATTERPORT_REF or m.get("threeDimensionalScanType") == 36
        ):
            mp_media_keys.append({"model_id": m["videoKey"], "media_type": t})
        if t == 127:
            mp_media_keys.append({"matterport_exterior_uri_host": (m.get("uri") or "").split("/")[2:3]})
        codes = {s.get("type") for s in m.get("scenes") or []}
        if t == MEDIA_PHOTO:
            for c in codes:
                scenes[c] += 1
            if SCENE_FRONT in codes:
                photo_views["front"] += 1
            if SCENE_REAR in codes:
                photo_views["rear"] += 1
            if SCENE_SIDE in codes:
                photo_views["side"] += 1
            text = " ".join(filter(None, [m.get("altText"), m.get("caption"), m.get("description")]))
            if text and not codes & {SCENE_FRONT, SCENE_REAR, SCENE_SIDE, 32}:
                if FURNITURE_WORDS.search(text):
                    alt_furn += 1
                if EMPTY_WORDS.search(text):
                    alt_empty += 1
    return {
        "media_counts": dict(by_type),
        "photo_count": by_type.get("photo", 0),
        "front_photos": photo_views["front"],
        "rear_photos": photo_views["rear"],
        "side_photos": photo_views["side"],
        "scene_counts": {k: scenes[c] for k, c in SCENES_OF_INTEREST.items() if scenes[c]},
        "alt_furniture_photos": alt_furn,
        "alt_empty_photos": alt_empty,
        "mp_media": mp_media_keys,
    }


def build(args: argparse.Namespace) -> dict:
    splats = load_splats()
    res_csv = load_csv(KIT / "residential-listings.csv", "ListingId")
    com_csv = load_csv(KIT / "commercial-properties.csv", "PropertyId")

    listings = {r["id"]: r for r in read_jsonl(KIT / "data/residential-listings.jsonl")}
    features = {r["id"]: r for r in read_jsonl(KIT / "data/residential-listing-features.jsonl")}

    by_prop: dict[int, list[dict]] = defaultdict(list)
    for r in listings.values():
        by_prop[r["propertyId"]].append(r)

    model_to_listing: dict[str, int] = {}
    for lid, mids in res_csv.items():
        for mid in mids:
            model_to_listing[mid] = int(lid)

    rows = []
    for pid, recs in by_prop.items():
        recs.sort(key=sort_key)
        best = recs[0]
        prop = best.get("property") or {}
        addr = prop.get("address") or {}
        reso = best.get("resoValues") or {}
        struct = reso.get("structure") or {}
        cfv = best.get("costarFeatureValues") or {}
        feat = feature_facts(features.get(best["id"]))
        med = media_facts(best.get("media") or [])

        # All Matterport models of the home: CSV rows of every listing, plus media videoKeys.
        model_ids: list[str] = []
        model_listing: dict[str, int] = {}
        for r in recs:
            for mid in res_csv.get(str(r["id"]), []):
                if mid not in model_ids:
                    model_ids.append(mid)
                    model_listing[mid] = r["id"]
            for m in r.get("media") or []:
                vk = m.get("videoKey")
                if vk and (m.get("externalReferenceType") in MATTERPORT_REF) and vk not in model_ids:
                    model_ids.append(vk)
                    model_listing[vk] = r["id"]

        splat_models = [splats[m] for m in model_ids if m in splats]
        splat = splat_models[0] if splat_models else None
        if len(splat_models) > 1:  # prefer an OUTSIDE tour
            splat = sorted(splat_models, key=lambda s: (s["tour"] != "OUTSIDE", -(s["splat_mb"] or 0)))[0]

        tx = best.get("transactionType")
        price = best.get("currentPrice")
        pool_type = reso.get("poolType") or 0
        cpool = cfv.get("poolType") or 0
        pool_private = bool(pool_type & 1 or cpool & 1 or feat["pool_private_text"])
        pool_community = bool(pool_type & 6 or cpool & 2 or feat["pool_community_text"])
        outdoor_flags = flags("OutdoorSpaceType", reso.get("outdoorSpaceType"))
        fenced = feat["fenced_yard"]
        fenced_src = "features.sections (fence line)" if fenced else None
        if fenced is None and "Fenced" in outdoor_flags:
            fenced, fenced_src = True, "resoValues.outdoorSpaceType & 8 (Fenced)"

        sqft = prop.get("sqftMin")
        lot_ac = prop.get("lotSizeAcresMin")
        stories = struct.get("stories")
        stories_src = "resoValues.structure.stories"
        if stories is None and feat["derived"].get("numberOfStories") is not None:
            stories, stories_src = (
                feat["derived"]["numberOfStories"],
                "features.derivedValues.numberOfStories",
            )
        if stories is None and feat["stories_text"] is not None:
            stories, stories_src = feat["stories_text"], "features.sections (N-Story Property)"
        if stories is None:
            stories_src = None

        garage = reso.get("hasGarage")
        if garage is None:
            garage = cfv.get("hasGarage")

        lat, lon = addr.get("latitude"), addr.get("longitude")
        state = addr.get("state")
        rows.append(
            {
                "property_id": pid,
                "listing_id": best["id"],
                "residential_listing_id": best.get("residentialListingId"),
                "listing_ids": [r["id"] for r in recs],
                "listing_count": len(recs),
                "address": addr.get("fullStreetAddress") or None,
                "unit": addr.get("unitNumber") or None,
                "city": addr.get("city"),
                "state": state,
                "zip": addr.get("zip"),
                "market": {"TX": "houston", "CA": "los_angeles"}.get(state),
                "lat": lat if lat else None,
                "lon": lon if lon else None,
                "listing_status_code": best.get("listingStatus"),
                "listing_status": enum("ListingStatus2", best.get("listingStatus")),
                "for_sale_status": enum("ForSaleStatus", best.get("forSaleStatus")),
                "transaction": {1: "sale", 2: "rent"}.get(tx, enum("TransactionType", tx)),
                "has_rent_listing": any(r.get("transactionType") == 2 for r in recs),
                "has_active_listing": any(r.get("listingStatus") in (1, 2) for r in recs),
                "list_date": (best.get("listDate") or "")[:10] or None,
                "price_usd": price if tx == 1 else None,
                "rent_usd_month": price if tx == 2 else None,
                "original_price_usd": best.get("originalPrice"),
                "beds": prop.get("bedsMin"),
                "baths": prop.get("bathsMin"),
                "baths_full": prop.get("bathsFull"),
                "area_sqft": sqft,
                "area_m2": round(sqft * SQFT_TO_M2, 1) if sqft else None,
                "stories": stories,
                "stories_source": stories_src,
                "year_built": prop.get("yearBuilt"),
                "lot_acres": lot_ac,
                "lot_m2": round(lot_ac * ACRE_TO_M2, 1) if lot_ac else None,
                "property_type": enum("ListingPropertyType", prop.get("propertyType")),
                "mls_property_type": prop.get("mlsPropertyType"),
                "is_in_condo_building": best.get("isInCondoBuilding"),
                "subdivision": prop.get("subdivision"),
                "pool_private": pool_private,
                "pool_community": pool_community,
                "pool_fenced_text": feat["pool_fenced_text"],
                "pool_lines": feat["pool_lines"],
                "pool_type_flags": flags("PoolType", pool_type),
                "costar_pool_flags": flags("CostarFeaturePoolType", cpool),
                "garage": garage,
                "garage_spaces": struct.get("garageSpaces"),
                "parking_spaces": prop.get("totalParkingSpaces"),
                "fenced_yard": fenced,
                "fenced_yard_source": fenced_src,
                "fence_lines": feat["fence_lines"],
                "outdoor_space_flags": outdoor_flags,
                "outdoor_lines": feat["outdoor_lines"],
                "lot_flags": flags("LotType", reso.get("lotType")),
                "room_type_flags": flags("RoomType2", reso.get("roomType")),
                "main_level_bedrooms": feat["main_level_bedrooms"],
                "schools": feat["schools"],
                "tax_usd_year": prop.get("taxes"),
                "hoa_usd_month": feat["hoa_month_usd"],
                "rental_terms": feat["rental_terms"],
                "highlights": feat["highlights"],
                "features_record": feat["features_record"],
                "public_on_homes": (best.get("permissionLevels") or {}).get("homes"),
                "mls_id": (best.get("mls") or {}).get("id"),
                "matterport_model_ids": model_ids,
                "model_listing": model_listing,
                "splat": splat is not None,
                "splat_model_id": splat["model_id"] if splat else None,
                "splat_tour": splat["tour"] if splat else None,
                "splat_size": splat["splat_size"] if splat else None,
                "splat_mb": splat["splat_mb"] if splat else None,
                "drone_photos": splat["drone_photos"] if splat else None,
                "drone_total_size": splat["total_size"] if splat else None,
                "splat_models_all": [s["model_id"] for s in splat_models],
                **{k: v for k, v in med.items() if k != "mp_media"},
                "mp_media_types": sorted(
                    {m.get("media_type") for m in med["mp_media"] if m.get("media_type")}
                ),
                "remarks_len": len(best.get("remarks") or ""),
            }
        )

    rows.sort(key=lambda r: (not r["splat"], r["market"] or "", r["property_id"]))
    n = write_jsonl(OUT / "listings.jsonl", rows)

    # Commercial splat models: apartment units / amenities in commercial properties (not homes).
    commercial = []
    com_props = {}
    wanted = {s["property"] for s in splats.values() if s["segment"] == "commercial"}
    for r in read_jsonl(KIT / "data/commercial-property.jsonl"):
        if str(r.get("id")) in wanted:
            pa = r.get("primaryAddress") or {}
            com_props[str(r["id"])] = {
                "property_id": r["id"],
                "name": r.get("buildingName"),
                "address": " ".join(
                    str(x) for x in (pa.get("streetNumber"), pa.get("streetName"), pa.get("streetType")) if x
                )
                or None,
                "city": pa.get("city"),
                "state": pa.get("state"),
                "lat": pa.get("latitude"),
                "lon": pa.get("longitude"),
                "property_type": enum("PropertyType", r.get("propertyType")),
                "stories": r.get("numStoriesAboveGrade"),
            }
    for s in splats.values():
        if s["segment"] != "commercial":
            continue
        p = com_props.get(s["property"], {})
        in_csv = s["model_id"] in com_csv.get(s["property"], [])
        commercial.append({**s, "commercial_property": p, "in_commercial_csv": in_csv})
    write_jsonl(OUT / "commercial_splats.jsonl", commercial)

    # Join checks the brief asks us to confirm.
    res_splats = [s for s in splats.values() if s["segment"] == "residential"]
    joined = [s for s in res_splats if s["model_id"] in model_to_listing]
    status_counts = Counter(listings[model_to_listing[s["model_id"]]]["listingStatus"] for s in joined)
    homes_with_splat = [r for r in rows if r["splat"]]
    summary = {
        "generated_at": now_iso(),
        "homes": n,
        "listings": len(listings),
        "features_records": len(features),
        "splat_models": len(splats),
        "splat_residential": len(res_splats),
        "splat_commercial": len(splats) - len(res_splats),
        "splat_outside": sum(s["tour"] == "OUTSIDE" for s in splats.values()),
        "splat_inside_only": sum(s["tour"] == "Inside only" for s in splats.values()),
        "residential_splats_joined_to_csv": len(joined),
        "residential_splat_listing_status": {
            f"{k} {enum('ListingStatus2', k)}": v for k, v in sorted(status_counts.items())
        },
        "commercial_splats_joined_to_csv": sum(c["in_commercial_csv"] for c in commercial),
        "homes_with_splat": len(homes_with_splat),
        "homes_with_splat_by_market": dict(Counter(r["market"] for r in homes_with_splat)),
        "homes_with_multiple_splats": sum(len(r["splat_models_all"]) > 1 for r in rows),
        "homes_by_market": dict(Counter(r["market"] for r in rows)),
        "homes_with_coords": sum(r["lat"] is not None for r in rows),
        "homes_by_status": dict(Counter(r["listing_status"] for r in rows)),
        "homes_by_transaction": dict(Counter(r["transaction"] for r in rows)),
        "homes_with_rent_listing": sum(r["has_rent_listing"] for r in rows),
        "homes_by_property_type": dict(Counter(r["property_type"] for r in rows)),
        "model_ids_total": sum(len(r["matterport_model_ids"]) for r in rows),
        "homes_without_models": sum(not r["matterport_model_ids"] for r in rows),
        "media_127_homes": sum(r["media_counts"].get("matterport_exterior", 0) > 0 for r in rows),
        "media_127_homes_with_splat": sum(
            r["media_counts"].get("matterport_exterior", 0) > 0 and r["splat"] for r in rows
        ),
        "media_137_homes": sum(
            r["media_counts"].get("matterport_aerial_plus_interior_xray", 0) > 0 for r in rows
        ),
        "media_137_homes_with_splat": sum(
            r["media_counts"].get("matterport_aerial_plus_interior_xray", 0) > 0 and r["splat"] for r in rows
        ),
        "media_128_homes": sum(r["media_counts"].get("gaussian_flyover_raw", 0) > 0 for r in rows),
        "homes_with_front_rear_side_photos": sum(
            bool(r["front_photos"] and r["rear_photos"] and r["side_photos"]) for r in rows
        ),
        "homes_pool_private": sum(r["pool_private"] for r in rows),
        "homes_fenced_yard": sum(bool(r["fenced_yard"]) for r in rows),
        "homes_with_schools": sum(bool(r["schools"]) for r in rows),
    }
    write_json(OUT / "phase1_summary.json", summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    args = p.parse_args(argv)
    ensure_out()
    s = build(args)
    print(json.dumps(s, indent=1), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
