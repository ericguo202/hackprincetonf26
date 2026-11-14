"""Cached, retried Matterport reads. Every call goes through MatterportClient().query (read-only)."""

from __future__ import annotations

import threading
from typing import Any

from worldgen.scout.common import Cache, load_env, now_iso, with_backoff
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
        dimensionEstimates(units: metric) { area areaIndoor height } } }
    panoLocations { id label position { x y z } }
    assets {
      meshes(formats: "obj") { id format filename resolution }
      floorplans { id format flags } }
    bundles { id availability assets { id format } }
  }
}
"""
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


def model_detail(model_id: str, cache: Cache | None = None, refresh: bool = False) -> dict:
    """Raw GraphQL response for one model, cached at out/scout/cache/mp_detail/<id>.json."""
    cache = cache or Cache("mp_detail")
    if not refresh:
        hit = cache.get(model_id)
        if hit is not None:
            return hit
    resp = query(MODEL_DETAIL, {"id": model_id}, what=f"model {model_id}")
    record: dict[str, Any] = {"fetched_at": now_iso(), "model_id": model_id, "response": resp}
    cache.put(model_id, record)
    return record
