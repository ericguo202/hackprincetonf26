"""Source data for the procedural block prototype: Vexcel (billed, cached) and OpenStreetMap (free).

Every Vexcel response is saved under out/block_compare/cache/vexcel/ with a descriptive name
(product, bbox, resolution) so the other block prototype can reuse it, and every network call is
appended to a ledger there. `fetch_all()` is idempotent: it only calls the network for files that
are missing.

Block frame: origin at the block centre on the ground, +X east, +Y up, +Z south, meters.
"""

from __future__ import annotations

import base64
import json
import math
import sys
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

WORLDGEN = Path(__file__).resolve().parent
OUT = WORLDGEN / "out" / "block_compare"
CACHE = OUT / "cache"
VX_CACHE = CACHE / "vexcel"
OSM_CACHE = CACHE / "osm"
LEDGER = VX_CACHE / "ledger_procedural.jsonl"

ORIGIN = (29.70113, -95.670841)  # lat, lon (manifest of hou_navidad_01)
HALF = 170.0  # meters: fetch a bit more than the 320 m square so edge lots are whole

OVERPASS = "https://overpass-api.de/api/interpreter"
UA = "worldgen-block-builder/0.1"
OSM_BBOX = (29.6988, -95.6735, 29.7034, -95.6682)  # s, w, n, e: about 250 m around the origin


def m_per_deg(lat: float) -> tuple[float, float]:
    """Meters per degree of latitude and longitude at `lat` (WGS84 series)."""
    p = math.radians(lat)
    mlat = 111132.92 - 559.82 * math.cos(2 * p) + 1.175 * math.cos(4 * p)
    mlon = 111412.84 * math.cos(p) - 93.5 * math.cos(3 * p)
    return mlat, mlon


MLAT, MLON = m_per_deg(ORIGIN[0])


def to_xz(lat: float, lon: float) -> tuple[float, float]:
    return (lon - ORIGIN[1]) * MLON, -(lat - ORIGIN[0]) * MLAT


def to_latlon(x: float, z: float) -> tuple[float, float]:
    return ORIGIN[0] - z / MLAT, ORIGIN[1] + x / MLON


def bbox_ll(half: float = HALF) -> tuple[float, float, float, float]:
    """(lon_min, lat_min, lon_max, lat_max) of the square of half-size `half` m."""
    lat_n, lon_w = to_latlon(-half, -half)
    lat_s, lon_e = to_latlon(half, half)
    return lon_w, lat_s, lon_e, lat_n


def bbox_wkt(half: float = HALF) -> str:
    w, s, e, n = bbox_ll(half)
    return f"POLYGON(({w:.7f} {s:.7f}, {e:.7f} {s:.7f}, {e:.7f} {n:.7f}, {w:.7f} {n:.7f}, {w:.7f} {s:.7f}))"


def _tag(half: float = HALF) -> str:
    w, s, e, n = bbox_ll(half)
    return f"{s:.5f}_{w:.5f}_{n:.5f}_{e:.5f}"


# ---------------- OSM ----------------


def fetch_osm() -> Path:
    s, w, n, e = OSM_BBOX
    path = OSM_CACHE / f"osm_navidad_bbox_{s}_{w}_{n}_{e}.json"
    if path.exists():
        return path
    bb = f"{s},{w},{n},{e}"
    q = (
        f"[out:json][timeout:60];(way[highway]({bb});way[building]({bb});way[barrier]({bb});"
        f"node[natural=tree]({bb});way[landuse]({bb});way[leisure]({bb});way[amenity]({bb});"
        f"node[highway]({bb}););out body;>;out skel qt;"
    )
    r = httpx.post(OVERPASS, data={"data": q}, headers={"User-Agent": UA}, timeout=90)
    r.raise_for_status()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(r.content)
    return path


# ---------------- Vexcel ----------------


def _env() -> None:
    load_dotenv(WORLDGEN.parent / ".env", override=False)


