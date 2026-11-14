"""Phase 5: visual check of the shortlist -> out/scout/visual_targets.json, visual_check.jsonl.

uv run python -m worldgen.scout.visual --select              # pick the top 15 (deterministic), no network
uv run python -m worldgen.scout.visual --fetch               # colored_rooms floor plans + drone thumbnail
uv run python -m worldgen.scout.visual --splats ID ID ID     # splat.ply for 3 hero models (size-capped)
uv run python -m worldgen.scout.visual --meshes ID ID ID     # 50k OBJ for the same models (optional)
uv run python -m worldgen.scout.visual --derive              # merge stats + visual_ratings.json

Downloads go to out/scout/downloads/<model_id>/ and are parsed only by worldgen/scout/file_stats.py run
with `python -I`. Signed URLs are requested fresh, used once and never stored. The ratings in
visual_ratings.json are written by a person or agent after looking at the images.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import httpx

from worldgen.scout import mp
from worldgen.scout.common import (
    DOWNLOADS,
    OUT,
    PORTAL,
    now_iso,
    read_json,
    read_jsonl,
    write_json,
    write_jsonl,
)
from worldgen.scout.score import PERSONAS

TOP_N = 15
THUMB_NAMES = ("thumbnail.JPG", "Thumbnail.JPG", "thumbnail.jpg", "Thumbnail.jpg")
THUMB_MAX_MB = 40
SPLAT_MAX_MB = 450
MESH_MAX_MB = 120
STATS = Path(__file__).with_name("file_stats.py")


# ---------------- selection ----------------


def select() -> list[dict]:
    rows = list(read_jsonl(OUT / "scores.jsonl"))
    by_id = {r["property_id"]: r for r in rows}
    heroes = [r for r in rows if r["hero_eligible"]]
    clusters = read_json(OUT / "clusters.json")
    picked: dict[int, list[str]] = {}

    def add(pid: int, why: str) -> None:
        if len(picked) < TOP_N + 2 or pid in picked:
            picked.setdefault(pid, []).append(why)

    # The demo-prompt pair (found after phase 4) is the recommended block: always reviewed, on top of TOP_N.
    for pr in (clusters.get("demo_areas") or {}).get("pairs", [])[:1]:
        for m in pr["members"]:
            picked.setdefault(m["property_id"], []).append("demo-prompt pair (recommended block)")
    for k in PERSONAS:
        n = 2 if k in ("infant", "movie") else 1  # the two scripted demo prompts get a runner-up
        best = sorted(heroes, key=lambda r: (-r["personas"][k]["points"], -r["total"]))[:n]
        for r in best:
            if r["personas"][k]["points"] >= 14:
                add(r["property_id"], f"persona leader: {k}")
    for m in clusters["markets"]["houston"]["clusters"]["300"][0]["splat_members"]:
        add(m["property_id"], "strategy A block #1 (300 m)")
    # Contradiction gems: a 'listed one story, scan has stairs' interior next to a splat hero, and a
    # pool conflict on a splat home (the drone thumbnail can settle it).
    from worldgen.scout.common import haversine_m

    stairs = [
        r
        for r in rows
        for c in r["contradictions"]
        if c["check"] == "stories_vs_scan" and c["severity"] == "conflict"
    ]
    best_pair = None
    for r in stairs:
        if r["lat"] is None or r["listing_status"] not in ("Active", "Contract"):
            continue
        for hr in heroes:
            d = haversine_m(r["lat"], r["lon"], hr["lat"], hr["lon"])
            if d <= 200 and (best_pair is None or d < best_pair[0]):
                best_pair = (d, r, hr)
    if best_pair:
        add(
            best_pair[1]["property_id"],
            f"gem: listed 1 story, scan has stairs; {best_pair[0]:.0f} m from a splat hero",
        )
        add(best_pair[2]["property_id"], "splat hero next to the stairs gem")
    for r in rows:
        if r["splat"] and any(c["check"] == "pool_vs_vexcel" for c in r["contradictions"]):
            add(r["property_id"], "gem: CoStar pool vs Vexcel pool on a splat home")
            break
    for r in heroes:
        if len(picked) >= TOP_N + 2:
            break
        add(r["property_id"], f"rank {r['rank']}")
    out = []
    for pid, why in picked.items():
        r = by_id[pid]
        out.append(
            {
                "property_id": pid,
                "address": r["address"],
                "unit": r["unit"],
                "model_id": r["model_id"],
                "splat": r["splat"],
                "rank": r["rank"],
                "why": why,
            }
        )
    write_json(OUT / "visual_targets.json", {"at": now_iso(), "targets": out})
    return out


# ---------------- downloads ----------------


def _download(url: str, dest: Path, max_mb: float) -> dict:
    """Stream a signed URL to dest (size-capped). Returns status and bytes, never the URL."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with httpx.Client(timeout=120, follow_redirects=True) as h, h.stream("GET", url) as resp:
        if resp.status_code != 200:
            return {"status": resp.status_code}
        size = int(resp.headers.get("content-length") or 0)
        if size and size > max_mb * 1e6:
            return {"status": "too_large", "bytes": size}
        n = 0
        with open(tmp, "wb") as f:
            for chunk in resp.iter_bytes(1 << 20):
                n += len(chunk)
                if n > max_mb * 1e6:
                    f.close()
                    tmp.unlink(missing_ok=True)
                    return {"status": "too_large", "bytes": n}
                f.write(chunk)
    tmp.rename(dest)
    return {"status": 200, "bytes": n}


