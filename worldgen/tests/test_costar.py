import json
import math
import os
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from shapely.geometry import LineString, Polygon

from worldgen import costar
from worldgen.costar import lot_area_m2, matterport_model_id, property_type
from worldgen.geo import block_xz, distance_m, enu_m, latlon
from worldgen.sources.costar import iter_residential_listings, matterport_csv

ROOT = Path(__file__).resolve().parents[2]
KIT = Path(os.environ.get("COSTAR_KIT_DIR", ROOT / "costar-hackathon-kit"))
OUT = ROOT / "worldgen" / "out"
LISTING_SCHEMA = json.loads((ROOT / "contracts" / "listing.schema.json").read_text(encoding="utf-8"))
NAVIDAD = {"block_id": "hou_navidad_01", "center": {"lat": 29.70113, "lon": -95.670841}, "radius_m": 200}


def schema_errors(listing: dict) -> list[str]:
    return [e.message for e in Draft202012Validator(LISTING_SCHEMA).iter_errors(listing)]


# ---------------------------------------------------------------- raw record mapping


@pytest.mark.parametrize(
    "code, expected",
    [
        (1, "single_family"),
        (2, "townhouse"),
        (4, "condo"),
        (512, "duplex"),
        (16, "multi_family"),
        (64, "other"),
        (0, None),
        (32, None),
        (128, None),
        (None, None),
    ],
)
def test_property_type(code, expected):
    assert property_type({"property": {"propertyType": code}}) == expected


def test_lot_area_m2():
    assert lot_area_m2({"property": {"lotSizeAcresMin": 0.15}}) == 607.0
    assert lot_area_m2({"property": {"lotSizeAcresMin": None}}) is None


def test_matterport_prefers_tour_media():
    csv_models = {7: ["floorplan", "tour"]}
    rec = {"id": 7, "media": [{"type": 10, "videoKey": "floorplan"}, {"type": 43, "videoKey": "tour"}]}
    assert matterport_model_id(rec, csv_models) == "tour"
    assert matterport_model_id({"id": 7, "media": []}, csv_models) == "floorplan"
    assert matterport_model_id({"id": 8}, csv_models) is None


def record(**over) -> dict:
    """A small raw residential listing; keyword args replace top-level keys."""
    rec = {
        "id": 42,
        "transactionType": 1,
        "currentPrice": 300000.0,
        "mls": {"id": 13000},
        "resoValues": {"hasGarage": False, "poolType": 0, "outdoorSpaceType": 0, "structure": {}},
        "property": {
            "propertyType": 1,
            "address": {
                "fullStreetAddress": "1 Test Road",
                "city": "Houston",
                "state": "TX",
                "zip": "77083",
                "latitude": 29.7,
                "longitude": -95.67,
            },
        },
    }
    rec.update(over)
    return rec


def test_map_listing_minimal_record_has_nulls_not_guesses():
    out = costar.map_listing(record(), None, {})
    assert out["id"] == "hou_42" and out["source"]["costar_id"] == "42"
    assert out["address"] == "1 Test Road, Houston, TX 77083"
    assert out["status"] == "for_sale" and out["price_usd"] == 300000.0 and out["rent_usd_month"] is None
    for key in ("tax_usd_year", "beds", "baths", "area_m2", "stories", "year_built"):
        assert out[key] is None, key
    # No feature record: RESO false flags are defaults, not answers.
    assert out["features"] == {
        "pool": None,
        "parking_spaces": None,
        "garage": None,
        "fenced_yard": None,
        "utilities": [],
    }
    assert out["schools"] == [] and out["site"] == {"lot_area_m2": None}
    assert out["world"] is None and out["block_id"] is None and out["source"]["matterport_model_id"] is None
    assert schema_errors(out) == []


def test_map_listing_units_and_fallbacks():
    rec = record(
        property={
            **record()["property"],
            "sqftMin": 1111,
            "bedsMin": 3,
            "bathsMin": 0,
            "stories": 9,
            "lotSizeAcresMin": 0.1645,
            "taxes": 3220,
            "yearBuilt": 1981,
            "unitNumber": None,
        },
        resoValues={"structure": {"stories": 1, "parkingTotal": 0.0}},
    )
    feat = {"features": [], "derivedValues": {"totalBathrooms": 2}}
    out = costar.map_listing(rec, feat, {})
    assert out["area_m2"] == 103.2  # 1111 sqft
    assert out["site"]["lot_area_m2"] == 665.7  # 0.1645 acres
    assert out["baths"] == 2  # 0 baths is "not given": fall back to the feature totals
    assert out["stories"] == 1  # the unit's stories (RESO) before property.stories
    assert out["features"]["parking_spaces"] is None  # RESO parkingTotal 0 is a default
    assert out["tax_usd_year"] == 3220 and out["year_built"] == 1981 and out["beds"] == 3


