"""Phase 3b: cluster homes into candidate map areas -> out/scout/clusters.json, shortlist_ids.json.

uv run python -m worldgen.scout.cluster

A cluster at scale R is every home within R meters of a centre. Centres tried: every home, plus the
midpoint of every pair of splat homes closer than 2R (so a group between two homes is not missed).
Clusters are picked greedily and kept disjoint: best centre first, its members removed, repeat.
Rank key (the brief): splat homes, then homes with interior >= 20, then persona coverage, then
contradiction flags. Distances use a local equirectangular projection per market (error < 0.1% at
these spans); reported distances are haversine.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from itertools import combinations

from worldgen.scout.common import EARTH_R, OUT, haversine_m, now_iso, read_jsonl, write_json
from worldgen.scout.score import PERSONAS

RADII = (300, 600, 1000)
TOP_N = 10
INTERIOR_OK = 20


def project(rows: list[dict]) -> dict[int, tuple[float, float]]:
    lat0 = sum(r["lat"] for r in rows) / len(rows)
    k = math.cos(math.radians(lat0))
    return {
        r["property_id"]: (math.radians(r["lon"]) * EARTH_R * k, math.radians(r["lat"]) * EARTH_R)
        for r in rows
    }


def member_stats(members: list[dict]) -> dict:
    splat = [m for m in members if m["splat"] and not m["skip_reason"]]
    ok_interior = [m for m in members if m["interior"]["total"] >= INTERIOR_OK and not m["skip_reason"]]
    heroes = [m for m in members if m["hero_eligible"]]
    cover_all = sorted({p for m in members if not m["skip_reason"] for p in m["strong_personas"]})
    cover_splat = sorted({p for m in heroes for p in m["strong_personas"]})
    contra = sum(m["contradiction_flags"] > 0 for m in members if not m["skip_reason"])
    return {
        "splat_homes": len(splat),
        "hero_houses": len(heroes),
        "interior_ge_20": len(ok_interior),
        "persona_coverage": cover_all,
        "persona_coverage_heroes": cover_splat,
        "homes_with_contradictions": contra,
        "members": len(members),
    }


def rank_key(st: dict) -> tuple:
    return (
        st["splat_homes"],
        st["interior_ge_20"],
        len(st["persona_coverage"]),
        st["homes_with_contradictions"],
    )


def clusters_for(rows: list[dict], radius: float) -> list[dict]:
    pos = project(rows)
    by_id = {r["property_id"]: r for r in rows}
    splat_ids = [r["property_id"] for r in rows if r["splat"]]
    centres: list[tuple[float, float, str]] = [
        (*pos[r["property_id"]], f"home {r['property_id']}") for r in rows
    ]
    for a, b in combinations(splat_ids, 2):
        (ax, ay), (bx, by) = pos[a], pos[b]
        if math.hypot(ax - bx, ay - by) <= 2 * radius:
            centres.append(((ax + bx) / 2, (ay + by) / 2, f"midpoint {a}/{b}"))
    remaining = set(by_id)
    out = []
    while len(out) < TOP_N and remaining:
        best = None
        for cx, cy, label in centres:
            mem = [pid for pid in remaining if math.hypot(pos[pid][0] - cx, pos[pid][1] - cy) <= radius]
            if not mem:
                continue
            st = member_stats([by_id[p] for p in mem])
            key = rank_key(st)
            if best is None or key > best[0]:
                best = (key, cx, cy, label, mem, st)
        if best is None:
            break
        key, cx, cy, label, mem, st = best
        if st["splat_homes"] == 0 and out:
            break
        remaining -= set(mem)
        ms = [by_id[p] for p in mem]
        lat_c = sum(m["lat"] for m in ms) / len(ms)
        lon_c = sum(m["lon"] for m in ms) / len(ms)
        splat_ms = [m for m in ms if m["splat"]]
        if splat_ms:
            lat_c = sum(m["lat"] for m in splat_ms) / len(splat_ms)
            lon_c = sum(m["lon"] for m in splat_ms) / len(splat_ms)
        span = max(
            (haversine_m(a["lat"], a["lon"], b["lat"], b["lon"]) for a, b in combinations(splat_ms, 2)),
            default=0.0,
        )
        need_r = max((haversine_m(lat_c, lon_c, m["lat"], m["lon"]) for m in splat_ms), default=0.0)
        out.append(
            {
                "rank": len(out) + 1,
                "radius_m": radius,
                "centre_label": label,
                "centre_lat": round(lat_c, 6),
                "centre_lon": round(lon_c, 6),
                "splat_span_m": round(span, 1),
                "radius_to_cover_splats_m": round(need_r, 1),
                **st,
                "splat_members": [
                    {
                        "property_id": m["property_id"],
                        "address": m["address"],
                        "model_id": m["model_id"],
                        "tour": m["splat_tour"],
                        "interior": m["interior"]["total"],
                        "hero": m["hero_eligible"],
                        "strong_personas": m["strong_personas"],
                        "dist_to_centre_m": round(haversine_m(lat_c, lon_c, m["lat"], m["lon"]), 1),
                    }
                    for m in sorted(splat_ms, key=lambda m: -m["total"])
                ],
                "interior_only_members": [
                    {
                        "property_id": m["property_id"],
                        "address": m["address"],
                        "model_id": m["model_id"],
                        "interior": m["interior"]["total"],
                        "strong_personas": m["strong_personas"],
                        "dist_to_centre_m": round(haversine_m(lat_c, lon_c, m["lat"], m["lon"]), 1),
                    }
                    for m in sorted(ms, key=lambda m: -m["total"])
                    if not m["splat"] and not m["skip_reason"] and m["interior"]["total"] >= INTERIOR_OK
                ],
                "member_ids": sorted(mem),
            }
        )
    return out


def max_splats_in_ball(rows: list[dict], radius: float) -> dict:
    sp = [r for r in rows if r["splat"]]
    if not sp:
        return {"max": 0}
    pos = project(rows)
    best = (0, None)
    pts = [pos[r["property_id"]] for r in sp]
    cands = list(pts) + [
        ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        for a, b in combinations(pts, 2)
        if math.dist(a, b) <= 2 * radius
    ]
    for c in cands:
        n = sum(1 for p in pts if math.dist(p, c) <= radius)
        if n > best[0]:
            best = (n, c)
    # complete linkage: largest set of splat homes whose pairwise distances are all <= radius
    ids = [r["property_id"] for r in sp]
    adj = {a: {b for b in ids if b != a and math.dist(pos[a], pos[b]) <= radius} for a in ids}
    clique = 1
    for a in ids:  # greedy clique growth from each node (exact enough at these sizes)
        group = {a}
        for b in sorted(adj[a], key=lambda x: -len(adj[x] & adj[a])):
            if all(b in adj[g] for g in group):
                group.add(b)
        clique = max(clique, len(group))
    return {"max_in_ball": best[0], "max_pairwise_group": clique}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.parse_args(argv)
    rows = [r for r in read_jsonl(OUT / "scores.jsonl") if r["lat"] is not None]
    result: dict = {"generated_at": now_iso(), "interior_ok": INTERIOR_OK, "markets": {}}
    for market in ("houston", "los_angeles"):
        mrows = [r for r in rows if r["market"] == market]
        result["markets"][market] = {
            "homes": len(mrows),
            "splat_homes": sum(r["splat"] for r in mrows),
            "density": {str(R): max_splats_in_ball(mrows, R) for R in RADII},
            "clusters": {str(R): clusters_for(mrows, R) for R in RADII},
        }

    # Strategy B: one small map per splat home.
    heroes = [r for r in rows if r["hero_eligible"]]
    best_by_persona = {}
    for k in PERSONAS:
        cands = sorted(
            (r for r in heroes if k in r["strong_personas"]),
            key=lambda r: (-r["personas"][k]["points"], -r["total"]),
        )
        best_by_persona[k] = [
            {"property_id": r["property_id"], "address": r["address"], "points": r["personas"][k]["points"]}
            for r in cands[:5]
        ]
    result["strategy_b"] = {
        "hero_houses": len(heroes),
        "hero_houses_by_market": {
            m: sum(r["market"] == m for r in heroes) for m in ("houston", "los_angeles")
        },
        "persona_coverage": sorted(k for k, v in best_by_persona.items() if v),
        "best_by_persona": best_by_persona,
    }
    write_json(OUT / "clusters.json", result)

    # Shortlist for phases 4-5: top-10 clusters' members, top 40 homes, all splat homes with interior >= 20.
    short: dict[int, list[str]] = {}

    def add(pid: int, why: str) -> None:
        short.setdefault(pid, [])
        if why not in short[pid]:
            short[pid].append(why)

    for market, md in result["markets"].items():
        for R, cls in md["clusters"].items():
            for c in cls:
                for m in c["splat_members"] + c["interior_only_members"]:
                    add(m["property_id"], f"{market} cluster {c['rank']} @ {R} m")
    for r in rows[:40]:
        add(r["property_id"], f"top 40 (rank {r['rank']})")
    for r in rows:
        if r["splat"] and r["interior"]["total"] >= INTERIOR_OK:
            add(r["property_id"], "splat with interior >= 20")
    write_json(
        OUT / "shortlist_ids.json", {"generated_at": now_iso(), "ids": {str(k): v for k, v in short.items()}}
    )

    for market, md in result["markets"].items():
        print(
            f"== {market}: {md['homes']} homes, {md['splat_homes']} splat; density {md['density']}",
            file=sys.stderr,
        )
        for R in RADII:
            print(f"  -- R={R} m", file=sys.stderr)
            for c in md["clusters"][str(R)][:5]:
                print(
                    f"   #{c['rank']} splat={c['splat_homes']} heroes={c['hero_houses']}"
                    f" int20={c['interior_ge_20']}"
                    f" personas={len(c['persona_coverage'])} contra={c['homes_with_contradictions']}"
                    f" span={c['splat_span_m']}m centre={c['centre_lat']},{c['centre_lon']} "
                    + " | ".join(m["address"] or "?" for m in c["splat_members"]),
                    file=sys.stderr,
                )
    print(
        "strategy B:",
        json.dumps({k: v for k, v in result["strategy_b"].items() if k != "best_by_persona"}),
        file=sys.stderr,
    )
    print(f"shortlist ids: {len(short)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
