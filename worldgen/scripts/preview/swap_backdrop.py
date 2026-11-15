"""Make a pack version from an existing build of a house with only its backdrop replaced: minutes instead of
a full build, for trying backdrops (docs/house-pipeline-handoff.md 3.13).

Run from worldgen/:
    uv run python -m worldgen.exterior --block <block> --listing=<id> --parts backdrop --backdrop sweeps \
        --dest out/cache/glb/<you>/backdrop.glb
    uv run python scripts/preview/swap_backdrop.py <block> <listing id> <base version> out/cache/glb/<you>/backdrop.glb <new version>

Takes the base version's uncompressed build (out/cache/glb/<model>_interior_pano_v<base>.glb), drops every
`backdrop*` node, adds the nodes of the backdrop glb (their "sweep" extras are kept), compresses it into
packs/<block>/v<new>/houses/<id>/interior.glb and copies collision.glb, rooms.json, graph.json and
audit.json from the base version. Far trees of the base build stay (they are part of `plants_N`)."""

import json
import shutil
import sys

import trimesh

from worldgen import build_house as bh
from worldgen import house_mesh as hm
from worldgen import versions
from worldgen.pack import load_block, pack_dir, update_manifest


def main() -> None:
    block_id, listing, base, backdrop, new = sys.argv[1:6]
    base, new = int(base), int(new)
    versions.refuse_live(block_id, new, "write a house")
    out = bh.HERE / "out"
    block = load_block(block_id)
    model_id = next(h for h in block["houses"] if h["listing_id"] == listing)["matterport_model_id"]
    scene = trimesh.load(out / "cache" / "glb" / f"{model_id}_interior_pano_v{base}.glb")
    scene.delete_geometry([n for n in scene.geometry if n.startswith("backdrop")])
    for name, g in trimesh.load(backdrop).geometry.items():
        meta = {"sweep": g.metadata["sweep"]} if "sweep" in g.metadata else None
        scene.add_geometry(g, node_name=name, geom_name=name, metadata=meta)
    raw = out / "cache" / "glb" / f"{model_id}_interior_pano_v{new}.glb"
    scene.export(raw)
    src = pack_dir(out, block_id, base) / "houses" / listing
    dst = pack_dir(out, block_id, new) / "houses" / listing
    dst.mkdir(parents=True, exist_ok=True)
    hm.compress_glb(raw, dst / "interior.glb")
    for f in ("collision.glb", "rooms.json", "graph.json", "audit.json"):
        if (src / f).exists():
            shutil.copy2(src / f, dst / f)
    update_manifest(out, block_id, new, block["center"], listing)
    print(
        json.dumps({"house": str(dst), "interior_mb": round((dst / "interior.glb").stat().st_size / 1e6, 2)})
    )


if __name__ == "__main__":
    main()
