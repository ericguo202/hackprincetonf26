"""Step 7: deterministic contradiction checks per listing -> houses/<listing_id>/audit.json.

uv run python -m worldgen.audit --block hou_navidad_01              # every house in the newest pack version
uv run python -m worldgen.audit --block hou_navidad_01 --version 1  # a given version
uv run python -m worldgen.audit --listing hou_231395941             # metadata only, prints findings

Every number comes from code: the listing (CoStar), the house's rooms.json and graph.json (Matterport), and
cached Vexcel property attributes. No model is involved. Each check emits exactly one finding, also when the
data agree (severity "info"), so the agent can say "checked and consistent". A value the listing does not
have is an "info" finding ("not listed"), never a conflict.

Checks and severities:
- stories_vs_scan: listed 1 story but the scan has more than one floor, a staircase room or a stairs edge
  -> conflict. Listed more stories than scanned floors with no staircase -> warn only: a scan can skip a
  floor (an unscanned upstairs), so one scanned floor does not prove a one-story house. Scanned more floors
  than listed (listed >= 2) -> warn: a basement counts as a scan floor but not as a story.
- beds_vs_rooms: listed beds vs rooms labelled bedroom or primary_bedroom. Different -> conflict, except
  when the scan has fewer bedrooms and its office rooms make up the difference -> warn (MLS listings often
  count a study as a bedroom).
- area_vs_scan: listed area vs the sum of room polygon areas (shapely, not the rooms' area_m2 field),
  garage excluded. Closets, hallways, staircases, utility and "other" rooms count: CoStar living area is
  the heated area, which includes them. Off by more than 15% -> warn, more than 30% -> conflict. Scans
  measure inside the walls, so a scan is normally somewhat smaller than the listed (exterior) area (the
  scout measured a median scanned/listed ratio of 0.87 over the CoStar kit).
- baths_vs_rooms: listed baths vs bathroom rooms. A count between floor(baths) and ceil(baths) is
  consistent (2.5 baths = 2 or 3 rooms); otherwise warn, never conflict: half baths and baths inside a
  primary suite are often merged or mislabelled by room classification.
- pool_vs_vexcel: listing features.pool vs Vexcel property/extract pool.detected (cached by the scout; this
  module never calls Vexcel). Different -> conflict. No cached Vexcel data -> info "not checked".
"""

from __future__ import annotations

import argparse
import json
import math
import os
from collections.abc import Iterable
from pathlib import Path

from shapely.geometry import Polygon

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
CONTRACTS = HERE.parent / "contracts"
KIT = Path(os.environ.get("COSTAR_KIT_DIR", HERE.parent / "costar-hackathon-kit"))
VEXCEL_CACHE = OUT / "scout" / "cache" / "vexcel"
MP_CACHES = (OUT / "cache" / "matterport", OUT / "scout" / "cache" / "mp_detail")

SQFT_M2 = 0.09290304
AREA_WARN = 0.15
AREA_CONFLICT = 0.30
BEDROOM_LABELS = ("bedroom", "primary_bedroom")
AREA_EXCLUDED = ("garage",)


# ---------------------------------------------------------------- helpers


def finding(check: str, severity: str, listed: object, observed: object, evidence: str) -> dict:
    return {
        "check": check,
        "severity": severity,
        "listed": listed,
        "observed": observed,
        "evidence": evidence,
    }


def polygon_area(room: dict) -> float:
    """Room floor area from its polygon (house-local [x, z], meters)."""
    poly = Polygon(room["polygon"])
    if not poly.is_valid:
        poly = poly.buffer(0)
    return float(poly.area)


def sqft(m2: float) -> int:
    return round(m2 / SQFT_M2)


def _ids(rooms: Iterable[dict], with_area: bool = False) -> str:
    items = [f"{r['id']} {polygon_area(r):.1f} m2" if with_area else r["id"] for r in rooms]
    return ", ".join(items) if items else "none"


def _bool(x: object) -> str:
    return "null" if x is None else str(x).lower()


# ---------------------------------------------------------------- checks


