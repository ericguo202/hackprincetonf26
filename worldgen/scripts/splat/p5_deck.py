"""P5: the deck and its railing as the splat has them: top view and two side views (thin slabs), 80 px/m."""
import sys
sys.path.insert(0, "scripts/splat")
import numpy as np
from PIL import Image
import splatlib as sl
M = "y1cVPR1HsnL"; D = sl.OUT / "splat_bake" / M
g = sl.load(M)
xyz = g["xyz"]
lo = np.array([-5.3, -1.6, 0.0]); hi = np.array([-2.1, 2.0, 1.5])  # the deck, read off the top view
xa, ya, xb, yb = lo[0] - 1, lo[1] - 1, hi[0] + 1, hi[1] + 1
box = (xyz[:, 0] > xa) & (xyz[:, 0] < xb) & (xyz[:, 1] > ya) & (xyz[:, 1] < yb) & (xyz[:, 2] < 2.2)
PX = 80
top, at, _ = sl.ortho(g, (xa, yb, 2.2), (1, 0, 0), (0, -1, 0), (xb - xa, yb - ya), PX, mask=box)
# from the west (looking +x): u = (0,-1,0); thin slab at the deck's west rail
west, aw, _ = sl.ortho(g, (lo[0], yb, 2.2), (0, -1, 0), (0, 0, -1), (yb - ya, 2.6), PX, near=-0.25, far=0.25, mask=box)
south, as_, _ = sl.ortho(g, (xa, lo[1], 2.2), (1, 0, 0), (0, 0, -1), (xb - xa, 2.6), PX, near=-0.25, far=0.25, mask=box)
ims = [sl.to_image(top, at, (1, 1, 1)), sl.to_image(west, aw, (1, 1, 1)), sl.to_image(south, as_, (1, 1, 1))]
Wm = max(i.width for i in ims)
sh = Image.new("RGB", (Wm, sum(i.height + 8 for i in ims)), (30, 30, 30)); y = 0
for i in ims: sh.paste(i, (0, y)); y += i.height + 8
sh.save(D / "p5_deck.jpg", quality=90); print(sh.size)
