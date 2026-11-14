# ruff: noqa: E501 - prose-heavy report generator
"""Phase 6: write docs/demo-candidates.md and out/scout/shortlist.json from the pipeline outputs.

uv run python -m worldgen.scout.report

Facts (numbers, ids, flags) are read from listings.jsonl, mp_derived.jsonl, scores.jsonl, clusters.json,
vexcel_verify.jsonl, vexcel_clusters.json, visual_check.jsonl and the Vexcel call ledger. The editorial
choices (which houses are heroes, the recommended strategy) are the constants below, chosen after
reading those files; the report cites the field behind every claim as `file:field`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from worldgen.scout.common import OUT, PORTAL, WORLDGEN, now_iso, read_json, read_jsonl, write_json
from worldgen.scout.score import PERSONAS
from worldgen.scout.vexcel_usage import summarize

DOC = WORLDGEN.parent / "docs" / "demo-candidates.md"

# Editorial picks (addresses as in listings.jsonl).
BLOCK = {
    "block_id": "hou_navidad_01",
    "homes": ["6915 Navidad Road", "7014 Navidad Road"],
    "radius_m": 200,
}
HEROES = [  # (address, role)
    ("6915 Navidad Road", "Block hero: the house the new-parents search lands on, furnished on stage"),
    ("7014 Navidad Road", "Block second house: walkable loops for the nerf war; same street, 194 m away"),
    ("5507 Bertellis Lane", "Best single-house backup: infant + movie, defurnished, splat checked"),
    ("3010 Bonnebridge Way Boulevard", "Nerf / pool / big family showcase (strategy B lot map)"),
    ("11709 Lakeside Place Drive", "Renter: active $2,500/mo lease, vacant scan"),
    ("1429 Godwin Street", "Contradiction gem with a splat; 1411 Lawson St (stairs gem) is 95 m away"),
]
PERSONA_PICKS = {  # persona -> [(address, strategy label)]
    "infant": [("6915 Navidad Road", "A"), ("5507 Bertellis Lane", "B")],
    "movie": [("6915 Navidad Road", "A"), ("8819 Kolbe Bend Lane", "B")],
    "nerf": [("7014 Navidad Road", "A"), ("3010 Bonnebridge Way Boulevard", "B")],
    "big_family": [("3010 Bonnebridge Way Boulevard", "B"), ("404 Avondale Street", "B")],
    "pool": [("16346 Havenpark Court", "B"), ("3010 Bonnebridge Way Boulevard", "B")],
    "renter": [("11709 Lakeside Place Drive", "B")],
    "contradiction": [("1429 Godwin Street", "B"), ("1411 Lawson Street", "interior only")],
}
GEMS = [  # (address, checks to show, explanation from the listing/visual check)
    (
        "1411 Lawson Street",
        ("stories_vs_scan",),
        "Listing remarks: 'efficiency garage apt … located upstairs in back'; features list 'Garage Apartment'. "
        "The scanned second floor is that apartment (floor plan, visual_check.jsonl). Active, $349,900, defurnished mesh.",
    ),
    (
        "1429 Godwin Street",
        ("beds_vs_scan", "area_vs_scan"),
        "Remarks: 'garage apartment offering a 3/2 layout'. The scan counts the apartment's rooms and area; the listing "
        "does not. Splat home (OUTSIDE), Active $469,888.",
    ),
    (
        "10418 Brinwood Drive",
        ("area_vs_scan", "beds_vs_scan", "stories_vs_vexcel_height"),
        "2026 new build (yearBuilt 2026). The drone thumbnail shows 2 storeys, so the low Vexcel roof height comes from "
        "imagery captured before construction (Dec 2025-Jan 2026). Listed 1,954 sqft vs 348 m2 scanned. Sold.",
    ),
    (
        "8435 Erasmus Landing Court",
        ("pool_vs_vexcel",),
        "CoStar lists a private pool; Vexcel property/extract detects none. Not checked visually (drone thumbnail not "
        "downloaded). Sold.",
    ),
    (
        "204 Dipping Lane",
        ("stories_vs_vexcel_height",),
        "Listed 1 story; Vexcel roof height 8.6 m. Matterport geocoordinates are 211 m from CoStar, so the parcel may "
        "be the wrong one: treat as unverified.",
    ),
]
QUESTIONS_TEMPLATE = [
    "**Vexcel units:** REST `/v2/usage/query` returns 403 and MCP `usage_query` says 'Forbidden by role permissions'. "
    "How do we see our unit balance? (We logged every call ourselves: see section 6.)",
    "**Vexcel 3D Cities:** the profile lists `3d-cities` and `/3d/tile`, `/3d/tileset`, `/3d/collections`. What is the "
    "request for a glTF/3D Tiles download of a 200 m block? (Not tried, to save units.)",
    "**Vexcel stories and roof height:** `property/extract` returns null for `number-of-stories-below-eave` and "
    "`maximum-roof-height` on all {vx_struct} homes with a structure ({vx_rows} queried). Is that a product we lack? We used DSM minus DTM instead.",
    "**Vexcel imagery age:** Houston urban imagery is Dec 2025-Jan 2026; the drone splats are Oct-Nov 2026. New builds "
    "(10418 Brinwood Dr) and some new subdivisions (14954 Eagle Feather Dr: no parcel at the point) are missing. "
    "Is newer coverage coming?",
    "**Matterport door positions:** `ModelRoom.boundary.edges[].openings[]` gives `type`, `label`, `width`, "
    "`height` but not where the opening sits along its edge. Is there a field for that, or should we read the DXF?",
    "**Splat frame:** for OUTSIDE tours the splat and the 50k mesh both centre near the origin, Z up, and the splat is "
    "densest over the house. Is the splat guaranteed to be in the model's local frame (same origin, scale, heading)? "
    "How does that frame map to `geocoordinates` (always `source: processing`)?",
    "**Smaller splat for the web:** splats are 230-420 MB of DC-colour Gaussians with background out to +-9 km. "
    "Is there a cropped or compressed (SPZ / quantized) version, or a recommended crop radius?",
    "**`mp:exteriorsplat` bundle:** `unavailable` on all {models_ok:,} models, including the {splat_homes} splat homes. Is the portal "
    "download the only route, and is the portal splat licensed for a public demo?",
    "**`mp:defurnish`:** unlocked on {defurn_unlocked} models but a `defurnished_50k.obj` exists on only {defurn_mesh} ({defurn_mesh_splat} splat "
    "homes). Will the rest be processed during the event?",
    "**Placeholder listings:** {placeholders} of the {splat_homes} splat homes are MLS 800000 photo-shoot placeholders (price $1, no "
    "features record). Can CoStar share the real listing (if any) for those homes?",
]


def mlink(mid: str) -> str:
    return f"[{mid}](https://my.matterport.com/show/?m={mid})"


def plink(mid: str) -> str:
    return f"[portal]({PORTAL}/models/{mid})"


def money(v) -> str:
    return f"${v:,.0f}" if v is not None else "n/a"


def load() -> dict:
    d = {
        "homes": {h["property_id"]: h for h in read_jsonl(OUT / "listings.jsonl")},
        "models": {m["model_id"]: m for m in read_jsonl(OUT / "mp_derived.jsonl")},
        "scores": list(read_jsonl(OUT / "scores.jsonl")),
        "vx": {r["property_id"]: r for r in read_jsonl(OUT / "vexcel_verify.jsonl")},
        "vis": {r["property_id"]: r for r in read_jsonl(OUT / "visual_check.jsonl")},
        "clusters": read_json(OUT / "clusters.json"),
        "vxc": read_json(OUT / "vexcel_clusters.json"),
        "p1": read_json(OUT / "phase1_summary.json"),
        "p2": read_json(OUT / "phase2_summary.json"),
        "p3": read_json(OUT / "phase3_score_summary.json"),
        "short": read_json(OUT / "shortlist_ids.json")["ids"],
    }
    d["scores_by_id"] = {r["property_id"]: r for r in d["scores"]}
    d["by_addr"] = {}
    for r in d["scores"]:
        d["by_addr"].setdefault(r["address"], r)
    return d


def row(d: dict, addr: str, unit: str | None = None) -> dict:
    cands = [r for r in d["scores"] if r["address"] == addr and (unit is None or r["unit"] == unit)]
    return max(cands, key=lambda r: r["total"])


def strong(r: dict) -> str:
    return ", ".join(r["strong_personas"]) or "none"


# ---------------- sections ----------------


def sec_recommendation(d: dict) -> list[str]:
    a = row(d, BLOCK["homes"][0])
    b = row(d, BLOCK["homes"][1])
    pair = d["clusters"]["demo_areas"]["pairs"][0]
    vxc = d["vxc"].get("demo_pair_1", {})
    hou300 = d["clusters"]["markets"]["houston"]["clusters"]["300"][0]
    dens = d["clusters"]["markets"]["houston"]["density"]
    nant_cov = hou300["persona_coverage_heroes"]
    sb = d["clusters"]["strategy_b"]
    nant_vx = d["vxc"].get("houston_300_1", {})
    nant = [d["scores_by_id"][m["property_id"]] for m in hou300["splat_members"]]
    nant_p = [r["price_usd"] for r in nant if r["price_usd"]]
    nant_prices = f"{money(min(nant_p))}-{money(max(nant_p))}" if nant_p else "n/a"
    fl = [r["floors_real"] for r in nant if r["floors_real"]]
    nant_floors = f"{min(fl)}-{max(fl)}" if fl else "?"
    out = [
        "## 1. Recommendation",
        "",
        f"**Strategy A (one shared block) on Navidad Rd, Houston (Mission Bend, 77083).** Suggested `block_id`: "
        f"`{BLOCK['block_id']}`, centre **{pair['centre_lat']}, {pair['centre_lon']}**, radius **{BLOCK['radius_m']} m**.",
        "",
        f"- **Houses:** {a['address']} (`{a['model_id']}`, hero) and {b['address']} (`{b['model_id']}`), "
        f"{pair['distance_m']:.0f} m apart on the same street (`clusters.json:demo_areas.pairs[0]`). "
        "Both have OUTSIDE drone splats, so nobody has to invent walls.",
        f"- **They answer all three scripted prompts** (`scores.jsonl:strong_personas`): {a['address']}: {strong(a)}; "
        f"{b['address']}: {strong(b)}. Both are Active, one story, under $450k "
        f"({money(a['price_usd'])} and {money(b['price_usd'])}, `listings.jsonl:price_usd`).",
        f"- **Neighbourhood mesh size:** {vxc.get('parcels_total')} parcels within {vxc.get('radius_m', 0):.0f} m, "
        f"{vxc.get('parcels_with_primary_structure')} with a primary structure "
        "(`vexcel_clusters.json:demo_pair_1`, MCP `property_metadata_count`). Vexcel urban ortho + DSM "
        "`us-tx-houston-2026`, captured 2025-12-19..2026-01-02.",
        "- **Why not the densest splat group?** By the brief's rank (splat homes first) the #1 area is "
        f"**Nantucket Dr** ({hou300['splat_homes']} splat homes within 300 m, span {hou300['splat_span_m']:.0f} m, "
        f"{nant_vx.get('parcels_total')} parcels). But those are {nant_floors}-storey townhome units (two share one "
        f"building), {nant_prices}, with no private yards; their heroes are strong only for {', '.join(nant_cov)}. "
        "No house there fits the scripted 'new parents under $450k' search. Houston never has more than "
        f"{dens['300']['max_in_ball']} splat homes within 300 m (`clusters.json:markets.houston.density`).",
        "",
        "**Strategy B (one small lot map per house)** gives many more heroes and every persona, but changes the "
        "one-block design in `AGENTS.md` and needs a human decision.",
        "",
        "| Strategy | Hero houses | Personas with a strong hero | Notes |",
        "|---|---|---|---|",
        f"| **A: Navidad Rd (recommended)** | 2 (194 m apart) | {', '.join(pair['coverage'])} | Covers the stage "
        "script; small one-storey houses are the easiest packs to build |",
        f"| A: Nantucket Dr (brief rank #1) | {hou300['hero_houses']} (3 buildings, {hou300['splat_span_m']:.0f} m) | "
        f"{', '.join(nant_cov)} | Townhomes, mostly furnished scans, no house under $450k |",
        f"| B: lot map per splat home | {sb['hero_houses']} ({sb['hero_houses_by_market']['houston']} Houston, "
        f"{sb['hero_houses_by_market']['los_angeles']} LA) | {', '.join(sb['persona_coverage'])} | Each lot map is the "
        "splat near field (about 100-200 m across); needs an `AGENTS.md` change |",
        "",
        "If the team adopts B later, the lot maps to build first are the B rows in the persona table (section 3).",
        "",
    ]
    return out


def hero_block(d: dict, addr: str, role: str) -> list[str]:
    r = row(d, addr)
    h = d["homes"][r["property_id"]]
    m = d["models"][r["model_id"]]
    vx = d["vx"].get(r["property_id"], {})
    vis = d["vis"].get(r["property_id"], {})
    bp = r["best_persona"]
    unit = f" Unit {h['unit']}" if h.get("unit") else ""
    price = (
        money(h["price_usd"])
        if h["price_usd"]
        else (f"{money(h['rent_usd_month'])}/mo" if h["rent_usd_month"] else "n/a")
    )
    lines = [
        f"### {addr}{unit}, {h['city']} {h['zip']}",
        "",
        f"*{role}.*",
        "",
        f"- **Links:** Matterport {mlink(r['model_id'])}, {plink(r['model_id'])}. CoStar listing `{h['listing_id']}` "
        f"(property `{h['property_id']}`).",
        f"- **Listing:** {h['listing_status']} {h['transaction']}, {price}; {h['beds']} bd / {h['baths']} ba, "
        f"{h['area_m2']} m2 ({h['area_sqft']} sqft), {h['stories']} storey(s), built {h['year_built']}; tax "
        f"{money(h['tax_usd_year']) + '/yr' if h['tax_usd_year'] else 'n/a'}; schools: {', '.join(s['name'].title() for s in h['schools'][:1]) or 'n/a'}.",
        f"- **Exterior:** splat tour **{h['splat_tour']}**, splat {h['splat_size']}, {h['drone_photos']} drone photos "
        f"(`splat-models.tsv`). Vexcel: parcel {vx.get('parcel_m2')} m2, yard {vx.get('yard_m2')} m2, pool "
        f"{vx.get('pool')}, roof {vx.get('roof_height_m')} m (`vexcel_verify.jsonl`).",
        f"- **Interior:** {m['floors_real']} floor(s), {m['room_count']} labelled rooms, scanned living "
        f"{m['area_living_m2']} m2; defurnish: bundle `{m['defurnish_bundle']}`, defurnished mesh "
        f"**{'yes' if m['defurnished_mesh'] else 'no'}**; wall/door vectors {'yes' if m['vectors'] else 'no'}; DXF "
        f"{'yes' if m['has_dxf'] else 'no'} (`mp_derived.jsonl`). Interior score {r['interior']['total']}/30.",
        f"- **Best persona:** {PERSONAS[bp]} ({r['personas'][bp]['points']}/20): {r['personas'][bp]['why']}. "
        f"Strong: {strong(r)}.",
    ]
    if vis:
        lines.append(
            f"- **Visual check:** layout {vis.get('layout_complete')}; drone: {vis.get('drone_view')}; "
            f"furnished: {vis.get('furnished')} (`visual_check.jsonl`)."
        )
    if vis.get("splat_stats"):
        s = vis["splat_stats"]
        lines.append(
            f"- **Splat file checked:** {s['points']:,} Gaussians, {s['bytes'] / 1e6:.0f} MB, Z up; near field "
            f"(p5-p95 within 150 m) {s.get('near150_p5')} .. {s.get('near150_p95')} m; density over the house "
            f"{vis.get('splat_house_density_ratio')}x the surroundings -> footprint inside: "
            f"{vis.get('footprint_inside_splat')}. 50k OBJ: {vis.get('mesh50k_stats', {}).get('triangles')} "
            "triangles, Z up."
        )
    contra = [c for c in r["contradictions"] if c["severity"] in ("conflict", "warn")]
    if contra:
        lines.append(
            "- **Contradictions:** "
            + "; ".join(
                f"{c['check']} ({c['severity']}): listed {c['listed']}, observed {c['observed']}"
                for c in contra
            )
        )
    lines.append("")
    return lines


def sec_heroes(d: dict) -> list[str]:
    out = ["## 2. Hero houses", ""]
    for addr, role in HEROES:
        out += hero_block(d, addr, role)
    return out


def sec_personas(d: dict) -> list[str]:
    out = [
        "## 3. Persona table",
        "",
        "Strong = persona score >= 14/20 (`scores.jsonl:personas.<persona>`). A = in the recommended block; "
        "B = needs its own lot map.",
        "",
        "| Persona | House | Strategy | Score | Fields that justify it |",
        "|---|---|---|---|---|",
    ]
    for k, picks in PERSONA_PICKS.items():
        for addr, strat in picks:
            r = row(d, addr)
            p = r["personas"][k]
            out.append(f"| {PERSONAS[k]} | {addr} | {strat} | {p['points']:g} | {p['why']} |")
    out.append("")
    return out


def sec_gems(d: dict) -> list[str]:
    out = [
        "## 4. Contradiction gems",
        "",
        "Context: scanned living area is a median "
        f"{d['p3']['scan_to_listed_area_ratio']['median']} of the listed area (scans measure inside the walls), so "
        "small area gaps are normal; the gems below are large or explained.",
        "",
        "| House | Check | Listed | Observed | Evidence / explanation |",
        "|---|---|---|---|---|",
    ]
    for addr, checks, expl in GEMS:
        r = row(d, addr)
        for i, c in enumerate(x for x in r["contradictions"] if x["check"] in checks):
            out.append(
                f"| {addr if i == 0 else ''} (`{r['model_id']}`) | {c['check']} ({c['severity']}) | {c['listed']} | "
                f"{c['observed']} | {expl if i == 0 else c['evidence']} |"
            )
    cc = d["p3"]["contradiction_checks"]
    out += [
        "",
        f"All homes: {cc.get('stories_vs_scan:conflict', 0)} 'listed one storey but the scan has 2+ floors or a "
        f"Staircase' (none on a splat home), {cc.get('beds_vs_scan:conflict', 0)} bed-count conflicts (>= 2), "
        f"{cc.get('area_vs_scan:conflict', 0)} area gaps > 30%, {cc.get('pool_vs_vexcel:conflict', 0)} pool conflicts "
        "with Vexcel (`phase3_score_summary.json:contradiction_checks`).",
        "",
    ]
    return out


def sec_runners(d: dict) -> list[str]:
    out = [
        "## 5. Runner-up areas",
        "",
        "Brief's rank: splat homes, then homes with interior >= 20, then persona coverage, then contradictions "
        "(`clusters.json`). Disjoint clusters, greedy.",
        "",
        "| Market | Radius | # | Splat homes (heroes) | Interior >= 20 | Personas | Parcels | Centre | Splat homes |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    vxc = d["vxc"]
    for market, R, n in (("houston", "300", 5), ("houston", "600", 3), ("los_angeles", "1000", 2)):
        for c in d["clusters"]["markets"][market]["clusters"][R][:n]:
            key = f"{market}_{R}_{c['rank']}"
            parcels = vxc.get(key, {}).get("parcels_total")
            if parcels is None:  # same area as a cluster already counted (centres within ~150 m)
                for e in vxc.values():
                    if (
                        abs(e["centre"][0] - c["centre_lat"]) < 0.0014
                        and abs(e["centre"][1] - c["centre_lon"]) < 0.0015
                    ):
                        parcels = f"{e['parcels_total']} (same area)"
                        break
            names = "; ".join(sorted({m["address"] for m in c["splat_members"]}))
            out.append(
                f"| {market} | {R} m | {c['rank']} | {c['splat_homes']} ({c['hero_houses']}) | {c['interior_ge_20']} | "
                f"{', '.join(c['persona_coverage'])} | {parcels if parcels is not None else 'n/a'} | "
                f"{c['centre_lat']}, {c['centre_lon']} | {names} |"
            )
    la = d["clusters"]["markets"]["los_angeles"]
    out += [
        "",
        "- **Eagle Feather Dr** (Houston 300 m #4): 14954 and 14963 Eagle Feather Dr face each other, 32 m apart; "
        "new subdivision, Vexcel has no parcel at 14954 (imagery predates it).",
        "- **Dipping Ln** (Houston 300 m #2): cheap new builds (210/204 Dipping Ln $193k, infant), but Matterport "
        "geocoordinates are 112-211 m from CoStar for both: check before building.",
        f"- **Best in LA:** at most {la['density']['1000']['max_in_ball']} splat homes within 1 km "
        "(5065 La Calandria Dr + 4510 Tourmaline St, 1.5 km apart). LA works only as strategy B; best LA lot map: "
        "5065 La Calandria Dr (3 floors, Active $895k, big family + movie).",
        "- **Single houses that answer all three scripted prompts** (strategy B fallback): "
        + "; ".join(
            f"{s['address']} ({', '.join(s['strong_personas'])})"
            for s in d["clusters"]["demo_areas"]["single_houses"][:5]
        )
        + ". Defurnished mesh among them: "
        + (
            ", ".join(
                s["address"]
                for s in d["clusters"]["demo_areas"]["single_houses"][:5]
                if d["scores_by_id"][s["property_id"]]["defurnished_mesh"]
            )
            or "none"
        )
        + " (`scores.jsonl:defurnished_mesh`).",
        "",
    ]
    return out


def sec_coverage(d: dict) -> list[str]:
    p1, p2, p3 = d["p1"], d["p2"], d["p3"]
    u = summarize()
    sp = p2["splat_models"]
    homes = d["homes"].values()
    outside = sum(1 for h in homes if h["splat_tour"] == "OUTSIDE")
    inside_only = sum(1 for h in homes if h["splat_tour"] == "Inside only")
    placeholders = sum(1 for h in homes if h["splat"] and h.get("placeholder_listing"))
    return [
        "## 6. Coverage and Vexcel spend",
        "",
        "| What | Count | Source |",
        "|---|---|---|",
        f"| Homes (CoStar propertyId) | {p1['homes']} ({p1['homes_by_market']['houston']} Houston, "
        f"{p1['homes_by_market']['los_angeles']} LA) | `phase1_summary.json` |",
        f"| Homes with a drone splat | {p1['homes_with_splat']} ({outside} OUTSIDE, {inside_only} Inside only) | "
        "`listings.jsonl:splat` |",
        f"| Splat homes that are MLS 800000 placeholders (price $1) | {placeholders} | "
        "`listings.jsonl:placeholder_listing` |",
        f"| Hero-eligible (splat, interior >= 20, data >= 10, real listing) | {p3['hero_eligible']} | "
        "`scores.jsonl:hero_eligible` |",
        f"| Matterport models fetched | {p2['models_ok']} / {p2['models_wanted']} (0 failed) | `phase2_summary.json` |",
        f"| Wall + door vectors (`boundary.edges`) | {p2['vectors']} (splat {sp['vectors']}) | `mp_derived:vectors` |",
        f"| `mp:defurnish` unlocked | {p2['defurnish_bundle_unlocked']} (splat {sp['defurnish_bundle_unlocked']}) | "
        "`bundles` |",
        f"| Defurnished mesh asset (`defurnished_50k.obj`) | {p2['defurnished_mesh']} (splat "
        f"{sp['defurnished_mesh']}) | `assets.meshes` |",
        f"| DXF floor plan | {p2['dxf']} | `assets.floorplans` |",
        f"| Geocoordinates (source `processing`) | {p2['geo_sources'].get('processing')}; median "
        f"{p2['geo_vs_costar_m']['median']} m from CoStar, {p2['geo_vs_costar_m']['over_100m']} over 100 m | "
        "`mp_derived:geo_vs_costar_m` |",
        "| `mp:exteriorsplat` | unavailable on all | `bundles` |",
        f"| Homes checked with Vexcel `property/extract` | {len(d['vx'])} | `vexcel_verify.jsonl` |",
        "",
        f"**Vexcel calls (our ledger; the account's unit counter is not visible to us):** {u['total_calls']} total = "
        f"{u['rest_calls']} REST ({u['rest_by_path'].get('property/extract', 0)} `property/extract`, "
        f"{u['image_extracts']} `oriented/extract` crops, {u['rest_by_path'].get('usage/query', 0)} usage probes) + "
        f"{u['mcp_tool_calls']} MCP tool calls. Budget was about 300 calls and 40 crops. "
        "Ledger: `worldgen/out/scout/ledger/vexcel_calls.jsonl`.",
        "",
    ]


def sec_questions(d: dict) -> list[str]:
    p2 = d["p2"]
    vals = {
        "vx_struct": sum(1 for r in d["vx"].values() if r.get("footprint_found")),
        "vx_rows": len(d["vx"]),
        "models_ok": p2["models_ok"],
        "splat_homes": d["p1"]["homes_with_splat"],
        "defurn_unlocked": p2["defurnish_bundle_unlocked"],
        "defurn_mesh": p2["defurnished_mesh"],
        "defurn_mesh_splat": p2["splat_models"]["defurnished_mesh"],
        "placeholders": sum(1 for h in d["homes"].values() if h["splat"] and h.get("placeholder_listing")),
    }
    qs = [q.format(**vals) for q in QUESTIONS_TEMPLATE]  # templates hold no literal braces
    return ["## 7. Questions for sponsor engineers", ""] + [f"{i}. {q}" for i, q in enumerate(qs, 1)] + [""]


def sec_summary(d: dict) -> list[str]:
    a, b = row(d, BLOCK["homes"][0]), row(d, BLOCK["homes"][1])
    sizes = f"{a['splat_mb']:.0f} + {b['splat_mb']:.0f} MB"
    return [
        "## Summary",
        "",
        f"Build the demo as strategy A on **Navidad Rd, Houston** (`{BLOCK['block_id']}`, centre "
        f"{d['clusters']['demo_areas']['pairs'][0]['centre_lat']}, {d['clusters']['demo_areas']['pairs'][0]['centre_lon']}, "
        f"radius {BLOCK['radius_m']} m): two Active, one-storey splat homes 194 m apart that together answer the three "
        f"scripted prompts. 6915 Navidad Rd (`{a['model_id']}`, defurnished mesh) is the hero for the new-parents search "
        f"and the movie room; 7014 Navidad Rd (`{b['model_id']}`) has the loops for the nerf war. Keep 5507 Bertellis "
        "Ln, 3010 Bonnebridge Way Blvd, 11709 Lakeside Place Dr #232 and 1429 Godwin St as strategy-B lot maps if the "
        "team approves per-house maps; together they cover all seven personas. The three biggest risks: "
        f"(1) **splat size** - the two Navidad splats are {sizes} of DC-colour Gaussians with background out to "
        "+-9 km, so they must be cropped to the ~100 m near field and compressed before they can load in a browser; "
        "(2) **frame alignment** - the splat, the Matterport mesh and the Vexcel block must line up; the shared splat/"
        "mesh frame is inferred from point density, not confirmed, and Vexcel imagery is ~10 months older than the "
        "drone capture; (3) **interior fidelity for play** - door positions along walls are not exposed (only widths), "
        "7014 Navidad Rd has no defurnished mesh, and both houses are small (85-95 m2 scanned), so collision walls "
        "and the nerf arena need hand checks.",
        "",
    ]


def write_shortlist(d: dict) -> int:
    rows = []
    by_id = {r["property_id"]: r for r in d["scores"]}
    for pid_s, why in d["short"].items():
        pid = int(pid_s)
        r = by_id.get(pid)
        if r is None:
            continue
        m = d["models"].get(r["model_id"] or "", {})
        rows.append(
            {
                "property_id": pid,
                "shortlist_reasons": why,
                "score": r,
                "listing": d["homes"][pid],
                "matterport": {k: v for k, v in m.items() if k != "rooms"},
                "matterport_rooms": m.get("rooms"),
                "vexcel": d["vx"].get(pid),
                "visual": d["vis"].get(pid),
                "links": {
                    "matterport": f"https://my.matterport.com/show/?m={r['model_id']}"
                    if r["model_id"]
                    else None,
                    "portal": f"{PORTAL}/models/{r['model_id']}" if r["splat"] else None,
                },
                "sources": {
                    "listing": "costar-hackathon-kit/data/residential-listings.jsonl + residential-listing-features.jsonl "
                    "(worldgen/scout/inventory.py)",
                    "splat": "docs/prompts/data/splat-models.tsv",
                    "matterport": "GraphQL model(id) incl. boundary vectors (worldgen/scout/mp_fetch.py, mp_derive.py)",
                    "vexcel": "REST property/extract; MCP dsm/dtm_elevation (worldgen/scout/vexcel_verify.py)",
                    "visual": "colored_rooms floor plans + drone thumbnail, rated by the scout agent (visual.py)",
                    "scores": "worldgen/scout/score.py (every sub-score has points, max, why)",
                },
            }
        )
    rows.sort(key=lambda x: x["score"]["rank"])
    write_json(
        OUT / "shortlist.json",
        {
            "generated_at": now_iso(),
            "recommendation": {
                **BLOCK,
                "strategy": "A",
                "centre": [
                    d["clusters"]["demo_areas"]["pairs"][0]["centre_lat"],
                    d["clusters"]["demo_areas"]["pairs"][0]["centre_lon"],
                ],
            },
            "heroes": [a for a, _ in HEROES],
            "persona_picks": PERSONA_PICKS,
            "homes": rows,
        },
    )
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.parse_args(argv)
    d = load()
    lines = [
        "# Demo candidates: which real homes to build the map from",
        "",
        f"Scout report, generated {now_iso()} by `worldgen/scout/report.py` (Lane A). Brief: "
        "`docs/prompts/demo-property-scout.md`. Full per-home data (every field and sub-score, with sources): "
        "`worldgen/out/scout/shortlist.json` (not committed; regenerate with the steps in "
        "`worldgen/out/scout/PROGRESS.md`). Field citations read `file:field`.",
        "",
    ]
    lines += sec_recommendation(d)
    lines += sec_heroes(d)
    lines += sec_personas(d)
    lines += sec_gems(d)
    lines += sec_runners(d)
    lines += sec_coverage(d)
    lines += sec_questions(d)
    lines += sec_summary(d)
    DOC.parent.mkdir(parents=True, exist_ok=True)
    Path(DOC).write_text("\n".join(lines))
    n = write_shortlist(d)
    print(f"wrote {DOC} ({len(lines)} lines) and shortlist.json ({n} homes)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
