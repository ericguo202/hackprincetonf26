"""Step 6c: normalize the selected ABO .glb files and write the furniture library (AGENTS.md 7.6, 9).

    uv run python -m worldgen.normalize [--version 1] [--force] [--jobs 6]
    uv run python -m worldgen.normalize --preview   # orientation contact sheets

Input:  out/abo_selection.json (from `worldgen.sources.abo select`) and out/raw/abo/<3dmodel_id>.glb.
Output: out/furniture/v<N>/<id>.glb and out/furniture/v<N>/catalog.json. The library is shared by every
block (contracts v2.3.0): a pack's manifest points at it with furniture_catalog, and asset `file` paths
are relative to the published root (furniture/v<N>/<id>.glb). Publish a new v<N> instead of changing one.

Per asset:
  1. Units. ABO meshes are already meters. The mesh is only rescaled when it is clearly in another unit
     (about 100x, 1000x or 39.37x off the listing's item_dimensions); otherwise a >15% mismatch with the
     listing text is recorded as a scale warning and the mesh is left alone (listing text is often wrong).
  2. Yaw fix per category from abo_orientation.yaml so the asset front faces +Z.
  3. Origin to bottom center (min Y = 0, X/Z centered).
  Steps 1-3 are one matrix on a new root node (pygltflib), so materials and textures are untouched.
  4. gltf-transform optimize: flatten (bakes the matrix), meshopt, textures to 1024 px WebP.
     If a file is still over 3 MB it is redone with 512 px textures.
  5. size_m and cover measured from the transformed vertices, never from listing text.

Resumable: an asset whose .glb and cache record (out/normalize_cache/<id>.json) both exist is skipped
unless --force. The cache never ships; catalog.json is rebuilt from it on every run.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import math
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

import numpy as np
import trimesh
import yaml
from pygltflib import GLTF2, Node

HERE = Path(__file__).resolve().parent  # worldgen/
OUT = HERE / "out"
SELECTION = OUT / "abo_selection.json"
RAW = OUT / "raw" / "abo"
CACHE = OUT / "normalize_cache"
ORIENTATION = HERE / "abo_orientation.yaml"
CLI = HERE / "node_modules" / "@gltf-transform" / "cli" / "bin" / "cli.js"

TARGET_BYTES = 3 * 1024 * 1024
HARD_BYTES = 5 * 1024 * 1024
SCALE_TOL = 0.15
UNIT_FACTORS = (100.0, 1000.0, 1 / 0.0254)  # cm, mm, inches -> m

# Short generic in-world names. Brand and product name stay in `source`.
NAMES = {
    "sofa": "Sofa",
    "sectional": "Sectional sofa",
    "loveseat": "Loveseat",
    "armchair": "Armchair",
    "coffee_table": "Coffee table",
    "side_table": "Side table",
    "tv_stand": "TV stand",
    "rug": "Rug",
    "bookshelf": "Bookshelf",
    "floor_lamp": "Floor lamp",
    "table_lamp": "Table lamp",
    "ottoman": "Ottoman",
    "plant": "Potted plant",
    "bed": "Bed",
    "nightstand": "Nightstand",
    "dresser": "Dresser",
    "wardrobe": "Wardrobe",
    "desk": "Desk",
    "office_chair": "Office chair",
    "dining_table": "Dining table",
    "dining_chair": "Dining chair",
    "bar_stool": "Bar stool",
    "kitchen_island": "Kitchen island",
    "bench": "Bench",
    "rocking_chair": "Rocking chair",
    "toy_storage": "Toy storage",
    "kids_table": "Kids table",
    "kids_chair": "Kids chair",
    "play_mat": "Play mat",
    "beanbag": "Beanbag",
    "play_equipment": "Play equipment",
    "canopy_tent": "Play tent",
}


# ---------------------------------------------------------------- geometry


def load_vertices(path: Path) -> np.ndarray:
    """All vertices in scene (world) coordinates, node transforms applied."""
    scene = trimesh.load(path, force="scene", process=False)
    parts = []
    for node in scene.graph.nodes_geometry:
        transform, geom_name = scene.graph[node]
        geom = scene.geometry[geom_name]
        if not hasattr(geom, "vertices") or len(geom.vertices) == 0:
            continue
        parts.append(trimesh.transformations.transform_points(geom.vertices, transform))
    if not parts:
        raise ValueError("no mesh vertices")
    return np.vstack(parts)


def rot_y(deg: float) -> np.ndarray:
    """4x4 rotation about +Y, counterclockwise seen from above (AGENTS.md 9)."""
    r = math.radians(deg)
    c, s = math.cos(r), math.sin(r)
    m = np.eye(4)
    m[0, 0], m[0, 2], m[2, 0], m[2, 2] = c, s, -s, c
    return m


def unit_scale(ext: np.ndarray, dims: dict | None) -> float:
    """1.0 unless the mesh is clearly in cm/mm/inches compared with the listing's dimensions."""
    if not dims or not dims.get("h"):
        return 1.0 if ext.max() < 20 else 0.01  # nothing household is 20 m; assume cm
    listed = max(dims.get("h") or 0, dims.get("l") or 0, dims.get("w") or 0)
    ratio = ext.max() / listed if listed else 1.0
    for f in UNIT_FACTORS:
        if abs(ratio / f - 1) < 0.25:
            return 1 / f
    return 1.0


