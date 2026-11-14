"""Phase 0: smoke-test Matterport, Vexcel REST, Vexcel MCP and one splat redirect (headers only).

uv run python -m worldgen.scout.smoke [--skip-rest]

Writes out/scout/phase0_smoke.json and out/scout/vexcel_mcp_tools.json. Never prints keys or URLs.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from urllib.parse import urlparse

import httpx

from worldgen.scout import mp
from worldgen.scout.common import (
    LEDGER,
    OUT,
    PORTAL,
    SPLAT_TSV,
    ensure_out,
    load_env,
    now_iso,
    write_json,
)
from worldgen.sources.vexcel import VexcelClient, VexcelError, VexcelMCP


def splat_ids() -> list[dict]:
    with open(SPLAT_TSV) as f:
        return list(csv.DictReader(f, delimiter="\t"))


def smoke_matterport(model_id: str) -> dict:
    rec = mp.model_detail(model_id)
    resp = rec["response"]
    m = (resp.get("data") or {}).get("model") or {}
    floors = m.get("floors") or []
    rooms = [r for fl in floors for r in (fl.get("rooms") or [])]
    assets = m.get("assets") or {}
    return {
        "ok": bool(m) and not resp.get("errors"),
        "errors": resp.get("errors"),
        "model_id": model_id,
        "name": m.get("name"),
        "geocoordinates": m.get("geocoordinates"),
        "floors": len(floors),
        "rooms": len(rooms),
        "room_labels": sorted({(r.get("label") or "") for r in rooms}),
        "bundles": [
            {"id": b.get("id"), "availability": b.get("availability")} for b in m.get("bundles") or []
        ],
        "mesh_resolutions": sorted({x.get("resolution") for x in assets.get("meshes") or []}),
        "floorplan_formats": sorted({x.get("format") for x in assets.get("floorplans") or []}),
    }


def smoke_splat_redirect(model_id: str) -> dict:
    """HEAD the splat resource without following the redirect, then HEAD the signed target.
    Records only status codes, host and size; the signed URL is discarded."""
    out: dict = {"model_id": model_id}
    with httpx.Client(timeout=30, follow_redirects=False) as h:
        r = h.head(f"{PORTAL}/resources/{model_id}/splat/splat.ply")
        out["status"] = r.status_code
        loc = r.headers.get("location")
        out["redirect_host"] = urlparse(loc).netloc if loc else None
        if loc:
            r2 = h.head(loc)
            out["target_status"] = r2.status_code
            out["target_content_length"] = int(r2.headers.get("content-length", 0)) or None
            out["target_content_type"] = r2.headers.get("content-type")
            out["target_last_modified"] = r2.headers.get("last-modified")
        thumb = h.head(f"{PORTAL}/resources/{model_id}/drone-imagery/thumbnail.JPG")
        out["thumbnail_status"] = thumb.status_code
        env = h.head(f"{PORTAL}/resources/{model_id}/splat/envmap/px.jpg")
        out["envmap_px_status"] = env.status_code
    out["ok"] = out["status"] in (301, 302, 303, 307, 308) and out.get("target_status") == 200
    return out


def smoke_mcp() -> dict:
    mcp = VexcelMCP(ledger_path=LEDGER / "vexcel_calls.jsonl")
    out: dict = {}
    try:
        info = mcp.initialize()
        out["server"] = info.get("serverInfo")
        out["protocol"] = info.get("protocolVersion")
        tools = mcp.list_tools()
        write_json(OUT / "vexcel_mcp_tools.json", tools)
        out["tool_count"] = len(tools)
        out["tools"] = [t["name"] for t in tools]
        for name in ("user_profile_query", "usage_query"):
            if name in out["tools"]:
                try:
                    out[name] = VexcelMCP.content_json(mcp.call_tool(name, {}))
                except VexcelError as e:
                    out[name] = {"error": str(e)}
        out["ok"] = True
    except VexcelError as e:
        out["ok"] = False
        out["error"] = str(e)
    finally:
        mcp.close()
    return out


def smoke_rest(lat: float, lon: float) -> dict:
    vx = VexcelClient(cache_dir=OUT / "cache" / "vexcel", ledger_path=LEDGER / "vexcel_calls.jsonl")
    try:
        meta, body = vx.get_json("property/extract", {"wkt": f"POINT({lon} {lat})", "srid": 4326})
    except VexcelError as e:
        return {"ok": False, "error": str(e)}
    finally:
        vx.close()
    summary: dict = {"status": meta["status"], "cached": meta["cached"], "bytes": meta["bytes"]}
    if isinstance(body, dict):
        summary["top_keys"] = sorted(body.keys())[:30]
    elif isinstance(body, list):
        summary["list_len"] = len(body)
    summary["ok"] = meta["status"] == 200
    return summary


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--model", default=None, help="splat model id to test (default: first in the TSV)")
    p.add_argument("--skip-rest", action="store_true", help="skip the billed Vexcel REST call")
    args = p.parse_args(argv)
    load_env()
    ensure_out()

    rows = splat_ids()
    model_id = args.model or rows[0]["Model"]
    report: dict = {"run_at": now_iso(), "model_id": model_id}

    print(f"[matterport] model {model_id}", file=sys.stderr)
    report["matterport"] = smoke_matterport(model_id)
    print(json.dumps(report["matterport"], indent=1)[:1500], file=sys.stderr)

    print("[splat] redirect headers", file=sys.stderr)
    report["splat"] = smoke_splat_redirect(model_id)
    print(json.dumps(report["splat"], indent=1), file=sys.stderr)

    print("[vexcel mcp] initialize, tools, profile, usage", file=sys.stderr)
    report["vexcel_mcp"] = smoke_mcp()
    print(json.dumps(report["vexcel_mcp"], indent=1)[:4000], file=sys.stderr)

    if not args.skip_rest:
        geo = report["matterport"].get("geocoordinates") or {}
        lat, lon = geo.get("latitude"), geo.get("longitude")
        if lat is None:
            lat, lon = 29.7458, -95.4873  # documented Houston cluster centre
        print(f"[vexcel rest] property/extract at {lat:.5f},{lon:.5f}", file=sys.stderr)
        report["vexcel_rest"] = smoke_rest(lat, lon)
        print(json.dumps(report["vexcel_rest"], indent=1), file=sys.stderr)

    write_json(OUT / "phase0_smoke.json", report)
    ok = all(report[k].get("ok") for k in ("matterport", "splat", "vexcel_mcp", "vexcel_rest") if k in report)
    print("PHASE0", "OK" if ok else "PARTIAL", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
