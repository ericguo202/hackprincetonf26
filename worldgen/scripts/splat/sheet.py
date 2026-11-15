import sys
sys.path.insert(0, "scripts/splat")
import numpy as np
from PIL import Image, ImageDraw
import splatlib as sl, sview
M = "y1cVPR1HsnL"; D = sl.OUT / "splat_bake" / M
VIEWS = ["-13.8,14.2,1.7;3,4,2.5;80", "-9.4,11.4,1.7;-16,18,2;80", "-13,13,1.6;-30,2,2;80", "12.7,11.9,1.5;30,5,2;80",
         "24,3,1.6;5,4,2.2;75", "14,4,1.6;40,-6,2;75", "18,-8,10;3,3,2.5;55", "-25,-20,25;3,4,0;50"]
ims = sview.Scene(str(sl.OUT / f"cache/glb/{M}_interior_pano_v43.glb")).shots(VIEWS, str(D / "p4_v43.jpg"))
after = np.asarray(Image.open(D / "p4_splat_all.jpg"))
pick = [0, 2, 5, 7]
rows = []
for k in pick:
    r, c = divmod(k, 2)
    rows.append(np.hstack([ims[k], after[r * 600:(r + 1) * 600, c * 800:(c + 1) * 800]]))
sh = Image.fromarray(np.vstack(rows)); d = ImageDraw.Draw(sh)
d.text((10, 8), "v43 now", fill=(255, 255, 0)); d.text((810, 8), "prototype: far field + trees + deck area from the splat (roof not included here)", fill=(255, 255, 0))
sh.save(D / "sheet_farfield_trees.jpg", quality=88)
a = np.asarray(Image.open(D / "p1_roof_v43.jpg")); b = np.asarray(Image.open(D / "p1b_roof_splat.jpg"))
sh = Image.fromarray(np.vstack([np.hstack([a[0:600, 0:800], b[0:600, 0:800]]), np.hstack([a[1200:1800, 0:800], b[1200:1800, 0:800]])]))
d = ImageDraw.Draw(sh); d.text((10, 8), "v43 roof (Vexcel planes, plain colour)", fill=(255, 255, 0)); d.text((810, 8), "prototype: planes and shingles from the splat", fill=(255, 255, 0))
sh.save(D / "sheet_roof.jpg", quality=88)
