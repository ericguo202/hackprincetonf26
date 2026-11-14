"""Step 1-2: CoStar kit -> out/listings/index.json (Listing schema), joined to Matterport model ids and
placed on the built blocks.

uv run python -m worldgen.costar --in ../costar-hackathon-kit --out out/listings/index.json

Every residential listing that is a home (property_type) and has coordinates becomes one Listing; the id is
`hou_` + the CoStar ListingId. Fields the kit does not have stay null; nothing is guessed. Mapping rules:

- status: transactionType 1 -> for_sale, 2 -> for_rent. The contract has no sold/off-market state, so a
  sold listing is still `for_sale`; its price is null in Texas (non-disclosure state).
- price_usd (sales) / rent_usd_month (rentals): currentPrice, else resoValues.listPrice. MLS 800000 listings
  are CoStar photo-shoot placeholders priced $1: they are kept, with the price null.
- beds, baths, area_m2, year_built, tax: `property`, then the features record's derivedValues. 0 baths and 0
  stories mean "not given" and become null.
- stories: resoValues.structure.stories (the home or unit), then derivedValues.numberOfStories, then
  property.stories (which holds the building's stories on some condos).
- features: pool and garage are true on any evidence (RESO flags, CoStar flags, MLS feature list) and false
  only when the listing has a feature record (its full MLS feature list) without any. fenced_yard is true on
  a fence feature and null otherwise: MLS lists leave fences out too often for absence to mean "no fence".
  utilities are tokens from the Utilities features (gas, electric, public_water, public_sewer, septic, well,
  solar). Placeholder listings carry all-false RESO defaults, so a RESO false is never read as "no".
- schools: the MLS feature list's assigned schools; distance and rating are not in the kit (null).
- block_id: the block (worldgen/blocks/*.json) whose centre is within radius_m; has_interior: a house pack
  exists under out/packs/<block>/v<N>/houses/<id>/; world: only for houses with an interior (house_world).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

from shapely import affinity
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import nearest_points, unary_union

from worldgen.geo import block_xz, distance_m
from worldgen.sources import costar as raw

HERE = Path(__file__).resolve().parent
BLOCKS = HERE / "blocks"

ACRE_M2 = 4046.8564224
SQFT_M2 = 0.09290304
PLACEHOLDER_MLS = 800000  # CoStar photo-shoot placeholder listings, priced $1

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
STATUS = {1: "for_sale", 2: "for_rent"}  # transactionType

MATTERPORT_TOUR = 43  # MediaType: Matterport 3D scan

# Feature-catalog ids (residential-listing-features `features[].id`).
SCHOOL_LEVELS = {3760: "elementary", 3761: "middle", 3762: "high"}
ANNUAL_TAX = 3767
NOT_A_POOL = {549}  # "Room in yard for a pool"
NOT_A_FENCE = {2381, 2383, 2384, 2385}  # fence condition lines ("New Fence", "...in Good Condition")
POOL_PRIVATE = 1  # resoValues.poolType / costarFeatureValues.poolType bit
OUTDOOR_FENCED = 8  # resoValues.outdoorSpaceType bit
# Utilities features -> token, by words in the feature name (subcategory1 Utilities or Eco-Friendly Details).
UTILITY_WORDS = (
    ("gas", ("gas",)),
    ("electric", ("electric",)),
    ("public_water", ("public water", "water connected", "water available")),
    ("public_sewer", ("public sewer", "sewer connected", "sewer available", "sewer in street")),
    ("septic", ("septic",)),
    ("well", ("well",)),
    ("solar", ("solar power", "solar owned")),
)


# ---------------------------------------------------------------------------------------------------------
# Raw record -> Listing fields
# ---------------------------------------------------------------------------------------------------------


def listing_id(costar_id: int) -> str:
    return f"hou_{costar_id}"


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


def is_placeholder(record: dict) -> bool:
    return (record.get("mls") or {}).get("id") == PLACEHOLDER_MLS


def _first(*values: object, positive: bool = False) -> object:
    """First value that is not None (and > 0 when positive)."""
    for v in values:
        if v is None:
            continue
        if positive and not (isinstance(v, int | float) and v > 0):
            continue
        return v
    return None


def _int(v: object) -> int | None:
    return None if v is None else int(round(float(v)))


def address(record: dict) -> str:
    a = (record.get("property") or {}).get("address") or {}
    street = a.get("fullStreetAddress") or ""
    unit = a.get("unitNumber")
    if unit and f"Unit {unit}" not in street:
        street = f"{street} Unit {unit}".strip()
    region = " ".join(x for x in (a.get("state"), a.get("zip")) if x)
    return ", ".join(x for x in (street, a.get("city"), region) if x)


def prices(record: dict) -> tuple[float | None, float | None]:
    """(price_usd, rent_usd_month). Placeholder listings ($1) have no price."""
    status = STATUS.get(record.get("transactionType"))
    if status is None or is_placeholder(record):
        return None, None
    value = _first(
        record.get("currentPrice"), (record.get("resoValues") or {}).get("listPrice"), positive=True
    )
    if status == "for_rent":
        return None, _first(value, record.get("rentMin"), positive=True)
    return value, None


def _features(feat: dict | None) -> list[dict]:
    """MLS features that the listing has (boolValue false means it does not)."""
    return [f for f in (feat or {}).get("features") or [] if f.get("boolValue") is not False]


def _derived(feat: dict | None) -> dict:
    return (feat or {}).get("derivedValues") or {}


def stories(record: dict, feat: dict | None) -> int | None:
    structure = (record.get("resoValues") or {}).get("structure") or {}
    v = _first(
        structure.get("stories"),
        _derived(feat).get("numberOfStories"),
        (record.get("property") or {}).get("stories"),
        positive=True,
    )
    return _int(v)


def parking_spaces(record: dict, feat: dict | None) -> int | None:
    structure = (record.get("resoValues") or {}).get("structure") or {}
    v = _first(
        (record.get("property") or {}).get("totalParkingSpaces"),
        _derived(feat).get("totalParkingSpaces"),
        structure.get("parkingTotal") or None,  # RESO 0 is the default, not "no parking"
    )
    return _int(v)


def pool(record: dict, feat: dict | None) -> bool | None:
    reso = record.get("resoValues") or {}
    flags = (reso.get("poolType") or 0) | ((record.get("costarFeatureValues") or {}).get("poolType") or 0)
    listed = any(
        f.get("subcategory1") == "Pool" and f.get("subcategory2") == "Pool" and f["id"] not in NOT_A_POOL
        for f in _features(feat)
    )
    if flags & POOL_PRIVATE or listed:
        return True
    return False if feat is not None else None


def garage(record: dict, feat: dict | None) -> bool | None:
    reso = record.get("resoValues") or {}
    structure = reso.get("structure") or {}
    d = _derived(feat)
    if (
        reso.get("hasGarage")
        or (record.get("costarFeatureValues") or {}).get("hasGarage")
        or (structure.get("garageSpaces") or 0) > 0
        or (d.get("totalPrivateGarageSpaces") or 0) > 0
        or (d.get("totalGarageSpaces") or 0) > 0
    ):
        return True
    return False if feat is not None else None


def fenced_yard(record: dict, feat: dict | None) -> bool | None:
    if ((record.get("resoValues") or {}).get("outdoorSpaceType") or 0) & OUTDOOR_FENCED:
        return True
    if any(f.get("subcategory2") == "Fence" and f["id"] not in NOT_A_FENCE for f in _features(feat)):
        return True
    return None


def utilities(feat: dict | None) -> list[str]:
    names = [
        f["name"].lower()
        for f in _features(feat)
        if f.get("subcategory1") in ("Utilities", "Eco-Friendly Details") and f["name"] != "No Utilities"
    ]
    return [token for token, words in UTILITY_WORDS if any(w in n for n in names for w in words)]


def schools(feat: dict | None) -> list[dict]:
    """Assigned schools from the MLS feature list. Names use the Homes.com display line when one matches."""
    lines = [
        line
        for s in (feat or {}).get("sections") or []
        if s.get("subcategory") == "Schools"
        for line in s.get("lines") or []
    ]
    out = []
    for f in _features(feat):
        level = SCHOOL_LEVELS.get(f["id"])
        if not level:
            continue
        for value in f.get("stringValues") or []:
            v = value.lower()
            shown = next((ln for ln in lines if v.startswith(ln.lower()) or ln.lower().startswith(v)), None)
            out.append({"name": shown or value.title(), "level": level, "distance_m": None, "rating": None})
    return out


def map_listing(record: dict, feat: dict | None, csv_models: dict[int, list[str]]) -> dict:
    """One Listing; block_id, has_interior and world are filled by build_index."""
    p = record.get("property") or {}
    a = p.get("address") or {}
    d = _derived(feat)
    structure = (record.get("resoValues") or {}).get("structure") or {}
    price, rent = prices(record)
    sqft = _first(p.get("sqftMin"), d.get("homeSquareFeet"), positive=True)
    tax = _first(
        p.get("taxes"),
        next((f.get("doubleValue") for f in _features(feat) if f["id"] == ANNUAL_TAX), None),
        positive=True,
    )
    year = _first(p.get("yearBuilt"), structure.get("yearBuilt"), d.get("yearBuilt"), positive=True)
    return {
        "id": listing_id(record["id"]),
        "address": address(record),
        "lat": a["latitude"],
        "lon": a["longitude"],
        "block_id": None,
        "status": STATUS[record["transactionType"]],
        "property_type": property_type(record),
        "price_usd": price,
        "rent_usd_month": rent,
        "tax_usd_year": tax,
        "beds": _int(_first(p.get("bedsMin"), d.get("totalBedrooms"))),
        "baths": _first(p.get("bathsMin"), d.get("totalBathrooms"), positive=True),
        "area_m2": None if sqft is None else round(sqft * SQFT_M2, 1),
        "stories": stories(record, feat),
        "year_built": _int(year),
        "features": {
            "pool": pool(record, feat),
            "parking_spaces": parking_spaces(record, feat),
            "garage": garage(record, feat),
            "fenced_yard": fenced_yard(record, feat),
            "utilities": utilities(feat),
        },
        "schools": schools(feat),
        "site": {"lot_area_m2": lot_area_m2(record)},
        "has_interior": False,
        "world": None,
        "source": {
            "costar_id": str(record["id"]),
            "matterport_model_id": matterport_model_id(record, csv_models),
            "vexcel": None,
        },
    }


def drop_reason(record: dict) -> str | None:
    if property_type(record) is None:
        return "not_a_home"
    a = (record.get("property") or {}).get("address") or {}
    if a.get("latitude") is None or a.get("longitude") is None:
        return "no_coordinates"
    if record.get("transactionType") not in STATUS:
        return "no_transaction_type"
    return None


# ---------------------------------------------------------------------------------------------------------
# Blocks and house packs
# ---------------------------------------------------------------------------------------------------------


def load_blocks(folder: Path = BLOCKS) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("*.json"))]


def block_for(lat: float, lon: float, blocks: Iterable[dict]) -> str | None:
    """The block whose centre is within its radius_m of (lat, lon); the nearest one if several."""
    best = None
    for b in blocks:
        c = b["center"]
        d = distance_m(lat, lon, c["lat"], c["lon"])
        if d <= b.get("radius_m", 200) and (best is None or d < best[0]):
            best = (d, b["block_id"])
    return best[1] if best else None


def house_dir(out: Path, block_id: str, lid: str) -> Path | None:
    """houses/<listing_id>/ in the block's LIVE pack version (worldgen/blocks/<block>.json), if it has rooms.

    Never "the newest folder": test builds in other versions must not change has_interior or world."""
    from worldgen import versions

    live = versions.live_version_or_none(block_id)
    if live is None:
        return None
    h = versions.pack_dir(block_id, live, out) / "houses" / lid
    return h if (h / "rooms.json").is_file() else None


# ---------------------------------------------------------------------------------------------------------
# World placement (Listing.world). Frames: block = origin at the block centre, +X east, +Y up, +Z south;
# house-local = Matterport model origin, X = model x, Z = -model y, Y up, floor at y = 0 (build_house.py).
# A house-local point p goes to the block as  block = position + rotY(heading_deg) * p,  rotY being the
# counterclockwise-from-above rotation of three.js (apps/web/src/world/pack.ts houseToBlock):
#   x' = x cos h + z sin h,   z' = -x sin h + z cos h.
# ---------------------------------------------------------------------------------------------------------

XZ = tuple[float, float]


def house_to_block(p: XZ, position: XZ, heading_deg: float) -> XZ:
    """House-local (x, z) -> block (x, z)."""
    h = math.radians(heading_deg)
    c, s = math.cos(h), math.sin(h)
    return position[0] + p[0] * c + p[1] * s, position[1] - p[0] * s + p[1] * c


def block_to_house(p: XZ, position: XZ, heading_deg: float) -> XZ:
    """Inverse of house_to_block."""
    h = math.radians(heading_deg)
    c, s = math.cos(h), math.sin(h)
    dx, dz = p[0] - position[0], p[1] - position[1]
    return dx * c - dz * s, dx * s + dz * c


def place_polygon(poly: Polygon, position: XZ, heading_deg: float) -> Polygon:
    """house_to_block for a whole polygon."""
    h = math.radians(heading_deg)
    c, s = math.cos(h), math.sin(h)
    return affinity.affine_transform(poly, [c, s, -s, c, position[0], position[1]])


def plan_angle(dx: float, dz: float) -> float:
    """Direction of an (x, z) vector, degrees counterclockwise from +X (east) seen from above."""
    return math.degrees(math.atan2(-dz, dx))


def matterport_pose(geo: dict, lat0: float, lon0: float) -> tuple[XZ, float]:
    """(position (x, z), heading_deg) of the house-local frame from the model's Matterport geo anchor.

    Matterport answers geoLocationOf(model point) itself (sources/matterport.py GEO_ANCHOR): the model origin
    gives the position, the model +x axis (origin -> (10, 0, 0)) gives the heading, since house-local +X is
    model +x. Without the probes, fall back to the quaternion: model +x points 2*atan2(qz, qw)
    counterclockwise from east (it matches the probes on both Navidad models: 12.97 vs 12.91 deg and
    2.47 vs 2.46 deg)."""
    if geo.get("origin") and geo.get("x10"):
        o = block_xz(float(geo["origin"]["lat"]), float(geo["origin"]["long"]), lat0, lon0)
        x10 = block_xz(float(geo["x10"]["lat"]), float(geo["x10"]["long"]), lat0, lon0)
        return o, plan_angle(x10[0] - o[0], x10[1] - o[1])
    q = geo["rotation"]
    heading = math.degrees(2 * math.atan2(q["z"], q["w"]))
    anchor = block_xz(geo["latitude"], geo["longitude"], lat0, lon0)
    t = geo.get("translation") or {"x": 0.0, "y": 0.0}
    # translation = east/north offset from the anchor to the model origin on both checked models.
    return (anchor[0] + t["x"], anchor[1] - t["y"]), heading


def dominant_angle(rings: Iterable[list[XZ]]) -> float:
    """Main wall direction of closed (x, z) rings, degrees in [-45, 45): circular mean of 4 x edge angle,
    weighted by squared edge length so long walls decide."""
    sx = sy = 0.0
    for ring in rings:
        for a, b in zip(ring, ring[1:] + ring[:1], strict=True):
            dx, dz = b[0] - a[0], b[1] - a[1]
            w = dx * dx + dz * dz
            t = math.radians(4 * plan_angle(dx, dz))
            sx += w * math.cos(t)
            sy += w * math.sin(t)
    return math.degrees(math.atan2(sy, sx)) / 4


def _ring(poly: Polygon) -> list[XZ]:
    return list(poly.exterior.coords)[:-1]


def fit_to_footprint(
    house: Polygon,
    footprint: Polygon,
    position: XZ,
    heading_deg: float,
    max_turn_deg: float = 20.0,
    search_m: float = 6.0,
) -> dict | None:
    """Refine a pose so the house outline (house-local) lies on its building footprint (block frame).

    Heading: the footprint's main wall direction minus the house's, on the 90-degree branch nearest the given
    heading. Position: grid search within search_m for the least house area outside the footprint, ties going
    to the smallest shift (an unscanned garage leaves the footprint larger than the house). None when the turn
    exceeds max_turn_deg or a tenth of the house still falls outside."""
    base = dominant_angle([_ring(footprint)]) - dominant_angle([_ring(house)])
    heading = base + 90 * round((heading_deg - base) / 90)
    if abs(heading - heading_deg) > max_turn_deg:
        return None
    shape = house.buffer(0.1, join_style="mitre")

    def score(pos: XZ) -> tuple[float, float]:
        outside = place_polygon(shape, pos, heading).difference(footprint).area
        return outside + 0.02 * math.dist(pos, position), outside

    best_pos, (best, outside) = position, score(position)
    for step, span in ((0.25, search_m), (0.05, 0.3)):
        cx, cz = best_pos
        n = int(round(span / step))
        for i in range(-n, n + 1):
            for j in range(-n, n + 1):
                pos = (cx + i * step, cz + j * step)
                s = score(pos)
                if s[0] < best:
                    best_pos, (best, outside) = pos, s
    if outside > 0.1 * shape.area:
        return None
    return {"position": best_pos, "heading_deg": heading, "outside_m2": outside}


PRIVATE_ROOMS = {"bedroom", "primary_bedroom", "bathroom", "closet", "garage", "utility"}


def _outward(a: XZ, b: XZ, room: Polygon) -> XZ:
    """Unit normal of opening a-b pointing out of the room (house-local x, z)."""
    dx, dz = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dz) or 1.0
    n = (dz / length, -dx / length)
    mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
    if room.contains(Point(mid[0] + 0.25 * n[0], mid[1] + 0.25 * n[1])):
        n = (-n[0], -n[1])
    return n


def front_door(
    rooms: list[dict], position: XZ, heading_deg: float, streets: list[tuple[str, LineString]]
) -> tuple[list[float], dict]:
    """House-local [x, y, z] of the front door (middle of the opening, on the floor), and why it was chosen.

    Candidates are outside doors (a door opening with no to_room) of rooms that are not private (bedrooms,
    bathrooms, closets, garage). Each is scored by how straight its outward normal points at the nearest
    street (block frame, cosine); the best wins. Without streets, a living-room outside door wins."""
    cands = []
    for r in rooms:
        if r["label"] in PRIVATE_ROOMS:
            continue
        poly = Polygon(r["polygon"])
        for o in r.get("openings") or []:
            if o.get("type") != "door" or o.get("to_room"):
                continue
            a, b = tuple(o["a"]), tuple(o["b"])
            mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
            n = _outward(a, b, poly)
            score, street = (1.0 if r["label"] == "living_room" else 0.0), None
            if streets:
                p = house_to_block(mid, position, heading_deg)
                tip = house_to_block((mid[0] + n[0], mid[1] + n[1]), position, heading_deg)
                street, line = min(streets, key=lambda s: s[1].distance(Point(p)))
                q = nearest_points(line, Point(p))[0]
                d = math.hypot(q.x - p[0], q.y - p[1]) or 1.0
                score = ((tip[0] - p[0]) * (q.x - p[0]) + (tip[1] - p[1]) * (q.y - p[1])) / d
            cands.append((score, r, mid, street))
    if not cands:
        raise ValueError("rooms.json has no outside door")
    score, room, mid, street = max(cands, key=lambda c: c[0])
    why = {
        "room": room["id"],
        "label": room["label"],
        "facing": round(score, 3),
        "street": street,
        "candidates": len(cands),
    }
    return [round(mid[0], 3), round(room.get("floor_y", 0.0), 3), round(mid[1], 3)], why


def street_lines(block: dict) -> list[tuple[str, LineString]]:
    """The block config's street centrelines in the block frame."""
    c = block["center"]
    return [
        (s.get("name", ""), LineString([block_xz(lat, lon, c["lat"], c["lon"]) for lat, lon in s["line"]]))
        for s in block.get("streets") or []
    ]


