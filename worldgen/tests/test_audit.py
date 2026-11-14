import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from worldgen import audit
from worldgen.sources.vexcel import VexcelClient

CONTRACTS = Path(__file__).resolve().parents[2] / "contracts"
SCHEMA = Draft202012Validator(json.loads((CONTRACTS / "audit.schema.json").read_text()))


def room(i, label, w, d, floor=0, x=0.0):
    return {
        "id": f"hou_1.r{i}",
        "label": label,
        "label_confidence": 0.9,
        "floor": floor,
        "floor_y": 3.0 * floor,
        "height_m": 2.5,
        "area_m2": 999.0,  # the audit must use the polygon, not this field
        "polygon": [[x, 0], [x + w, 0], [x + w, d], [x, d]],
        "openings": [],
    }


def house():
    """One floor, 100 m2 counted (garage excluded): 3 bedrooms, 2 bathrooms."""
    return [
        room(1, "living_room", 6, 5),  # 30
        room(2, "kitchen", 4, 5),  # 20
        room(3, "primary_bedroom", 4, 4),  # 16
        room(4, "bedroom", 3, 4),  # 12
        room(5, "bedroom", 3, 4),  # 12
        room(6, "bathroom", 2, 2),  # 4
        room(7, "bathroom", 2, 2),  # 4
        room(8, "closet", 1, 2),  # 2
        room(9, "garage", 6, 6),  # 36, excluded
    ]


def listing(**kw):
    base = {"id": "hou_1", "stories": 1, "beds": 3, "baths": 2, "area_m2": 100.0, "features": {"pool": False}}
    base.update(kw)
    return base


GRAPH = {"nodes": [], "edges": []}
NO_POOL = {"vexcel": {"pool": False, "evidence": "Vexcel property/extract pool.detected"}}


def by_check(doc):
    return {f["check"]: f for f in doc["findings"]}


def run(lst, rooms=None, graph=GRAPH, extras=NO_POOL):
    doc = audit.audit_listing(lst, house() if rooms is None else rooms, graph, extras)
    assert not list(SCHEMA.iter_errors(doc))
    return by_check(doc)


def test_consistent_house_is_all_info():
    f = run(listing())
    assert set(f) == {"stories_vs_scan", "beds_vs_rooms", "area_vs_scan", "baths_vs_rooms", "pool_vs_vexcel"}
    assert {x["severity"] for x in f.values()} == {"info"}
    assert f["area_vs_scan"]["observed"] == 100.0  # polygons, garage excluded
    assert "consistent" in f["beds_vs_rooms"]["evidence"]


def test_one_story_with_staircase_and_second_floor_is_conflict():
    rooms = [*house(), room(10, "staircase", 1, 3), room(11, "bedroom", 4, 4, floor=1)]
    f = run(listing(beds=4, area_m2=119.0), rooms)
    s = f["stories_vs_scan"]
    assert (s["severity"], s["listed"], s["observed"]) == ("conflict", 1, 2)
    assert "hou_1.r10" in s["evidence"] and "staircase" in s["evidence"]


def test_one_story_with_stairs_edge_only_is_conflict():
    graph = {"nodes": [], "edges": [{"a": "hou_1.r1", "b": "hou_1.r2", "kind": "stairs", "width_m": 1.0}]}
    assert run(listing(), graph=graph)["stories_vs_scan"]["severity"] == "conflict"


def test_two_stories_with_one_scanned_floor_is_warn_not_conflict():
    assert run(listing(stories=2))["stories_vs_scan"]["severity"] == "warn"
    with_stairs = [*house(), room(10, "staircase", 1, 3)]
    assert run(listing(stories=2, area_m2=103.0), with_stairs)["stories_vs_scan"]["severity"] == "info"


def test_beds_mismatch_is_conflict():
    f = run(listing(beds=4))["beds_vs_rooms"]
    assert (f["severity"], f["listed"], f["observed"]) == ("conflict", 4, 3)


def test_office_counted_as_bedroom_is_warn():
    rooms = [*house(), room(10, "office", 3, 3)]
    assert run(listing(beds=4, area_m2=109.0), rooms)["beds_vs_rooms"]["severity"] == "warn"


@pytest.mark.parametrize(
    "listed, severity",
    [
        (100.0, "info"),
        (115.0, "info"),
        (88.0, "info"),
        (120.0, "warn"),
        (80.0, "warn"),
        (150.0, "conflict"),
        (70.0, "conflict"),
    ],
)
def test_area_thresholds(listed, severity):
    f = run(listing(area_m2=listed))["area_vs_scan"]
    assert f["severity"] == severity
    assert f["observed"] == 100.0


