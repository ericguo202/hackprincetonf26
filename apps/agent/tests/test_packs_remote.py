import json

import httpx
import pytest

import settings
from data import packs

CATALOG = [{"id": "sofa_abo_B075X4QMX3", "category": "sofa", "file": "furniture/v2/sofa_abo_B075X4QMX3.glb"}]


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "USE_FIXTURES", False)
    monkeypatch.setattr(settings, "PACK_DIR", tmp_path)
    monkeypatch.setattr(settings, "PACK_BASE_URL", "")
    monkeypatch.setattr(settings, "PACK_VERSION", None)
    monkeypatch.setattr(settings, "FURNITURE_VERSION", None)
    monkeypatch.setattr(settings, "BLOCK_ID", "hou_navidad_01")
    packs.clear_cache()
    yield
    packs.clear_cache()


def _fake_get(calls, status=200, body=CATALOG):
    def get(url, **_):
        calls.append(url)
        return httpx.Response(status, json=body, request=httpx.Request("GET", url))

    return get


def test_local_pack_wins_and_picks_newest_version(tmp_path, monkeypatch):
    for v, items in ((1, []), (2, CATALOG)):
        f = tmp_path / f"furniture/v{v}/catalog.json"
        f.parent.mkdir(parents=True)
        f.write_text(json.dumps(items), encoding="utf-8")
    calls = []
    monkeypatch.setattr(settings, "PACK_BASE_URL", "https://packs.example")
    monkeypatch.setattr(httpx, "get", _fake_get(calls))
    assert packs.catalog() == CATALOG
    assert calls == []


def test_falls_back_to_pack_base_url_once(monkeypatch):
    calls = []
    monkeypatch.setattr(settings, "PACK_BASE_URL", "https://packs.example")
    monkeypatch.setattr(settings, "PACK_VERSION", 7)  # pack and furniture versions are independent
    monkeypatch.setattr(settings, "FURNITURE_VERSION", 3)
    monkeypatch.setattr(httpx, "get", _fake_get(calls))
    assert packs.get_asset("sofa_abo_B075X4QMX3")["category"] == "sofa"
    assert packs.catalog() == CATALOG
    assert calls == ["https://packs.example/furniture/v3/catalog.json"]


def test_missing_everywhere_is_a_clear_error(monkeypatch):
    with pytest.raises(packs.PackFileMissing, match="PACK_BASE_URL is not set"):
        packs.catalog()
    monkeypatch.setattr(settings, "PACK_BASE_URL", "https://packs.example")
    monkeypatch.setattr(httpx, "get", _fake_get([], status=403))
    with pytest.raises(packs.PackFileMissing, match="403"):
        packs.catalog()