def house_world(
    rooms: list[dict], geo: dict, block: dict, footprints: list[Polygon] | None = None
) -> tuple[dict, dict]:
    """Listing.world for a house with an interior, and a report of how it was derived.

    1. Matterport's geo anchor gives the first pose (matterport_pose).
    2. If building footprints (block frame) are given, the pose is refined onto the nearest one
       (fit_to_footprint): the Matterport pose is an on-site estimate (source "processing"); for 7014 Navidad
       its heading is 9 deg off the footprint and 17 m2 of the house fell outside it.
    3. The front door is the outside door facing the street (front_door).
    position y is 0: the house floor sits at block ground level until the block has terrain."""
    c = block["center"]
    pos, heading = matterport_pose(geo, c["lat"], c["lon"])
    report: dict = {
        "matterport": {"position": [round(pos[0], 2), round(pos[1], 2)], "heading_deg": round(heading, 2)}
    }
    method = "matterport"
    house = unary_union([Polygon(r["polygon"]) for r in rooms])
    if house.geom_type == "MultiPolygon":
        house = max(house.geoms, key=lambda g: g.area)
    if footprints:
        placed = place_polygon(house, pos, heading)
        fp = min(footprints, key=lambda f: f.distance(placed.centroid))
        fit = fit_to_footprint(house, fp, pos, heading)
        if fit:
            report["footprint_fit"] = {
                "shift_m": round(math.dist(fit["position"], pos), 2),
                "turn_deg": round(fit["heading_deg"] - heading, 2),
                "outside_m2_before": round(placed.difference(fp).area, 2),
                "outside_m2_after": round(fit["outside_m2"], 2),
            }
            pos, heading, method = fit["position"], fit["heading_deg"], "footprint"
        else:
            report["footprint_fit"] = "rejected"
    door, why = front_door(rooms, pos, heading, street_lines(block))
    report.update(method=method, door=why)
    world = {
        "position": [round(pos[0], 2), 0.0, round(pos[1], 2)],
        "heading_deg": round(heading, 1),
        "door": door,
    }
    return world, report