def check_stories(listing: dict, rooms: list[dict], graph: dict) -> dict:
    c = "stories_vs_scan"
    listed = listing.get("stories")
    floors = sorted({r["floor"] for r in rooms})
    stair_rooms = [r for r in rooms if r["label"] == "staircase"]
    stair_edges = [e for e in graph.get("edges") or [] if e.get("kind") == "stairs"]
    stairs = bool(stair_rooms or stair_edges)
    n = len(floors)
    scan = (
        f"scan: {n} floor(s) (rooms[].floor = {', '.join(map(str, floors)) or 'none'}); "
        f"staircase rooms: {_ids(stair_rooms)} (rooms[label=staircase]); "
        f"stairs edges: {len(stair_edges)} (graph.edges[kind=stairs])"
    )
    if listed is None:
        return finding(c, "info", None, n, f"listing.stories not listed; {scan}")
    if listed == 1:
        if n > 1 or stairs:
            what = f"{n} floors" if n > 1 else "one scanned floor"
            what += " and a staircase" if stairs else ""
            return finding(c, "conflict", listed, n, f"Listed as one story but the scan has {what}; {scan}")
        return finding(c, "info", listed, n, f"consistent: listing.stories = 1; {scan}")
    if n == listed:
        return finding(c, "info", listed, n, f"consistent: listing.stories = {listed}; {scan}")
    if n > listed:
        why = "scan has more floors than listed stories (a basement is a scan floor, not a story)"
        return finding(c, "warn", listed, n, f"{why}; {scan}")
    if stairs:
        why = "fewer scanned floors than listed stories, but a staircase leads to an unscanned floor"
        return finding(c, "info", listed, n, f"{why}; {scan}")
    why = "fewer scanned floors than listed stories and no staircase; a scan can skip a floor, so not proof"
    return finding(c, "warn", listed, n, f"{why}; {scan}")


def check_beds(listing: dict, rooms: list[dict]) -> dict:
    c = "beds_vs_rooms"
    listed = listing.get("beds")
    beds = [r for r in rooms if r["label"] in BEDROOM_LABELS]
    offices = [r for r in rooms if r["label"] == "office"]
    n = len(beds)
    scan = f"rooms[label in (bedroom, primary_bedroom)]: {_ids(beds, with_area=True)}"
    if offices:
        scan += f"; rooms[label=office]: {_ids(offices, with_area=True)}"
    if listed is None:
        return finding(c, "info", None, n, f"listing.beds not listed; {scan}")
    if n == listed:
        return finding(c, "info", listed, n, f"consistent: listing.beds = {listed}; {scan}")
    if n < listed <= n + len(offices):
        why = f"listed {listed} beds, scan has {n} bedrooms plus office rooms that may be counted as bedrooms"
        return finding(c, "warn", listed, n, f"{why}; {scan}")
    return finding(c, "conflict", listed, n, f"Listed {listed} beds but the scan has {n} bedrooms; {scan}")


def check_area(listing: dict, rooms: list[dict]) -> dict:
    c = "area_vs_scan"
    listed = listing.get("area_m2")
    counted = [r for r in rooms if r["label"] not in AREA_EXCLUDED]
    excluded = [r for r in rooms if r["label"] in AREA_EXCLUDED]
    total = round(sum(polygon_area(r) for r in counted), 1)
    scan = (
        f"sum of {len(counted)} room polygons = {total} m2 ({sqft(total)} sqft), "
        f"excluded: {_ids(excluded, with_area=True)}"
    )
    if listed is None:
        return finding(c, "info", None, total, f"listing.area_m2 not listed; {scan}")
    if not counted or listed <= 0:
        return finding(c, "info", listed, total, f"not comparable (no rooms or no listed area); {scan}")
    off = (total - listed) / listed
    head = (
        f"listing.area_m2 = {listed} m2 ({sqft(listed)} sqft); {scan}; "
        f"scan {abs(off) * 100:.1f}% {'smaller' if off < 0 else 'larger'}"
    )
    note = "; scans measure inside the walls, so a scan is usually a little smaller than the listed area"
    if abs(off) <= AREA_WARN:
        return finding(c, "info", listed, total, f"consistent within 15%: {head}{note}")
    if abs(off) <= AREA_CONFLICT:
        return finding(c, "warn", listed, total, f"off by more than 15%: {head}{note}")
    return finding(c, "conflict", listed, total, f"off by more than 30%: {head}{note}")


