import subprocess
import sys

from fastapi.testclient import TestClient

import settings
from api.main import app
from data import packs


def test_health():
    assert TestClient(app).get("/health").json()["ok"] is True


def test_create_session():
    r = TestClient(app).post("/sessions", json={"player_id": "p1", "block_id": "hou_heights_01"})
    assert r.status_code == 200
    assert r.json()["session_id"]


def test_unknown_session_404():
    assert TestClient(app).post("/sessions/nope/messages", json={"text": "hi"}).status_code == 404


def test_contracts_validate():
    script = settings.CONTRACTS_DIR / "validate.py"
    result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_fixture_packs(monkeypatch):
    monkeypatch.setattr(settings, "USE_FIXTURES", True)
    packs.listings.cache_clear()
    listing = packs.listings()[0]
    assert packs.get_listing(listing["id"]) == listing
    assert packs.get_rooms(listing["id"])[0]["id"].startswith(listing["id"])
    assert packs.get_house_graph(listing["id"])["nodes"]
    assert packs.get_audit(listing["id"])["listing_id"] == listing["id"]
