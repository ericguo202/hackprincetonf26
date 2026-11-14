"""Vexcel API client (key from VEXCEL_API_KEY). Offline only; the game never calls Vexcel.

Vexcel bills per unit, so every REST response is cached on disk (keyed by path + params without the
key) and every network call is appended to a ledger. The REST key travels in the query string, so:
full URLs are never logged, stored or put in exception messages, and any echo of the key in a
response body is scrubbed before it is cached.

REST:  VexcelClient().get_json("property/extract", {"wkt": "POINT(lon lat)", "srid": 4326})
MCP:   VexcelMCP().call_tool("usage_query", {...})  (JSON-RPC over streamable HTTP)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

REST_BASE = "https://api.vexcelgroup.com/v2"
MCP_URL = "https://api.vexcelgroup.com/v2/mcp"
MCP_PROTOCOL = "2025-06-18"

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

_RETRY = {429, 500, 502, 503, 504}


class VexcelError(RuntimeError):
    """Raised without the request URL (it carries the key)."""


class BudgetExceeded(VexcelError):
    pass


def _key() -> str:
    k = os.environ.get("VEXCEL_API_KEY", "")
    if not k:
        raise VexcelError("VEXCEL_API_KEY is not set")
    return k


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


class _Ledger:
    """Append-only JSONL of network calls: endpoint, params without the key, status, bytes."""

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self._lock = threading.Lock()

    def add(self, **row: Any) -> None:
        if not self.path:
            return
        row = {"ts": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), **row}
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a") as f:
                f.write(json.dumps(row) + "\n")

    def count(self, kind: str | None = None) -> int:
        if not self.path or not self.path.exists():
            return 0
        n = 0
        with open(self.path) as f:
            for line in f:
                if not line.strip():
                    continue
                if kind is None or json.loads(line).get("kind") == kind:
                    n += 1
        return n


class VexcelClient:
    """REST client. `max_network_calls` is a hard stop across restarts (counted from the ledger)."""

    def __init__(
        self,
        cache_dir: Path | str | None = None,
        ledger_path: Path | str | None = None,
        max_network_calls: int | None = None,
        timeout: float = 60,
    ) -> None:
        self._key = _key()
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.ledger = _Ledger(Path(ledger_path) if ledger_path else None)
        self.max_network_calls = max_network_calls
        self._http = httpx.Client(timeout=timeout, follow_redirects=True)
        self._lock = threading.Lock()

    # -- helpers --

    def scrub(self, text: str) -> str:
        return text.replace(self._key, "***") if self._key else text

    @staticmethod
    def cache_key(path: str, params: dict[str, Any]) -> str:
        clean = {k: params[k] for k in sorted(params) if k != "api_key"}
        h = hashlib.sha1(json.dumps([path, clean], sort_keys=True, default=str).encode()).hexdigest()
        return f"{path.strip('/').replace('/', '_')}__{h[:24]}"

    def _cache_paths(self, key: str) -> tuple[Path, Path] | None:
        if not self.cache_dir:
            return None
        return self.cache_dir / f"{key}.body", self.cache_dir / f"{key}.meta.json"

    def cached(self, path: str, params: dict[str, Any]) -> bool:
        cp = self._cache_paths(self.cache_key(path, params))
        return bool(cp and cp[1].exists())

    # -- core --

    def get(self, path: str, params: dict[str, Any], *, use_cache: bool = True) -> tuple[dict, bytes]:
        """GET REST_BASE/path with params. Returns (meta, body). meta has status, content_type, cached."""
        key = self.cache_key(path, params)
        cp = self._cache_paths(key)
        if use_cache and cp and cp[1].exists() and cp[0].exists():
            meta = json.loads(cp[1].read_text())
            meta["cached"] = True
            return meta, cp[0].read_bytes()

        with self._lock:
            if self.max_network_calls is not None:
                used = self.ledger.count("rest")
                if used >= self.max_network_calls:
                    raise BudgetExceeded(f"Vexcel REST budget reached ({used} calls)")

        safe_params = {k: v for k, v in params.items() if k != "api_key"}
        url = f"{REST_BASE}/{path.strip('/')}"
        resp = None
        for attempt in range(5):
            try:
                resp = self._http.get(url, params={**safe_params, "api_key": self._key})
            except httpx.TransportError as e:
                if attempt == 4:
                    self.ledger.add(
                        kind="rest", path=path, params=safe_params, status=None, error=type(e).__name__
                    )
                    raise VexcelError(f"{path}: transport error {type(e).__name__}") from None
                time.sleep(2**attempt)
                continue
            if resp.status_code in _RETRY and attempt < 4:
                self.ledger.add(
                    kind="rest",
                    path=path,
                    params=safe_params,
                    status=resp.status_code,
                    bytes=len(resp.content),
                    retry=True,
                )
                time.sleep(2**attempt)
                continue
            break
        assert resp is not None
        body = resp.content
        ctype = resp.headers.get("content-type", "")
        if "json" in ctype or "text" in ctype:
            body = self.scrub(body.decode("utf-8", "replace")).encode()
        meta = {
            "path": path,
            "params": safe_params,
            "status": resp.status_code,
            "content_type": ctype,
            "bytes": len(body),
            "fetched_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        self.ledger.add(kind="rest", path=path, params=safe_params, status=resp.status_code, bytes=len(body))
        # Cache 2xx and 4xx (a 400/404 is a stable answer we should not pay for twice); not 5xx.
        if cp and resp.status_code < 500:
            _atomic_write(cp[0], body)
            _atomic_write(cp[1], json.dumps(meta).encode())
        meta["cached"] = False
        return meta, body

    def get_json(self, path: str, params: dict[str, Any], **kw: Any) -> tuple[dict, Any]:
        meta, body = self.get(path, params, **kw)
        try:
            return meta, json.loads(body)
        except ValueError:
            return meta, {"_non_json": body[:500].decode("utf-8", "replace")}

    def close(self) -> None:
        self._http.close()


class VexcelMCP:
    """Minimal MCP (streamable HTTP, JSON-RPC 2.0) client for usage, profile and coverage tools."""

    def __init__(self, ledger_path: Path | str | None = None, timeout: float = 60) -> None:
        self._key = _key()
        self.ledger = _Ledger(Path(ledger_path) if ledger_path else None)
        self._http = httpx.Client(timeout=timeout)
        self._session: str | None = None
        self._id = 0
        self.server_info: dict | None = None

    def _headers(self) -> dict[str, str]:
        h = {
            "X-API-KEY": self._key,
            "MCP-Protocol-Version": MCP_PROTOCOL,
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._session:
            h["Mcp-Session-Id"] = self._session
        return h

    def _post(self, payload: dict) -> tuple[httpx.Response, dict | None]:
        try:
            resp = self._http.post(MCP_URL, json=payload, headers=self._headers())
        except httpx.TransportError as e:
            raise VexcelError(f"MCP transport error {type(e).__name__}") from None
        if resp.status_code >= 400:
            raise VexcelError(f"MCP HTTP {resp.status_code}: {resp.text[:300].replace(self._key, '***')}")
        if "id" not in payload:  # notification
            return resp, None
        ctype = resp.headers.get("content-type", "")
        if "text/event-stream" in ctype:
            msg = None
            for line in resp.text.splitlines():
                if line.startswith("data:"):
                    data = line[5:].strip()
                    if data:
                        obj = json.loads(data)
                        if obj.get("id") == payload["id"]:
                            msg = obj
            return resp, msg
        return resp, resp.json() if resp.content else None

    def _rpc(self, method: str, params: dict | None = None) -> Any:
        self._id += 1
        payload = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or {}}
        _, msg = self._post(payload)
        if msg is None:
            raise VexcelError(f"MCP {method}: no response")
        if "error" in msg:
            raise VexcelError(f"MCP {method}: {json.dumps(msg['error'])[:300]}")
        return msg.get("result")

    def initialize(self) -> dict:
        self._id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._id,
            "method": "initialize",
            "params": {
                "protocolVersion": MCP_PROTOCOL,
                "capabilities": {},
                "clientInfo": {"name": "worldgen-scout", "version": "0.1"},
            },
        }
        resp, msg = self._post(payload)
        self._session = resp.headers.get("mcp-session-id")
        if not msg or "result" not in msg:
            raise VexcelError("MCP initialize failed")
        self.server_info = msg["result"]
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return msg["result"]

    def list_tools(self) -> list[dict]:
        tools: list[dict] = []
        cursor = None
        while True:
            res = self._rpc("tools/list", {"cursor": cursor} if cursor else {})
            tools.extend(res.get("tools", []))
            cursor = res.get("nextCursor")
            if not cursor:
                return tools

    def call_tool(self, name: str, arguments: dict | None = None) -> Any:
        if not self._session and self.server_info is None:
            self.initialize()
        res = self._rpc("tools/call", {"name": name, "arguments": arguments or {}})
        self.ledger.add(kind="mcp", tool=name, arguments=arguments or {}, is_error=bool(res.get("isError")))
        return res

    @staticmethod
    def content_json(result: Any) -> Any:
        """Best-effort: parse the first text content block of a tools/call result as JSON."""
        if not isinstance(result, dict):
            return result
        if "structuredContent" in result and result["structuredContent"] is not None:
            return result["structuredContent"]
        for block in result.get("content", []):
            if block.get("type") == "text":
                try:
                    return json.loads(block["text"])
                except ValueError:
                    return block["text"]
        return result

    def close(self) -> None:
        self._http.close()