def check_baths(listing: dict, rooms: list[dict]) -> dict:
    c = "baths_vs_rooms"
    listed = listing.get("baths")
    baths = [r for r in rooms if r["label"] == "bathroom"]
    n = len(baths)
    scan = f"rooms[label=bathroom]: {_ids(baths, with_area=True)}"
    if listed is None:
        return finding(c, "info", None, n, f"listing.baths not listed; {scan}")
    if float(listed).is_integer():
        listed = int(listed)
    if math.floor(listed) <= n <= math.ceil(listed):
        return finding(c, "info", listed, n, f"consistent: listing.baths = {listed}; {scan}")
    why = (
        f"listed {listed} baths, scan has {n} bathroom rooms (half baths and suite baths are sometimes "
        f"merged or mislabelled in a scan)"
    )
    return finding(c, "warn", listed, n, f"{why}; {scan}")


def check_pool(listing: dict, extras: dict | None) -> dict:
    c = "pool_vs_vexcel"
    listed = (listing.get("features") or {}).get("pool")
    vx = (extras or {}).get("vexcel")
    observed = vx.get("pool") if vx else None
    lst = (
        f"listing.features.pool = {_bool(listed)}"
        if listed is not None
        else "listing.features.pool not listed"
    )
    if observed is None:
        why = (vx or {}).get("note") or "no cached Vexcel property/extract for this listing"
        return finding(c, "info", listed, None, f"not checked: {why}; {lst}")
    src = f"{vx.get('evidence', 'Vexcel property/extract pool.detected')} = {_bool(observed)}"
    if listed is None:
        return finding(c, "info", None, observed, f"{lst}; {src}")
    if listed == observed:
        return finding(c, "info", listed, observed, f"consistent: {lst}; {src}")
    say = (
        "Listed with a pool but aerial imagery shows none"
        if listed
        else "Listed without a pool but imagery shows one"
    )
    return finding(c, "conflict", listed, observed, f"{say}; {lst}; {src}")


def audit_listing(listing: dict, rooms: list[dict], graph: dict, extras: dict | None = None) -> dict:
    """Audit document (contracts/audit.schema.json) for one listing against its scanned rooms and graph.

    extras: optional {"vexcel": {"pool": bool | None, "evidence": str} | {"pool": None, "note": str} | None}.
    """
    return {
        "listing_id": listing["id"],
        "findings": [
            check_stories(listing, rooms, graph),
            check_beds(listing, rooms),
            check_area(listing, rooms),
            check_baths(listing, rooms),
            check_pool(listing, extras),
        ],
    }


# ---------------------------------------------------------------- inputs


def load_index(out: Path) -> dict[str, dict]:
    path = out / "listings" / "index.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data.get("listings", []) if isinstance(data, dict) else data
    return {x["id"]: x for x in items}


def listing_from_raw(record: dict, csv_models: dict[int, list[str]] | None = None) -> dict:
    """TEMPORARY fallback until worldgen/costar.py writes out/listings/index.json: only the Listing fields the
    audit reads, mapped from a raw CoStar residential listing. Delete once the index exists everywhere."""
    from worldgen.costar import matterport_model_id

    prop = record.get("property") or {}
    reso = record.get("resoValues") or {}
    addr = prop.get("address") or {}
    stories = (reso.get("structure") or {}).get("stories") or prop.get("stories") or None  # 0 = unknown
    sqft_min = prop.get("sqftMin")
    pool_type = reso.get("poolType")
    return {
        "id": f"hou_{record['id']}",
        "address": addr.get("fullStreetAddress"),
        "lat": addr.get("latitude"),
        "lon": addr.get("longitude"),
        "beds": prop.get("bedsMin"),
        "baths": prop.get("bathsMin"),
        "area_m2": round(sqft_min * SQFT_M2, 2) if sqft_min else None,
        "stories": stories,
        # PoolType 1 = Private. 0 Unknown, 2 Association and 4 Community say nothing about a private pool.
        "features": {"pool": True if pool_type == 1 else None},
        "source": {
            "costar_id": str(record["id"]),
            "matterport_model_id": matterport_model_id(record, csv_models)
            if csv_models is not None
            else None,
        },
    }