# ---------------------------------------------------------------------------------------------------------
# Inputs for house_world: Matterport geo anchor and Vexcel footprints (cached under out/cache, few calls)
# ---------------------------------------------------------------------------------------------------------


def vexcel_footprints(lat: float, lon: float, block: dict, cache_dir: Path) -> list[Polygon]:
    """Building footprints (block frame) on the parcel at (lat, lon): Vexcel property/extract, cached."""
    from shapely import wkt

    from worldgen.sources.matterport import load_env
    from worldgen.sources.vexcel import VexcelClient

    load_env()
    client = VexcelClient(cache_dir=cache_dir, ledger_path=cache_dir / "ledger.jsonl", max_network_calls=20)
    try:
        meta, body = client.get_json("property/extract", {"wkt": f"POINT({lon} {lat})", "srid": 4326})
    finally:
        client.close()
    if meta.get("status") != 200:
        return []
    c = block["center"]
    out = []
    for feature in body.get("features") or []:
        for s in (feature.get("properties") or {}).get("structures") or []:
            g = wkt.loads(s["footprint.wkt"])
            out.append(Polygon([block_xz(y, x, c["lat"], c["lon"]) for x, y in g.exterior.coords]))
    return out


def place_house(listing: dict, hdir: Path, block: dict, out: Path, use_vexcel: bool = True) -> dict:
    """Fill listing["world"] for a listing with a house pack. Returns the derivation report."""
    from worldgen.sources.matterport import fetch_geo_anchor

    rooms = json.loads((hdir / "rooms.json").read_text(encoding="utf-8"))
    geo = fetch_geo_anchor(listing["source"]["matterport_model_id"], out / "cache" / "matterport")
    footprints: list[Polygon] = []
    if use_vexcel:
        try:
            footprints = vexcel_footprints(listing["lat"], listing["lon"], block, out / "cache" / "vexcel")
        except Exception as e:  # no key, budget, network: keep the Matterport pose
            print(f"warning: {listing['id']}: no Vexcel footprint ({type(e).__name__})", file=sys.stderr)
    listing["world"], report = house_world(rooms, geo, block, footprints)
    return report


