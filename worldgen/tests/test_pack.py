import json

from worldgen import pack

CENTER = {"lat": 29.70113, "lon": -95.670841}


def test_new_manifest_points_at_the_shared_furniture_library(tmp_path):
    path = pack.update_manifest(tmp_path, "hou_navidad_01", 1, CENTER, "hou_236595179")
    manifest = json.loads(path.read_text())
    assert manifest["furniture_catalog"] == "furniture/v1/catalog.json"
    assert [h["listing_id"] for h in manifest["houses"]] == ["hou_236595179"]


def test_pre_v2_3_manifest_is_migrated_and_keeps_its_houses(tmp_path):
    path = pack.update_manifest(tmp_path, "hou_navidad_01", 1, CENTER, "hou_237901269")
    old = json.loads(path.read_text())
    old["furniture_catalog"] = "furniture/catalog.json"  # what manifests said before contracts v2.3.0
    path.write_text(json.dumps(old))
    pack.update_manifest(tmp_path, "hou_navidad_01", 1, CENTER, "hou_236595179")
    manifest = json.loads(path.read_text())
    assert manifest["furniture_catalog"] == "furniture/v1/catalog.json"
    assert [h["listing_id"] for h in manifest["houses"]] == ["hou_236595179", "hou_237901269"]