def _portal_size(path: str) -> tuple[int | None, int | None]:
    """(redirect status, size) of a portal resource via a 1-byte range request on the signed target."""
    with httpx.Client(timeout=30, follow_redirects=False) as h:
        r = h.head(f"{PORTAL}/resources/{path}")
        loc = r.headers.get("location")
        if not loc:
            return r.status_code, None
        r2 = h.get(loc, headers={"Range": "bytes=0-0"})
        cr = r2.headers.get("content-range", "")
        return r.status_code, int(cr.split("/")[-1]) if "/" in cr else None


def fetch_floorplans(model_id: str) -> list[dict]:
    q = "query F($id: ID!) { model(id: $id) { assets { floorplans { id format flags url } } } }"
    fps = mp.query(q, {"id": model_id})["data"]["model"]["assets"]["floorplans"]
    out = []
    for fp in fps:
        if "colored_rooms" not in (fp.get("flags") or []):
            continue
        name = fp["url"].split("?")[0].rsplit("/", 1)[-1]
        dest = DOWNLOADS / model_id / name
        if dest.exists():
            out.append({"file": name, "status": "cached", "bytes": dest.stat().st_size})
            continue
        res = _download(fp["url"], dest, 30)
        out.append({"file": name, **res})
    return out


def fetch_thumbnail(model_id: str) -> dict:
    dest = DOWNLOADS / model_id / "drone_thumbnail.jpg"
    if dest.exists():
        return {"status": "cached", "bytes": dest.stat().st_size}
    for name in THUMB_NAMES:
        status, size = _portal_size(f"{model_id}/drone-imagery/{name}")
        if status in (301, 302, 303, 307, 308):
            if size and size > THUMB_MAX_MB * 1e6:
                return {"name": name, "status": "too_large", "bytes": size}
            res = _download(f"{PORTAL}/resources/{model_id}/drone-imagery/{name}", dest, THUMB_MAX_MB)
            return {"name": name, **res}
    return {"status": 404, "tried": list(THUMB_NAMES)}


def run_fetch() -> None:
    targets = read_json(OUT / "visual_targets.json")["targets"]
    log = {}
    for t in targets:
        mid = t["model_id"]
        entry = {"floorplans": fetch_floorplans(mid)}
        if t["splat"]:
            entry["thumbnail"] = fetch_thumbnail(mid)
        log[mid] = entry
        print(mid, t["address"], json.dumps(entry)[:200], file=sys.stderr)
    write_json(OUT / "visual_fetch_log.json", {"at": now_iso(), "models": log})


def run_splats(ids: list[str]) -> None:
    if len(ids) > 3:
        raise SystemExit("at most 3 splats (brief)")
    for mid in ids:
        dest = DOWNLOADS / mid / "splat.ply"
        if dest.exists():
            print(mid, "cached", dest.stat().st_size, file=sys.stderr)
            continue
        status, size = _portal_size(f"{mid}/splat/splat.ply")
        print(mid, "redirect", status, "size", size, file=sys.stderr)
        if size and size > SPLAT_MAX_MB * 1e6:
            print(mid, "too large; skip", file=sys.stderr)
            continue
        res = _download(f"{PORTAL}/resources/{mid}/splat/splat.ply", dest, SPLAT_MAX_MB)
        print(mid, res, file=sys.stderr)


