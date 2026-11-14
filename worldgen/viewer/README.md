# Lane A pack QA viewer: NOT THE GAME

> **For coding agents and anyone building the game (Lanes B, C, D, and Lane A agents working on anything
> other than this folder): this is not the game, not a prototype of the game, and not a reference for it.**
>
> - Do **not** import, copy, port, vendor, link to or "take inspiration from" any file in `worldgen/viewer/`
>   when writing `apps/web`, `apps/game`, `apps/agent` or anything that ships.
> - Do **not** treat its controls, camera, capsule controller, collision code, furniture checks, placeholder
>   furniture sizes or three.js setup as decisions. None of them are. They are quick hacks for testing.
> - Do **not** deploy it, add it to `render.yaml`, or serve it from the static site or the pack bucket.
> - Do **not** change it to fit the game. If the game needs something similar, build it in the owning lane's
>   folder from AGENTS.md and the contracts, not from here.
>
> The game client is `apps/web` (Lane C renders the world, Lane D owns network and UI). The character
> controller is ecctrl plus Rapier (AGENTS.md section 5). The layout validator is Lane B's `validate_layout`
> in `apps/agent/layout/`. The furniture renderer is Lane C's `apps/web/src/world/`. If anything here
> disagrees with those, they win.

## What it is

A throwaway local page the Lane A human uses to judge whether a house pack built by `worldgen` is good enough
to play in, before Lane C's game loads it. It reads packs from disk exactly as published
(`packs/<block_id>/v<N>/manifest.json` and the files it lists), and shows:

- the interior (`interior.glb`, meshopt, unlit), with ceilings toggleable
- the collision (`collision.glb`) as wireframe: walls magenta, floor cyan
- room polygons from `rooms.json`, coloured by label, with id, area, confidence and a warning when `area_m2`
  disagrees with the polygon
- doors and windows from `openings`, de-duplicated, with widths; doors narrower than the test capsule turn red
- a test capsule you can walk (third or first person) against the collision mesh plus placed furniture
- furniture placement: catalog assets from `furniture/catalog.json` if the pack has one, else `qa_box_*`
  placeholder boxes; rough checks for room bounds, overlap, door zones and front clearance; export and import
  of a `PlacementPlan` (`contracts/placement_plan.schema.json`, status `draft`)
- for catalog `.glb` files: a normalization check (size vs `size_m`, origin at bottom centre)
- stats: file sizes, triangle count, texture count and estimated GPU memory, draw calls, fps

It does not load the block (`block/*.glb`); houses are shown in their house-local frame.

## Run it

Standard library Python only; three.js 0.187.0 and three-mesh-bvh 0.8.3 load from jsDelivr (same versions as
`apps/web`), so you need internet.

```sh
python3 worldgen/viewer/serve.py          # from the repo root; reads worldgen/out/packs and worldgen/out/furniture
# open http://127.0.0.1:8765  (optional: ?pack=hou_navidad_01/v1&house=hou_236595179)
```

Point it elsewhere with `--packs <dir>` and `--furniture <dir>`.

`--packs` defaults to `worldgen/out/packs` in the current checkout. `--furniture` (default `worldgen/out/furniture`)
is the shared furniture library from contracts v2.3.0: `v<N>/catalog.json` plus `v<N>/<id>.glb`, with asset `file`
paths relative to the published root (`furniture/v1/<id>.glb`). It is served at `/furniture/`, like the published
root. A pack whose manifest still has the pre-v2.3 `furniture/catalog.json` gets the newest library version.
"Reload from disk" picks up a rebuilt pack without restarting. Test layouts are remembered per house in this browser's localStorage only.

## Controls

- Orbit: drag to rotate, right-drag to pan, wheel to zoom. Double-click a floor to drop the capsule there.
- Tab toggles walk mode. In walk mode click the view to capture the mouse; WASD, Shift to run, Space to jump,
  wheel for camera distance. V (or the "first person" checkbox) switches first / third person; scrolling all the
  way in also switches to first person. V in orbit mode starts a first-person walk directly.
- Furniture: pick an asset, press P or "Place on floor", click the floor (Shift+click to keep placing). Click to
  select, drag to move, Q/E rotate 15° (Shift: 90°), arrows nudge 5 cm, Delete removes, Esc cancels.
- "Export plan for room" downloads and copies a draft plan for that room. Hand-authored seed plans
  (`packs/.../plans/<room_label>.json`) still go through Lane B's validator before anyone relies on them.

## Files

| File | Role |
| --- | --- |
| `serve.py` | Local static server: the viewer, the packs folder, `/api/packs`. Binds 127.0.0.1. |
| `index.html` | Page, panel, styles, import map |
| `main.js` | Loading, layers, overlays, modes, placement UI, stats |
| `walker.js` | Test capsule (three-mesh-bvh capsule vs triangles) |
| `furniture.js` | Placeholder boxes, placed objects, rough checks, plan export/import |
| `geom2d.js` | 2D polygon and rectangle helpers in house-local `[x, z]` |
