"""Top view of the splat (colour, and height above the floor as a ramp) over a plan box: x0 y0 x1 y1 out.png"""
import sys
import numpy as np
from PIL import Image
from worldgen import build_house as bh, splat as sp
x0, y0, x1, y1 = map(float, sys.argv[1:5])
s = sp.load(bh.HERE / "out", "y1cVPR1HsnL", (4.2, 4.3))
keep = s.xyz[:, 2] < float(sys.argv[6]) if len(sys.argv) > 6 else None
rgb, al, dep = s.flatten((x0, y1, 12.0), (1, 0, 0), (0, -1, 0), x1 - x0, y1 - y0, 80, (0, 0, -1), 0, 1e3, keep=keep)
z = 12.0 - dep
t = np.clip((z - 1.5) / 3.5, 0, 1)
ramp = (np.dstack([t, 1 - np.abs(t - .5) * 2, 1 - t]) * 255).astype(np.uint8)
Image.fromarray(np.hstack([rgb.astype(np.uint8), ramp])).save(sys.argv[5])
print("z at centre row:", np.round(z[z.shape[0] // 2, ::40], 2).tolist())
