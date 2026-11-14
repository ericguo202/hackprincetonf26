"""Contact sheets of every room seen from its corners: the views a player gets, away from the scan positions.

Run from worldgen/:
    uv run python scripts/preview/room_views.py <raw glb> <rooms.json> <out dir> [room ids ...]

Why this exists: a render from a panorama's own position always looks right, because the texture was
projected from there and any geometry error is hidden. Artefacts (crumpled door leaves, fans printed on the
ceiling, scan crumbs on walls, ragged door frames) only show from somewhere else. This script stands in each
corner of each room (0.6 m in from both walls, eye height 1.6 m) and looks at the opposite corner.

TINT=1 paints scan-geometry nodes (`fixtures_*` magenta, `ceiling_scan_*` cyan) so you can tell kept scan
triangles from the clean shell. NO_CEILING=1 hides ceilings. One JPG per room, named <room id>.jpg."""
import json
import os
import sys

import numpy as np
from PIL import Image
from shapely.geometry import Point, Polygon

sys.path.insert(0, sys.path[0])
from render_compare import load, render, yup  # noqa: E402

from worldgen.panos import Raycaster  # noqa: E402


def corner_views(poly: Polygon, floor_y: float, inset: float = 0.6, eye: float = 1.6, n: int = 4):
    """Up to n (eye, look-at) pairs in plan coordinates: the n sharpest convex corners, looking across the room."""
    ring = list(poly.exterior.coords)[:-1]
    c = np.array(poly.representative_point().coords[0])
    scored = []
    for i, p in enumerate(ring):
        a, b = np.array(ring[i - 1]), np.array(ring[(i + 1) % len(ring)])
        p = np.array(p)
        da, db = (a - p) / max(np.linalg.norm(a - p), 1e-9), (b - p) / max(np.linalg.norm(b - p), 1e-9)
        bis = da + db
        if np.linalg.norm(bis) < 1e-6:
            continue
        bis /= np.linalg.norm(bis)
        e = p + bis * inset * 1.4
        if not poly.contains(Point(e)):
            continue
        scored.append((np.linalg.norm(p - c), e, p))
    scored.sort(key=lambda s: -s[0])
    out = []
    for _, e, p in scored[:n]:
        far = max(ring, key=lambda q: np.linalg.norm(np.array(q) - e))
        look = np.array(far) * 0.7 + c * 0.3
        out.append((np.array([e[0], e[1], floor_y + eye]), np.array([look[0], look[1], floor_y + 1.2])))
    return out


def main() -> None:
    glb, rooms_path, out_dir = sys.argv[1:4]
    only = set(sys.argv[4:])
    tint = bool(os.environ.get("TINT"))
    tris, uvs, tid, texs = load(glb)
    if tint:
        import trimesh

        names = [k for k in trimesh.load(glb).geometry if not (os.environ.get("NO_CEILING") and k.startswith("ceiling"))]
        for i, name in enumerate(names):
            col = (255, 0, 255) if name.startswith("fixtures") else (0, 255, 255) if name.startswith("ceiling_scan") else None
            if col:
                t = texs[i].astype(float)
                t[..., :3] = t[..., :3] * 0.35 + np.array(col) * 0.65
                texs[i] = t.astype(np.uint8)
    scene = (tris, uvs, tid, texs, Raycaster(tris))
    os.makedirs(out_dir, exist_ok=True)
    for r in json.load(open(rooms_path)):
        if only and r["id"] not in only and r["id"].split(".")[-1] not in only:
            continue
        poly = Polygon([(x, -z) for x, z in r["polygon"]])  # house-local [x, z] -> plan (x, y)
        ims = []
        for pos, look in corner_views(poly, r["floor_y"]):
            f = look - pos
            f /= np.linalg.norm(f)
            rt = np.cross(f, [0, 0, 1.0])
            rt /= np.linalg.norm(rt)
            up = np.cross(rt, f)
            ims.append(render(scene, yup(pos), yup(f), yup(rt), yup(up), W=800, H=600, fov=85))
        if not ims:
            continue
        while len(ims) % 2:
            ims.append(np.zeros_like(ims[0]))
        rows = [np.hstack(ims[i : i + 2]) for i in range(0, len(ims), 2)]
        Image.fromarray(np.vstack(rows)).save(f"{out_dir}/{r['id']}.jpg", quality=88)
        print(r["id"], r["label"], len(ims))


if __name__ == "__main__":
    main()
