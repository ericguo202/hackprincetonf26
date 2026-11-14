"""Phase 2b: derive per-model interior facts from the cached Matterport detail.

uv run python -m worldgen.scout.mp_derive

Reads out/scout/cache/mp_detail/*.json and listings.jsonl; writes out/scout/mp_derived.jsonl (one row
per model) and phase2_summary.json. Pure computation, no network. Every number is computed here from
fields named in the row (dimensionEstimates.area, boundary.edges, bundles, assets, geocoordinates).

Room graph: two rooms are adjacent when they list the same boundary edge id. The edge kind is
`door` (edge carries a doorway opening), `open` (edge type `invisible` or a `divider` opening) or
`wall`. Walking paths use door and open edges only.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter, defaultdict, deque
from typing import Any

from shapely.geometry import Polygon

from worldgen.scout.common import OUT, Cache, haversine_m, now_iso, read_jsonl, write_json, write_jsonl
from worldgen.scout.mp import QUERY_VERSION

LIVING = {"Living Room", "Family Room", "Media Room", "Game Room", "Loft", "Bonus Room", "Great Room", "Den"}
PUBLIC = LIVING | {"Kitchen", "Dining Room", "Hallway", "Entrance", "Other", "Office"}
OUTDOOR = {"Patio", "Porch", "Deck", "Yard", "Balcony", "Terrace", "Outdoor", "Exterior", "Garden", "Pool"}
NON_LIVING_AREA = {"Void", "Garage"} | OUTDOOR
AMENITY_NAME = re.compile(
    r"fitness|gym|lobby|leasing|clubhouse|club house|amenit|pool|spa\b|paw|pet|laundry room|business center|"
    r"mail|lounge|rooftop|courtyard|model unit|game room|theater|cinema|conference|office suite",
    re.I,
)
SMALL_BEDROOM_M2 = 12.0
OPEN_PLAN_MIN_M2 = 30.0  # a Kitchen/Dining room this big with no Living label is an open-plan living area
SPECIFIC_MIN_SHARE = 0.3  # below this share of area with specific labels, rooms count as unlabelled
TV_WALL_MIN_M = 3.0


# User-set room labels (Room.label, rare) folded onto the classification vocabulary. Labels that name a
# feature rather than a room (Fireplace, Shower, Elevator, Chimney, Cabinet, Attic, Shed) fall back to the
# room's classification. Order matters: the first matching rule wins.
USER_LABEL_RULES: list[tuple[str, str | None]] = [
    (r"open to below", "Void"),
    (r"fireplace|chimney|elevator|shower$|^shower|cabinet|attic|shed", None),
    (r"garage apartment|above garage", "Other"),
    (r"bedroom|^studio$", "Bedroom"),
    (r"bath|toilet", "Bathroom"),
    (r"kitchen", "Kitchen"),
    (r"great room|family room|living room|formal living", "Living Room"),
    (r"media room|movie room", "Media Room"),
    (r"game room|play ?room", "Game Room"),
    (r"sun ?room|flex|den$|^den|loft", "Den"),
    (r"breakfast|dining", "Dining Room"),
    (r"porch|poch", "Porch"),
    (r"patio|backyard|courtyard|rooftop|terrace|outdoor kitchen", "Patio"),
    (r"deck", "Deck"),
    (r"balcony", "Balcony"),
    (r"^pool", "Pool"),
    (r"garage", "Garage"),
    (r"pantry|storage|closet|wine|bar$|^bar|butler", "Closet"),
    (r"entry|entrance|foyer|landing|hallway|mud ?room|atrium", "Hallway"),
    (r"stair", "Staircase"),
    (r"office|study|workshop|warehouse", "Office"),
    (r"laundry|utility", "Laundry"),
]
_USER_RULES = [(re.compile(p, re.I), b) for p, b in USER_LABEL_RULES]


def labels_of(room: dict) -> tuple[str | None, str | None]:
    """(display label, base label). Base is the classification vocabulary used by every rule here."""
    cls = (room.get("classifications") or [{}])[0].get("label")
    user = room.get("label") or None
    base = cls
    if user:
        for rx, b in _USER_RULES:
            if rx.search(user.strip()):
                base = b if b is not None else cls
                break
    return (user or cls), base


def edge_len(e: dict) -> float | None:
    v = e.get("vertices") or []
    if len(v) < 2:
        return None
    a, b = v[0]["position"], v[-1]["position"]
    return math.hypot(a["x"] - b["x"], a["y"] - b["y"])


def room_polygon(edges: list[dict]) -> Polygon | None:
    """Chain the room's edges (by shared vertex id) into one ring. None if they do not close."""
    adj: dict[str, list[str]] = defaultdict(list)
    pos: dict[str, tuple[float, float]] = {}
    for e in edges:
        v = e.get("vertices") or []
        if len(v) != 2:
            return None
        a, b = v[0], v[1]
        pos[a["id"]] = (a["position"]["x"], a["position"]["y"])
        pos[b["id"]] = (b["position"]["x"], b["position"]["y"])
        adj[a["id"]].append(b["id"])
        adj[b["id"]].append(a["id"])
    if not adj or any(len(n) != 2 for n in adj.values()):
        return None
    start = next(iter(adj))
    ring, prev, cur = [start], None, start
    while True:
        nxt = adj[cur][0] if adj[cur][0] != prev else adj[cur][1]
        if nxt == start:
            break
        ring.append(nxt)
        prev, cur = cur, nxt
        if len(ring) > len(adj):
            return None
    if len(ring) != len(adj) or len(ring) < 3:
        return None
    poly = Polygon([pos[i] for i in ring])
    return poly if poly.is_valid else poly.buffer(0) if poly.buffer(0).geom_type == "Polygon" else None