def scale_warning(size: dict, dims: dict | None) -> str | None:
    """Compare measured size with listing text: height, and the sorted horizontal pair."""
    if not dims:
        return None
    problems = []
    if dims.get("h") and dims["h"] >= 0.05:  # skip rugs and mats: mm-level thickness is noise
        r = size["h"] / dims["h"]
        if abs(r - 1) > SCALE_TOL:
            problems.append(f"h {size['h']:.2f} m vs listed {dims['h']:.2f} m")
    horiz_listed = sorted(v for v in (dims.get("l"), dims.get("w")) if v)
    horiz_mesh = sorted((size["w"], size["d"]))
    if len(horiz_listed) == 2:
        for name, m, lst in zip(("short side", "long side"), horiz_mesh, horiz_listed, strict=True):
            if abs(m / lst - 1) > SCALE_TOL:
                problems.append(f"{name} {m:.2f} m vs listed {lst:.2f} m")
    return "; ".join(problems) or None


def back_side(v: np.ndarray) -> float:
    """Heuristic for seating and beds: mean Z of the top 15% of vertices (backrest/headboard),
    relative to the Z center, as a fraction of depth. Negative = tall part at -Z = front faces +Z."""
    y = v[:, 1]
    top = v[y >= np.quantile(y, 0.85)]
    zmin, zmax = v[:, 2].min(), v[:, 2].max()
    depth = max(zmax - zmin, 1e-6)
    return float((top[:, 2].mean() - (zmin + zmax) / 2) / depth)


# ---------------------------------------------------------------- one asset


def _cli(*args: str) -> None:
    node = shutil.which("node")
    if not node or not CLI.exists():
        raise RuntimeError("gltf-transform CLI missing: run `npm i` in worldgen/")
    r = subprocess.run([node, str(CLI), *args], capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        raise RuntimeError(f"gltf-transform {args[0]} failed: {(r.stderr or r.stdout)[-800:]}")


def wrap_root(src: Path, dst: Path, matrix: np.ndarray) -> None:
    """Put every scene root under one new node carrying `matrix`. Materials and images are untouched."""
    g = GLTF2().load(str(src))
    si = g.scene or 0
    roots = list(g.scenes[si].nodes or [])
    g.nodes.append(Node(name="normalize", matrix=[float(x) for x in matrix.T.flatten()], children=roots))
    g.scenes[si].nodes = [len(g.nodes) - 1]
    g.save_binary(str(dst))


def texture_report(path: Path) -> dict:
    g = GLTF2().load(str(path))
    with_base = sum(
        1
        for m in g.materials
        if m.pbrMetallicRoughness and m.pbrMetallicRoughness.baseColorTexture is not None
    )
    return {
        "images": len(g.images),
        "materials": len(g.materials),
        "base_color_textured": with_base,
        "mime": sorted({i.mimeType or "?" for i in g.images}),
        "extensions": sorted(g.extensionsUsed or []),
    }


def transform_for(entry: dict, yaw: float) -> tuple[np.ndarray, dict, float, np.ndarray]:
    raw = RAW / f"{entry['3dmodel_id']}.glb"
    v = load_vertices(raw)
    scale = unit_scale(v.max(0) - v.min(0), entry.get("item_dimensions_m"))
    m = rot_y(yaw) @ np.diag([scale, scale, scale, 1.0])
    vt = trimesh.transformations.transform_points(v, m)
    lo, hi = vt.min(0), vt.max(0)
    t = np.eye(4)
    t[:3, 3] = [-(lo[0] + hi[0]) / 2, -lo[1], -(lo[2] + hi[2]) / 2]
    m = t @ m
    vt = trimesh.transformations.transform_points(v, m)
    ext = vt.max(0) - vt.min(0)
    size = {"w": round(float(ext[0]), 3), "d": round(float(ext[2]), 3), "h": round(float(ext[1]), 3)}
    return m, size, scale, vt


def process(entry: dict, out_dir: Path, yaw: float, force: bool) -> dict:
    aid = entry["id"]
    out = out_dir / f"{aid}.glb"
    cache = CACHE / f"{aid}.json"
    if not force and out.exists() and cache.exists():
        rec = json.loads(cache.read_text(encoding="utf-8"))
        if rec.get("yaw_deg") == yaw:
            rec["skipped"] = True
            rec["scale_warning"] = scale_warning(rec["size_m"], entry.get("item_dimensions_m"))
            return rec
    raw = RAW / f"{entry['3dmodel_id']}.glb"
    if not raw.exists():
        raise FileNotFoundError(raw)

    m, size, scale, vt = transform_for(entry, yaw)
    with tempfile.TemporaryDirectory(prefix="normalize_") as tmp:
        mid = Path(tmp) / "wrapped.glb"
        wrap_root(raw, mid, m)
        tex = 1024
        tmp_out = Path(tmp) / "out.glb"
        _cli(
            "optimize",
            str(mid),
            str(tmp_out),
            "--compress",
            "meshopt",
            "--texture-compress",
            "webp",
            "--texture-size",
            str(tex),
        )
        if tmp_out.stat().st_size > TARGET_BYTES:
            tex = 512
            _cli(
                "optimize",
                str(mid),
                str(tmp_out),
                "--compress",
                "meshopt",
                "--texture-compress",
                "webp",
                "--texture-size",
                str(tex),
            )
        out_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(tmp_out), out)

    rec = {
        "id": aid,
        "yaw_deg": yaw,
        "unit_scale": scale,
        "size_m": size,
        "bytes": out.stat().st_size,
        "raw_bytes": raw.stat().st_size,
        "texture_px": tex,
        "textures": texture_report(out),
        "scale_warning": scale_warning(size, entry.get("item_dimensions_m")),
        "back_side": round(back_side(vt), 3),
    }
    CACHE.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(rec, indent=1), encoding="utf-8")
    return rec


