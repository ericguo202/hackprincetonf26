"""Cached, retried Matterport reads. Every call goes through MatterportClient().query (read-only)."""

from __future__ import annotations

import threading
from typing import Any

from worldgen.scout.common import Cache, load_env, now_iso, stable_hash, with_backoff
from worldgen.sources.matterport import MatterportClient

# Field names verified on the hackathon portal (docs/prompts/demo-property-scout.md).
MODEL_DETAIL = """
query ModelDetail($id: ID!) {
  model(id: $id) {
    id name created modified
    publication { published address }
    geocoordinates { latitude longitude altitude source }
    floors { id label
      rooms { id label
        classifications { label confidence defaultKeywords }
        dimensionEstimates(units: metric) { area areaIndoor height width depth }
        boundary { edges { id type thickness
          vertices { id position { x y } }
          openings { id type label width height } } } } }
    panoLocations { id label position { x y z } }
    assets {
      meshes(formats: "obj") { id format filename resolution }
      floorplans { id format flags } }
    bundles { id availability assets { id format } }
  }
}
"""
# `boundary` (Property Intelligence room vectors) was found by probing on 2026-11-14: BoundedRegion.edges
# [Edge{id type thickness vertices[Vertex{id position{x y}}] openings[Opening{id type label width height}]}].
# Edge ids are shared between rooms that share a wall, which gives adjacency.
# Note: `url` fields are deliberately not requested in the bulk query. They are signed, expiring
# CDN links; we store model and asset ids only and ask for a fresh url when we actually download.

_client: MatterportClient | None = None
_lock = threading.Lock()


def client() -> MatterportClient:
    global _client
    with _lock:
        if _client is None:
            load_env()
            _client = MatterportClient()
        return _client


def query(q: str, variables: dict | None = None, what: str = "matterport") -> dict:
    return with_backoff(lambda: client().query(q, variables), what=what)


QUERY_VERSION = stable_hash(MODEL_DETAIL)


def model_detail(model_id: str, cache: Cache | None = None, refresh: bool = False) -> dict:
    """Raw GraphQL response for one model, cached at out/scout/cache/mp_detail/<id>.json.
    A cached record from an older query version is refetched."""
    cache = cache or Cache("mp_detail")
    if not refresh:
        hit = cache.get(model_id)
        if hit is not None and hit.get("query_version") == QUERY_VERSION:
            return hit
    resp = query(MODEL_DETAIL, {"id": model_id}, what=f"model {model_id}")
    record: dict[str, Any] = {
        "fetched_at": now_iso(),
        "model_id": model_id,
        "query_version": QUERY_VERSION,
        "response": resp,
    }
    cache.put(model_id, record)
    return record