def run_meshes(ids: list[str]) -> None:
    q = (
        'query M($id: ID!) { model(id: $id) { assets { meshes(formats: "obj") '
        "{ id filename resolution url } } } }"
    )
    for mid in ids[:3]:
        meshes = mp.query(q, {"id": mid})["data"]["model"]["assets"]["meshes"]
        m50 = [m for m in meshes if m.get("resolution") == "50k"]
        if not m50:
            print(mid, "no 50k mesh", file=sys.stderr)
            continue
        m = m50[0]
        ext = m["url"].split("?")[0].rsplit(".", 1)[-1]
        dest = DOWNLOADS / mid / f"mesh_50k.{ext}"
        if dest.exists():
            print(mid, "cached", file=sys.stderr)
            continue
        res = _download(m["url"], dest, MESH_MAX_MB)
        print(mid, m["filename"], ext, res, file=sys.stderr)


def stats_for(path: Path, box: list[float] | None = None) -> dict:
    """Parse an untrusted download in an isolated interpreter (python -I)."""
    cmd = [sys.executable, "-I", str(STATS), str(path)]
    if box:
        cmd += ["--box", *[str(v) for v in box]]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=600, check=False)
    if out.returncode != 0:
        return {"error": out.stderr.strip()[-300:]}
    return json.loads(out.stdout)


def run_derive() -> None:
    targets = read_json(OUT / "visual_targets.json")["targets"]
    ratings = read_json(OUT / "visual_ratings.json") if (OUT / "visual_ratings.json").exists() else {}
    vx = {r["property_id"]: r for r in read_jsonl(OUT / "vexcel_verify.jsonl")}
    homes = {h["property_id"]: h for h in read_jsonl(OUT / "listings.jsonl")}
    rows = []
    for t in targets:
        pid, mid = t["property_id"], t["model_id"]
        h = homes[pid]
        d = DOWNLOADS / mid
        row = {
            **t,
            "floorplan_files": sorted(p.name for p in d.glob("colorplan_room_*")) if d.exists() else [],
            "thumbnail_file": "drone_thumbnail.jpg" if (d / "drone_thumbnail.jpg").exists() else None,
            "listing_alt_furniture_photos": h["alt_furniture_photos"],
            "listing_alt_empty_photos": h["alt_empty_photos"],
            "listing_photo_count": h["photo_count"],
        }
        for mesh in d.glob("mesh_50k.*") if d.exists() else []:
            row["mesh50k_stats"] = stats_for(mesh)
        if (d / "splat.ply").exists():
            # Footprint check: OUTSIDE splats appear to share the model's local frame (both centred near
            # the origin, Z up), so compare splat density inside the 50k mesh's XY core box with the
            # density in the rest of the 150 m near field.
            box = None
            mb = (row.get("mesh50k_stats") or {}).get("bbox")
            if mb:
                box = [mb["core_p1"][0], mb["core_p1"][1], mb["core_p99"][0], mb["core_p99"][1]]
            row["splat_stats"] = stats_for(d / "splat.ply", box)
            st = row["splat_stats"]
            if box and "density_in_box_per_m2" in st:
                ratio = st["density_in_box_per_m2"] / max(st["density_rest_150m_per_m2"], 1e-6)
                row["splat_house_density_ratio"] = round(ratio, 1)
                row["footprint_inside_splat"] = ratio >= 3
            fp = vx.get(pid, {})
            row["vexcel_primary_footprint_m2"] = fp.get("primary_footprint_m2")
            row["vexcel_parcel_m2"] = fp.get("parcel_m2")
        r = ratings.get(str(pid)) or ratings.get(mid)
        if r:
            row.update({k: v for k, v in r.items() if k != "address"})
        rows.append(row)
    write_jsonl(OUT / "visual_check.jsonl", rows)
    print(
        f"visual_check.jsonl: {len(rows)} rows, rated {sum(1 for r in rows if 'layout_complete' in r)}",
        file=sys.stderr,
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--select", action="store_true")
    g.add_argument("--fetch", action="store_true")
    g.add_argument("--splats", nargs="+")
    g.add_argument("--meshes", nargs="+")
    g.add_argument("--derive", action="store_true")
    a = p.parse_args(argv)
    if a.select:
        for t in select():
            print(t["rank"], t["address"], t["unit"] or "", t["model_id"], t["why"], file=sys.stderr)
    elif a.fetch:
        run_fetch()
    elif a.splats:
        run_splats(a.splats)
    elif a.meshes:
        run_meshes(a.meshes)
    elif a.derive:
        run_derive()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