def _ledger(**row: Any) -> None:
    from datetime import UTC, datetime

    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    row = {"ts": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "by": "procedural", **row}
    with open(LEDGER, "a") as f:
        f.write(json.dumps(row) + "\n")


def _mcp_json(tool: str, args: dict, path: Path) -> Any:
    if path.exists():
        return json.loads(path.read_text())
    from worldgen.sources.vexcel import VexcelMCP

    _env()
    mcp = VexcelMCP()
    try:
        res = mcp.call_tool(tool, args)
    finally:
        mcp.close()
    _ledger(kind="mcp", tool=tool, arguments=args, is_error=bool(res.get("isError")), file=path.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(res))
    return res


def _rest_bin(endpoint: str, params: dict, path: Path) -> Path:
    if path.exists():
        return path
    from worldgen.sources.vexcel import VexcelClient

    _env()
    vx = VexcelClient(cache_dir=VX_CACHE / "rest_raw")
    try:
        meta, body = vx.get(endpoint, params)
    finally:
        vx.close()
    _ledger(
        kind="rest", path=endpoint, params=params, status=meta["status"], bytes=meta["bytes"], file=path.name
    )
    if meta["status"] != 200:
        raise RuntimeError(f"{endpoint}: HTTP {meta['status']} {body[:300]!r}")
    ctype = meta.get("content_type", "")
    if "json" in ctype:
        data = json.loads(body)
        b64 = data.get("image") or data.get("data")
        if not b64:
            raise RuntimeError(f"{endpoint}: JSON without image: {str(data)[:300]}")
        body = base64.b64decode(b64)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return path


def fetch_structures() -> Any:
    path = VX_CACHE / f"structure_geometry_metadata_extract_bbox{_tag()}_roofheight.json"
    args = {
        "wkt": bbox_wkt(),
        "srid": 4326,
        "layer": ["urban"],
        "latest": True,
        "include": ["roof-elevation", "roof-height"],
        "unit-system": "metric",
        "page-size": 500,
    }
    return _mcp_json("structure_geometry_metadata_extract", args, path)


def fetch_ortho(zoom: int = 21) -> Path:
    path = VX_CACHE / f"ortho_extract_urban_bbox{_tag()}_z{zoom}.jpg"
    params = {
        "layer": "urban",
        "wkt": bbox_wkt(),
        "srid": 4326,
        "zoom": zoom,
        "crop": "bounding_box",
        "image-format": "jpeg",
        "order-by": "last-capture-date-desc",
    }
    return _rest_bin("ortho/extract", params, path)


def fetch_elev(kind: str, zoom: int = 19) -> Path:
    """kind = 'dsm' or 'dtm'. Returns a (probably float32 GeoTIFF) raster."""
    path = VX_CACHE / f"{kind}_extract_urban_bbox{_tag()}_z{zoom}.tif"
    params = {
        "layer": "urban",
        "wkt": bbox_wkt(),
        "srid": 4326,
        "zoom": zoom,
        "crop": "bounding_box",
    }
    return _rest_bin(f"{kind}/extract", params, path)


def fetch_all() -> None:
    print("osm:", fetch_osm(), file=sys.stderr)
    s = fetch_structures()
    print("structures:", str(s)[:200], file=sys.stderr)
    print("ortho:", fetch_ortho(), file=sys.stderr)
    print("dsm:", fetch_elev("dsm", 20), file=sys.stderr)
    # dtm/extract (layer urban) answers 404 for this block; the ground model is derived from the DSM.
    print("parcels:", str(fetch_parcels())[:120], file=sys.stderr)


if __name__ == "__main__":
    fetch_all()


def fetch_parcels() -> Any:
    """Parcels (property geometry) with precomputed structure attributes (roof shape and material)."""
    path = VX_CACHE / f"property_metadata_extract_bbox{_tag(160)}_precomputed.json"
    args = {
        "wkt": bbox_wkt(160),
        "srid": 4326,
        "layer": ["urban"],
        "latest": True,
        "include": ["precomputed"],
        "unit-system": "metric",
        "page-size": 500,
    }
    return _mcp_json("property_metadata_extract", args, path)
