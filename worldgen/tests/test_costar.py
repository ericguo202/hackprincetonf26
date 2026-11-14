import os
from pathlib import Path

import pytest

from worldgen.costar import lot_area_m2, matterport_model_id, property_type
from worldgen.sources.costar import iter_residential_listings, matterport_csv

KIT = Path(os.environ.get("COSTAR_KIT_DIR", Path(__file__).resolve().parents[2] / "costar-hackathon-kit"))


@pytest.mark.parametrize(
    "code, expected",
    [(1, "single_family"), (2, "townhouse"), (4, "condo"), (512, "duplex"), (16, "multi_family"),
     (64, "other"), (0, None), (32, None), (128, None), (None, None)],
)
def test_property_type(code, expected):
    assert property_type({"property": {"propertyType": code}}) == expected


def test_lot_area_m2():
    assert lot_area_m2({"property": {"lotSizeAcresMin": 0.15}}) == 607.0
    assert lot_area_m2({"property": {"lotSizeAcresMin": None}}) is None


def test_matterport_prefers_tour_media():
    csv_models = {7: ["floorplan", "tour"]}
    rec = {"id": 7, "media": [{"type": 10, "videoKey": "floorplan"}, {"type": 43, "videoKey": "tour"}]}
    assert matterport_model_id(rec, csv_models) == "tour"
    assert matterport_model_id({"id": 7, "media": []}, csv_models) == "floorplan"
    assert matterport_model_id({"id": 8}, csv_models) is None


@pytest.mark.skipif(not (KIT / "residential-listings.csv").exists(), reason="CoStar kit not present")
def test_kit_join():
    csv_models = matterport_csv(KIT)
    assert len(csv_models) == 2120
    recs = list(iter_residential_listings(KIT))
    joined = [matterport_model_id(r, csv_models) for r in recs]
    assert sum(m is not None for m in joined) == 2119  # listing 231305778 has no model