def test_area_uses_polygon_not_area_field():
    rooms = house()
    rooms[0]["polygon"] = [[0, 0], [3, 0], [3, 3], [0, 3]]  # 9 instead of 30
    assert run(listing(area_m2=79.0), rooms)["area_vs_scan"]["observed"] == 79.0


@pytest.mark.parametrize(
    "listed, severity", [(2, "info"), (2.5, "info"), (1.5, "info"), (3, "warn"), (1, "warn")]
)
def test_baths(listed, severity):
    assert run(listing(baths=listed))["baths_vs_rooms"]["severity"] == severity


def test_nulls_are_info_not_listed():
    lst = {
        "id": "hou_1",
        "stories": None,
        "beds": None,
        "baths": None,
        "area_m2": None,
        "features": {"pool": None},
    }
    f = run(lst)
    assert {x["severity"] for x in f.values()} == {"info"}
    for check in ("stories_vs_scan", "beds_vs_rooms", "area_vs_scan", "baths_vs_rooms", "pool_vs_vexcel"):
        assert f[check]["listed"] is None
        assert "not listed" in f[check]["evidence"]
    assert f["beds_vs_rooms"]["observed"] == 3


def test_missing_listing_is_all_info():
    f = run({"id": "hou_1"})
    assert {x["severity"] for x in f.values()} == {"info"}


def test_pool():
    assert run(listing(features={"pool": True}))["pool_vs_vexcel"]["severity"] == "conflict"
    assert run(listing())["pool_vs_vexcel"]["severity"] == "info"
    unchecked = run(listing(features={"pool": True}), extras=None)["pool_vs_vexcel"]
    assert (unchecked["severity"], unchecked["observed"]) == ("info", None)
    assert "not checked" in unchecked["evidence"]


def test_listing_from_raw_fallback():
    rec = {
        "id": 42,
        "property": {
            "bedsMin": 3,
            "bathsMin": 2.5,
            "sqftMin": 1111,
            "stories": None,
            "address": {"latitude": 29.7, "longitude": -95.6, "fullStreetAddress": "1 A St"},
        },
        "resoValues": {"poolType": 0, "structure": {"stories": 1}},
    }
    lst = audit.listing_from_raw(rec)
    assert lst["id"] == "hou_42"
    assert (lst["beds"], lst["baths"], lst["stories"], lst["area_m2"]) == (3, 2.5, 1, 103.22)
    assert lst["features"]["pool"] is None
    rec["resoValues"] = {"poolType": 1, "structure": {"stories": 0}}
    lst = audit.listing_from_raw(rec)
    assert lst["features"]["pool"] is True and lst["stories"] is None


def test_load_listings_prefers_index(tmp_path):
    (tmp_path / "listings").mkdir()
    (tmp_path / "listings" / "index.json").write_text(json.dumps([listing(id="hou_7", beds=5)]))
    got = audit.load_listings(["hou_7", "hou_8"], tmp_path, kit=tmp_path / "no-kit")
    assert got["hou_7"] == (listing(id="hou_7", beds=5), "index")
    assert got["hou_8"] == ({"id": "hou_8"}, "missing")


def test_vexcel_extras_reads_cache(tmp_path):
    key = VexcelClient.cache_key("property/extract", {"wkt": "POINT(-95.6 29.7)", "srid": 4326})
    props = {
        "pool.detected": True,
        "request-information.inside-parcel": True,
        "metadata.ortho-date-estimate": "2025-12-31T00:00:00",
    }
    (tmp_path / f"{key}.body").write_text(json.dumps({"features": [{"properties": props}]}))
    assert audit.vexcel_extras(29.7, -95.6, tmp_path)["pool"] is True
    assert audit.vexcel_extras(29.8, -95.6, tmp_path) is None
    props["request-information.inside-parcel"] = False
    (tmp_path / f"{key}.body").write_text(json.dumps({"features": [{"properties": props}]}))
    assert audit.vexcel_extras(29.7, -95.6, tmp_path)["pool"] is None


def test_audit_pack_writes_valid_files(tmp_path):
    hdir = tmp_path / "packs" / "blk" / "v3" / "houses" / "hou_1"
    hdir.mkdir(parents=True)
    (hdir / "rooms.json").write_text(json.dumps(house()))
    (hdir / "graph.json").write_text(json.dumps(GRAPH))
    (tmp_path / "listings").mkdir()
    (tmp_path / "listings" / "index.json").write_text(json.dumps([listing(beds=2, lat=None, lon=None)]))
    [(doc, source)] = audit.audit_pack("blk", 3, tmp_path)
    assert source == "index"
    written = json.loads((hdir / "audit.json").read_text())
    assert written == doc and not list(SCHEMA.iter_errors(written))
    assert by_check(written)["beds_vs_rooms"]["severity"] == "conflict"
