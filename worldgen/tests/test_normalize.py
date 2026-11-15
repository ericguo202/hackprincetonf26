import json
import re
from pathlib import Path

import numpy as np
import pytest
import trimesh
from jsonschema import Draft202012Validator

from worldgen import normalize
from worldgen.sources.furniture import EXTRA, load_extra
from worldgen.sources.furniture import sha256 as file_sha256

ASSET_SCHEMA = json.loads(
    (Path(__file__).resolve().parents[2] / "contracts" / "asset.schema.json").read_text(encoding="utf-8")
)


def _box_glb(path: Path, extents=(2.0, 1.0, 0.5), offset=(5.0, 3.0, -2.0)) -> Path:
    box = trimesh.creation.box(extents=extents)
    box.apply_translation(offset)
    box.export(path)
    return path


def test_fit_scale_uses_the_axis_after_the_yaw():
    v = np.array([[0, 0, 0], [2.0, 1.0, 0.5]])
    assert normalize.fit_scale(v, 0, {"w": 1.0}) == pytest.approx(0.5)
    assert normalize.fit_scale(v, 90, {"w": 1.0}) == pytest.approx(2.0)  # old Z (0.5 m) is now along X
    assert normalize.fit_scale(v, 0, {"h": 2.0}) == pytest.approx(2.0)
    with pytest.raises(ValueError):
        normalize.fit_scale(v, 0, {"w": 1.0, "h": 1.0})


def test_extra_item_is_scaled_turned_and_set_on_the_floor(tmp_path):
    raw = _box_glb(tmp_path / "box.glb")
    entry = {"id": "tv_kenney_x", "category": "tv", "raw": str(raw), "fit_m": {"w": 1.0}}
    m, size, scale, vt = normalize.transform_for(entry, 180)
    assert scale == pytest.approx(0.5)
    assert size == {"w": 1.0, "d": 0.25, "h": 0.5}
    lo, hi = vt.min(0), vt.max(0)
    assert lo[1] == pytest.approx(0)  # bottom on the floor
    assert (lo[0] + hi[0]) / 2 == pytest.approx(0) and (lo[2] + hi[2]) / 2 == pytest.approx(0)
    # yaw 180 maps a point on the source's -Z face to the +Z face
    p = trimesh.transformations.transform_points([[5.0, 3.0, -2.25]], m)[0]
    assert p[2] == pytest.approx(0.125)


def test_extra_catalog_entry_matches_the_asset_contract():
    entry = {
        "id": "tv_kenney_television55",
        "category": "tv",
        "name": "Flat-screen TV",
        "dataset": "Kenney",
        "item_id": "9trLeWoBek",
        "product_name": "Television",
        "license": "CC0 1.0",
        "credit": "Kenney, kenney.nl",
        "tags": ["living_room", "screen"],
        "placement": "on_surface",
        "clearance_m": {"front": 0.0},
    }
    rec = {"size_m": {"w": 1.24, "d": 0.23, "h": 0.82}}
    a = normalize.catalog_entry(entry, rec, 2)
    assert list(Draft202012Validator(ASSET_SCHEMA).iter_errors(a)) == []
    assert a["file"] == "furniture/v2/tv_kenney_television55.glb"
    assert a["cover"] == "half"
    assert a["style"] is None and a["color"] is None and a["material"] is None
    assert a["source"] == {
        "dataset": "Kenney",
        "item_id": "9trLeWoBek",
        "product_name": "Television",
        "brand": None,
        "license": "CC0 1.0",
        "credit": "Kenney, kenney.nl",
    }


def test_copy_base_keeps_assets_and_rewrites_paths(tmp_path):
    v1 = tmp_path / "furniture" / "v1"
    v1.mkdir(parents=True)
    (v1 / "sofa_abo_X1.glb").write_bytes(b"glb bytes")
    asset = {"id": "sofa_abo_X1", "file": "furniture/v1/sofa_abo_X1.glb", "size_m": {"w": 2, "d": 1, "h": 1}}
    (v1 / "catalog.json").write_text(json.dumps([asset]))
    v2 = tmp_path / "furniture" / "v2"
    out = normalize.copy_base(v1, v2, 2)
    assert out == [{**asset, "file": "furniture/v2/sofa_abo_X1.glb"}]
    assert (v2 / "sofa_abo_X1.glb").read_bytes() == b"glb bytes"
    assert json.loads((v1 / "catalog.json").read_text()) == [asset]  # base untouched
    with pytest.raises(ValueError):
        normalize.copy_base(v1, v1, 1)


def test_extra_furniture_yaml_is_complete(tmp_path):
    items = load_extra(EXTRA, tmp_path)
    assert items, "extra_furniture.yaml has no items"
    pattern = re.compile(ASSET_SCHEMA["properties"]["id"]["pattern"])
    for it in items:
        source = {"FurniMesh": "fm"}.get(it["dataset"], it["dataset"].lower())
        assert pattern.match(it["id"]) and it["id"].startswith(f"{it['category']}_{source}_")
        assert it["raw"] == str(tmp_path / it["file"])
        assert len(it["sha256"]) == 64
        assert it["placement"] in ASSET_SCHEMA["properties"]["placement"]["enum"]
        assert it["dataset"] in ASSET_SCHEMA["properties"]["source"]["properties"]["dataset"]["enum"]
        assert it["license"] in (
            "CC0 1.0",
            "CC BY 4.0",
            "FurniMesh free license: commercial use, no attribution required",
        )
        if it.get("local"):  # committed source file, checked against its pinned hash
            assert file_sha256(Path(EXTRA).parent / it["local"]) == it["sha256"]


def test_packed_source_is_measured_in_meters():
    # FurniMesh cribs arrive meshopt-compressed and quantized; trimesh alone reads them ~100x too big
    crib = Path(EXTRA).parent / "furniture_src" / "furnimesh" / "crib_fm_03.glb"
    assert normalize.is_meshopt(crib)
    v = normalize.load_vertices(crib)
    ext = v.max(0) - v.min(0)
    assert ext == pytest.approx([1.37, 0.893, 0.699], abs=0.01)


def test_wall_mounted_entry_carries_mount_height_and_no_cover():
    entry = {
        "id": "tv_team_wall",
        "category": "tv",
        "name": "Wall-mounted flat-screen TV",
        "dataset": "Team",
        "item_id": "tv_55_wall",
        "product_name": "55 in flat-screen TV, wall-mounted",
        "license": "CC0 1.0",
        "credit": "HackPrinceton team",
        "tags": ["living_room", "tv"],
        "placement": "wall",
        "clearance_m": {"front": 1.0},
        "mount_y_m": 0.75,
    }
    a = normalize.catalog_entry(entry, {"size_m": {"w": 1.236, "d": 0.065, "h": 0.703}}, 3)
    assert list(Draft202012Validator(ASSET_SCHEMA).iter_errors(a)) == []
    assert a["mount_y_m"] == 0.75 and a["cover"] == "none"
    unmounted = normalize.catalog_entry({**entry, "mount_y_m": None}, {"size_m": a["size_m"]}, 3)
    assert "mount_y_m" not in unmounted
