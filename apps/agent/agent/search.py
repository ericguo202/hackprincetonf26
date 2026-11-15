"""Home search over the listings index: hard filters, then soft needs scored from real fields (no model math).

AGENTS.md section 10 gives search_listings to Lane A (agent/data). This is a temporary Lane B version behind
the same tool signature; see HANDOFF.md. Pure functions: the tool wrapper in agent/tools.py adds the fixed
filters from runtime context and writes /search/results.json.

Evidence follows contracts/agent-events.md `listings.results`: {criterion, passed, value, source}.
`passed` is None when the field is not in the data: unknown is reported, never read as false.
"""

from __future__ import annotations

from collections.abc import Callable

import networkx as nx

HARD = ("status", "property_type", "max_price_usd", "max_rent_usd_month", "min_beds", "min_baths")
SMALL_BEDROOM_M2 = 12.0


def _graph_distance_to_small_bedroom(graph: dict) -> tuple[int | None, str]:
    """Fewest rooms from the primary bedroom to a bedroom under 12 m2, and which bedroom."""
    g = nx.Graph()
    g.add_nodes_from(n["id"] for n in graph.get("nodes", []))
    g.add_edges_from((e["a"], e["b"]) for e in graph.get("edges", []))
    nodes = {n["id"]: n for n in graph.get("nodes", [])}
    primaries = [i for i, n in nodes.items() if n["label"] == "primary_bedroom"]
    small = [i for i, n in nodes.items() if n["label"] == "bedroom" and n["area_m2"] < SMALL_BEDROOM_M2]
    best: tuple[int, str] | None = None
    for p in primaries:
        lengths = nx.single_source_shortest_path_length(g, p)
        for b in small:
            if b in lengths and (best is None or lengths[b] < best[0]):
                best = (lengths[b], f"{b} ({nodes[b]['area_m2']} m2) is {lengths[b]} from {p}")
    if best is None:
        why = "no primary bedroom" if not primaries else f"no bedroom under {SMALL_BEDROOM_M2:g} m2 reachable"
        return None, why
    return best


def _need_checks(listing: dict, graph: dict | None, rooms: list[dict] | None) -> dict[str, Callable]:
    f = listing.get("features") or {}

    def single_story():
        stories = listing.get("stories")
        floors = sorted({r["floor"] for r in rooms}) if rooms else None
        if stories is None and floors is None:
            return None, None, "listing.stories"
        passed = (stories == 1 if stories is not None else True) and (floors is None or len(floors) == 1)
        return (
            passed,
            {"stories": stories, "scanned_floors": len(floors) if floors else None},
            ("listing.stories, rooms[].floor"),
        )

    def no_pool():
        pool = f.get("pool")
        return (None if pool is None else not pool), pool, "listing.features.pool"

    def fenced_yard():
        v = f.get("fenced_yard")
        return v, v, "listing.features.fenced_yard"

    def garage():
        v = f.get("garage")
        return v, v, "listing.features.garage"

    def small_bedroom_near_primary():
        if graph is None:
            return None, None, "graph.json"
        d, detail = _graph_distance_to_small_bedroom(graph)
        if d is None:
            return False, detail, "graph.json"
        # Straight off the primary bedroom earns full credit, one room between (a hallway) half.
        value = {"graph_distance": d, "detail": detail, "credit": 1.0 if d <= 1 else 0.5 if d == 2 else 0.0}
        return d <= 2, value, "graph.json nodes/edges"

    def elementary_school():
        schools = [s for s in listing.get("schools") or [] if s.get("level") == "elementary"]
        if not listing.get("schools"):
            return None, None, "listing.schools"
        return bool(schools), [s["name"] for s in schools] or None, "listing.schools"

    return {
        "single_story": single_story,
        "no_pool": no_pool,
        "fenced_yard": fenced_yard,
        "garage": garage,
        "small_bedroom_near_primary": small_bedroom_near_primary,
        "elementary_school": elementary_school,
    }


NEEDS = (
    "single_story",
    "no_pool",
    "fenced_yard",
    "garage",
    "small_bedroom_near_primary",
    "elementary_school",
)
WEIGHTS = {"high": 3.0, "medium": 2.0, "low": 1.0}


def _hard_failures(listing: dict, hard: dict) -> list[dict]:
    """Hard criteria the listing fails or cannot show. A null price is unknown, so it is not excluded."""
    out = []

    def check(criterion, ok, value, source):
        out.append({"criterion": criterion, "passed": ok, "value": value, "source": source})

    if hard.get("status"):
        check("status", listing.get("status") == hard["status"], listing.get("status"), "listing.status")
    if hard.get("property_type"):
        v = listing.get("property_type")
        check("property_type", v == hard["property_type"], v, "listing.property_type")
    for key, field, op in (
        ("max_price_usd", "price_usd", lambda v, lim: v <= lim),
        ("max_rent_usd_month", "rent_usd_month", lambda v, lim: v <= lim),
        ("min_beds", "beds", lambda v, lim: v >= lim),
        ("min_baths", "baths", lambda v, lim: v >= lim),
    ):
        if hard.get(key) is not None:
            v = listing.get(field)
            check(key, None if v is None else op(v, hard[key]), v, f"listing.{field}")
    return out


