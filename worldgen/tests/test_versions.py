"""The live pack version is pinned in git; nothing picks "the newest folder"; builders never write into it."""

import json

import pytest

from worldgen import publish, versions

BLOCK = "blk"


def setup(tmp_path, live=1, extra=(9, 98)):
    blocks = tmp_path / "blocks"
    blocks.mkdir()
    cfg = {"block_id": BLOCK, "houses": [{"listing_id": "hou_1"}], "live_pack_version": live}
    (blocks / f"{BLOCK}.json").write_text(json.dumps(cfg))
    out = tmp_path / "out"
    for v in (live, *extra):
        h = out / "packs" / BLOCK / f"v{v}" / "houses" / "hou_1"
        h.mkdir(parents=True)
        for name in (*versions.HOUSE_FILES, "audit.json"):
            (h / name).write_text("{}")
        (h.parent.parent / "manifest.json").write_text(
            json.dumps({"furniture_catalog": "furniture/v1/catalog.json"})
        )
    return blocks, out


def test_live_version_is_the_pin_not_the_newest_folder(tmp_path):
    blocks, out = setup(tmp_path)
    assert versions.live_version(BLOCK, blocks) == 1
    assert versions.live_pack_dir(BLOCK, out, blocks).name == "v1"


def test_unpinned_block_is_an_error_not_a_guess(tmp_path):
    blocks, _ = setup(tmp_path)
    (blocks / f"{BLOCK}.json").write_text(json.dumps({"block_id": BLOCK}))
    with pytest.raises(SystemExit, match="no live_pack_version"):
        versions.live_version(BLOCK, blocks)


def test_builders_refuse_the_live_version(tmp_path):
    blocks, out = setup(tmp_path)
    with pytest.raises(SystemExit, match="LIVE"):
        versions.refuse_live(BLOCK, 1, "build a house", blocks)
    versions.refuse_live(BLOCK, 2, "build a house", blocks)  # any other version is fine
    with pytest.raises(SystemExit, match="furniture/v1"):
        versions.refuse_live_furniture(1, out, blocks)
    versions.refuse_live_furniture(2, out, blocks)


def test_publish_only_ships_the_live_version(tmp_path, monkeypatch):
    blocks, out = setup(tmp_path)
    monkeypatch.setattr(versions, "BLOCKS", blocks)
    assert publish.live_pack(out, BLOCK).name == "v1"
    with pytest.raises(SystemExit, match="not the live version"):
        publish.live_pack(out, BLOCK, version=98)
    dest = tmp_path / "public"
    assert publish.publish_local(BLOCK, out, dest) == f"/packs/packs/{BLOCK}/v1"
    assert [p.name for p in (dest / "packs" / BLOCK).iterdir()] == ["v1"]


def test_pin_needs_a_complete_version_and_a_comparison(tmp_path):
    blocks, out = setup(tmp_path)
    with pytest.raises(SystemExit, match="compared"):
        versions.pin(BLOCK, 9, "x", "", out, blocks, skip_validate=True)
    (out / "packs" / BLOCK / "v98" / "houses" / "hou_1" / "audit.json").unlink()
    with pytest.raises(SystemExit, match="audit.json missing"):
        versions.pin(BLOCK, 98, "x", "better on every house", out, blocks, skip_validate=True)
    entry = versions.pin(BLOCK, 9, "sharper walls", "score 14.1 -> 13.2", out, blocks, skip_validate=True)
    assert entry["previous"] == 1 and versions.live_version(BLOCK, blocks) == 9
    cfg = json.loads((blocks / f"{BLOCK}.json").read_text())
    assert cfg["pack_history"][-1]["compared_to_live"] == "score 14.1 -> 13.2"


def test_status_marks_the_live_version_and_problems(tmp_path):
    blocks, out = setup(tmp_path)
    (out / "packs" / BLOCK / "v9" / "manifest.json").unlink()
    text = versions.status(BLOCK, out, blocks)
    assert "live pack version = v1" in text
    assert "LIVE v1" in text and "v9" in text and "manifest.json missing" in text
