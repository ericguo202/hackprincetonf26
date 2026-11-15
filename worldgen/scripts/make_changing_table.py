"""Team-made changing tables (procedural, CC0): white and natural wood.

    cd worldgen && uv run python scripts/make_changing_table.py
    -> worldgen/furniture_src/team/changing_table_team_white.glb, changing_table_team_wood.glb

About 0.90 x 0.50 x 0.95 m (w x d x h): four round legs, a changing surface at 0.85 m with a padded
mat, a low rail (0.10 m) on the back and both ends, closed end panels and back panel, and two open shelves
below (the lower one holds two fabric bins). The open side is the front.

Conventions the furniture pipeline expects (docs/prompts/handoff-prompt-skills-and-furniture.md 4.2):
meters, +Y up, front faces +Z, origin at the bottom center (min Y = 0, X and Z centered). Every part has a
named PBR material with a plain color: no textures, no logos. After changing this file, re-run it, update
the sha256 of both files in worldgen/extra_furniture.yaml, and build a new furniture version.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import trimesh
from trimesh.visual.material import PBRMaterial
from trimesh.visual.texture import TextureVisuals

OUT = Path(__file__).resolve().parents[1] / "furniture_src" / "team"

W, D = 0.90, 0.50  # overall width (X) and depth (Z)
TOP_Y = 0.85  # changing surface (top of the top panel)
TOP_T = 0.025  # top panel thickness
RAIL_H = 0.10  # rail height above the surface -> overall height 0.95
PANEL_T = 0.018  # end, back and rail panel thickness
LEG_R = 0.022  # round legs
SHELVES_Y = (0.08, 0.44)  # top of each open shelf
SHELF_T = 0.02

VARIANTS = {
    "white": {
        "ct_frame": ((0.93, 0.93, 0.91), 0.0, 0.55),  # painted white wood
        "ct_pad": ((0.72, 0.80, 0.86), 0.0, 0.85),  # pale blue vinyl mat
        "ct_bin": ((0.78, 0.78, 0.76), 0.0, 0.95),  # light gray fabric bins
    },
    "wood": {
        "ct_frame": ((0.70, 0.52, 0.34), 0.0, 0.65),  # natural oak-like wood
        "ct_pad": ((0.92, 0.89, 0.82), 0.0, 0.85),  # cream mat
        "ct_bin": ((0.62, 0.55, 0.43), 0.0, 0.95),  # woven tan bins
    },
}


def box(cx: float, cy: float, cz: float, w: float, h: float, d: float) -> trimesh.Trimesh:
    """Axis-aligned box centered at (cx, cy, cz) with size w (X), h (Y), d (Z)."""
    return trimesh.creation.box(
        extents=(w, h, d), transform=trimesh.transformations.translation_matrix((cx, cy, cz))
    )


def leg(cx: float, cz: float, h: float) -> trimesh.Trimesh:
    """Vertical round leg from the floor to height h (trimesh cylinders run along Z, so turn it to Y)."""
    c = trimesh.creation.cylinder(radius=LEG_R, height=h, sections=16)
    c.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, (1, 0, 0)))
    c.apply_translation((cx, h / 2, cz))
    return c


def parts() -> dict[str, list[trimesh.Trimesh]]:
    hx, hz = W / 2, D / 2
    lx, lz = hx - LEG_R, hz - LEG_R  # leg centers, flush with the outline
    under = TOP_Y - TOP_T  # underside of the top panel
    frame = [leg(sx * lx, sz * lz, under) for sx in (-1, 1) for sz in (-1, 1)]
    # changing surface, full outline
    frame.append(box(0, TOP_Y - TOP_T / 2, 0, W, TOP_T, D))
    # low rail on the back and both ends (front stays open)
    rail_y = TOP_Y + RAIL_H / 2
    frame.append(box(0, rail_y, -hz + PANEL_T / 2, W, RAIL_H, PANEL_T))
    for sx in (-1, 1):
        frame.append(box(sx * (hx - PANEL_T / 2), rail_y, PANEL_T / 2, PANEL_T, RAIL_H, D - PANEL_T))
    # closed ends and back between the legs, from the lower shelf to the top
    lo = SHELVES_Y[0] - SHELF_T
    ph = under - lo
    py = lo + ph / 2
    inner_w = W - 4 * LEG_R
    inner_d = D - 4 * LEG_R
    for sx in (-1, 1):
        frame.append(box(sx * (lx), py, 0, PANEL_T, ph, inner_d))
    frame.append(box(0, py, -lz, inner_w, ph, PANEL_T))
    # two open shelves
    for y in SHELVES_Y:
        frame.append(box(0, y - SHELF_T / 2, 0, inner_w, SHELF_T, inner_d))
    # padded changing mat, inset inside the rails
    pad = [box(0, TOP_Y + 0.02, 0.01, W - 2 * PANEL_T - 0.04, 0.04, D - PANEL_T - 0.05)]
    # two fabric bins on the lower shelf
    bw, bh, bd = 0.33, 0.24, 0.34
    bins = [box(sx * 0.19, SHELVES_Y[0] + bh / 2, 0.01, bw, bh, bd) for sx in (-1, 1)]
    return {"ct_frame": frame, "ct_pad": pad, "ct_bin": bins}


def build(variant: str) -> trimesh.Scene:
    scene = trimesh.Scene()
    for mat_name, meshes in parts().items():
        rgb, metal, rough = VARIANTS[variant][mat_name]
        mesh = trimesh.util.concatenate(meshes)
        mesh.visual = TextureVisuals(
            material=PBRMaterial(
                name=mat_name,
                baseColorFactor=[*rgb, 1.0],
                metallicFactor=metal,
                roughnessFactor=rough,
            )
        )
        scene.add_geometry(
            mesh, geom_name=mat_name.removeprefix("ct_"), node_name=mat_name.removeprefix("ct_")
        )
    return scene


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for variant in VARIANTS:
        scene = build(variant)
        lo, hi = scene.bounds
        path = OUT / f"changing_table_team_{variant}.glb"
        path.write_bytes(scene.export(file_type="glb"))
        size = hi - lo
        print(
            f"{path.name}: {path.stat().st_size / 1e3:.1f} KB, "
            f"w {size[0]:.3f} d {size[2]:.3f} h {size[1]:.3f} m, "
            f"min y {lo[1]:.3f}, center x {(lo[0] + hi[0]) / 2:.3f} z {(lo[2] + hi[2]) / 2:.3f}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
