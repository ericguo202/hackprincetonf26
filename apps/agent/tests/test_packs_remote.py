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


def _pin(tmp_path, monkeypatch, live, furniture):
    """Block config pinning `live`; its manifest names furniture/v<furniture>. Plus a newer decoy."""
    blocks = tmp_path / "blocks"
    blocks.mkdir()
    (blocks / "hou_navidad_01.json").write_text(json.dumps({"live_pack_version": live}), encoding="utf-8")
    monkeypatch.setattr(settings, "BLOCKS_DIR", blocks)
    for v in (live, live + 50):  # a newer test build must never be picked
        m = tmp_path / f"packs/hou_navidad_01/v{v}/manifest.json"
        m.parent.mkdir(parents=True)
        m.write_text(
            json.dumps({"furniture_catalog": f"furniture/v{furniture if v == live else 9}/catalog.json"})
        )


def test_local_pack_wins_and_uses_the_pinned_live_version(tmp_path, monkeypatch):
    for v, items in ((1, []), (2, CATALOG), (9, [])):
        f = tmp_path / f"furniture/v{v}/catalog.json"
        f.parent.mkdir(parents=True)
        f.write_text(json.dumps(items), encoding="utf-8")
    _pin(tmp_path, monkeypatch, live=1, furniture=2)
    assert packs.pack_version("hou_navidad_01") == 1
    assert packs.furniture_version() == 2
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


def test_no_pin_and_no_env_is_a_clear_error(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "BLOCKS_DIR", tmp_path / "nowhere")
    (tmp_path / "packs/hou_navidad_01/v7").mkdir(parents=True)  # a folder alone is never enough
    with pytest.raises(packs.PackVersionUnknown, match="PACK_VERSION"):
        packs.pack_version("hou_navidad_01")
