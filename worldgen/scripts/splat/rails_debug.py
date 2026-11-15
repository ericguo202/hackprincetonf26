import json, sys, dataclasses
import numpy as np
from shapely.geometry import Polygon
from worldgen import build_house as bh, exterior as ex, rebake as rb, splat as sp, railings
from worldgen.pack import load_block
from worldgen.sources import matterport as mp
block, listing = sys.argv[1:3]
out = bh.HERE / "out"
house = next(h for h in load_block(block)["houses"] if h["listing_id"] == listing)
mid = house["matterport_model_id"]
model = mp.fetch_model(mid, out / "cache" / "matterport")
ov = bh.OVERRIDES / f"{mid}.json"
overrides = json.loads(ov.read_text()) if ov.exists() else {}
parts = bh.apply_splits(bh.read_rooms(model), overrides)
scan = rb.load_scan(bh.extract_matterpak(mid, out / "raw" / mid))
fz = bh.scan_floor_z(scan, parts)
for p in parts: p.floor_z = fz.get(p.floor, 0.0)
footprint = ex.house_footprint(scan, bh.shell_rooms(parts), bh.void_polys(parts, overrides))
rooms = bh.shell_rooms(parts)
floor_z = min(r.floor_z for r in rooms)
g = ex.ground_grid(scan, footprint, floor_z)
corners, normals = ex.facade_ring(footprint, scan, floor_z)
walls = Polygon(corners)
splat = sp.load(out, mid, footprint.centroid.coords[0])
lift, _ = ex.splat_level(splat, g, walls)
splat = dataclasses.replace(splat, xyz=splat.xyz + np.array([0, 0, lift], np.float32))
from PIL import Image
from worldgen.panos import PanoSet
panos = PanoSet(model, out / 'raw' / mid / 'skybox_2k')
for k, r in enumerate(railings.find_rails(splat, g, walls)):
    p = railings.rail_plane(splat, r, scan, panos)
    print(k, np.round(r.a, 2).tolist(), "->", np.round(r.b, 2).tolist(), "L %.2f" % r.length, r.meta, "built" if p is not None else "dropped")
    if p is not None: Image.fromarray(p.image).save(out / f"cache/glb/splatfill/rail_{k}.png")