def test_placeholder_and_rental_prices():
    placeholder = costar.map_listing(record(currentPrice=1.0, mls={"id": 800000}), None, {})
    assert placeholder["price_usd"] is None and placeholder["status"] == "for_sale"
    rental = costar.map_listing(record(transactionType=2, currentPrice=2300.0), None, {})
    assert (
        rental["status"] == "for_rent" and rental["rent_usd_month"] == 2300.0 and rental["price_usd"] is None
    )
    asking = costar.map_listing(record(currentPrice=None, resoValues={"listPrice": 250000.0}), None, {})
    assert asking["price_usd"] == 250000.0


def test_features_from_feature_record():
    feat = {
        "features": [
            {
                "id": 303,
                "name": "Private Pool",
                "subcategory1": "Pool",
                "subcategory2": "Pool",
                "boolValue": False,
            },
            {"id": 3221, "name": "Back Yard Fenced", "subcategory1": "Lot Details", "subcategory2": "Fence"},
            {"id": 2893, "name": "Heating System Uses Gas", "subcategory1": "Utilities"},
            {"id": 1961, "name": "Public Water Source", "subcategory1": "Utilities"},
            {"id": 3473, "name": "Public Sewer", "subcategory1": "Utilities"},
            {
                "id": 3760,
                "name": "Elementary School",
                "subcategory1": "Schools",
                "stringValues": ["MISSION BEND ELEMENTARY SCHOOL"],
            },
            {"id": 3762, "name": "High School", "subcategory1": "Schools", "stringValues": ["AISD DRAW"]},
        ],
        "sections": [
            {"subcategory": "Schools", "lines": ["Mission Bend Elementary School", "Aisd Draw High School"]}
        ],
        "derivedValues": {"totalGarageSpaces": 2, "totalParkingSpaces": 2},
    }
    out = costar.map_listing(record(), feat, {})
    f = out["features"]
    assert f["pool"] is False  # boolValue false: says no pool
    assert f["garage"] is True and f["parking_spaces"] == 2 and f["fenced_yard"] is True
    assert f["utilities"] == ["gas", "public_water", "public_sewer"]
    assert out["schools"] == [
        {"name": "Mission Bend Elementary School", "level": "elementary", "distance_m": None, "rating": None},
        {"name": "Aisd Draw High School", "level": "high", "distance_m": None, "rating": None},
    ]
    # A feature record without any pool/garage/fence line: pool and garage no, fence unknown.
    empty = costar.map_listing(record(), {"features": []}, {})["features"]
    assert (empty["pool"], empty["garage"], empty["fenced_yard"]) == (False, False, None)


def test_drop_reasons():
    assert costar.drop_reason(record()) is None
    assert costar.drop_reason(record(property={"propertyType": 32})) == "not_a_home"
    no_xy = record()
    no_xy["property"]["address"]["latitude"] = None
    assert costar.drop_reason(no_xy) == "no_coordinates"


# ---------------------------------------------------------------- blocks and frames


def test_enu_round_trip_and_scale():
    lat0, lon0 = 29.70113, -95.670841
    for e, n in ((0.0, 0.0), (150.0, -80.0), (-199.0, 12.5)):
        lat, lon = latlon(e, n, lat0, lon0)
        e2, n2 = enu_m(lat, lon, lat0, lon0)
        assert math.isclose(e, e2, abs_tol=1e-6) and math.isclose(n, n2, abs_tol=1e-6)
    # 0.001 deg of latitude is about 110.8 m here; block z is south.
    assert math.isclose(block_xz(lat0 - 0.001, lon0, lat0, lon0)[1], 110.8, abs_tol=0.2)


def test_block_radius():
    c = NAVIDAD["center"]
    inside = latlon(0.0, 199.0, c["lat"], c["lon"])
    outside = latlon(141.0, 142.0, c["lat"], c["lon"])  # 200.1 m away
    assert costar.block_for(*inside, [NAVIDAD]) == "hou_navidad_01"
    assert distance_m(*outside, c["lat"], c["lon"]) > 200
    assert costar.block_for(*outside, [NAVIDAD]) is None


