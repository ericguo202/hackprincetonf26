"""Can a player walk through each door? Rays through the doorway against collision.glb.

Run from worldgen/:
    uv run python scripts/preview/door_check.py out/packs/hou_navidad_01/v41 hou_236595179 [--all]

For every door opening in rooms.json (by default only outside doors: `to_room` is null), nine rays cross
the doorway from 1 m on the room side to 1.5 m beyond it: at 0.5, 1.0 and 1.5 m above the floor, through
the door's centre and 0.2 m either side (inside a 0.25 m player capsule). A door is OPEN when no ray hits
anything; otherwise the collision nodes hit are listed (`walls`, `fixtures` such as a door-leaf slab,
`outdoor`, `bounds` ...). Doors shared by two rooms (or by a split room) are checked once.

Expected on the Navidad houses with the outdoors (worldgen/exterior.py): the front and back doors OPEN (they
are cut for play), closet doors whose closet is not a room "blocked by walls". Use it after any change to
doors, wall cuts, door-leaf slabs or outdoor collision. Purely geometric: it does not run a character
controller, so steps and slopes are not checked."""

import json
import sys
from pathlib import Path

import numpy as np
import trimesh

from worldgen.panos import Raycaster


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    house = Path(argv[0]) / "houses" / argv[1]
    every = "--all" in argv
    rooms = json.loads((house / "rooms.json").read_text())
    scene = trimesh.load(house / "collision.glb")
    tris, names = [], []
    for node in scene.graph.nodes_geometry:
        transform, geom = scene.graph[node]
        mesh = scene.geometry[geom]
        verts = trimesh.transformations.transform_points(mesh.vertices, transform)
        tris.append(verts[mesh.faces])
        names += [node] * len(mesh.faces)
    rays = Raycaster(np.concatenate(tris))
    names = np.array(names)
    done: list[np.ndarray] = []
    for room in rooms:
        cx, cz = np.mean(room["polygon"], axis=0)
        for o in room["openings"]:
            if o["type"] != "door" or (o.get("to_room") and not every):
                continue
            a, b = np.array(o["a"], float), np.array(o["b"], float)
            mid = (a + b) / 2
            if any(np.linalg.norm(mid - m) < 0.3 for m in done):
                continue
            done.append(mid)
            u = (b - a) / max(np.linalg.norm(b - a), 1e-9)
            n = np.array([-u[1], u[0]])
            if n @ (mid - [cx, cz]) < 0:
                n = -n  # point away from the room
            hits = set()
            for side in (-0.2, 0.0, 0.2):
                for y in (0.5, 1.0, 1.5):
                    p = mid + u * side
                    origin = np.array([p[0] - n[0] * 1.0, room["floor_y"] + y, p[1] - n[1] * 1.0])
                    t, prim = rays.cast(origin[None], np.array([[n[0], 0.0, n[1]]]))
                    if prim[0] >= 0 and t[0] < 2.5:
                        hits.add(str(names[prim[0]]))
            kind = f"to {o['to_room']}" if o.get("to_room") else "outside"
            state = "OPEN" if not hits else "blocked by " + ",".join(sorted(hits))
            width = np.linalg.norm(b - a)
            where = f"{o['a']} -> {o['b']}"
            print(f"{room['id']:<18} {room['label']:<16} {kind:<22} {width:4.2f} m  {where}: {state}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
