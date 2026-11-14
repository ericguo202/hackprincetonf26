"""Phase 3a: deterministic per-home scores -> out/scout/scores.jsonl.

uv run python -m worldgen.scout.score

Inputs: listings.jsonl (phase 1), mp_derived.jsonl (phase 2), and when present vexcel_verify.jsonl
(phase 4) and visual_check.jsonl (phase 5); rerun after those phases to fold them in.

Every sub-score is written as {points, max, why}, where `why` names the fields read. Total =
exterior (0-35) + interior (0-30) + data (0-15) + best persona fit (0-20). Contradictions are separate
bonus flags. Thresholds are constants below.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter

from worldgen.scout.common import OUT, now_iso, read_jsonl, write_json, write_jsonl

INFANT_BUDGET_USD = 450_000
SMALL_BEDROOM_M2 = 12.0
TV_WALL_M = 3.0
SECTIONAL_MIN_DIM_M = 3.5
RENT_AFFORDABLE_USD = 3_000
AREA_WARN, AREA_CONFLICT = 0.15, 0.30
GEO_OK_M = 50.0
STRONG = 14  # persona score that counts as a strong house

PERSONAS = {
    "infant": "New parents with an infant, buying under $450k",
    "movie": "Family that watches movies together",
    "nerf": "Nerf war / playroom",
    "big_family": "Big or multi-generational family",
    "pool": "Pool / outdoor",
    "renter": "Renter",
    "contradiction": "Contradiction",
}


def sub(points: float, maximum: float, why: str) -> dict:
    return {"points": round(min(points, maximum), 2), "max": maximum, "why": why}


# ---------------- exterior ----------------


def exterior(h: dict, vx: dict | None) -> tuple[str, dict]:
    if h["splat"] and h["splat_tour"] == "OUTSIDE":
        return "A+", sub(35, 35, f"splat {h['splat_model_id']} tour OUTSIDE (splat-models.tsv)")
    if h["splat"]:
        return "A", sub(28, 35, f"splat {h['splat_model_id']} tour Inside only (splat-models.tsv)")
    sides = (vx or {}).get("oblique_sides_clear")
    photos = h["front_photos"] > 0 and (h["rear_photos"] > 0 or h["side_photos"] > 0)
    if sides is not None and sides >= 4 and photos:
        return "B", sub(
            18, 35, "vexcel obliques: 4 sides clear; listing front+rear/side photos (scenes 131-133)"
        )
    if vx and vx.get("footprint_found"):
        return "C", sub(5, 35, "vexcel property/extract footprint; no splat")
    cand = photos or h["media_counts"].get("matterport_exterior", 0) > 0
    why = "no splat; footprint not checked yet" + ("; Tier B candidate (exterior photos)" if cand else "")
    return "C?", sub(5, 35, why)


# ---------------- interior ----------------


def interior(m: dict | None, h: dict) -> dict:
    if not m or not m.get("ok"):
        return {"total": 0, "parts": {}, "why": "no fetched model"}
    p: dict[str, dict] = {}
    p["rooms"] = sub(
        6 * m["label_completeness"],
        6,
        f"label_completeness {m['label_completeness']} (Bedroom, Bathroom, Kitchen, Living found)",
    )
    p["areas"] = sub(
        2 if m["area_living_m2"] > 0 and m["room_count"] >= 5 else 1 if m["area_living_m2"] > 0 else 0,
        2,
        f"dimensionEstimates(units: metric) area; {m['room_count']} labelled rooms",
    )
    vs = m["void_share"]
    p["void"] = sub(
        2 if vs is not None and vs <= 0.03 else 1 if vs is not None and vs <= 0.08 else 0,
        2,
        f"void_share {vs}",
    )
    p["dxf"] = sub(2 if m["has_dxf"] else 0, 2, "assets.floorplans format dxf")
    p["matterpak"] = sub(
        2 if m["matterpak_bundle"] == "unlocked" else 0, 2, f"mp:matterpak {m['matterpak_bundle']}"
    )
    if m["defurnished_mesh"]:
        p["defurnish"] = sub(8, 8, "assets.meshes defurnished_50k.obj present")
    elif m["defurnish_bundle"] == "unlocked":
        p["defurnish"] = sub(2, 8, "mp:defurnish unlocked but no defurnished mesh asset")
    else:
        p["defurnish"] = sub(0, 8, f"mp:defurnish {m['defurnish_bundle']}")
    g = m["geo_vs_costar_m"]
    p["geo"] = sub(
        2 if g is not None and g <= GEO_OK_M else 1 if m["geo_lat"] is not None else 0,
        2,
        f"geocoordinates source {m['geo_source']}, {g} m from CoStar lat/lon",
    )
    pcs = m["poly_closed_share"] or 0
    p["vectors"] = sub(
        (3 if m["vectors"] else 0) + (1 if pcs >= 0.8 else 0),
        4,
        f"boundary edges present={m['vectors']}; closed room polygons {pcs}",
    )
    ratio = m["area_living_m2"] / h["area_m2"] if h.get("area_m2") else None
    p["coverage"] = sub(
        2 if ratio and 0.75 <= ratio <= 1.25 else 1 if ratio and 0.6 <= ratio <= 1.4 else 0,
        2,
        f"scanned living {m['area_living_m2']} m2 / listed {h.get('area_m2')} m2 = "
        f"{round(ratio, 2) if ratio else None}",
    )
    return {"total": round(sum(x["points"] for x in p.values()), 2), "parts": p}


# ---------------- data ----------------


def data_score(h: dict) -> dict:
    present = {
        "price_or_rent": h["price_usd"] is not None or h["rent_usd_month"] is not None,
        "beds": h["beds"] is not None,
        "baths": h["baths"] is not None,
        "area": h["area_m2"] is not None,
        "stories": h["stories"] is not None,
        "year_built": h["year_built"] is not None,
        "lot": h["lot_m2"] is not None,
        "tax": h["tax_usd_year"] is not None,
        "schools": bool(h["schools"]),
        "parking": h["parking_spaces"] is not None or h["garage"] is not None,
        "features_text": h["features_record"],
    }
    pts = sum(present.values()) + (1 if present["price_or_rent"] else 0)  # price counts double
    fields = sub(pts, 12, "present: " + ", ".join(k for k, v in present.items() if v))
    st = h["listing_status"]
    if h.get("placeholder_listing"):
        status = sub(0, 3, "MLS 800000 placeholder listing (photo shoot), not on the market; price $1")
    elif st == "Active" or (h["transaction"] == "rent" and st in ("Active", "Contract")):
        status = sub(3, 3, f"listingStatus {st}, {h['transaction']}")
    elif st == "Contract":
        status = sub(2, 3, "listingStatus Contract")
    elif h["has_rent_listing"]:
        status = sub(1, 3, f"listingStatus {st}; home has a rent listing")
    else:
        status = sub(0, 3, f"listingStatus {st}")
    return {"total": fields["points"] + status["points"], "parts": {"fields": fields, "status": status}}


# ---------------- personas ----------------


def persona_scores(h: dict, m: dict | None, vx: dict | None, vis: dict | None, contra: list[dict]) -> dict:
    m = m if m and m.get("ok") else {}
    out: dict[str, dict] = {}

    # 1 infant
    price = h["price_usd"]
    if h["transaction"] == "sale" and price is not None and price <= INFANT_BUDGET_USD:
        parts = []
        pts = 0.0
        one_floor = m.get("floors_real") == 1
        entry_bed = (m.get("bedrooms_entry_floor") or 0) >= 1 or (h.get("main_level_bedrooms") or 0) >= 1
        if one_floor or entry_bed:
            pts += 5
            parts.append("one floor" if one_floor else "bedroom on entry floor")
        nur = m.get("small_bedroom_near_primary")
        if nur:
            pts += 6
            parts.append(f"{nur['area_m2']} m2 bedroom {nur['graph_distance']} step(s) from primary")
        pool_ok = not h["pool_private"] or h["pool_fenced_text"]
        if vx and vx.get("pool") is True and not h["pool_fenced_text"]:
            pool_ok = False
        if pool_ok:
            pts += 3 if not h["pool_private"] else 2
            parts.append("no pool listed" if not h["pool_private"] else "pool fenced")
        if h["fenced_yard"]:
            pts += 3
            parts.append("fenced yard")
        if any(s["level"] == "elementary" for s in h["schools"]):
            pts += 2
            parts.append("elementary school named")
        if (h["beds"] or 0) >= 2:
            pts += 1
        out["infant"] = sub(pts, 20, f"${price:,.0f} <= $450k; " + "; ".join(parts))
    else:
        out["infant"] = sub(0, 20, f"not a sale under $450k (transaction {h['transaction']}, price {price})")

    # 2 movie
    pts, parts = 0.0, []
    la = m.get("largest_living_m2")
    if la and la >= 20:
        pts += 8 + (2 if la >= 30 else 0)
        parts.append(f"{m.get('largest_living_label')} {la} m2 ({m.get('living_source')})")
    tv = m.get("largest_living_clear_wall_m") or 0
    if la and tv >= TV_WALL_M:
        pts += 5 + (1 if tv >= 4 else 0)
        parts.append(f"clear wall {tv} m")
    md = m.get("largest_living_min_dim_m")
    if la and md and md >= SECTIONAL_MIN_DIM_M:
        pts += 4
        parts.append(f"min dimension {md} m")
    out["movie"] = sub(pts, 20, "; ".join(parts) or "no living area >= 20 m2")

    # 3 nerf
    pts, parts = 0.0, []
    ol = m.get("open_living_area_m2") or 0
    if ol >= 40:
        pts += 6 + (2 if ol >= 70 else 0)
        parts.append(f"open living {ol} m2")
    loops = m.get("loops_entry_floor") or 0
    if loops >= 1:
        pts += 5 + (2 if loops >= 2 else 0)
        parts.append(f"{loops} loop(s) on entry floor")
    yard = (vx or {}).get("yard_m2")
    if h["splat"] and ((yard and yard >= 100) or (yard is None and (h["lot_m2"] or 0) >= 400)):
        pts += 3
        parts.append(
            f"splat with yard ({'vexcel yard ' + str(yard) if yard else 'lot ' + str(h['lot_m2'])} m2)"
        )
    if m.get("outdoor_rooms"):
        pts += 2
        parts.append("scan has " + ", ".join(m["outdoor_rooms"]))
    out["nerf"] = sub(pts, 20, "; ".join(parts) or "no open area or loops")

    # 4 big family
    pts, parts = 0.0, []
    beds = max(m.get("bedrooms") or 0, h["beds"] or 0)
    if beds >= 4:
        pts += 8 + (2 if beds >= 5 else 0)
        parts.append(f"{beds} bedrooms (scan {m.get('bedrooms')}, listed {h['beds']})")
    if (m.get("floors_real") or 0) >= 2:
        pts += 5
        parts.append(f"{m['floors_real']} floors")
    if m.get("has_staircase"):
        pts += 3
        parts.append("Staircase room")
    if (h["baths"] or 0) >= 3:
        pts += 2
        parts.append(f"{h['baths']} baths")
    out["big_family"] = sub(pts, 20, "; ".join(parts) or "fewer than 4 bedrooms, one floor")

    # 5 pool
    pts, parts = 0.0, []
    if h["pool_private"]:
        pts += 8
        parts.append("CoStar private pool")
    if vx and vx.get("pool") is True:
        pts += 6
        parts.append("Vexcel pool")
    if vis and vis.get("pool_visible_drone") is True:
        pts += 4
        parts.append("pool visible in drone thumbnail")
    elif h["scene_counts"].get("swimming_pool"):
        pts += 2
        parts.append(f"{h['scene_counts']['swimming_pool']} pool photos (scene 19)")
    if m.get("outdoor_rooms"):
        pts += 2
        parts.append("scan outdoor room")
    out["pool"] = sub(pts, 20, "; ".join(parts) or "no pool")

    # 6 renter
    if h["has_rent_listing"]:
        pts, parts = 10.0, ["rent listing"]
        if h["transaction"] == "rent" and h["listing_status"] in ("Active", "Contract"):
            pts += 5
            parts.append(f"{h['listing_status']} rent")
        rent = h["rent_usd_month"]
        if rent is not None and rent <= RENT_AFFORDABLE_USD:
            pts += 3
            parts.append(f"${rent:,.0f}/mo")
        if (h["beds"] or 0) >= 2:
            pts += 2
        out["renter"] = sub(pts, 20, "; ".join(parts))
    else:
        out["renter"] = sub(0, 20, "no rent listing")

    # 7 contradiction
    strong = [c for c in contra if c["severity"] == "conflict"]
    weak = [c for c in contra if c["severity"] == "warn"]
    pts = 20 if len(strong) >= 2 else 14 if strong else 7 if weak else 0
    out["contradiction"] = sub(pts, 20, "; ".join(c["check"] for c in contra) or "none")
    return out


# ---------------- contradictions ----------------


def contradictions(h: dict, m: dict | None, vx: dict | None) -> list[dict]:
    out: list[dict] = []
    if not m or not m.get("ok"):
        return out
    labels_ok = m["label_completeness"] >= 0.75
    st = h["stories"] or None  # 0 means unknown in CoStar, not "zero stories"
    if st is not None:
        if st <= 1 and (m["floors_real"] >= 2 or m["has_staircase"]):
            out.append(
                {
                    "check": "stories_vs_scan",
                    "severity": "conflict",
                    "listed": f"{st} story ({h['stories_source']})",
                    "observed": f"{m['floors_real']} floors, {m['staircase_rooms']} Staircase room(s)",
                    "evidence": f"model {m['model_id']} floors {m['floor_labels']}",
                }
            )
        elif st >= 2 and m["floors_real"] == 1 and not m["has_staircase"]:
            out.append(
                {
                    "check": "stories_vs_scan",
                    "severity": "warn",
                    "listed": f"{st} stories ({h['stories_source']})",
                    "observed": "1 scanned floor, no Staircase room",
                    "evidence": f"model {m['model_id']}",
                }
            )
    if h["beds"] is not None and labels_ok:
        d = m["bedrooms"] - h["beds"]
        if d != 0:
            out.append(
                {
                    "check": "beds_vs_scan",
                    "severity": "conflict" if abs(d) >= 2 else "warn",
                    "listed": f"{h['beds']} beds (property.bedsMin)",
                    "observed": f"{m['bedrooms']} Bedroom rooms {m['bedroom_areas_m2']}",
                    "evidence": f"model {m['model_id']} classifications",
                }
            )
    if h["area_m2"] and m["area_living_m2"]:
        ratio = m["area_living_m2"] / h["area_m2"]
        dev = abs(ratio - 1)
        if dev > AREA_WARN:
            out.append(
                {
                    "check": "area_vs_scan",
                    "severity": "conflict" if dev > AREA_CONFLICT else "warn",
                    "listed": f"{h['area_sqft']} sqft = {h['area_m2']} m2 (property.sqftMin)",
                    "observed": f"{m['area_living_m2']} m2 scanned living area (ratio {ratio:.2f})",
                    "evidence": f"model {m['model_id']} sum of area excl. Void/Garage/outdoor",
                }
            )
    if h["main_level_bedrooms"] is not None and labels_ok:
        d = m["bedrooms_entry_floor"] - h["main_level_bedrooms"]
        if abs(d) >= 2:
            out.append(
                {
                    "check": "main_level_beds_vs_scan",
                    "severity": "warn",
                    "listed": f"{h['main_level_bedrooms']} main level bedrooms (features text)",
                    "observed": f"{m['bedrooms_entry_floor']} Bedrooms on entry floor {m['entry_floor']}",
                    "evidence": f"model {m['model_id']}",
                }
            )
    if vx and vx.get("pool") is not None and vx["pool"] != h["pool_private"]:
        out.append(
            {
                "check": "pool_vs_vexcel",
                "severity": "conflict",
                "listed": f"private pool {h['pool_private']} (resoValues.poolType / features Pool)",
                "observed": f"Vexcel pool {vx['pool']}",
                "evidence": "vexcel property/extract attributes",
            }
        )
    # property/extract never filled number-of-stories (all null); use roof height = DSM - DTM instead.
    hgt = (vx or {}).get("roof_height_m")
    if hgt is not None and st is not None and ((st <= 1 and hgt >= 7.5) or (st >= 2 and hgt < 5.0)):
        out.append(
            {
                "check": "stories_vs_vexcel_height",
                "severity": "warn",
                "listed": f"{st} stories ({h['stories_source']})",
                "observed": f"roof {hgt} m (DSM - DTM), ~{vx['stories_est_from_height']} stories",
                "evidence": "vexcel dsm_elevation / dtm_elevation inside the primary footprint",
            }
        )
    g = m.get("geo_vs_costar_m")
    if g is not None and g > 100:
        out.append(
            {
                "check": "location_vs_scan",
                "severity": "info",
                "listed": f"CoStar {h['lat']}, {h['lon']}",
                "observed": f"Matterport geocoordinates {g:.0f} m away",
                "evidence": f"model {m['model_id']} geocoordinates source {m['geo_source']}",
            }
        )
    return out


# ---------------- main ----------------


def pick_model(h: dict, models: dict[str, dict]) -> tuple[dict | None, str]:
    """Splat homes use their splat model (it is registered to the exterior). Others: best interior."""
    if h["splat"] and h["splat_model_id"] in models:
        return models[h["splat_model_id"]], "splat model"
    cands = [models[x] for x in h["matterport_model_ids"] if x in models and models[x].get("ok")]
    if not cands:
        return None, "no fetched model"
    own = set(h.get("media_model_ids") or [])
    best = max(
        cands,
        key=lambda m: (m["is_real_home"], m["model_id"] in own, interior(m, h)["total"], m["area_living_m2"]),
    )
    return best, f"best interior of {len(cands)} model(s)"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.parse_args(argv)
    homes = list(read_jsonl(OUT / "listings.jsonl"))
    models = {r["model_id"]: r for r in read_jsonl(OUT / "mp_derived.jsonl")}
    vexcel = {r["property_id"]: r for r in read_jsonl(OUT / "vexcel_verify.jsonl")}
    visual = {r["property_id"]: r for r in read_jsonl(OUT / "visual_check.jsonl")}

    rows = []
    for h in homes:
        m, why_model = pick_model(h, models)
        vx = vexcel.get(h["property_id"])
        vis = visual.get(h["property_id"])
        tier, ext = exterior(h, vx)
        inte = interior(m, h)
        dat = data_score(h)
        contra = contradictions(h, m, vx)
        pers = persona_scores(h, m, vx, vis, contra)
        best_p = max(pers, key=lambda k: (pers[k]["points"], k != "contradiction"))
        total = ext["points"] + inte["total"] + dat["total"] + pers[best_p]["points"]
        skip = None
        if m is None:
            skip = "no Matterport model fetched"
        elif not m.get("is_real_home"):
            skip = "model is not a real home: " + "; ".join(m.get("not_home_reasons") or [])
        rows.append(
            {
                "property_id": h["property_id"],
                "listing_id": h["listing_id"],
                "address": h["address"],
                "unit": h["unit"],
                "city": h["city"],
                "market": h["market"],
                "lat": h["lat"] if h["lat"] is not None else (m or {}).get("geo_lat"),
                "lon": h["lon"] if h["lon"] is not None else (m or {}).get("geo_lon"),
                "coords_source": "costar" if h["lat"] is not None else "matterport" if m else None,
                "model_id": (m or {}).get("model_id"),
                "model_choice": why_model,
                "splat": h["splat"],
                "splat_tour": h["splat_tour"],
                "splat_mb": h["splat_mb"],
                "exterior_tier": tier,
                "exterior": ext,
                "interior": inte,
                "data": dat,
                "personas": pers,
                "best_persona": best_p,
                "best_persona_points": pers[best_p]["points"],
                "strong_personas": sorted(k for k, v in pers.items() if v["points"] >= STRONG),
                "contradictions": contra,
                "contradiction_flags": sum(1 for c in contra if c["severity"] in ("conflict", "warn")),
                "total": round(total, 2),
                # Hero: splat exterior, good interior, real listing data (placeholders have no price/status).
                "hero_eligible": tier in ("A+", "A")
                and inte["total"] >= 20
                and dat["total"] >= 10
                and not h.get("placeholder_listing")
                and skip is None,
                "placeholder_listing": h.get("placeholder_listing"),
                "skip_reason": skip,
                "listing_status": h["listing_status"],
                "transaction": h["transaction"],
                "price_usd": h["price_usd"],
                "rent_usd_month": h["rent_usd_month"],
                "beds": h["beds"],
                "baths": h["baths"],
                "area_m2": h["area_m2"],
                "stories": h["stories"],
                "defurnished_mesh": (m or {}).get("defurnished_mesh"),
                "floors_real": (m or {}).get("floors_real"),
                "vexcel_checked": vx is not None,
                "visual_checked": vis is not None,
            }
        )
    # Totals saturate near the top, so break ties on demo value: strong personas, defurnished, interior.
    rows.sort(
        key=lambda r: (
            -r["total"],
            -len(r["strong_personas"]),
            not r["defurnished_mesh"],
            -r["interior"]["total"],
            r["property_id"],
        )
    )
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    write_jsonl(OUT / "scores.jsonl", rows)

    sp = [r for r in rows if r["splat"]]
    ratios = sorted(
        models[r["model_id"]]["area_living_m2"] / r["area_m2"]
        for r in rows
        if r["model_id"] and r["area_m2"] and models.get(r["model_id"], {}).get("ok")
    )
    summary = {
        "generated_at": now_iso(),
        "homes": len(rows),
        "skipped": dict(Counter((r["skip_reason"] or "").split(":")[0] for r in rows if r["skip_reason"])),
        "tiers": dict(Counter(r["exterior_tier"] for r in rows)),
        "hero_eligible": sum(r["hero_eligible"] for r in rows),
        "splat_homes_interior_ge_20": sum(r["interior"]["total"] >= 20 for r in sp),
        "homes_interior_ge_20": sum(r["interior"]["total"] >= 20 for r in rows),
        "strong_by_persona_all": {k: sum(k in r["strong_personas"] for r in rows) for k in PERSONAS},
        "strong_by_persona_splat": {k: sum(k in r["strong_personas"] for r in sp) for k in PERSONAS},
        "contradiction_checks": dict(
            Counter(f"{c['check']}:{c['severity']}" for r in rows for c in r["contradictions"])
        ),
        "scan_to_listed_area_ratio": {
            "n": len(ratios),
            "p25": round(ratios[len(ratios) // 4], 3),
            "median": round(ratios[len(ratios) // 2], 3),
            "p75": round(ratios[3 * len(ratios) // 4], 3),
        },
        "vexcel_rows": len(vexcel),
        "visual_rows": len(visual),
    }
    write_json(OUT / "phase3_score_summary.json", summary)
    print(json.dumps(summary, indent=1), file=sys.stderr)
    top = [
        (r["address"], r["exterior_tier"], r["interior"]["total"], r["best_persona"], r["total"])
        for r in rows[:15]
    ]
    print(json.dumps(top, indent=0), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
