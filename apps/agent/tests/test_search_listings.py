"""agent/search.py scoring and the save_picks checks."""

import json

import pytest

from agent import search, tools
from tests.test_tools import ROOM, RT, files, fixture_world  # noqa: F401  (pytest fixtures)

INFANT = [
    {"need": "single_story", "weight": "high"},
    {"need": "small_bedroom_near_primary", "weight": "high"},
    {"need": "fenced_yard", "weight": "medium"},
]


def listing(lid, **kw):
    base = {"id": lid, "status": "for_sale", "price_usd": 300000, "beds": 3, "stories": 1}
    base["features"] = {"pool": False, "fenced_yard": None}
    return {**base, **kw}


def graph(lid, hops):
    """Primary bedroom r1; a 9.8 m2 bedroom r9 `hops` rooms away (through hallways)."""
    nodes = [{"id": f"{lid}.r1", "label": "primary_bedroom", "area_m2": 14}]
    nodes += [{"id": f"{lid}.h{i}", "label": "hallway", "area_m2": 3} for i in range(hops - 1)]
    nodes.append({"id": f"{lid}.r9", "label": "bedroom", "area_m2": 9.8})
    ids = [n["id"] for n in nodes]
    return {"nodes": nodes, "edges": [{"a": a, "b": b} for a, b in zip(ids, ids[1:], strict=False)]}


def data_for(hops):
    return lambda lid: (graph(lid, hops[lid]), [{"floor": 0}])


def test_nursery_off_the_primary_beats_one_across_a_hallway():
    homes = [listing("far", price_usd=200000), listing("near", price_usd=250000)]
    out = search.search(homes, {}, INFANT, data_for({"far": 2, "near": 1}))
    assert [m["listing_id"] for m in out["matches"]] == ["near", "far"]
    ev = {e["criterion"]: e for e in out["matches"][0]["evidence"]}
    assert ev["small_bedroom_near_primary"]["value"]["graph_distance"] == 1
    assert ev["fenced_yard"]["passed"] is None  # not listed is unknown, not "no"


def test_hard_criteria_exclude_failures_but_keep_unknowns():
    homes = [
        listing("pricey", price_usd=900000),
        listing("unpriced", price_usd=None),
        listing("two", stories=2),
    ]
    out = search.search(homes, {"max_price_usd": 450000}, INFANT, data_for({"unpriced": 1, "two": 1}))
    assert [e["listing_id"] for e in out["excluded"]] == ["pricey"]
    assert [m["listing_id"] for m in out["matches"]] == ["unpriced", "two"]  # two stories scores lower
    price = next(e for e in out["matches"][0]["evidence"] if e["criterion"] == "max_price_usd")
    assert price["passed"] is None


def test_unknown_need_is_an_error():
    with pytest.raises(ValueError):
        search.search([listing("a")], {}, [{"need": "moat"}], data_for({"a": 1}))


def test_search_listings_tool_only_searches_this_block(files, monkeypatch):  # noqa: F811
    from data import packs

    here = listing("hou_here", block_id="hou_navidad_01", has_interior=True)
    no_scan = listing("hou_noscan", block_id="hou_navidad_01", has_interior=False)
    away = listing("hou_away", block_id="hou_heights_01", has_interior=True)
    monkeypatch.setattr(packs, "listings", lambda: [here, no_scan, away])
    out = json.loads(tools.search_listings.func(needs=INFANT, runtime=RT, max_price_usd=450000))
    assert [m["listing_id"] for m in out["matches"]] == ["hou_here"] and out["considered"] == 1
    assert files.read_json(tools.SEARCH_PATH)["matches"][0]["listing_id"] == "hou_here"
    assert out["market"]["listings"] == 3 and out["market"]["passed_hard"] == 3  # whole index, counts only


def test_market_counts_the_whole_index_and_prices_matches():
    homes = [
        listing("a", price_usd=200000, area_m2=100),  # 2000 $/m2
        listing("b", price_usd=300000, area_m2=200),  # 1500
        listing("c", price_usd=400000, area_m2=400),  # 1000
        listing("pricey", price_usd=900000, area_m2=300),
        listing("unpriced", price_usd=None, area_m2=120),
    ]
    m = search.market(homes, {"max_price_usd": 450000})
    assert (m["listings"], m["passed_hard"], m["unknown_hard"]) == (5, 3, 1)
    assert m["median_price_usd"] == 300000 and m["median_price_per_m2_usd"] == 1500
    assert m["median_price_per_sqft_usd"] == round(1500 * search.M2_PER_SQFT)
    matches = [{"listing_id": "a"}, {"listing_id": "unpriced"}]
    search.add_pricing(matches, {x["id"]: x for x in homes}, m["median_price_per_m2_usd"])
    assert matches[0]["price_per_m2_usd"] == 2000 and matches[0]["vs_market_pct"] == 33
    assert matches[1]["price_per_m2_usd"] is None and matches[1]["vs_market_pct"] is None


def test_market_on_the_real_index_is_fast():
    import time

    from data import packs

    try:
        every = packs.listings()
    except Exception:
        pytest.skip("no listings index on this machine")
    hard = {"status": "for_sale", "property_type": "single_family", "max_price_usd": 450000, "min_beds": 3}
    t = time.perf_counter()
    m = search.market(every, hard)
    assert time.perf_counter() - t < 0.1
    assert m["passed_hard"] <= m["listings"] == len(every)


def test_save_picks_checks_catalog_and_size(files):  # noqa: F811
    def save(*items):
        return json.loads(tools.save_picks.func(room_id=ROOM, items=list(items)))

    ok = save({"asset_id": "sofa_abo_B072C8Q9RF", "role": "seating", "priority": 1})
    assert ok == {"path": f"/plans/{ROOM}.picks.json", "items": 1}
    assert files.read_json(tools.picks_path(ROOM))["items"][0]["name"] == "Sofa"
    assert "unknown asset_id" in save({"asset_id": "nope", "role": "x", "priority": 1})["error"]
    too_wide = save({"asset_id": "huge", "role": "x", "priority": 2})["error"]
    assert "7.00 m wide" in too_wide and "longest free wall" in too_wide
    assert "error" in save({"asset_id": "rug", "role": "x", "priority": 9})  # priority is 1 to 3