# ---------------------------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------------------------


def build_index(kit: Path, out: Path, use_vexcel: bool = True) -> tuple[list[dict], dict]:
    csv_models = raw.matterport_csv(kit)
    feats = {f["id"]: f for f in raw.iter_residential_listing_features(kit)}
    blocks = load_blocks()
    by_id = {b["block_id"]: b for b in blocks}
    listings: list[dict] = []
    dropped: Counter = Counter()
    worlds: dict = {}
    placeholders = 0
    n_raw = 0
    for record in raw.iter_residential_listings(kit):
        n_raw += 1
        reason = drop_reason(record)
        if reason:
            dropped[reason] += 1
            continue
        listing = map_listing(record, feats.get(record["id"]), csv_models)
        placeholders += is_placeholder(record)
        listing["block_id"] = block_for(listing["lat"], listing["lon"], blocks)
        if listing["block_id"] and listing["source"]["matterport_model_id"]:
            hdir = house_dir(out, listing["block_id"], listing["id"])
            if hdir is not None:
                listing["has_interior"] = True
                worlds[listing["id"]] = place_house(
                    listing, hdir, by_id[listing["block_id"]], out, use_vexcel
                )
        listings.append(listing)
    listings.sort(key=lambda x: x["id"])
    stats = {
        "raw": n_raw,
        "kept": len(listings),
        "dropped": dict(dropped),
        "placeholders_kept_price_null": placeholders,
        "for_sale": sum(x["status"] == "for_sale" for x in listings),
        "for_rent": sum(x["status"] == "for_rent" for x in listings),
        "with_interior": sum(x["has_interior"] for x in listings),
        "on_block": dict(Counter(x["block_id"] for x in listings if x["block_id"])),
        "worlds": worlds,
    }
    return listings, stats


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--in", dest="src", type=Path, required=True, help="CoStar kit folder")
    p.add_argument("--out", type=Path, default=Path("out/listings/index.json"))
    p.add_argument(
        "--no-vexcel", action="store_true", help="place houses from Matterport only (no footprint fit)"
    )
    args = p.parse_args(argv)
    out_root = args.out.resolve().parents[1]  # out/listings/index.json -> out
    listings, stats = build_index(args.src, out_root, use_vexcel=not args.no_vexcel)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(listings, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(stats, indent=1))
    print(f"wrote {len(listings)} listings to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
