"""Bake only some rooms of a house into an uncompressed glb: the fast loop for looking at a pipeline change.

Run from worldgen/:
    uv run python scripts/preview/bake_rooms.py <block> <listing id> <out.glb> r4 r10 [--no-boxes]

A whole house takes 5 to 6 minutes; a kitchen and a bathroom take about one. The whole house still decides
what each camera sees (boxes and stand-in solids of every room are fitted), so the baked rooms look the same
as in a full build. Writes no pack. View the result with room_views.py / freeview.py, e.g.
    uv run python scripts/preview/room_views.py <out.glb> out/packs/<block>/v<N>/houses/<listing>/rooms.json <dir> r4
Needs the house to have been built once (cached model JSON, MatterPak and skyboxes under out/)."""
import json
import sys
from pathlib import Path

from worldgen import build_house as bh
from worldgen import rebake as rb
from worldgen.pack import load_block
from worldgen.panos import PanoSet


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    block_id, listing, dest, *wanted = args
    out = bh.HERE / "out"
    house = next(h for h in load_block(block_id)["houses"] if h["listing_id"] == listing)
    model_id = house["matterport_model_id"]
    model = json.loads((out / "cache" / "matterport" / f"{model_id}.json").read_text(encoding="utf-8"))
    ov_path = bh.OVERRIDES / f"{model_id}.json"
    overrides = json.loads(ov_path.read_text(encoding="utf-8")) if ov_path.exists() else {}
    parts = bh.apply_splits(bh.read_rooms(model), overrides)
    bh.infer_primary(parts)
    kept = [p for p in parts if p.keep]
    for i, p in enumerate(kept, 1):
        p.id = f"{listing}.r{i}"
    raw = out / "raw" / model_id
    scan = rb.load_scan(bh.extract_matterpak(model_id, raw))
    floor_z = bh.scan_floor_z(scan, parts)
    for p in parts:
        p.floor_z = floor_z.get(p.floor, 0.0)
    only = {i for i, p in enumerate(kept) if p.id.split(".")[-1] in wanted or p.id in wanted}
    if not only:
        raise SystemExit(f"no such rooms: {wanted}; have {[p.id.split('.')[-1] + ':' + p.label for p in kept]}")
    report, _ = rb.write_rebaked_interior_glb(
        scan,
        bh.shell_rooms(parts),
        PanoSet(model, raw / "skybox_2k"),
        Path(dest),
        fit_boxes="--no-boxes" not in sys.argv,
        drop_boxes=[tuple(d["at"]) for d in overrides.get("drop_boxes") or []],
        only=only,
    )
    print(json.dumps(report))


if __name__ == "__main__":
    main()