def search(
    listings: list[dict],
    hard: dict,
    needs: list[dict],
    house_data: Callable[[str], tuple[dict | None, list[dict] | None]],
    limit: int = 3,
) -> dict:
    """Rank listings: hard criteria filter (failed = excluded, unknown = kept and flagged), soft needs score.

    needs: [{"need": one of NEEDS, "weight": "high" | "medium" | "low"}]. Score is the weighted share of
    needs met, 0 to 1; ties go to the lower price. house_data(listing_id) gives (graph, rooms) or Nones.
    """
    unknown = [n["need"] for n in needs if n["need"] not in NEEDS]
    if unknown:
        raise ValueError(f"unknown needs {unknown}; use {', '.join(NEEDS)}")
    bad = [k for k in hard if k not in HARD]
    if bad:
        raise ValueError(f"unknown hard criteria {bad}; use {', '.join(HARD)}")

    matches, excluded = [], []
    total = sum(WEIGHTS.get(n.get("weight", "medium"), 2.0) for n in needs) or 1.0
    for listing in listings:
        hard_ev = _hard_failures(listing, hard)
        if any(e["passed"] is False for e in hard_ev):
            excluded.append(
                {"listing_id": listing["id"], "failed": [e for e in hard_ev if e["passed"] is False]}
            )
            continue
        graph, rooms = house_data(listing["id"])
        checks = _need_checks(listing, graph, rooms)
        evidence, got = list(hard_ev), 0.0
        for n in needs:
            passed, value, source = checks[n["need"]]()
            evidence.append({"criterion": n["need"], "passed": passed, "value": value, "source": source})
            if passed:
                credit = value.get("credit", 1.0) if isinstance(value, dict) else 1.0
                got += WEIGHTS.get(n.get("weight", "medium"), 2.0) * credit
        matches.append(
            {
                "listing_id": listing["id"],
                "address": listing.get("address"),
                "price_usd": listing.get("price_usd"),
                "score": round(got / total, 3),
                "evidence": evidence,
            }
        )
    matches.sort(key=lambda m: (-m["score"], m["price_usd"] if m["price_usd"] is not None else float("inf")))
    return {"matches": matches[: max(1, limit)], "excluded": excluded, "considered": len(listings)}


M2_PER_SQFT = 0.09290304


def _price_per_m2(listing: dict) -> float | None:
    price, area = listing.get("price_usd"), listing.get("area_m2")
    return price / area if price and area else None


def market(listings: list[dict], hard: dict) -> dict:
    """Counts and medians over the whole index for the same hard criteria. Never returns listings.

    passed_hard: every hard criterion is known and passes ("762 homes fit your budget and beds").
    unknown_hard: none fails but at least one is not in the data (for example no price listed).
    median_price_per_m2_usd: over passed_hard homes with both price_usd and area_m2.
    """
    passed, unknown = [], 0
    for listing in listings:
        ev = _hard_failures(listing, hard)
        if any(e["passed"] is False for e in ev):
            continue
        if any(e["passed"] is None for e in ev):
            unknown += 1
        else:
            passed.append(listing)
    prices = sorted(x["price_usd"] for x in passed if x.get("price_usd"))
    per_m2 = sorted(p for p in map(_price_per_m2, passed) if p is not None)

    def median(xs: list[float]) -> float | None:
        if not xs:
            return None
        mid = len(xs) // 2
        return xs[mid] if len(xs) % 2 else (xs[mid - 1] + xs[mid]) / 2

    med_price, med_m2 = median(prices), median(per_m2)
    return {
        "listings": len(listings),
        "passed_hard": len(passed),
        "unknown_hard": unknown,
        "median_price_usd": None if med_price is None else round(med_price),
        "median_price_per_m2_usd": None if med_m2 is None else round(med_m2),
        "median_price_per_sqft_usd": None if med_m2 is None else round(med_m2 * M2_PER_SQFT),
        "priced_with_area": len(per_m2),
        "source": "listings/index.json price_usd, area_m2",
    }


def add_pricing(matches: list[dict], listings_by_id: dict[str, dict], median_per_m2: float | None) -> None:
    """Per match: listed price per m2 and per sq ft, and percent above (+) or below (-) the market median."""
    for m in matches:
        p = _price_per_m2(listings_by_id.get(m["listing_id"], {}))
        m["price_per_m2_usd"] = None if p is None else round(p)
        m["price_per_sqft_usd"] = None if p is None else round(p * M2_PER_SQFT)
        m["vs_market_pct"] = None if p is None or not median_per_m2 else round(100 * (p / median_per_m2 - 1))
