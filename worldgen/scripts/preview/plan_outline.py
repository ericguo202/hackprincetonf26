"""Top-down plan of a scan with the outdoor outlines drawn on it: the quickest check of exterior.py's lot,
footprint and facades.

Run from worldgen/:
    uv run python scripts/preview/plan_outline.py hou_navidad_01 hou_236595179 <out.jpg> \
        [--below 2.2] [--res 0.04]

Draws the raw MatterPak seen from above, with its own texture (only triangles whose centre is below
`--below` metres, default 2.2, so roofs and tree canopies do not hide the ground), and on top:
- red: the house footprint (exterior.house_footprint: rooms, Void rooms, ground-less notches)
- blue: the facade ring (exterior.facade_ring: outline moved onto the real outer wall faces)
- green: the lot (exterior.ground_grid: where the scan has ground; the yard heightfield and its fence)
- circles: panoramas, green indoors and yellow outdoors, with their labels
- ticks every 5 m along the left and bottom edges, in Matterport plan coordinates (x right, y up)

What it found before: the whole 7014 driveway missing from the lot (green line around it), an unmodelled
closet treated as yard (red notch), facades off the walls (blue far from red). Takes about a minute
(loading the scan and casting the ground rays)."""

import json
import sys
from pathlib import Path

import numpy as np
import shapely
from PIL import Image, ImageDraw

from worldgen import build_house as bh
from worldgen import exterior as ex
from worldgen import rebake as rb
from worldgen.pack import load_block
from worldgen.panos import Raycaster


def main(argv: list[str]) -> int:
    args, opts, it = [], {}, iter(argv)
    for a in it:
        if a in ("--below", "--res"):
            opts[a] = float(next(it))
        else:
            args.append(a)
    if len(args) < 3:
        print(__doc__)
        return 2
    block, listing, out = args[0], args[1], args[2]
    below, res = opts.get("--below", 2.2), opts.get("--res", 0.04)
    house = next(h for h in load_block(block)["houses"] if h["listing_id"] == listing)
    mid = house["matterport_model_id"]
    model = json.loads(Path(f"out/cache/matterport/{mid}.json").read_text())
    ov = bh.OVERRIDES / f"{mid}.json"
    parts = bh.apply_splits(bh.read_rooms(model), json.loads(ov.read_text()) if ov.exists() else {})
    scan = rb.load_scan(bh.extract_matterpak(mid, Path(f"out/raw/{mid}")))
    floor = bh.scan_floor_z(scan, parts)
    for p in parts:
        p.floor_z = floor.get(p.floor, 0.0)
    rooms = bh.shell_rooms(parts)
    footprint = ex.house_footprint(scan, rooms, bh.void_polys(parts))
    floor_z = min(r.floor_z for r in rooms)
    corners, _ = ex.facade_ring(footprint, scan, floor_z)
    ground = ex.ground_grid(scan, footprint, floor_z)

    # Top-down render of the low scan triangles with their own texture.
    c = scan.tris.mean(1)
    low = np.nonzero(c[:, 2] < floor_z + below)[0]
    x0, y0 = c[:, 0].min() - 1, c[:, 1].min() - 1
    x1, y1 = c[:, 0].max() + 1, c[:, 1].max() + 1
    W, H = int((x1 - x0) / res), int((y1 - y0) / res)
    X, Y = np.meshgrid(x0 + (np.arange(W) + 0.5) * res, y1 - (np.arange(H) + 0.5) * res)
    origin = np.c_[X.ravel(), Y.ravel(), np.full(X.size, floor_z + 30.0)]
    down = np.tile([0.0, 0.0, -1.0], (X.size, 1))
    t, prim = Raycaster(scan.tris[low]).cast(origin, down)
    hit = prim >= 0
    img = np.full((X.size, 3), 40, np.uint8)
    face = low[prim[hit]]
    q = origin[hit] + down[hit] * t[hit, None]
    img[hit] = rb.scan_colors(scan, face, rb._bary(scan.tris[face], q)).astype(np.uint8)
    im = Image.fromarray(img.reshape(H, W, 3))
    draw = ImageDraw.Draw(im)

    def px(x, y):
        return ((x - x0) / res, (y1 - y) / res)

    def line(coords, colour):
        draw.line([px(*v) for v in coords], fill=colour, width=2)

    line(footprint.exterior.coords, (255, 0, 0))
    line([*map(tuple, corners), tuple(corners[0])], (0, 128, 255))
    for piece in ex.polygons(ground.lot):
        line(piece.exterior.coords, (0, 255, 0))
    inside = footprint.buffer(0.1)
    for p in model.get("panoLocations") or []:
        x, y = p["position"]["x"], p["position"]["y"]
        a, b = px(x, y)
        colour = (0, 255, 0) if inside.contains(shapely.Point(x, y)) else (255, 255, 0)
        draw.ellipse([a - 5, b - 5, a + 5, b + 5], outline=colour, width=2)
        draw.text((a + 6, b - 6), str(p.get("label")), fill=colour)
    for gx in range(int(np.ceil(x0 / 5)) * 5, int(x1) + 1, 5):
        draw.line([px(gx, y0), px(gx, y0 + 0.5)], fill=(255, 255, 255))
        draw.text(px(gx + 0.2, y0 + 1.2), str(gx), fill=(255, 255, 255))
    for gy in range(int(np.ceil(y0 / 5)) * 5, int(y1) + 1, 5):
        draw.line([px(x0, gy), px(x0 + 0.5, gy)], fill=(255, 255, 255))
        draw.text(px(x0 + 0.7, gy + 0.4), str(gy), fill=(255, 255, 255))
    im.save(out, quality=90)
    print(
        json.dumps(
            {
                "image": out,
                "footprint_m2": round(footprint.area, 1),
                "lot_m2": round(ground.lot.area, 1),
                "facade_corners": len(corners),
                "panoramas_outdoor": sum(
                    not inside.contains(shapely.Point(p["position"]["x"], p["position"]["y"]))
                    for p in model.get("panoLocations") or []
                ),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
