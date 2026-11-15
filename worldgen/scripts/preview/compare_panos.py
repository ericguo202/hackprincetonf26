"""Where do two builds differ? Mean abs error per panorama for two uncompressed glbs, side by side.

Run from worldgen/:
    uv run python scripts/preview/compare_panos.py <a.glb> <b.glb> out/cache/matterport/<id>.json \
        out/raw/<id>/skybox_2k [--outdoor]

render_compare.py --score gives one number per build; when it moves, this says which panoramas moved it.
For each indoor panorama (outdoor ones with --outdoor; indoor = inside the model's room polygons, the
pipeline's rule) it renders the 4 side faces from both glbs exactly like the scorer (192 px, unlit,
holes ignored) and prints both errors and the difference, largest change first.

Example: v10 (interior only) against v41 (interior + outdoors) on 7014 showed every panorama within 0.2
except the ones looking through the opened front and back doors, which located the whole score change.
A difference in a room that the change should not touch means the change leaks there: look at that
panorama with render_compare.py <glb> ... <label>. Takes about a minute per house."""

import json
import sys

import numpy as np
from PIL import Image
from shapely.geometry import Point
from shapely.ops import unary_union

sys.path.insert(0, sys.path[0])
from render_compare import load, render, yup  # noqa: E402

from worldgen.build_house import read_rooms  # noqa: E402
from worldgen.panos import Raycaster, quat_matrix  # noqa: E402

SIZE = 192


def face_errors(scene, pos, R, pano_id, sky) -> float:
    f, r, u = R @ [1, 0, 0], R @ [0, -1, 0], R @ [0, 0, 1]
    dirs = ((f, r, u, 1), (r, -f, u, 2), (-f, -r, u, 3), (-r, f, u, 4))
    errs = []
    for fwd, right, up, face in dirs:
        img = render(scene, yup(pos), yup(fwd), yup(right), yup(up), W=SIZE, H=SIZE).astype(float)
        real = Image.open(f"{sky}/{pano_id}_{face}.jpg").convert("RGB").resize((SIZE, SIZE))
        real = np.asarray(real).astype(float)
        hole = (img == [40, 0, 60]).all(-1)
        errs.append(np.abs(img[~hole] - real[~hole]).mean() if (~hole).any() else 255.0)
    return float(np.mean(errs))


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    if len(args) != 4:
        print(__doc__)
        return 2
    a_glb, b_glb, model_json, sky = args
    outdoor = "--outdoor" in argv
    model = json.load(open(model_json))
    rooms = unary_union([r.poly for r in read_rooms(model) if r.keep]).buffer(0.1)
    scenes = []
    for glb in (a_glb, b_glb):
        tris, uvs, tid, texs = load(glb)
        scenes.append((tris, uvs, tid, texs, Raycaster(tris)))
    rows = []
    for p in model["panoLocations"]:
        pos = np.array([p["position"][k] for k in "xyz"])
        if rooms.contains(Point(pos[0], pos[1])) == outdoor:
            continue
        R = quat_matrix(p["rotation"])
        ea, eb = (face_errors(s, pos, R, p["id"], sky) for s in scenes)
        rows.append((p["label"], pos[:2], ea, eb))
    print(f"{'pano':>5}  {'x':>6} {'y':>6}   {'a':>6}  {'b':>6}  {'b - a':>6}")
    for label, xy, ea, eb in sorted(rows, key=lambda r: -abs(r[3] - r[2])):
        print(f"{label:>5}  {xy[0]:6.1f} {xy[1]:6.1f}   {ea:6.2f}  {eb:6.2f}  {eb - ea:+6.2f}")
    if rows:
        ea, eb = np.mean([r[2] for r in rows]), np.mean([r[3] for r in rows])
        print(f"mean over {len(rows)} panoramas: a {ea:.2f}  b {eb:.2f}  b - a {eb - ea:+.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