# ---------------------------------------------------------------- catalog


def cover_for(h: float) -> str:
    return "full" if h > 1.2 else "half" if h >= 0.5 else "none"


def catalog_entry(entry: dict, rec: dict, version: int) -> dict:
    """Asset contract, AGENTS.md section 9. style/color/material straight from ABO, else null."""
    src = entry.get("source") or {}
    return {
        "id": entry["id"],
        "name": NAMES.get(entry["category"], entry["category"].replace("_", " ").capitalize()),
        "category": entry["category"],
        "style": entry.get("style"),
        "color": entry.get("color"),
        "material": entry.get("material"),
        "tags": list(entry.get("tags") or []),
        "file": f"furniture/v{version}/{entry['id']}.glb",
        "size_m": rec["size_m"],
        "placement": entry["placement"],
        "clearance_m": entry.get("clearance_m") or {},
        "cover": cover_for(rec["size_m"]["h"]),
        "source": {
            "dataset": "ABO",
            "item_id": src.get("item_id") or entry["item_id"],
            "product_name": entry.get("product_name"),
            "brand": entry.get("brand"),
            "license": "CC BY 4.0",
            "credit": "Amazon Berkeley Objects, Amazon.com",
        },
    }


# ---------------------------------------------------------------- preview (orientation by eye)


def _png(path: Path, rgb: np.ndarray) -> None:
    h, w, _ = rgb.shape
    raw = b"".join(b"\x00" + rgb[y].astype(np.uint8).tobytes() for y in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw, 6))
        + chunk(b"IEND", b"")
    )


def _panel(pts: np.ndarray, depth: np.ndarray, px: int = 160) -> np.ndarray:
    """Splat 2D points (u right, v up) into a px*px image shaded by depth (near = dark)."""
    img = np.full((px, px, 3), 255, np.uint8)
    lo, hi = pts.min(0), pts.max(0)
    span = max(float((hi - lo).max()), 1e-6)
    uv = ((pts - lo) / span * (px - 9) + 4).astype(int)
    d = (depth - depth.min()) / max(float(np.ptp(depth)), 1e-6)
    order = np.argsort(-d)  # far first, near drawn last
    shade = (40 + 170 * d[order]).astype(np.uint8)
    img[px - 1 - uv[order, 1], uv[order, 0]] = shade[:, None]
    img[:, -3:] = (220, 30, 30)  # red bar = +Z side (side view) / +Z side (top view, bottom edge below)
    return img