def bfs(adj: dict[str, set[str]], src: str) -> dict[str, int]:
    dist = {src: 0}
    q = deque([src])
    while q:
        u = q.popleft()
        for v in adj[u]:
            if v not in dist:
                dist[v] = dist[u] + 1
                q.append(v)
    return dist


def components(nodes: list[str], adj: dict[str, set[str]]) -> list[set[str]]:
    seen: set[str] = set()
    out = []
    for n in nodes:
        if n in seen:
            continue
        comp = set(bfs(adj, n))
        seen |= comp
        out.append(comp)
    return out


def derive(model_id: str, rec: dict, home: dict | None) -> dict[str, Any]:
    resp = rec["response"]
    m = (resp.get("data") or {}).get("model")
    row: dict[str, Any] = {
        "model_id": model_id,
        "property_id": home["property_id"] if home else None,
        "query_version": rec.get("query_version"),
        "fetched_at": rec.get("fetched_at"),
    }
    if not m:
        row["ok"] = False
        row["skip_reason"] = f"no model data: {json.dumps(resp.get('errors'))[:200]}"
        return row
    row["ok"] = True
    row["name"] = m.get("name")
    row["created"] = m.get("created")
    pub = m.get("publication") or {}
    row["published"] = pub.get("published")
    row["publication_address"] = pub.get("address")

    geo = m.get("geocoordinates") or {}
    row["geo_lat"], row["geo_lon"] = geo.get("latitude"), geo.get("longitude")
    row["geo_alt"], row["geo_source"] = geo.get("altitude"), geo.get("source")
    row["geo_vs_costar_m"] = None
    if home and home.get("lat") is not None and row["geo_lat"] is not None:
        row["geo_vs_costar_m"] = round(
            haversine_m(home["lat"], home["lon"], row["geo_lat"], row["geo_lon"]), 1
        )

    # ---- bundles and assets ----
    bundles = {b["id"]: b["availability"] for b in m.get("bundles") or []}
    row["bundles_available"] = {k: v for k, v in bundles.items() if v != "unavailable"}
    row["defurnish_bundle"] = bundles.get("mp:defurnish")
    row["matterpak_bundle"] = bundles.get("mp:matterpak")
    row["exteriorsplat_bundle"] = bundles.get("mp:exteriorsplat")
    assets = m.get("assets") or {}
    meshes = assets.get("meshes") or []
    row["mesh_resolutions"] = sorted({x.get("resolution") for x in meshes if x.get("resolution")})
    row["mesh_files"] = sorted({f"{x.get('resolution')}:{x.get('filename')}" for x in meshes})
    row["defurnished_mesh"] = any((x.get("filename") or "").startswith("defurnished") for x in meshes)
    fps = assets.get("floorplans") or []
    row["floorplan_count"] = len(fps)
    row["has_dxf"] = any(x.get("format") == "dxf" for x in fps)
    row["has_colored_rooms"] = any("colored_rooms" in (x.get("flags") or []) for x in fps)
    row["pano_count"] = len(m.get("panoLocations") or [])

    # ---- rooms ----
    rooms: dict[str, dict] = {}
    edge_rooms: dict[str, list[str]] = defaultdict(list)
    edge_info: dict[str, dict] = {}
    floors = m.get("floors") or []
    for fi, fl in enumerate(floors):
        for r in fl.get("rooms") or []:
            de = r.get("dimensionEstimates") or {}
            edges = (r.get("boundary") or {}).get("edges") or []
            lab, base = labels_of(r)
            poly = room_polygon(edges)
            min_dim = None
            if de.get("width") and de.get("depth"):
                min_dim = min(de["width"], de["depth"])
            elif poly is not None:
                rect = poly.minimum_rotated_rectangle.exterior.coords
                sides = [math.dist(rect[i], rect[i + 1]) for i in range(2)]
                min_dim = min(sides)
            clear_walls = [
                edge_len(e) or 0 for e in edges if e.get("type") == "wall" and not e.get("openings")
            ]
            rooms[r["id"]] = {
                "id": r["id"],
                "floor": fi,
                "label": lab,
                "base": base,
                "confidence": ((r.get("classifications") or [{}])[0]).get("confidence"),
                "area": de.get("area") or 0.0,
                "area_indoor": de.get("areaIndoor"),
                "height": de.get("height"),
                "width": de.get("width"),
                "depth": de.get("depth"),
                "min_dim": round(min_dim, 2) if min_dim else None,
                "edges": len(edges),
                "poly_area": round(poly.area, 2) if poly is not None else None,
                "doors": sum(1 for e in edges for o in e.get("openings") or [] if o.get("type") == "doorway"),
                "windows": sum(
                    1 for e in edges for o in e.get("openings") or [] if o.get("type") == "window"
                ),
                "longest_clear_wall": round(max(clear_walls), 2) if clear_walls else 0.0,
            }
            for e in edges:
                edge_rooms[e["id"]].append(r["id"])
                info = edge_info.setdefault(e["id"], {"type": e.get("type"), "door": False, "open": False})
                if e.get("type") == "invisible":
                    info["open"] = True
                for o in e.get("openings") or []:
                    if o.get("type") == "doorway":
                        info["door"] = True
                    elif o.get("type") == "divider":
                        info["open"] = True

    real = {rid: r for rid, r in rooms.items() if r["base"] is not None and r["area"] > 0}
    labelled = {rid: r for rid, r in real.items() if r["base"] != "Void"}
    # ---- adjacency ----
    walk: dict[str, set[str]] = defaultdict(set)
    pair_kind: dict[tuple[str, str], str] = {}
    for eid, rids in edge_rooms.items():
        rids = [x for x in dict.fromkeys(rids) if x in labelled]
        if len(rids) < 2:
            continue
        info = edge_info[eid]
        kind = "door" if info["door"] else "open" if info["open"] else "wall"
        for i in range(len(rids)):
            for j in range(i + 1, len(rids)):
                a, b = sorted((rids[i], rids[j]))
                prev = pair_kind.get((a, b))
                rank = {"door": 2, "open": 1, "wall": 0}
                if prev is None or rank[kind] > rank[prev]:
                    pair_kind[(a, b)] = kind
    for (a, b), kind in pair_kind.items():
        if kind in ("door", "open"):
            walk[a].add(b)
            walk[b].add(a)
    for rid in labelled:
        walk[rid]  # noqa: B018 - ensure isolated rooms exist as nodes

    row["vectors"] = bool(edge_rooms)
    row["rooms_with_boundary"] = sum(1 for r in rooms.values() if r["edges"] > 0)
    row["room_count"] = len(labelled)
    row["edges_total"] = len(edge_info)
    row["edge_types"] = dict(Counter(i["type"] for i in edge_info.values()))
    row["door_edges"] = sum(1 for i in edge_info.values() if i["door"])
    row["shared_edges"] = sum(1 for rids in edge_rooms.values() if len(set(rids)) > 1)
    row["adjacency_pairs"] = dict(Counter(pair_kind.values()))
    row["poly_closed_share"] = (
        round(sum(1 for r in labelled.values() if r["poly_area"] is not None) / len(labelled), 3)
        if labelled
        else None
    )
    poly_err = [
        abs(r["poly_area"] - r["area"]) / r["area"]
        for r in labelled.values()
        if r["poly_area"] and r["area"] > 1
    ]
    row["poly_vs_estimate_median_rel_err"] = (
        round(sorted(poly_err)[len(poly_err) // 2], 3) if poly_err else None
    )

    labels = Counter(r["base"] for r in labelled.values())
    row["labels"] = dict(labels)
    total_area = sum(r["area"] for r in real.values())
    void_area = sum(r["area"] for r in real.values() if r["base"] == "Void")
    living_area = sum(r["area"] for r in labelled.values() if r["base"] not in NON_LIVING_AREA)
    row["area_total_m2"] = round(total_area, 1)
    row["area_living_m2"] = round(living_area, 1)
    row["area_garage_m2"] = round(sum(r["area"] for r in labelled.values() if r["base"] == "Garage"), 1)
    row["void_share"] = round(void_area / total_area, 4) if total_area else None
    row["outdoor_rooms"] = sorted(r["label"] for r in labelled.values() if r["base"] in OUTDOOR)
    specific = sum(r["area"] for r in labelled.values() if r["base"] not in ("Other",))
    row["specific_label_share"] = (
        round(specific / sum(r["area"] for r in labelled.values()), 3) if labelled else 0
    )
    row["rooms_unlabelled"] = row["specific_label_share"] < SPECIFIC_MIN_SHARE
    row["unclassified_rooms"] = sum(1 for r in rooms.values() if r["label"] is None)

    # ---- floors ----
    floor_area = Counter()
    for r in labelled.values():
        if r["base"] not in NON_LIVING_AREA:
            floor_area[r["floor"]] += r["area"]
    real_floors = sorted(f for f, a in floor_area.items() if a >= 10.0)
    row["floors_total"] = len(floors)
    row["floor_labels"] = [f.get("label") for f in floors]
    row["floors_real"] = len(real_floors)
    row["floor_living_m2"] = {str(f): round(a, 1) for f, a in sorted(floor_area.items())}
    kitchens = sorted({r["floor"] for r in labelled.values() if r["base"] == "Kitchen"})
    entry_floor = kitchens[0] if kitchens else (real_floors[0] if real_floors else 0)
    row["entry_floor"] = entry_floor
    row["staircase_rooms"] = labels.get("Staircase", 0)
    row["has_staircase"] = labels.get("Staircase", 0) > 0

    # ---- bedrooms ----
    beds = sorted((r for r in labelled.values() if r["base"] == "Bedroom"), key=lambda r: -r["area"])
    row["bedrooms"] = len(beds)
    row["bedroom_areas_m2"] = [round(r["area"], 1) for r in beds]
    row["bathrooms"] = labels.get("Bathroom", 0)
    row["bedrooms_entry_floor"] = sum(1 for r in beds if r["floor"] == entry_floor)
    primary = next(
        (r for r in beds if r["label"] and "primary" in r["label"].lower()), beds[0] if beds else None
    )
    row["primary_bedroom_m2"] = round(primary["area"], 1) if primary else None
    row["primary_bedroom_floor"] = primary["floor"] if primary else None
    nursery = None
    if primary:
        dist = bfs(walk, primary["id"])
        cands = [
            (dist[r["id"]], r["area"], r)
            for r in beds
            if r["id"] != primary["id"]
            and r["area"] < SMALL_BEDROOM_M2
            and r["id"] in dist
            and dist[r["id"]] <= 2
        ]
        if cands:
            d, _, r = min(cands, key=lambda c: (c[0], -c[1]))
            nursery = {"area_m2": round(r["area"], 1), "graph_distance": d, "floor": r["floor"]}
    row["small_bedroom_near_primary"] = nursery
    row["smallest_bedroom_m2"] = round(beds[-1]["area"], 1) if beds else None

    # ---- living / play ----
    livings = sorted((r for r in labelled.values() if r["base"] in LIVING), key=lambda r: -r["area"])
    row["living_rooms"] = len(livings)
    row["living_source"] = "label" if livings else None
    if not livings:
        big = [
            r
            for r in labelled.values()
            if r["base"] in ("Kitchen", "Dining Room") and r["area"] >= OPEN_PLAN_MIN_M2
        ]
        if big:
            livings = [max(big, key=lambda r: r["area"])]
            row["living_source"] = f"open_plan_{livings[0]['base'].lower().replace(' ', '_')}"
    lr = livings[0] if livings else None
    row["largest_living_m2"] = round(lr["area"], 1) if lr else None
    row["largest_living_label"] = lr["label"] if lr else None
    row["largest_living_min_dim_m"] = lr["min_dim"] if lr else None
    row["largest_living_clear_wall_m"] = lr["longest_clear_wall"] if lr else None
    row["largest_living_windows"] = lr["windows"] if lr else None
    open_adj: dict[str, set[str]] = defaultdict(set)
    for (a, b), kind in pair_kind.items():
        if kind == "open":
            open_adj[a].add(b)
            open_adj[b].add(a)
    if lr:
        comp = set(bfs(open_adj, lr["id"])) if lr["id"] in open_adj else {lr["id"]}
        comp = {c for c in comp if labelled[c]["base"] in PUBLIC}
        row["open_living_area_m2"] = round(sum(labelled[c]["area"] for c in comp), 1)
        row["open_living_rooms"] = sorted(labelled[c]["label"] for c in comp)
    else:
        row["open_living_area_m2"] = None
        row["open_living_rooms"] = []
    # Loops per floor (cyclomatic number of the walkable room graph, one edge per room pair).
    loops = {}
    for f in sorted({r["floor"] for r in labelled.values()}):
        nodes = [rid for rid, r in labelled.items() if r["floor"] == f]
        sub = {n: {x for x in walk[n] if labelled[x]["floor"] == f} for n in nodes}
        e = sum(len(v) for v in sub.values()) // 2
        c = len(components(nodes, sub))
        loops[str(f)] = e - len(nodes) + c
    row["loops_by_floor"] = loops
    row["loops_total"] = sum(loops.values())
    row["loops_entry_floor"] = loops.get(str(entry_floor), 0)
    # Reachability check: rooms not reachable from the entry-floor kitchen through doors/open edges.
    row["walk_components_entry_floor"] = (
        len(
            components(
                [rid for rid, r in labelled.items() if r["floor"] == entry_floor],
                {n: {x for x in walk[n] if labelled[x]["floor"] == entry_floor} for n in labelled},
            )
        )
        if labelled
        else None
    )

    # ---- sanity: is this a real home ----
    name = row["name"] or ""
    num_name = re.match(r"\s*(\d+)", name)
    num_addr = re.match(r"\s*(\d+)", (home or {}).get("address") or "")
    row["name_number_matches_address"] = (
        bool(num_name and num_addr and num_name.group(1) == num_addr.group(1)) if home else None
    )
    row["amenity_name"] = bool(AMENITY_NAME.search(name))
    core = {"Bedroom", "Bathroom", "Kitchen"} | LIVING
    core_found = sorted({r["base"] for r in labelled.values()} & core)
    row["core_labels_found"] = core_found
    row["label_completeness"] = round(
        sum(
            1
            for need in ({"Bedroom"}, {"Bathroom"}, {"Kitchen"}, LIVING)
            if need & {r["base"] for r in labelled.values()}
        )
        / 4,
        2,
    )
    plausible = bool(core_found) or (row["rooms_unlabelled"] and len(labelled) >= 5)
    row["is_real_home"] = bool(
        plausible and living_area >= 25 and len(labelled) >= 3 and not row["amenity_name"]
    )
    reasons = []
    if not plausible:
        reasons.append("no residential room labels")
    if len(labelled) < 3:
        reasons.append(f"only {len(labelled)} labelled rooms")
    if living_area < 25:
        reasons.append(f"living area {living_area:.0f} m2 < 25")
    if row["amenity_name"]:
        reasons.append("amenity-like name")
    row["not_home_reasons"] = reasons
    row["rooms"] = [
        {k: v for k, v in r.items() if k not in ("id",)} | {"id": r["id"]}
        for r in sorted(labelled.values(), key=lambda r: (r["floor"], -r["area"]))
    ]
    return row


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.parse_args(argv)
    cache = Cache("mp_detail")
    homes = list(read_jsonl(OUT / "listings.jsonl"))
    model_home: dict[str, dict] = {}
    for h in homes:
        for mid in h["matterport_model_ids"]:
            model_home.setdefault(mid, h)
    commercial = {c["model_id"] for c in read_jsonl(OUT / "commercial_splats.jsonl")}
    wanted = list(dict.fromkeys(list(model_home) + sorted(commercial)))

    rows, missing = [], []
    for mid in wanted:
        rec = cache.get(mid)
        if rec is None or rec.get("query_version") != QUERY_VERSION:
            missing.append(mid)
            rows.append(
                {
                    "model_id": mid,
                    "property_id": model_home.get(mid, {}).get("property_id"),
                    "ok": False,
                    "skip_reason": "not fetched yet (run mp_fetch)",
                }
            )
            continue
        try:
            row = derive(mid, rec, model_home.get(mid))
        except Exception as e:  # noqa: BLE001 - record and continue
            row = {
                "model_id": mid,
                "ok": False,
                "skip_reason": f"derive error: {type(e).__name__}: {e}"[:300],
            }
        row["group"] = "commercial_splat" if mid in commercial else "home"
        rows.append(row)
    write_jsonl(OUT / "mp_derived.jsonl", rows)

    ok = [r for r in rows if r.get("ok")]
    splat_ids = {m for h in homes for m in h["splat_models_all"]}
    sp = [r for r in ok if r["model_id"] in splat_ids]
    geo_d = sorted(r["geo_vs_costar_m"] for r in ok if r.get("geo_vs_costar_m") is not None)

    def share(rs: list[dict], key: str, val: Any = True) -> str:
        n = sum(1 for r in rs if r.get(key) == val)
        return f"{n}/{len(rs)}"

    summary = {
        "generated_at": now_iso(),
        "models_wanted": len(wanted),
        "models_ok": len(ok),
        "models_missing": len(missing),
        "models_failed": sum(1 for r in rows if not r.get("ok") and r["model_id"] not in missing),
        "real_homes": share(ok, "is_real_home"),
        "vectors": share(ok, "vectors"),
        "defurnish_bundle_unlocked": share(ok, "defurnish_bundle", "unlocked"),
        "defurnished_mesh": share(ok, "defurnished_mesh"),
        "matterpak_unlocked": share(ok, "matterpak_bundle", "unlocked"),
        "exteriorsplat_bundle": dict(Counter(r.get("exteriorsplat_bundle") for r in ok)),
        "dxf": share(ok, "has_dxf"),
        "geo_sources": dict(Counter(r.get("geo_source") for r in ok)),
        "geo_vs_costar_m": {
            "n": len(geo_d),
            "median": geo_d[len(geo_d) // 2] if geo_d else None,
            "p90": geo_d[int(len(geo_d) * 0.9)] if geo_d else None,
            "over_100m": sum(1 for d in geo_d if d > 100),
        },
        "bundles_seen": dict(Counter(f"{k}={v}" for r in ok for k, v in r["bundles_available"].items())),
        "outdoor_room_labels": dict(Counter(lab for r in ok for lab in r["outdoor_rooms"])),
        "splat_models": {
            "ok": len(sp),
            "real_homes": share(sp, "is_real_home"),
            "vectors": share(sp, "vectors"),
            "defurnish_bundle_unlocked": share(sp, "defurnish_bundle", "unlocked"),
            "defurnished_mesh": share(sp, "defurnished_mesh"),
            "dxf": share(sp, "has_dxf"),
        },
    }
    write_json(OUT / "phase2_summary.json", summary)
    print(json.dumps(summary, indent=1), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
