"""Diagnostic for exterior.splat_roof: sheet heights, plane labels, residuals, outlines. Writes a PNG sheet."""
import json, sys
from pathlib import Path
import numpy as np, shapely
from PIL import Image, ImageDraw
from shapely.geometry import Polygon
from shapely.geometry.polygon import orient
from worldgen import build_house as bh, exterior as ex, rebake as rb, roof as rf, splat as sp, aerial as ae
from worldgen.pack import load_block
from worldgen.sources import matterport as mp

block, listing, dest = sys.argv[1:4]
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
spans = bh.opening_spans(parts, lambda e, z: bh.runs_from_rays(e, scan.rays, z), np.zeros((0, 3)))
footprint = ex.house_footprint(scan, bh.shell_rooms(parts), bh.void_polys(parts, overrides))
rooms = bh.shell_rooms(parts, spans)
for r in rooms: rb.measure_ceiling(scan, r)
floor_z = min(r.floor_z for r in rooms)
g = ex.ground_grid(scan, footprint, floor_z)
corners, normals = ex.facade_ring(footprint, scan, floor_z)
walls = Polygon(corners)
air = ae.load(out, load_block(block), bh.listing_entry(out, listing))
vex = air.roof_outline(walls) if air is not None else None
splat = sp.load(out, mid, footprint.centroid.coords[0])
lift, spread = ex.splat_level(splat, g, walls)
import dataclasses
splat = dataclasses.replace(splat, xyz=splat.xyz + np.array([0, 0, lift], np.float32))
report = {}
roof, colour = ex.splat_roof(splat, vex, walls, rooms, floor_z, report)
print(json.dumps(report, indent=1))
b = walls.buffer(3).bounds
S = 0.05
nx, ny = int((b[2] - b[0]) / S), int((b[3] - b[1]) / S)
X, Y = np.meshgrid(b[0] + (np.arange(nx) + 0.5) * S, b[3] - (np.arange(ny) + 0.5) * S)
sheet = splat.roof_sheet(b, floor_z + 1.9, floor_z + 9.0)
zs = sheet(X.ravel(), Y.ravel()).reshape(ny, nx)
zr = roof.surface(X.ravel(), Y.ravel()).reshape(ny, nx)
i, j = roof._ij(X.ravel(), Y.ravel())
lab = roof.label[i, j].reshape(ny, nx)
inreg = shapely.contains_xy(roof.region, X, Y)
def ramp(v, lo, hi):
    t = np.clip((v - lo) / (hi - lo), 0, 1); t = np.where(np.isfinite(v), t, 0)
    return (np.dstack([t, 1 - np.abs(t - .5) * 2, 1 - t]) * 255 * np.isfinite(v)[..., None]).astype(np.uint8)
rng = np.random.default_rng(1); pal = rng.integers(60, 255, (len(roof.planes), 3))
A = ramp(zs, floor_z + 2, floor_z + 5.5)
B = np.where(inreg[..., None], pal[lab], 30).astype(np.uint8)
C = ramp(np.where(inreg, zs - zr, np.nan), -0.3, 0.3)
Dm = ramp(np.where(inreg, zr, np.nan), floor_z + 2, floor_z + 5.5)
ims = []
for arr in (A, B, C, Dm):
    im = Image.fromarray(arr); d = ImageDraw.Draw(im)
    for poly, col in ((walls, (255, 255, 255)), (vex, (255, 255, 0)), (roof.region, (0, 255, 255))):
        if poly is None: continue
        xy = [((x - b[0]) / S, (b[3] - y) / S) for x, y in poly.exterior.coords]
        d.line(xy, fill=col, width=2)
    ims.append(np.asarray(im))
Image.fromarray(np.vstack([np.hstack(ims[:2]), np.hstack(ims[2:])])).save(dest)
print("planes", np.round(roof.planes, 3).tolist())
# the part of Vexcel's outline the splat outline left out
miss = vex.difference(roof.region.buffer(0.2)) if vex is not None else None
if miss is not None and not miss.is_empty:
    for q in sorted(getattr(miss, "geoms", [miss]), key=lambda q: -q.area)[:3]:
        if q.area < 2: continue
        m = shapely.contains_xy(q, splat.xyz[:, 0], splat.xyz[:, 1]) & (splat.alpha > 0.2)
        z = splat.xyz[m, 2] - floor_z
        h, e = np.histogram(z, bins=np.arange(-0.5, 8.01, 0.25), weights=splat.alpha[m])
        print("left out: %.1f m2 at %s, gaussians %d" % (q.area, np.round(q.centroid.coords[0], 1), m.sum()))
        print("  z hist (0.25 m bins from -0.5):", (h / q.area).round(0).astype(int).tolist())
        sz = sheet(*np.array(q.representative_point().coords[0]).reshape(2, 1))
        cells = shapely.contains_xy(q, X, Y)
        print("  sheet cells finite %.0f%%, median %.2f, p10 %.2f p90 %.2f" % (np.isfinite(zs[cells]).mean() * 100, *np.nanpercentile(zs[cells] - floor_z, [50, 10, 90])))
# heights along the roof's edge (3 cm inside), jumps between neighbours
from shapely.geometry.polygon import orient as _or
ring = np.asarray(_or(roof.region, -1.0).exterior.coords)
tot = 0
for a, c in zip(ring[:-1], ring[1:]):
    L = np.linalg.norm(c - a)
    if L < 0.3: continue
    u2 = (c - a) / L; nm = np.array([-u2[1], u2[0]])
    t = np.arange(0.05, L, 0.1)
    pts = a - nm * 0.03 + np.outer(t, u2)
    hz = roof.height(pts[:, 0], pts[:, 1])
    lab_e = roof.label[roof._ij(pts[:, 0], pts[:, 1])]
    jumps = np.abs(np.diff(hz))
    if (jumps > 0.12).any():
        print("edge (%.1f, %.1f) -> (%.1f, %.1f) L %.1f: %d jumps over 0.12 m (max %.2f), labels %s" % (*a, *c, L, (jumps > 0.12).sum(), jumps.max(), np.unique(lab_e).tolist()))
        tot += (jumps > 0.12).sum()
print("edge jumps total", tot)
# zoomed label map
Z = 4
big = np.kron(B, np.ones((Z, Z, 1), np.uint8))
Image.fromarray(big).save(dest.replace(".png", "_labels.png"))
for (px, py) in [(3.3, -2.5), (3.2, -2.2), (3.0, -2.2), (3.5, -2.2), (3.2, -1.5), (2.5, -2.0), (4.0, -2.0)]:
    ii, jj = roof._ij(np.array([px]), np.array([py]))
    print((px, py), "label", int(roof.label[ii, jj][0]), "surface %.2f height %.2f" % (roof.surface(np.array([px]), np.array([py]))[0], roof.height(np.array([px]), np.array([py]))[0]), "near", np.nonzero(roof.near[:, ii[0], jj[0]])[0].tolist(), "sheet", sheet(np.array([px]), np.array([py]))[0], "in region", roof.region.contains(shapely.Point(px, py)))