@pytest.mark.parametrize("heading", [0.0, 4.3, 37.0, 90.0, -135.0])
def test_house_block_round_trip(heading):
    pos = (12.0, -30.5)
    for p in ((0.0, 0.0), (1.2, -3.4), (-5.0, 7.0)):
        q = costar.house_to_block(p, pos, heading)
        back = costar.block_to_house(q, pos, heading)
        assert math.isclose(back[0], p[0], abs_tol=1e-9) and math.isclose(back[1], p[1], abs_tol=1e-9)


def test_heading_is_counterclockwise_from_above():
    # three.js rotation.y = +90 deg turns local +X (east) to block -Z (north).
    x, z = costar.house_to_block((1.0, 0.0), (0.0, 0.0), 90.0)
    assert math.isclose(x, 0.0, abs_tol=1e-9) and math.isclose(z, -1.0)
    assert math.isclose(costar.plan_angle(x, z), 90.0)


def geo_for(pos: tuple[float, float], heading: float) -> dict:
    """Synthetic Matterport geo anchor for a house-local origin at block pos with the given heading."""
    c = NAVIDAD["center"]

    def probe(px: float, pz: float) -> dict:
        lat, lon = latlon(px, -pz, c["lat"], c["lon"])
        return {"lat": str(lat), "long": str(lon)}

    x10 = costar.house_to_block((10.0, 0.0), pos, heading)
    half = math.radians(heading) / 2
    return {
        "origin": probe(*pos),
        "x10": probe(*x10),
        "rotation": {"x": 0.0, "y": 0.0, "z": math.sin(half), "w": math.cos(half)},
        "translation": {"x": 0.0, "y": 0.0, "z": 0.0},
        "latitude": float(probe(*pos)["lat"]),
        "longitude": float(probe(*pos)["long"]),
    }


def test_matterport_pose_from_probes_and_quaternion():
    geo = geo_for((-22.8, 97.5), 12.9)
    c = NAVIDAD["center"]
    pos, heading = costar.matterport_pose(geo, c["lat"], c["lon"])
    assert math.dist(pos, (-22.8, 97.5)) < 1e-3 and math.isclose(heading, 12.9, abs_tol=1e-3)
    no_probes = {k: v for k, v in geo.items() if k not in ("origin", "x10")}
    pos2, heading2 = costar.matterport_pose(no_probes, c["lat"], c["lon"])
    assert math.dist(pos2, pos) < 1e-3 and math.isclose(heading2, 12.9, abs_tol=1e-6)


L_HOUSE = Polygon([(0, 0), (10, 0), (10, -6), (4, -6), (4, -11), (0, -11)])  # house-local x, z


def test_dominant_angle():
    assert math.isclose(costar.dominant_angle([list(L_HOUSE.exterior.coords)[:-1]]), 0.0, abs_tol=1e-9)
    turned = costar.place_polygon(L_HOUSE, (0, 0), 7.0)
    assert math.isclose(costar.dominant_angle([list(turned.exterior.coords)[:-1]]), 7.0, abs_tol=1e-6)


def test_fit_to_footprint_recovers_pose():
    truth_pos, truth_heading = (-21.0, 98.0), 4.0
    footprint = costar.place_polygon(L_HOUSE, truth_pos, truth_heading).buffer(0.4, join_style="mitre")
    fit = costar.fit_to_footprint(L_HOUSE, footprint, (-22.5, 97.0), 13.0)
    assert fit is not None
    assert math.isclose(fit["heading_deg"], truth_heading, abs_tol=0.1)
    assert math.dist(fit["position"], truth_pos) < 0.45 and fit["outside_m2"] < 0.05
    assert costar.fit_to_footprint(L_HOUSE, footprint, (-21.0, 98.0), 40.0) is None  # too far to turn


def test_front_door_faces_the_street():
    room = {
        "id": "h.r1",
        "label": "living_room",
        "floor_y": 0.0,
        "polygon": [[0, 0], [5, 0], [5, -4], [0, -4]],
        "openings": [
            {"type": "door", "a": [0, -1.5], "b": [0, -2.4], "to_room": None},  # west wall
            {"type": "door", "a": [5, -1.5], "b": [5, -2.4], "to_room": None},  # east wall
            {"type": "door", "a": [2, 0], "b": [3, 0], "to_room": "h.r2"},  # inside door
        ],
    }
    east_street = [("Test Road", LineString([(30, -100), (30, 100)]))]
    door, why = costar.front_door([room], (0.0, 0.0), 0.0, east_street)
    assert door == [5.0, 0.0, -1.95] and why["street"] == "Test Road" and why["facing"] > 0.99
    # Turn the house 180 degrees: the west wall now faces the street.
    door, _ = costar.front_door([room], (0.0, 0.0), 180.0, east_street)
    assert door[0] == 0.0


