"""The only way to call Matterport. Read-only: any GraphQL mutation is rejected.

Matterport keys are admin keys that can buy things (AGENTS.md rule 9). Offline worldgen only.
Lane A owns this file (worldgen/sources/matterport.py, AGENTS.md rule 9).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import httpx

ENDPOINT = "https://api.matterport.com/api/models/graph"

# Strip comments and string literals, then look for a mutation/subscription operation.
_COMMENT = re.compile(r"#[^\n]*")
_STRING = re.compile(r'"""[\s\S]*?"""|"(?:\\.|[^"\\])*"')
_FORBIDDEN_OP = re.compile(r"(^|[\s{}])(mutation|subscription)\b", re.IGNORECASE)


class MutationRejected(ValueError):
    pass


def assert_read_only(query: str) -> None:
    cleaned = _COMMENT.sub("", _STRING.sub('""', query))
    if _FORBIDDEN_OP.search(cleaned):
        raise MutationRejected("Matterport client is read-only; mutations are not allowed")


class MatterportClient:
    def __init__(self, token_id: str | None = None, token_secret: str | None = None) -> None:
        self._auth = (
            token_id or os.environ["MATTERPORT_API_TOKEN_ID"],
            token_secret or os.environ["MATTERPORT_API_TOKEN_SECRET"],
        )

    def query(self, query: str, variables: dict | None = None) -> dict:
        assert_read_only(query)
        resp = httpx.post(
            ENDPOINT, json={"query": query, "variables": variables or {}}, auth=self._auth, timeout=60
        )
        resp.raise_for_status()
        body = resp.json()
        if body.get("errors"):
            raise RuntimeError(f"Matterport GraphQL errors: {body['errors']}")
        return body


def load_env() -> None:
    """Load .env from WORLDGEN_ENV_FILE, else the repo root. Values already set in the environment win."""
    from dotenv import load_dotenv

    path = os.environ.get("WORLDGEN_ENV_FILE") or str(Path(__file__).resolve().parents[2] / ".env")
    load_dotenv(path, override=False)


# Room vectors (`boundary`) are Property Intelligence data on our keys (found 2026-11-14). Edge ids are
# shared between rooms that share a wall. Openings carry type/width/height but not their position on the
# edge. Signed `url` fields are never cached: ask for a fresh one right before a download.
MODEL_DETAIL = """
query ModelDetail($id: ID!) {
  model(id: $id) {
    id name created modified
    publication { address }
    geocoordinates { latitude longitude altitude source }
    floors { id label
      rooms { id label
        classifications { label confidence }
        dimensionEstimates(units: metric) { area areaIndoor height width depth }
        boundary { edges { id type thickness
          vertices { id position { x y } }
          openings { id type label width height } } } } }
    panoLocations { id label position { x y z } rotation { x y z w } }
    assets { meshes(formats: "obj") { id format filename resolution } }
  }
}
"""

# Geo anchor of a model: `rotation` is "the rotation from an East-North-Up (x, y, z) to the model coordinate
# system around the translation point"; `translation` is "the translation in model coordinates from
# (latitude, longitude, altitude) to the (0, 0, 0) point in the model mesh" (schema descriptions).
# The probes ask Matterport itself where model points (0,0,0), (10,0,0) and (0,10,0) are on the globe, so the
# sign convention can be checked against the API instead of assumed (worldgen/costar.py house_world).
GEO_ANCHOR = """
query Geo($id: ID!) {
  model(id: $id) {
    id
    geocoordinates {
      latitude longitude altitude source
      rotation { x y z w }
      translation { x y z }
      origin: geoLocationOf(modelLocation: {x: 0, y: 0, z: 0}) { lat long }
      x10: geoLocationOf(modelLocation: {x: 10, y: 0, z: 0}) { lat long }
      y10: geoLocationOf(modelLocation: {x: 0, y: 10, z: 0}) { lat long }
    }
  }
}
"""


def fetch_geo_anchor(model_id: str, cache_dir: Path, refresh: bool = False) -> dict:
    """The model's `geocoordinates` with rotation, translation and three geoLocationOf probes, cached as
    cache_dir/<model_id>.geo.json."""
    path = cache_dir / f"{model_id}.geo.json"
    if path.exists() and not refresh:
        return json.loads(path.read_text(encoding="utf-8"))
    load_env()
    geo = MatterportClient().query(GEO_ANCHOR, {"id": model_id})["data"]["model"]["geocoordinates"]
    cache_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(geo, indent=1), encoding="utf-8")
    return geo


_MESH_URLS = 'query M($id: ID!) { model(id: $id) { assets { meshes(formats: "obj") { filename url } } } }'


def fetch_model(model_id: str, cache_dir: Path, refresh: bool = False) -> dict:
    """Model detail (floors, rooms, boundary vectors, mesh list), cached as JSON in cache_dir."""
    path = cache_dir / f"{model_id}.json"
    if path.exists() and not refresh:
        return json.loads(path.read_text(encoding="utf-8"))
    load_env()
    model = MatterportClient().query(MODEL_DETAIL, {"id": model_id})["data"]["model"]
    cache_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(model, indent=1), encoding="utf-8")
    return model


_BUNDLES = "query B($id: ID!) { model(id: $id) { bundles { id availability assets { id format url } } } }"


def download_bundle_asset(model_id: str, bundle_id: str, dest: Path, max_mb: float = 1500) -> Path:
    """Download the asset of an ALREADY UNLOCKED bundle (e.g. mp:matterpak). Never unlocks or buys anything:
    a bundle that is not `unlocked` raises instead. Skips if dest exists."""
    if dest.exists():
        return dest
    load_env()
    bundles = MatterportClient().query(_BUNDLES, {"id": model_id})["data"]["model"]["bundles"]
    b = next((x for x in bundles if x["id"] == bundle_id), None)
    if b is None or b.get("availability") != "unlocked":
        raise PermissionError(
            f"{model_id}: bundle {bundle_id} is {b and b.get('availability')}; not unlocking it"
        )
    urls = [a["url"] for a in b.get("assets") or [] if a.get("url")]
    if not urls:
        raise FileNotFoundError(f"{model_id}: bundle {bundle_id} has no downloadable asset")
    return _stream(urls[0], dest, max_mb, f"{model_id}/{bundle_id}")


_SKYBOX = """query S($id: ID!, $res: String!) { model(id: $id) { panoLocations {
  id skybox(resolution: $res) { status children } } } }"""


def download_skyboxes(model_id: str, dest: Path, resolution: str = "2k") -> dict[str, list[Path]]:
    """Download each scan position's panorama as 6 cube faces: dest/<pano_id>_<face 0-5>.jpg.

    These are the model's standard skybox images (status `available` on our keys), not the locked
    mp:imagery bundle. Returns {pano_id: [6 paths]}; positions whose skybox is not available are skipped."""
    load_env()
    try:
        panos = MatterportClient().query(_SKYBOX, {"id": model_id, "res": resolution})
    except RuntimeError:
        q = _SKYBOX.replace("$res: String!", "$res: SkyboxResolution!")
        panos = MatterportClient().query(q, {"id": model_id, "res": resolution})
    out: dict[str, list[Path]] = {}
    dest.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=120, follow_redirects=True) as h:
        for p in panos["data"]["model"]["panoLocations"]:
            sky = p.get("skybox") or {}
            urls = sky.get("children") or []
            if sky.get("status") != "available" or len(urls) != 6:
                continue
            paths = [dest / f"{p['id']}_{i}.jpg" for i in range(6)]
            for url, path in zip(urls, paths, strict=True):
                if not path.exists():
                    r = h.get(url)  # signed URL: never logged or stored
                    r.raise_for_status()
                    path.write_bytes(r.content)
            out[p["id"]] = paths
    return out


def _stream(url: str, dest: Path, max_mb: float, what: str) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    n = 0
    # Never log or store the signed URL.
    with httpx.Client(timeout=600, follow_redirects=True) as h, h.stream("GET", url) as resp:
        resp.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in resp.iter_bytes(1 << 20):
                n += len(chunk)
                if n > max_mb * 1e6:
                    raise ValueError(f"{what}: larger than {max_mb} MB")
                f.write(chunk)
    tmp.rename(dest)
    return dest


def download_mesh(model_id: str, filename: str, dest: Path, max_mb: float = 400) -> Path:
    """Download one OBJ-format mesh asset (by its filename) to dest. Skips if dest already exists.

    The asset may arrive as a zip (OBJ + MTL + textures); the caller unpacks it."""
    if dest.exists():
        return dest
    load_env()
    meshes = MatterportClient().query(_MESH_URLS, {"id": model_id})["data"]["model"]["assets"]["meshes"]
    match = [m for m in meshes if m.get("filename") == filename]
    if not match or not match[0].get("url"):
        raise FileNotFoundError(f"{model_id}: no mesh asset named {filename}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    n = 0
    # Never log or store the signed URL.
    with httpx.Client(timeout=300, follow_redirects=True) as h, h.stream("GET", match[0]["url"]) as resp:
        resp.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in resp.iter_bytes(1 << 20):
                n += len(chunk)
                if n > max_mb * 1e6:
                    raise ValueError(f"{model_id}/{filename}: larger than {max_mb} MB")
                f.write(chunk)
    tmp.rename(dest)
    return dest