def preview(selection: list[dict], orient: dict, dest: Path) -> None:
    """Per category: one row per item, [side view: +Z to the right] [top view: +X right, +Z down].
    Red bar marks the +Z side of the side view; in the top view +Z is the bottom edge (red)."""
    dest.mkdir(parents=True, exist_ok=True)
    by_cat: dict[str, list[dict]] = {}
    for e in selection:
        by_cat.setdefault(e["category"], []).append(e)
    for cat, items in by_cat.items():
        rows = []
        for e in items:
            _, _, _, vt = transform_for(e, float(orient.get(cat, 0)))
            mesh_pts = vt
            scene = trimesh.load(RAW / f"{e['3dmodel_id']}.glb", force="scene", process=False)
            try:
                mesh = scene.to_geometry()
                m, _, _, _ = transform_for(e, float(orient.get(cat, 0)))
                mesh.apply_transform(m)
                mesh_pts = trimesh.sample.sample_surface(mesh, 60000)[0]
            except Exception:  # noqa: BLE001 - preview only; fall back to vertices
                pass
            side = _panel(mesh_pts[:, [2, 1]], mesh_pts[:, 0])
            top = _panel(np.c_[mesh_pts[:, 0], -mesh_pts[:, 2]], -mesh_pts[:, 1])
            top[:, -3:] = 255
            top[-3:, :] = (220, 30, 30)
            rows.append(np.concatenate([side, np.full((160, 6, 3), 255, np.uint8), top], axis=1))
        sheet = np.concatenate(
            [np.concatenate([r, np.full((6, r.shape[1], 3), 200, np.uint8)]) for r in rows]
        )
        _png(dest / f"{cat}.png", sheet)
        print(f"preview {dest / (cat + '.png')}")


# ---------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--version", type=int, default=1, help="furniture library version (out/furniture/v<N>)")
    p.add_argument("--selection", type=Path, default=SELECTION)
    p.add_argument("--force", action="store_true", help="redo assets that already have output")
    p.add_argument("--jobs", type=int, default=6)
    p.add_argument("--only", nargs="*", help="asset ids or categories to process")
    p.add_argument("--preview", action="store_true", help="write orientation contact sheets and exit")
    a = p.parse_args(argv)

    selection = json.loads(a.selection.read_text(encoding="utf-8"))
    orient = (yaml.safe_load(ORIENTATION.read_text(encoding="utf-8")) or {}) if ORIENTATION.exists() else {}
    if a.only:
        selection = [e for e in selection if e["id"] in a.only or e["category"] in a.only]
    if a.preview:
        preview(selection, orient, OUT / "previews")
        return 0

    fdir = OUT / "furniture" / f"v{a.version}"
    recs: dict[str, dict] = {}
    failed: dict[str, str] = {}
    with cf.ProcessPoolExecutor(max_workers=a.jobs) as pool:
        futs = {
            pool.submit(process, e, fdir, float(orient.get(e["category"], 0)), a.force): e for e in selection
        }
        for i, f in enumerate(cf.as_completed(futs), 1):
            e = futs[f]
            try:
                rec = f.result()
                recs[e["id"]] = rec
                tag = "skip" if rec.get("skipped") else f"{rec['bytes'] / 1e6:.2f} MB"
                print(f"[{i}/{len(futs)}] {e['id']}: {tag}", flush=True)
            except Exception as ex:  # noqa: BLE001 - report and keep going
                failed[e["id"]] = f"{type(ex).__name__}: {ex}"
                print(f"[{i}/{len(futs)}] {e['id']}: FAILED {failed[e['id']]}", file=sys.stderr, flush=True)

    # catalog over every asset that has an output (keeps assets from earlier partial runs)
    full = json.loads(a.selection.read_text(encoding="utf-8"))
    catalog = []
    for e in full:
        cache = CACHE / f"{e['id']}.json"
        if (fdir / f"{e['id']}.glb").exists() and cache.exists():
            catalog.append(
                catalog_entry(
                    e, recs.get(e["id"]) or json.loads(cache.read_text(encoding="utf-8")), a.version
                )
            )
    fdir.mkdir(parents=True, exist_ok=True)
    (fdir / "catalog.json").write_text(
        json.dumps(catalog, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    done = [r for r in recs.values()]
    sizes = sorted(r["bytes"] for r in done)
    print(f"\n{len(done)} ok, {len(failed)} failed; catalog.json has {len(catalog)} assets -> {fdir}")
    if sizes:
        print(
            f"total {sum(sizes) / 1e6:.1f} MB, median {sizes[len(sizes) // 2] / 1e6:.2f} MB, "
            f"max {sizes[-1] / 1e6:.2f} MB"
        )
    for r in done:
        if r["bytes"] > TARGET_BYTES:
            print(f"OVER {'5' if r['bytes'] > HARD_BYTES else '3'} MB: {r['id']} {r['bytes'] / 1e6:.2f} MB")
        if r["textures"]["images"] == 0:  # optimize turns single-color textures into material factors
            print(f"no textures left (solid color, kept as material factor): {r['id']}")
        if r["unit_scale"] != 1.0:
            print(f"RESCALED x{r['unit_scale']}: {r['id']}")
        if r.get("scale_warning"):
            print(f"scale_warning {r['id']}: {r['scale_warning']}")
    for k, v in failed.items():
        print(f"FAILED {k}: {v}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