def test_house_world_uses_footprint_and_streets():
    truth_pos, truth_heading = (-21.0, 98.0), 4.0
    rooms = [
        {
            "id": "h.r1",
            "label": "living_room",
            "floor_y": 0.0,
            "polygon": [list(p) for p in list(L_HOUSE.exterior.coords)[:-1]],
            "openings": [{"type": "door", "a": [10, -2], "b": [10, -3], "to_room": None}],
        }
    ]
    block = {**NAVIDAD, "streets": [{"name": "East St", "line": [[29.69, -95.6706], [29.71, -95.6706]]}]}
    footprint = costar.place_polygon(L_HOUSE, truth_pos, truth_heading).buffer(0.3, join_style="mitre")
    world, report = costar.house_world(rooms, geo_for((-22.0, 97.0), 12.0), block, [footprint])
    assert report["method"] == "footprint" and world["heading_deg"] == 4.0
    assert math.dist((world["position"][0], world["position"][2]), truth_pos) < 0.35
    assert world["door"] == [10.0, 0.0, -2.5] and report["door"]["street"] == "East St"
    world2, report2 = costar.house_world(rooms, geo_for((-22.0, 97.0), 12.0), block)
    assert report2["method"] == "matterport" and world2["heading_deg"] == 12.0


# ---------------------------------------------------------------- the real kit


needs_kit = pytest.mark.skipif(
    not (KIT / "residential-listings.csv").exists(), reason="CoStar kit not present"
)


@needs_kit
def test_kit_join():
    csv_models = matterport_csv(KIT)
    assert len(csv_models) == 2120
    recs = list(iter_residential_listings(KIT))
    joined = [matterport_model_id(r, csv_models) for r in recs]
    assert sum(m is not None for m in joined) == 2119  # listing 231305778 has no model


@needs_kit
def test_kit_navidad_listings():
    feats = {f["id"]: f for f in costar.raw.iter_residential_listing_features(KIT)}
    csv_models = matterport_csv(KIT)
    recs = {r["id"]: r for r in iter_residential_listings(KIT)}
    hero = costar.map_listing(recs[236595179], feats.get(236595179), csv_models)
    assert hero["id"] == "hou_236595179" and hero["address"] == "7014 Navidad Road, Houston, TX 77083"
    assert (hero["price_usd"], hero["beds"], hero["baths"], hero["stories"]) == (220000.0, 3, 2, 1)
    assert hero["area_m2"] == 103.2 and hero["source"]["matterport_model_id"] == "y1cVPR1HsnL"
    second = costar.map_listing(recs[237901269], feats.get(237901269), csv_models)
    assert (
        second["address"].startswith("6915 Navidad Road")
        and second["source"]["matterport_model_id"] == "xpspAtb5H59"
    )
    assert costar.block_for(hero["lat"], hero["lon"], [NAVIDAD]) == "hou_navidad_01"
    assert costar.block_for(second["lat"], second["lon"], [NAVIDAD]) == "hou_navidad_01"
    dropped = [costar.drop_reason(r) for r in recs.values()]
    assert dropped.count("not_a_home") == 5 and dropped.count("no_coordinates") == 4


@pytest.mark.skipif(
    not (KIT / "residential-listings.csv").exists()
    or not (OUT / "cache" / "matterport" / "y1cVPR1HsnL.geo.json").exists()
    or costar.house_dir(OUT, "hou_navidad_01", "hou_236595179") is None,
    reason="needs the kit, the Navidad house packs and the cached Matterport geo anchors",
)
def test_build_index_validates(tmp_path):
    listings, stats = costar.build_index(KIT, OUT)
    assert stats["kept"] == 2111 and stats["with_interior"] == 2
    errors = [f"{x['id']}: {m}" for x in listings for m in schema_errors(x)]
    assert errors == []
    by_id = {x["id"]: x for x in listings}
    for lid in ("hou_236595179", "hou_237901269"):
        w = by_id[lid]["world"]
        assert by_id[lid]["has_interior"] and w is not None
        assert stats["worlds"][lid]["door"]["label"] == "living_room"


def test_geo_anchor_query_is_read_only():
    from worldgen.sources.matterport import GEO_ANCHOR, assert_read_only

    assert_read_only(GEO_ANCHOR)
