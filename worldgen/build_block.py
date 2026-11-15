"""Step 3: the combined block -> packs/<block_id>/v<N>/block/{render,collision}.glb + manifest spawns.

uv run python -m worldgen.build_block --block hou_navidad_01 --version 2          # rebuild block, then pack
uv run python -m worldgen.build_block --block hou_navidad_01 --version 2 --reuse  # pack the last build
uv run python -m worldgen.build_block --block hou_navidad_01 --version 3 --block-from 2 --furniture 3
    # furniture-only version: block/ and spawns copied unchanged from v2 (no combined build needed)

The block itself is built by worldgen.block_combined (procedural near zone with ortho-fitted roads, curbs,
sidewalks, driveways and walks; Vexcel 3D Cities far zone; see worldgen/out/block_compare/combined/notes.md).
This step turns that build into a pack version:
  - houses/ are copied unchanged from --houses-from (default: the live version), so a block change and a house
    change are never mixed in one version (docs/house-pipeline-handoff.md section 10);
  - block/render.glb and block/collision.glb come from the combined build;
  - manifest.json: block paths, houses with the hero first, sidewalk spawns in front of the hero house,
    furniture_catalog = furniture/v<--furniture>/catalog.json.
Never writes into the live version (worldgen/versions.py). Pin the result with `worldgen.versions pin`."""

from __future__ import annotations

import argparse
import json
import math
import shutil
from pathlib import Path

from worldgen import versions

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
COMBINED = OUT / "block_compare" / "combined"

SPAWN_COUNT = 8
SPAWN_SEPARATION_M = 3.0
SPAWN_RADIUS_M = 60.0  # sidewalk points within this distance of the hero's front door
SPAWN_INSET_M = 0.35  # keep the player's feet inside the sidewalk band


def door_in_block(listing: dict) -> tuple[float, float]:
    """Listing.world.door (house-local) in the block frame: position + rotY(heading_deg) * local."""
    w = listing["world"]
    h = math.radians(w["heading_deg"])
    dx, _, dz = w["door"]
    return (
        w["position"][0] + dx * math.cos(h) + dz * math.sin(h),
        w["position"][2] - dx * math.sin(h) + dz * math.cos(h),
    )


def ground_sampler(collision_glb: Path):
    """(x, z) -> terrain y from the block collision mesh, or None off the mesh. Uses the LOWEST hit: the
    collision mesh is terrain plus house prisms, fences and trunks, all standing on the terrain, so at a front
    door (on a house wall) the highest hit would be the roof."""
    import numpy as np
    import trimesh

    scene = trimesh.load(collision_glb)
    mesh = scene.to_geometry() if hasattr(scene, "to_geometry") else scene

    def ground(x: float, z: float) -> float | None:
        locs, _, _ = mesh.ray.intersects_location(np.array([[x, 20.0, z]]), np.array([[0.0, -1.0, 0.0]]))
        return float(min(locs[:, 1])) if len(locs) else None

    return ground


def sidewalk_spawns(hardscape: dict, ground, door_xz: tuple[float, float]) -> list[list[float]]:
    """Up to SPAWN_COUNT points on the sidewalk nearest the hero's front door, at least SPAWN_SEPARATION_M
    apart, nearest first, on the ground (the client lifts the capsule above the point)."""
    import numpy as np
    from shapely.geometry import Point, shape
    from shapely.prepared import prep

    walk = shape(hardscape["sidewalk"]).buffer(-SPAWN_INSET_M)
    if walk.is_empty:
        raise SystemExit("combined build has no sidewalk to spawn on")
    inside = prep(walk)
    dx, dz = door_xz
    xs = np.arange(dx - SPAWN_RADIUS_M, dx + SPAWN_RADIUS_M, 0.5)
    zs = np.arange(dz - SPAWN_RADIUS_M, dz + SPAWN_RADIUS_M, 0.5)
    cands = [(x, z) for x in xs for z in zs if math.hypot(x - dx, z - dz) <= SPAWN_RADIUS_M]
    cands = [c for c in cands if inside.contains(Point(c))]
    cands.sort(key=lambda c: math.hypot(c[0] - dx, c[1] - dz))
    picked: list[list[float]] = []
    for x, z in cands:
        if any(math.hypot(x - p[0], z - p[2]) < SPAWN_SEPARATION_M for p in picked):
            continue
        y = ground(x, z)
        if y is None:
            continue
        picked.append([round(float(x), 2), round(y, 3), round(float(z), 2)])
        if len(picked) == SPAWN_COUNT:
            break
    if not picked:
        raise SystemExit(f"no sidewalk within {SPAWN_RADIUS_M} m of the hero's front door {door_xz}")
    return picked