def load_listings(ids: Iterable[str], out: Path = OUT, kit: Path = KIT) -> dict[str, tuple[dict, str]]:
    """listing id -> (listing, source). Prefers out/listings/index.json; ids it lacks come from the raw CoStar
    kit (temporary fallback); ids found nowhere get an empty listing (every check says "not listed")."""
    ids = list(ids)
    index = load_index(out)
    found = {i: (index[i], "index") for i in ids if i in index}
    missing = {
        int(i.removeprefix("hou_")) for i in ids if i not in found and i.removeprefix("hou_").isdigit()
    }
    if missing and (kit / "data" / "residential-listings.jsonl").exists():
        from worldgen.sources.costar import iter_residential_listings, matterport_csv

        csv_models = matterport_csv(kit) if (kit / "residential-listings.csv").exists() else None
        for rec in iter_residential_listings(kit):
            if rec.get("id") in missing:
                lst = listing_from_raw(rec, csv_models)
                found[lst["id"]] = (lst, "costar_raw (temporary fallback)")
    for i in ids:
        found.setdefault(i, ({"id": i}, "missing"))
    return found


def vexcel_extras(lat: float | None, lon: float | None, cache_dir: Path = VEXCEL_CACHE) -> dict | None:
    """Pool attributes from a cached Vexcel property/extract at the listing point (requested by the scout
    with the same parameters). Reads the cache only; None when there is no cached response."""
    if lat is None or lon is None:
        return None
    from worldgen.sources.vexcel import VexcelClient

    key = VexcelClient.cache_key("property/extract", {"wkt": f"POINT({lon} {lat})", "srid": 4326})
    path = cache_dir / f"{key}.body"
    if not path.exists():
        return None
    feats = json.loads(path.read_text(encoding="utf-8")).get("features") or []
    if not feats:
        return {"pool": None, "note": "Vexcel property/extract found no parcel at the listing point"}
    p = feats[0].get("properties") or {}
    if p.get("request-information.inside-parcel") is False:
        return {"pool": None, "note": "listing point is outside the Vexcel parcel"}
    date = (p.get("metadata.ortho-date-estimate") or "")[:10] or "undated"
    return {
        "pool": p.get("pool.detected"),
        "evidence": f"Vexcel property/extract pool.detected (imagery {date})",
    }


# ---------------------------------------------------------------- pack mode


def newest_version(out: Path, block_id: str) -> int:
    root = out / "packs" / block_id
    versions = [int(p.name[1:]) for p in root.glob("v*") if p.is_dir() and p.name[1:].isdigit()]
    if not versions:
        raise SystemExit(f"no packs/{block_id}/v<N> under {out}")
    return max(versions)


def _validator():
    from jsonschema import Draft202012Validator

    return Draft202012Validator(json.loads((CONTRACTS / "audit.schema.json").read_text(encoding="utf-8")))


def audit_pack(block_id: str, version: int, out: Path = OUT) -> list[tuple[dict, str]]:
    """Write houses/<listing_id>/audit.json for every house in the pack version; returns (audit, source)."""
    houses = sorted(p for p in (out / "packs" / block_id / f"v{version}" / "houses").glob("*") if p.is_dir())
    listings = load_listings([h.name for h in houses], out)
    validator = _validator()
    results = []
    for house in houses:
        listing, source = listings[house.name]
        rooms = json.loads((house / "rooms.json").read_text(encoding="utf-8"))
        graph = json.loads((house / "graph.json").read_text(encoding="utf-8"))
        doc = audit_listing(
            listing, rooms, graph, {"vexcel": vexcel_extras(listing.get("lat"), listing.get("lon"))}
        )
        errors = [e.message for e in validator.iter_errors(doc)]
        if errors:
            raise SystemExit(f"{house.name}: audit does not match contracts/audit.schema.json: {errors}")
        (house / "audit.json").write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
        results.append((doc, source))
    return results


