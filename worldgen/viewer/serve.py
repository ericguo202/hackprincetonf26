"""Lane A pack QA viewer: local static server. NOT THE GAME.

This serves worldgen/viewer/ (a throwaway three.js test page) and a local packs folder so Lane A can eyeball
house packs. It is not part of the game, is never deployed, and nothing in apps/ may import, copy or port it.
See worldgen/viewer/README.md.

    python3 worldgen/viewer/serve.py      # reads worldgen/out/packs and worldgen/out/furniture
    open http://127.0.0.1:8765

Standard library only, so it runs without `uv sync`. Binds to 127.0.0.1.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

HERE = Path(__file__).resolve().parent
DEFAULT_PACKS = HERE.parent / "out" / "packs"
DEFAULT_FURNITURE = HERE.parent / "out" / "furniture"
TYPES = {
    ".glb": "model/gltf-binary",
    ".json": "application/json",
    ".js": "text/javascript",
    ".html": "text/html; charset=utf-8",
    ".css": "text/css",
    ".md": "text/markdown; charset=utf-8",
}


def list_packs(root: Path) -> list[dict]:
    """Every <block_id>/v<N>/manifest.json under the packs root."""
    out = []
    for manifest in sorted(root.glob("*/v*/manifest.json")):
        rel = manifest.parent.relative_to(root).as_posix()
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            houses = [h.get("listing_id") for h in data.get("houses", [])]
        except (OSError, ValueError):
            houses = []
        out.append({"pack": rel, "houses": houses})
    return out


def safe_join(root: Path, rel: str) -> Path | None:
    """root/rel, or None if rel escapes root."""
    path = (root / rel).resolve()
    return path if path.is_relative_to(root.resolve()) else None


def furniture_versions(furniture_root: Path | None) -> list[int]:
    """v<N> folders of the shared furniture library (contracts v2.3.0: <root>/furniture/v<N>/)."""
    if furniture_root is None:
        return []
    return sorted(int(p.name[1:]) for p in furniture_root.glob("v*") if p.name[1:].isdigit() and p.is_dir())


def make_handler(packs_root: Path, furniture_root: Path | None):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:  # quieter than the default
            if args and str(args[1]) not in ("200", "304"):
                super().log_message(fmt, *args)

        def do_GET(self) -> None:  # noqa: N802 (http.server API)
            path = unquote(urlparse(self.path).path)
            if path == "/api/packs":
                body = json.dumps({
                    "packs_root": str(packs_root),
                    "furniture_root": str(furniture_root) if furniture_root else None,
                    "furniture_versions": furniture_versions(furniture_root),
                    "packs": list_packs(packs_root),
                }).encode()
                return self._send(HTTPStatus.OK, body, "application/json")
            if path.startswith("/packs/"):
                target = safe_join(packs_root, path.removeprefix("/packs/"))
            elif path.startswith("/furniture/") and furniture_root is not None:
                # Same layout as the published root: /furniture/v<N>/{catalog.json,<id>.glb}
                target = safe_join(furniture_root, path.removeprefix("/furniture/"))
            else:
                target = safe_join(HERE, path.lstrip("/") or "index.html")
                if target is not None and target.suffix == ".py":
                    target = None
            if target is None or not target.is_file():
                return self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")
            guessed = mimetypes.guess_type(target.name)[0]
            ctype = TYPES.get(target.suffix) or guessed or "application/octet-stream"
            self._send(HTTPStatus.OK, target.read_bytes(), ctype)

        def _send(self, status: HTTPStatus, body: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")  # rebuilt packs show up on reload
            self.end_headers()
            self.wfile.write(body)

    return Handler


def main() -> None:
    ap = argparse.ArgumentParser(description="Lane A pack QA viewer (testing only, not the game)")
    ap.add_argument("--packs", type=Path, default=DEFAULT_PACKS, help="folder holding <block_id>/v<N>/")
    ap.add_argument(
        "--furniture",
        type=Path,
        default=DEFAULT_FURNITURE,
        help="shared furniture library folder holding v<N>/{catalog.json,*.glb}, served at /furniture/",
    )
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    root = args.packs.expanduser().resolve()
    if not root.is_dir():
        raise SystemExit(f"packs folder not found: {root} (pass --packs <worldgen/out/packs>)")
    furniture = args.furniture.expanduser().resolve()
    if not furniture_versions(furniture):
        print(f"no furniture/v<N>/ library at {furniture}: placeholder boxes only (pass --furniture)")
        furniture = None
    found = list_packs(root)
    print(f"Lane A pack QA viewer (NOT the game) on http://127.0.0.1:{args.port}")
    print(f"packs root: {root}  ({len(found)} pack(s): {', '.join(p['pack'] for p in found) or 'none'})")
    if furniture:
        print(f"furniture library: {furniture}")
    ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(root, furniture)).serve_forever()


if __name__ == "__main__":
    main()