def build(
    block_id: str,
    version: int,
    houses_from: int | None = None,
    furniture: int = 2,
    reuse: bool = False,
    force: bool = False,
    out: Path = OUT,
    block_from: int | None = None,
) -> dict:
    versions.refuse_live(block_id, version, "build the block")
    cfg = versions.block_config(block_id)
    src_v = houses_from if houses_from is not None else versions.live_version(block_id)
    src = versions.pack_dir(block_id, src_v, out)
    dst = versions.pack_dir(block_id, version, out)
    if not (src / "manifest.json").is_file():
        raise SystemExit(f"{src} has no manifest.json")
    if dst.exists():
        if not force:
            raise SystemExit(f"{dst} exists; pass --force to replace it (it is not live)")
        shutil.rmtree(dst)
    if not (out / "furniture" / f"v{furniture}" / "catalog.json").is_file():
        raise SystemExit(f"furniture/v{furniture} is not built under {out}")

    if block_from is not None:
        return _repack(block_id, version, src, dst, versions.pack_dir(block_id, block_from, out), furniture)
    if not reuse:
        from worldgen import block_combined

        block_combined.main([])
    for name in ("render.glb", "collision.glb", "hardscape.json"):
        if not (COMBINED / name).is_file():
            raise SystemExit(f"{COMBINED / name} missing; run without --reuse")

    listings = {entry["id"]: entry for entry in json.loads((out / "listings" / "index.json").read_text())}
    hero = next(h["listing_id"] for h in cfg["houses"] if h.get("role") == "hero")
    if not (listings.get(hero) or {}).get("world"):
        raise SystemExit(f"{hero} has no world placement in listings/index.json; run worldgen.costar")

    shutil.copytree(src / "houses", dst / "houses")
    (dst / "block").mkdir(parents=True)
    shutil.copy2(COMBINED / "render.glb", dst / "block" / "render.glb")
    shutil.copy2(COMBINED / "collision.glb", dst / "block" / "collision.glb")

    ground = ground_sampler(dst / "block" / "collision.glb")
    hardscape = json.loads((COMBINED / "hardscape.json").read_text())
    spawns = sidewalk_spawns(hardscape, ground, door_in_block(listings[hero]))

    manifest = json.loads((src / "manifest.json").read_text())
    manifest["version"] = version
    manifest["block"] = {"render": "block/render.glb", "collision": "block/collision.glb"}
    manifest["houses"].sort(key=lambda h: h["listing_id"] != hero)  # hero first (lane C fallback spawn)
    manifest["spawns"] = spawns
    manifest["furniture_catalog"] = f"furniture/v{furniture}/catalog.json"
    (dst / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")

    return {
        "pack": str(dst),
        "houses_from": f"v{src_v}",
        "hero": hero,
        "spawns": spawns,
        "furniture_catalog": manifest["furniture_catalog"],
        "sizes_mb": {f.name: round(f.stat().st_size / 1e6, 2) for f in sorted((dst / "block").glob("*.glb"))},
    }


def _repack(block_id: str, version: int, src: Path, dst: Path, block_src: Path, furniture: int) -> dict:
    """A version that changes only what --houses-from and --furniture change: block/, spawns and the rest of
    the manifest come unchanged from `block_src` (so a furniture bump never touches the block)."""
    manifest = json.loads((block_src / "manifest.json").read_text())
    shutil.copytree(src / "houses", dst / "houses")
    shutil.copytree(block_src / "block", dst / "block")
    manifest["version"] = version
    manifest["houses"] = json.loads((src / "manifest.json").read_text())["houses"]
    manifest["furniture_catalog"] = f"furniture/v{furniture}/catalog.json"
    (dst / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    return {
        "pack": str(dst),
        "houses_from": src.name,
        "block_from": block_src.name,
        "furniture_catalog": manifest["furniture_catalog"],
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--block", required=True, help="block id; worldgen/blocks/<block>.json")
    p.add_argument("--version", type=int, required=True, help="pack version to write; never the live one")
    p.add_argument("--houses-from", type=int, help="pack version whose houses/ are copied (default: live)")
    p.add_argument("--furniture", type=int, default=2, help="furniture library version the manifest names")
    p.add_argument("--reuse", action="store_true", help="pack the last combined build instead of rebuilding")
    p.add_argument("--force", action="store_true", help="replace an existing (non-live) target version")
    p.add_argument(
        "--block-from", type=int, help="copy block/ and spawns from this pack version instead of building"
    )
    a = p.parse_args(argv)
    result = build(a.block, a.version, a.houses_from, a.furniture, a.reuse, a.force, block_from=a.block_from)
    print(json.dumps(result, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
