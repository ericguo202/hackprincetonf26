"""Phase 4: budgeted Vexcel checks for the shortlist -> out/scout/vexcel_verify.jsonl, vexcel_clusters.json.

uv run python -m worldgen.scout.vexcel_verify --plan          # targets and call estimate, no calls
uv run python -m worldgen.scout.vexcel_verify --homes         # property/extract per target home
uv run python -m worldgen.scout.vexcel_verify --clusters      # parcel counts + ortho/DSM coverage per cluster
uv run python -m worldgen.scout.vexcel_verify --obliques      # 4 oblique crops for <= 8 non-splat homes
uv run python -m worldgen.scout.vexcel_verify --derive        # parse cached responses -> vexcel_verify.jsonl

Every response is cached (out/scout/cache/vexcel, keyed without the key); the call ledger is
out/scout/ledger/vexcel_calls.jsonl. Hard caps: REST_BUDGET network calls and IMAGE_BUDGET image
extracts in total, counted from the ledger across restarts. Yard area is computed here as parcel area
minus the union of structure footprints, in a local metric projection.
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from shapely import transform as shp_transform
from shapely import wkt as shapely_wkt
from shapely.geometry import Point, shape
from shapely.ops import unary_union

from worldgen.scout.common import (
    DOWNLOADS,
    EARTH_R,
    LEDGER,
    MAX_CONCURRENCY,
    OUT,
    load_env,
    now_iso,
    read_json,
    read_jsonl,
    write_json,
    write_jsonl,
)
from worldgen.scout.vexcel_usage import summarize
from worldgen.sources.vexcel import VexcelClient, VexcelError, VexcelMCP

REST_BUDGET = 260  # total REST network calls allowed in the ledger (brief: ~300 calls overall)
IMAGE_BUDGET = 40  # total image extracts (brief: at most 40 small crops)
OBLIQUE_HOMES = 8
SIDES = ("north", "south", "east", "west")
CACHE_DIR = OUT / "cache" / "vexcel"
CALLS = LEDGER / "vexcel_calls.jsonl"
CLUSTER_SPECS = [
    ("houston", "300", 5),
    ("houston", "600", 3),
    ("houston", "1000", 2),
    ("los_angeles", "1000", 2),
    ("los_angeles", "300", 1),
]


def client() -> VexcelClient:
    load_env()
    return VexcelClient(cache_dir=CACHE_DIR, ledger_path=CALLS, max_network_calls=REST_BUDGET)


# ---------------- targets ----------------


def load() -> tuple[list[dict], dict, dict]:
    scores = list(read_jsonl(OUT / "scores.jsonl"))
    clusters = read_json(OUT / "clusters.json")
    homes = {h["property_id"]: h for h in read_jsonl(OUT / "listings.jsonl")}
    return scores, clusters, homes


def target_clusters(clusters: dict) -> list[dict]:
    """Top clusters per spec, de-duplicated by centre (< 150 m apart counts as the same area)."""
    out: list[dict] = []
    for market, R, n in CLUSTER_SPECS:
        for c in clusters["markets"][market]["clusters"][R][:n]:
            dup = any(
                o["market"] == market
                and math.dist(_xy(o["centre_lat"], o["centre_lon"]), _xy(c["centre_lat"], c["centre_lon"]))
                < 150
                for o in out
            )
            if not dup:
                out.append({**c, "market": market, "key": f"{market}_{R}_{c['rank']}"})
    return out


def target_homes(scores: list[dict], clusters: dict) -> dict[int, list[str]]:
    by_id = {r["property_id"]: r for r in scores}
    t: dict[int, list[str]] = {}

    def add(pid: int, why: str) -> None:
        if pid in by_id and by_id[pid]["lat"] is not None:
            t.setdefault(pid, []).append(why)

    for r in scores[:40]:
        add(r["property_id"], f"top40 rank {r['rank']}")
    for c in target_clusters(clusters):
        for m in c["splat_members"]:
            add(m["property_id"], f"splat in {c['key']}")
        for m in c["interior_only_members"][:3]:
            add(m["property_id"], f"interior-only in {c['key']}")
    for r in scores:
        if (
            r["splat"]
            and not r["skip_reason"]
            and (r["personas"]["pool"]["points"] >= 8 or "Vexcel" in r["personas"]["pool"]["why"])
        ):
            add(r["property_id"], "splat with CoStar pool")
    for k, lst in clusters["strategy_b"]["best_by_persona"].items():
        for e in lst[:3]:
            add(e["property_id"], f"strategy B best {k}")
    for o in oblique_targets(scores, clusters):
        add(o["property_id"], f"oblique Tier B check ({o['cluster']})")
    return t


def oblique_targets(scores: list[dict], clusters: dict) -> list[dict]:
    """Non-splat homes worth a Tier B check: interior-only cluster members with exterior photos."""
    by_id = {r["property_id"]: r for r in scores}
    homes = {h["property_id"]: h for h in read_jsonl(OUT / "listings.jsonl")}
    picked: list[dict] = []
    for c in target_clusters(clusters):
        for m in c["interior_only_members"]:
            r, h = by_id[m["property_id"]], homes[m["property_id"]]
            photos = h["front_photos"] > 0 and (h["rear_photos"] > 0 or h["side_photos"] > 0)
            if (
                photos
                and r["interior"]["total"] >= 24
                and all(p["property_id"] != r["property_id"] for p in picked)
            ):
                picked.append({"property_id": r["property_id"], "cluster": c["key"], "address": r["address"]})
                break  # one per cluster first
    for c in target_clusters(clusters):  # then fill
        for m in c["interior_only_members"]:
            if len(picked) >= OBLIQUE_HOMES:
                return picked
            r, h = by_id[m["property_id"]], homes[m["property_id"]]
            photos = h["front_photos"] > 0 and (h["rear_photos"] > 0 or h["side_photos"] > 0)
            if (
                photos
                and r["interior"]["total"] >= 24
                and all(p["property_id"] != r["property_id"] for p in picked)
            ):
                picked.append({"property_id": r["property_id"], "cluster": c["key"], "address": r["address"]})
    return picked[:OBLIQUE_HOMES]


# ---------------- geometry ----------------


def _xy(lat: float, lon: float, lat0: float = 29.75) -> tuple[float, float]:
    return (math.radians(lon) * EARTH_R * math.cos(math.radians(lat0)), math.radians(lat) * EARTH_R)


def to_local(geom, lat0: float):
    k = math.cos(math.radians(lat0))
    s = EARTH_R * math.pi / 180
    return shp_transform(geom, lambda c: c * [s * k, s])


def circle_wkt(lat: float, lon: float, radius_m: float, n: int = 32) -> str:
    dlat = math.degrees(radius_m / EARTH_R)
    dlon = dlat / math.cos(math.radians(lat))
    pts = [
        (lon + dlon * math.cos(2 * math.pi * i / n), lat + dlat * math.sin(2 * math.pi * i / n))
        for i in range(n)
    ]
    pts.append(pts[0])
    return "POLYGON((" + ", ".join(f"{x:.7f} {y:.7f}" for x, y in pts) + "))"


# ---------------- calls ----------------


def extract_params(lat: float, lon: float) -> dict:
    return {"wkt": f"POINT({lon} {lat})", "srid": 4326}


def fetch_home(vx: VexcelClient, h: dict, mp_geo: tuple[float, float] | None) -> dict:
    meta, body = vx.get_json("property/extract", extract_params(h["lat"], h["lon"]))
    used = "costar"
    inside = _inside(body)
    if (not inside) and mp_geo and _dist(h["lat"], h["lon"], *mp_geo) > 10:
        meta2, body2 = vx.get_json("property/extract", extract_params(*mp_geo))
        if _inside(body2):
            meta, body, used = meta2, body2, "matterport"
    return {
        "property_id": h["property_id"],
        "status": meta["status"],
        "cached": meta["cached"],
        "point": used,
    }


def _inside(body) -> bool:
    feats = (body or {}).get("features") if isinstance(body, dict) else None
    return bool(feats) and bool(feats[0].get("properties", {}).get("request-information.inside-parcel"))


def _dist(lat1, lon1, lat2, lon2) -> float:
    from worldgen.scout.common import haversine_m

    return haversine_m(lat1, lon1, lat2, lon2)


def run_homes(args) -> None:
    scores, clusters, homes = load()
    targets = target_homes(scores, clusters)
    models = {r["model_id"]: r for r in read_jsonl(OUT / "mp_derived.jsonl") if r.get("ok")}
    by_id = {r["property_id"]: r for r in scores}
    vx = client()
    print(f"usage before: {json.dumps(summarize()['rest_by_path'])}", file=sys.stderr)

    def one(pid: int) -> dict:
        h = homes[pid]
        m = models.get(by_id[pid]["model_id"] or "")
        geo = (m["geo_lat"], m["geo_lon"]) if m and m.get("geo_lat") is not None else None
        try:
            return fetch_home(vx, h, geo)
        except VexcelError as e:
            return {"property_id": pid, "error": str(e)}

    with ThreadPoolExecutor(max_workers=MAX_CONCURRENCY) as pool:
        res = list(pool.map(one, sorted(targets)))
    vx.close()
    errs = [r for r in res if r.get("error")]
    print(
        f"homes: {len(res)} targets, {sum(not r.get('cached', True) for r in res)} network, "
        f"{len(errs)} errors",
        file=sys.stderr,
    )
    for e in errs[:5]:
        print("  ", e, file=sys.stderr)
    write_json(
        OUT / "vexcel_targets.json", {"at": now_iso(), "homes": {str(k): v for k, v in targets.items()}}
    )


def run_clusters(args) -> None:
    _, clusters, _ = load()
    load_env()
    mcp = VexcelMCP(ledger_path=CALLS)
    mcp.initialize()
    cache_path = OUT / "vexcel_clusters_raw.json"
    raw = read_json(cache_path) if cache_path.exists() else {}
    for c in target_clusters(clusters):
        key = c["key"]
        if key in raw:
            continue
        radius = max(150.0, c["radius_to_cover_splats_m"] + 100.0)
        poly = circle_wkt(c["centre_lat"], c["centre_lon"], radius)
        entry = {"radius_m": radius, "centre": [c["centre_lat"], c["centre_lon"]]}
        calls = {
            "parcels_all": (
                "property_metadata_count",
                {"wkt": poly, "primary-structure": False, "latest": True},
            ),
            "parcels_primary": (
                "property_metadata_count",
                {"wkt": poly, "primary-structure": True, "latest": True},
            ),
            "ortho": (
                "ortho_imagery_collections",
                {
                    "wkt": poly,
                    "include": [
                        "layer",
                        "collection",
                        "first-capture-date",
                        "last-capture-date",
                        "min-gsd",
                        "product-type",
                    ],
                },
            ),
            "dsm": (
                "dsm_coverage_collections",
                {
                    "wkt": poly,
                    "include": [
                        "layer",
                        "collection",
                        "first-capture-date",
                        "last-capture-date",
                        "min-gsd",
                        "product-type",
                    ],
                },
            ),
        }
        for name, (tool, a) in calls.items():
            try:
                entry[name] = VexcelMCP.content_json(mcp.call_tool(tool, a))
            except VexcelError as e:
                entry[name] = {"error": str(e)}
        raw[key] = entry
        write_json(cache_path, raw)
        print(f"  {key}: done", file=sys.stderr)
    mcp.close()


def run_obliques(args) -> None:
    scores, clusters, homes = load()
    picks = oblique_targets(scores, clusters)
    vx = client()
    used = summarize()["image_extracts"]
    print(f"image extracts used so far: {used}/{IMAGE_BUDGET}; planned homes: {len(picks)}", file=sys.stderr)
    for p in picks:
        pid = p["property_id"]
        body_path = _extract_body_path(pid)
        if not body_path:
            print(f"  {pid}: no property/extract cached; skip", file=sys.stderr)
            continue
        fp = _primary_footprint(json.loads(body_path.read_text()))
        if fp is None:
            print(f"  {pid}: no footprint; skip", file=sys.stderr)
            continue
        outdir = DOWNLOADS / f"vexcel_{pid}"
        outdir.mkdir(parents=True, exist_ok=True)
        for side in SIDES:
            img = outdir / f"oblique_{side}.jpg"
            params = {
                "layer": "urban",
                "wkt": fp.wkt,
                "srid": 4326,
                "buffer-meter": 8,
                "product-type": f"oblique-{side}",
                "crop": "bbox",
                "rotation": "natural",
                "image-format": "jpeg",
            }
            if img.exists():
                continue
            if not vx.cached("oriented/extract", params) and summarize()["image_extracts"] >= IMAGE_BUDGET:
                print("  image budget reached; stop", file=sys.stderr)
                vx.close()
                return
            meta, body = vx.get("oriented/extract", params)
            if meta["status"] == 200 and "image" in meta["content_type"]:
                img.write_bytes(body)
            elif meta["status"] == 200:
                try:  # some endpoints return base64 in JSON
                    data = json.loads(body)
                    b64 = data.get("image") or data.get("data")
                    if b64:
                        img.write_bytes(base64.b64decode(b64))
                except ValueError:
                    pass
            print(
                f"  {pid} {side}: {meta['status']} {meta['content_type']} {meta['bytes']} B", file=sys.stderr
            )
    vx.close()
    write_json(OUT / "vexcel_oblique_targets.json", {"at": now_iso(), "homes": picks})


# ---------------- derive ----------------


def _extract_body_path(pid: int) -> Path | None:
    homes = {h["property_id"]: h for h in read_jsonl(OUT / "listings.jsonl")}
    h = homes[pid]
    for lat, lon in [(h["lat"], h["lon"])] + _mp_geo(pid):
        key = VexcelClient.cache_key("property/extract", extract_params(lat, lon))
        p = CACHE_DIR / f"{key}.body"
        if p.exists():
            body = json.loads(p.read_text())
            if _inside(body) or (lat, lon) == (h["lat"], h["lon"]):
                best = p
                if _inside(body):
                    return p
    return locals().get("best")


_MP_GEO: dict[int, list[tuple[float, float]]] | None = None


def _mp_geo(pid: int) -> list[tuple[float, float]]:
    global _MP_GEO
    if _MP_GEO is None:
        scores = {r["property_id"]: r for r in read_jsonl(OUT / "scores.jsonl")}
        models = {r["model_id"]: r for r in read_jsonl(OUT / "mp_derived.jsonl") if r.get("ok")}
        _MP_GEO = {}
        for p, r in scores.items():
            m = models.get(r["model_id"] or "")
            if m and m.get("geo_lat") is not None:
                _MP_GEO[p] = [(m["geo_lat"], m["geo_lon"])]
    return _MP_GEO.get(pid, [])


def _primary_footprint(body: dict):
    feats = body.get("features") or []
    if not feats:
        return None
    structs = feats[0].get("properties", {}).get("structures") or []
    prim = [s for s in structs if s.get("primary-structure")] or structs
    if not prim:
        return None
    best = max(prim, key=lambda s: s.get("footprint.area") or 0)
    return shapely_wkt.loads(best["footprint.wkt"])


def derive_home(pid: int, h: dict, body_path: Path) -> dict:
    body = json.loads(body_path.read_text())
    feats = body.get("features") or []
    row: dict = {"property_id": pid, "address": h["address"], "source_cache": body_path.name}
    if not feats:
        row.update({"footprint_found": False, "reason": "no parcel feature at point"})
        return row
    f = feats[0]
    p = f.get("properties", {})
    lat0 = h["lat"]
    parcel = shape(f["geometry"]) if f.get("geometry") else None
    structs = p.get("structures") or []
    fps = [shapely_wkt.loads(s["footprint.wkt"]) for s in structs if s.get("footprint.wkt")]
    parcel_m = to_local(parcel, lat0) if parcel is not None else None
    fps_m = unary_union([to_local(g, lat0) for g in fps]) if fps else None
    prim = [s for s in structs if s.get("primary-structure")]
    prim_fp = to_local(shapely_wkt.loads(prim[0]["footprint.wkt"]), lat0) if prim else None
    pt = to_local(Point(h["lon"], h["lat"]), lat0)
    row.update(
        {
            "footprint_found": bool(fps),
            "inside_parcel": p.get("request-information.inside-parcel"),
            "parcel_source": p.get("parcel-source"),
            "parcel_m2": round(parcel_m.area, 1) if parcel_m is not None else None,
            "structure_count": p.get("structure.count"),
            "footprints_m2": round(fps_m.area, 1) if fps_m is not None else None,
            "primary_footprint_m2": round(prim_fp.area, 1) if prim_fp is not None else None,
            "yard_m2": round(parcel_m.difference(fps_m).area, 1)
            if parcel_m is not None and fps_m is not None
            else None,
            "costar_point_to_primary_m": round(prim_fp.distance(pt), 1) if prim_fp is not None else None,
            "pool": p.get("pool.detected"),
            "pool_area_m2": p.get("pool.total-area"),
            "pool_in_ground": p.get("pool.types.in-ground.detected"),
            "pool_enclosed_count": (p.get("pool.types.in-ground.enclosed.count") or 0)
            + (p.get("pool.types.above-ground.enclosed.count") or 0),
            "hot_tub": p.get("pool.types.hot-tub.detected"),
            "trampoline": p.get("trampoline.detected"),
            "playground": p.get("playground.detected"),
            "deck": p.get("deck.detected"),
            "enclosure": p.get("enclosure.detected"),
            "sport_court": p.get("sport-court.detected"),
            "parcel_tree_cover_pct": p.get("parcel-tree-cover.percent"),
            "hardscape_pct": p.get("hardscapes.percent"),
            "roof_material": prim[0].get("roof-characteristics.material.class") if prim else None,
            "roof_shape": prim[0].get("roof-characteristics.shape.value") if prim else None,
            "roof_condition": prim[0].get("roof-characteristics.condition") if prim else None,
            "roof_tree_cover_pct": prim[0].get("roof-characteristics.tree-cover.percent") if prim else None,
            "stories_vexcel": prim[0].get("roof-characteristics.number-of-stories-below-eave")
            if prim
            else None,
            "max_roof_height_m": prim[0].get("roof-characteristics.maximum-roof-height") if prim else None,
            "ground_elevation_m": prim[0].get("ground-elevation.value") if prim else None,
            "building_type": prim[0].get("building-type.class") if prim else None,
            "ortho_collection": p.get("metadata.collection"),
            "ortho_date_estimate": p.get("metadata.ortho-date-estimate"),
            "ortho_layer": p.get("metadata.layer-name"),
            "gsd_m": p.get("metadata.gsd"),
        }
    )
    return row


def run_derive(args) -> None:
    scores, clusters, homes = load()
    rows = []
    for pid in sorted({r["property_id"] for r in scores}):
        bp = _extract_body_path(pid)
        if bp is None:
            continue
        rows.append(derive_home(pid, homes[pid], bp))
    # oblique ratings are written by hand after looking at the crops (phase 4 visual step)
    ratings_path = OUT / "oblique_ratings.json"
    ratings = read_json(ratings_path) if ratings_path.exists() else {}
    for r in rows:
        rt = ratings.get(str(r["property_id"]))
        if rt:
            r["oblique_ratings"] = rt["sides"]
            r["oblique_sides_clear"] = sum(1 for v in rt["sides"].values() if v == "clear")
            r["oblique_note"] = rt.get("note")
    heights = parse_heights()
    for r in rows:
        hgt = heights.get(r["property_id"])
        r["roof_height_m"] = hgt
        r["roof_height_source"] = "max(DSM - DTM) at 1-2 points inside the primary footprint" if hgt else None
        r["stories_est_from_height"] = None if hgt is None else 1 if hgt < 6.5 else 2 if hgt < 10.0 else 3
    write_jsonl(OUT / "vexcel_verify.jsonl", rows)

    raw = read_json(OUT / "vexcel_clusters_raw.json") if (OUT / "vexcel_clusters_raw.json").exists() else {}
    cl_out = {}
    for key, e in raw.items():
        cl_out[key] = {
            "radius_m": e["radius_m"],
            "centre": e["centre"],
            # property_metadata_count with primary-structure=true counts parcels WITH a primary structure;
            # =false returned the (small) rest, so it filters rather than "includes all". Total = sum.
            "parcels_with_primary_structure": _count(e.get("parcels_primary")),
            "parcels_without_primary_structure": _count(e.get("parcels_all")),
            "parcels_total": (_count(e.get("parcels_primary")) or 0) + (_count(e.get("parcels_all")) or 0),
            "ortho_collections": _colls(e.get("ortho")),
            "dsm_collections": _colls(e.get("dsm")),
        }
    write_json(OUT / "vexcel_clusters.json", cl_out)
    print(f"vexcel_verify.jsonl: {len(rows)} homes; clusters: {len(cl_out)}", file=sys.stderr)
    print(json.dumps(cl_out, indent=1)[:3000], file=sys.stderr)


def parse_heights() -> dict[int, float]:
    path = OUT / "vexcel_heights_raw.json"
    if not path.exists():
        return {}
    out: dict[int, float] = {}
    for e in read_json(path).values():
        try:
            dsm = shapely_wkt.loads(e["dsm"]["wkt"]).geoms
            dtm = shapely_wkt.loads(e["dtm"]["wkt"]).geoms
        except (KeyError, TypeError, ValueError):
            continue
        for (pid, _, _), a, b in zip(e["points"], dsm, dtm, strict=False):
            h = round(a.z - b.z, 2)
            out[pid] = max(out.get(pid, h), h)
    return out


def _count(x) -> int | None:
    if isinstance(x, dict):
        for k in ("property-count", "count", "total", "totalCount", "total-count"):
            if k in x and isinstance(x[k], (int, float)):
                return int(x[k])
    if isinstance(x, (int, float)):
        return int(x)
    return None


def _colls(x) -> list[dict]:
    feats = (x or {}).get("features") if isinstance(x, dict) else None
    out = []
    for f in feats or []:
        p = f.get("properties", {})
        out.append(
            {
                k: p.get(k)
                for k in (
                    "collection",
                    "layer",
                    "first-capture-date",
                    "last-capture-date",
                    "min-gsd",
                    "product-type",
                )
                if k in p
            }
        )
    return out


def height_points(rows: list[dict]) -> dict[str, list[tuple[int, float, float]]]:
    """Two sample points inside each primary footprint (centroid if inside, representative point)."""
    pts: dict[str, list[tuple[int, float, float]]] = {"houston": [], "los_angeles": []}
    homes = {h["property_id"]: h for h in read_jsonl(OUT / "listings.jsonl")}
    for r in rows:
        bp = _extract_body_path(r["property_id"])
        fp = _primary_footprint(json.loads(bp.read_text())) if bp else None
        if fp is None:
            continue
        cands = [fp.representative_point()]
        if fp.contains(fp.centroid):
            cands.append(fp.centroid)
        for c in cands:
            pts[homes[r["property_id"]]["market"]].append((r["property_id"], round(c.y, 7), round(c.x, 7)))
    return pts


def run_heights(args) -> None:
    rows = list(read_jsonl(OUT / "vexcel_verify.jsonl"))
    pts = height_points(rows)
    path = OUT / "vexcel_heights_raw.json"
    raw = read_json(path) if path.exists() else {}
    load_env()
    mcp = VexcelMCP(ledger_path=CALLS)
    mcp.initialize()
    for market, plist in pts.items():
        if not plist or market in raw:
            continue
        wkt = "MULTIPOINT(" + ", ".join(f"({lon} {lat})" for _, lat, lon in plist) + ")"
        entry = {"points": plist}
        for name, tool, a in (
            (
                "dsm",
                "dsm_elevation",
                {
                    "wkt": wkt,
                    "layer": ["urban"],
                    "order-by": ["last-capture-date-desc"],
                    "unit-system": "metric",
                },
            ),
            (
                "dtm",
                "dtm_elevation",
                {"wkt": wkt, "dem-priority": ["vexcel-dtm", "public-dtm"], "unit-system": "metric"},
            ),
        ):
            try:
                entry[name] = VexcelMCP.content_json(mcp.call_tool(tool, a))
            except VexcelError as e:
                entry[name] = {"error": str(e)}
        raw[market] = entry
        write_json(path, raw)
        print(f"  {market}: {len(plist)} points", file=sys.stderr)
    mcp.close()


def run_plan(args) -> None:
    scores, clusters, _ = load()
    t = target_homes(scores, clusters)
    cl = target_clusters(clusters)
    ob = oblique_targets(scores, clusters)
    cached = sum(1 for pid in t if _extract_body_path(pid) is not None)
    s = summarize()
    print(
        json.dumps(
            {
                "homes": len(t),
                "homes_cached": cached,
                "clusters": [c["key"] for c in cl],
                "mcp_calls_planned": 4 * len(cl),
                "oblique_homes": ob,
                "image_extracts_planned": 4 * len(ob),
                "ledger_now": {
                    k: s[k] for k in ("rest_calls", "image_extracts", "mcp_tool_calls", "total_calls")
                },
                "estimated_total_after": s["total_calls"] + (len(t) - cached) + 4 * len(cl) + 4 * len(ob),
            },
            indent=1,
        )
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = p.add_mutually_exclusive_group(required=True)
    for flag in ("plan", "homes", "clusters", "heights", "obliques", "derive"):
        g.add_argument(f"--{flag}", action="store_true")
    args = p.parse_args(argv)
    for flag in ("plan", "homes", "clusters", "heights", "obliques", "derive"):
        if getattr(args, flag):
            globals()[f"run_{flag}"](args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