# ---------------------------------------------------------------- metadata-only mode


def load_model_detail(model_id: str) -> dict | None:
    """Cached Matterport model detail: build_house's cache, or the scout's (which wraps the GraphQL
    response). Never calls the API."""
    for cache in MP_CACHES:
        path = cache / f"{model_id}.json"
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            return data["response"]["data"]["model"] if "response" in data else data
    return None


def rooms_from_model(model: dict, listing_id: str) -> tuple[list[dict], dict]:
    """Rooms (no openings) and a nodes-only graph from Matterport detail, with build_house's room reading,
    label mapping, overrides and primary-bedroom inference. No stairs edges: those come from a built house."""
    from worldgen import build_house as bh

    ov_path = bh.OVERRIDES / f"{model.get('id')}.json"
    overrides = json.loads(ov_path.read_text(encoding="utf-8")) if ov_path.exists() else {}
    parts = bh.apply_splits(bh.read_rooms(model), overrides)
    bh.infer_primary(parts)
    rooms = []
    for i, p in enumerate((q for q in parts if q.keep), 1):
        rooms.append(
            {
                "id": f"{listing_id}.r{i}",
                "label": p.label,
                "label_confidence": round(min(max(p.confidence, 0.0), 1.0), 2),
                "floor": p.floor,
                "floor_y": 0.0,
                "height_m": round(p.height, 2) or 2.4,
                "area_m2": round(p.poly.area, 1),
                "polygon": bh.local_polygon(p.poly),
                "openings": [],
            }
        )
    nodes = [
        {
            "id": r["id"],
            "label": r["label"],
            "area_m2": r["area_m2"],
            "floor": r["floor"],
            "has_window": False,
        }
        for r in rooms
    ]
    return rooms, {"nodes": nodes, "edges": []}


def audit_metadata(listing_id: str, model_id: str | None = None, out: Path = OUT) -> tuple[dict, str, str]:
    """Audit one listing from cached Matterport detail, no built house. Returns (audit, source, model)."""
    listing, source = load_listings([listing_id], out)[listing_id]
    model_id = model_id or (listing.get("source") or {}).get("matterport_model_id")
    if not model_id:
        raise SystemExit(f"{listing_id}: no Matterport model id (pass --model)")
    model = load_model_detail(model_id)
    if model is None:
        raise SystemExit(f"{model_id}: no cached Matterport detail in {', '.join(map(str, MP_CACHES))}")
    rooms, graph = rooms_from_model(model, listing_id)
    doc = audit_listing(
        listing, rooms, graph, {"vexcel": vexcel_extras(listing.get("lat"), listing.get("lon"))}
    )
    return doc, source, model_id


# ---------------------------------------------------------------- CLI


def _print(doc: dict, source: str) -> None:
    print(f"{doc['listing_id']} (listing from {source})")
    for f in doc["findings"]:
        print(f"  {f['severity']:8} {f['check']:16} listed={f['listed']!r} observed={f['observed']!r}")
        print(f"           {f['evidence']}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--block", help="audit every house in packs/<block>/v<N>; writes houses/<id>/audit.json")
    g.add_argument(
        "--listing", help="metadata only: audit one listing from cached Matterport detail and print"
    )
    p.add_argument("--version", type=int, help="--block: pack version (default: newest)")
    p.add_argument("--model", help="--listing: Matterport model id (default: from the listing)")
    p.add_argument("--json", action="store_true", help="--listing: print the audit JSON")
    args = p.parse_args(argv)
    if args.listing:
        doc, source, model_id = audit_metadata(args.listing, args.model)
        if args.json:
            print(json.dumps(doc, indent=1))
        else:
            _print(doc, f"{source}, Matterport {model_id}")
        return 0
    version = args.version or newest_version(OUT, args.block)
    for doc, source in audit_pack(args.block, version):
        _print(doc, source)
    print(f"wrote audit.json for each house in packs/{args.block}/v{version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
