import sys, time
sys.path.insert(0, "scripts/splat")
import numpy as np
import splatlib as sl

t = time.time()
g = sl.load("y1cVPR1HsnL")
print("gaussians", len(g["xyz"]), "load s", round(time.time() - t, 1))
xyz, size, alpha = g["xyz"], g["size"], g["alpha"]
r = np.hypot(xyz[:, 0] - 4.2, xyz[:, 1] - 4.3)
for lo, hi in [(0, 15), (15, 30), (30, 45), (45, 75)]:
    m = (r >= lo) & (r < hi) & (alpha > 0.3)
    print(f"r {lo}-{hi} m: {m.sum():8d} opaque, size median {np.median(size[m])*100:.1f} cm, p90 {np.percentile(size[m],90)*100:.1f} cm, per m2 {m.sum()/(np.pi*(hi*hi-lo*lo)):.0f}")
print("z p1..p99", np.percentile(xyz[:, 2], [1, 50, 99]))
# top-down true ortho of 90 x 90 m at 4 cm
t = time.time()
img, al, dep = sl.ortho(g, origin=(4.2 - 45, 4.3 + 45, 40.0), ax_u=(1, 0, 0), ax_v=(0, -1, 0), size_m=(90, 90), px_per_m=25)
print("ortho s", round(time.time() - t, 1), img.shape, "coverage", (al > 0.5).mean())
sl.to_image(img, al).save(sl.OUT / "splat_bake/y1cVPR1HsnL/top_90m_4cm.jpg", quality=90)
np.save(sl.OUT / "splat_bake/y1cVPR1HsnL/top_90m_4cm_depth.npy", dep)
