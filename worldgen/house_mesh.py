"""Mesh helpers for build_house: read a Matterport OBJ, crop it to the house, write interior.glb and
collision.glb.

Frames: Matterport plan/mesh coordinates are Z-up (x, y, z). The pack's house-local frame is Y-up:
X = x, Y = z, Z = -y (a -90 degree turn about X, done once here, AGENTS.md section 7 step 4)."""

from __future__ import annotations

import re
import subprocess
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import shapely
import trimesh
from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry

# Z-up (Matterport) -> Y-up (pack). Applied to every vertex we export.
Z_UP_TO_Y_UP = np.array(
    [[1, 0, 0, 0], [0, 0, 1, 0], [0, -1, 0, 0], [0, 0, 0, 1]],
    dtype=float,
)

# Semantic group types in the Matterport defurnished OBJ (group names end in `_typeNNN`), identified on
# 6915 Navidad Rd by area, normal direction and height: 042 floor (94.2 m2 = scanned living area),
# 041 walls, 043 ceiling at 2.44 m, 032/024/033 windows, 003 outdoor ground, 071 countertops, 072 fireplace.
FLOOR, WALLS, CEILING, WINDOWS, FIXTURES, OTHER = "floor", "walls", "ceiling", "windows", "fixtures", "other"
TYPE_CLASS = {"042": FLOOR, "041": WALLS, "043": CEILING, "032": WINDOWS, "024": WINDOWS, "033": WINDOWS}
DROP_TYPES = {"003"}  # outdoor ground

# Flat PBR colours (linear-ish RGBA 0-255). The scan has no textures; a clean, light look reads as "empty
# house, ready to furnish" and keeps furniture readable.
COLORS = {
    FLOOR: (166, 128, 92, 255),
    WALLS: (236, 232, 222, 255),
    CEILING: (247, 247, 244, 255),
    WINDOWS: (170, 205, 235, 90),
    FIXTURES: (200, 196, 188, 255),
    OTHER: (214, 212, 206, 255),
}

_TYPE_RE = re.compile(r"type(\d+)")


@dataclass
class ObjMesh:
    vertices: np.ndarray  # (n, 3) Z-up
    faces_by_type: dict[str, np.ndarray]  # group type ("041", ..., or "none") -> (m, 3) vertex indices

    def faces(self, *types: str) -> np.ndarray:
        parts = [self.faces_by_type[t] for t in types if t in self.faces_by_type]
        return np.concatenate(parts) if parts else np.zeros((0, 3), dtype=np.int64)


def read_obj(path: Path) -> ObjMesh:
    """Positions and triangle faces grouped by Matterport semantic type. Ignores normals/UVs."""
    verts: list[list[float]] = []
    faces: dict[str, list[list[int]]] = defaultdict(list)
    group_type = "none"
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.startswith("v "):
                verts.append([float(x) for x in line.split()[1:4]])
            elif line.startswith("g "):
                m = _TYPE_RE.search(line)
                group_type = m.group(1) if m else "none"
            elif line.startswith("f "):
                idx = [int(tok.split("/")[0]) for tok in line.split()[1:]]
                idx = [i - 1 if i > 0 else len(verts) + i for i in idx]
                for k in range(1, len(idx) - 1):  # fan-triangulate polygons
                    faces[group_type].append([idx[0], idx[k], idx[k + 1]])
    return ObjMesh(
        vertices=np.asarray(verts, dtype=float),
        faces_by_type={t: np.asarray(fs, dtype=np.int64) for t, fs in faces.items()},
    )


def sample_points(mesh: ObjMesh, types: tuple[str, ...], count: int, seed: int = 0) -> np.ndarray:
    """Area-weighted surface samples (Z-up) from the given semantic types; empty if none."""
    faces = mesh.faces(*types)
    if len(faces) == 0:
        return np.zeros((0, 3))
    tm = trimesh.Trimesh(mesh.vertices, faces, process=False)
    pts, _ = trimesh.sample.sample_surface(tm, count, seed=seed)
    return np.asarray(pts)


def crop_faces(
    mesh: ObjMesh, faces: np.ndarray, footprint: BaseGeometry, z_min: float, z_max: float
) -> np.ndarray:
    """Faces whose centroid is inside the footprint (plan x, y) and between z_min and z_max."""
    if len(faces) == 0:
        return faces
    return faces[in_crop(mesh.vertices[faces].mean(axis=1), footprint, z_min, z_max)]


def in_crop(centers: np.ndarray, footprint: BaseGeometry, z_min: float, z_max: float) -> np.ndarray:
    """Mask of points (Z-up) inside the footprint (plan x, y) and between z_min and z_max."""
    keep = (centers[:, 2] >= z_min) & (centers[:, 2] <= z_max)
    keep &= shapely.contains_xy(footprint, centers[:, 0], centers[:, 1])
    return keep


def write_textured_interior_glb(
    obj_path: Path, footprint: BaseGeometry, z_min: float, z_max: float, ceiling_z: float, dest: Path
) -> dict[str, int]:
    """interior.glb from the MatterPak's photo-textured OBJ: cropped to the house, Y-up, one node per texture.

    High, horizontal faces (above ceiling_z) go into separate `ceiling_<n>` nodes so the client can hide
    them for top-down views. Returns face counts {"scan": n, "ceiling": n}. Compress afterwards."""
    loaded = trimesh.load(obj_path, process=False, maintain_order=True)
    geoms = list(loaded.geometry.values()) if isinstance(loaded, trimesh.Scene) else [loaded]
    missing = [g for g in geoms if getattr(g.visual.material, "image", None) is None]
    if missing:
        raise RuntimeError(
            f"{obj_path.name}: {len(missing)}/{len(geoms)} textures did not load (is Pillow installed?)"
        )
    scene = trimesh.Scene()
    counts = {"scan": 0, "ceiling": 0}
    for i, g in enumerate(geoms):
        keep = in_crop(g.triangles_center, footprint, z_min, z_max)
        if not keep.any():
            continue
        high = (g.triangles_center[:, 2] > ceiling_z) & (np.abs(g.face_normals[:, 2]) > 0.7)
        mat = _textured(g.visual.material)  # one material (one texture) shared by both parts
        for name, mask in (("scan", keep & ~high), ("ceiling", keep & high)):
            idx = np.nonzero(mask)[0]
            if len(idx) == 0:
                continue
            part = g.submesh([idx], append=True, repair=False)
            part.apply_transform(Z_UP_TO_Y_UP)
            part.visual.material = mat
            scene.add_geometry(part, node_name=f"{name}_{i:02d}", geom_name=f"{name}_{i:02d}")
            counts[name] += len(idx)
    dest.parent.mkdir(parents=True, exist_ok=True)
    scene.export(dest)
    return counts


def _textured(mat: trimesh.visual.material.Material) -> trimesh.visual.material.PBRMaterial:
    """Scan texture as an unlit-looking PBR material (rough, non-metal, double-sided)."""
    image = getattr(mat, "image", None) or getattr(mat, "baseColorTexture", None)
    return trimesh.visual.material.PBRMaterial(
        name=getattr(mat, "name", None),
        baseColorTexture=image,
        metallicFactor=0.0,
        roughnessFactor=1.0,
        doubleSided=True,
    )


def compress_glb(src: Path, dest: Path, texture_size: int = 2048) -> None:
    """gltf-transform (worldgen/package.json): meshopt geometry, WebP textures capped at texture_size."""
    cli = Path(__file__).resolve().parent / "node_modules" / ".bin" / "gltf-transform"
    if not cli.exists():
        raise FileNotFoundError("run `npm i` in worldgen/ to install gltf-transform")
    # The scan photos already contain the room's lighting, and the mesh carries no normals: lit materials
    # turn every small triangle into a shaded facet ("crumpled foil"). KHR_materials_unlit shows the photo.
    # Run it first: it rewrites the file without meshopt compression.
    unlit = src.with_name(src.stem + "_unlit.glb")
    subprocess.run([str(cli), "unlit", str(src), str(unlit)], check=True, capture_output=True)
    subprocess.run(
        [
            str(cli),
            "optimize",
            str(unlit),
            str(dest),
            "--compress",
            "meshopt",
            "--texture-compress",
            "webp",
            "--texture-size",
            str(texture_size),
            "--simplify",
            "false",
            "--join",
            "false",
            "--flatten",
            "false",
        ],
        check=True,
        capture_output=True,
    )


def _material(name: str) -> trimesh.visual.material.PBRMaterial:
    rgba = COLORS[name]
    return trimesh.visual.material.PBRMaterial(
        name=name,
        baseColorFactor=rgba,
        metallicFactor=0.0,
        roughnessFactor=0.9 if name != WINDOWS else 0.1,
        alphaMode="BLEND" if rgba[3] < 255 else "OPAQUE",
        doubleSided=True,  # scan faces are not consistently wound; players see both sides indoors
    )


def write_interior_glb(
    mesh: ObjMesh, footprint: BaseGeometry, z_min: float, z_max: float, dest: Path
) -> dict[str, int]:
    """interior.glb: the house part of the scan, one node per surface class, Y-up. Returns face counts."""
    by_class: dict[str, list[np.ndarray]] = defaultdict(list)
    for t, faces in mesh.faces_by_type.items():
        if t in DROP_TYPES:
            continue
        cls = TYPE_CLASS.get(t, OTHER if t == "none" else FIXTURES)
        kept = crop_faces(mesh, faces, footprint, z_min, z_max)
        if len(kept):
            by_class[cls].append(kept)
    scene = trimesh.Scene()
    counts: dict[str, int] = {}
    for cls in (FLOOR, WALLS, CEILING, WINDOWS, FIXTURES, OTHER):
        if cls not in by_class:
            continue
        faces = np.concatenate(by_class[cls])
        tm = trimesh.Trimesh(mesh.vertices, faces, process=False)
        tm.remove_unreferenced_vertices()
        tm.apply_transform(Z_UP_TO_Y_UP)
        tm.visual = trimesh.visual.TextureVisuals(material=_material(cls))
        scene.add_geometry(tm, node_name=cls, geom_name=cls)
        counts[cls] = len(faces)
    dest.parent.mkdir(parents=True, exist_ok=True)
    scene.export(dest)
    return counts


@dataclass
class WallSegment:
    a: tuple[float, float]  # plan coords (x, y)
    b: tuple[float, float]
    thickness: float
    base_z: float
    height: float


def wall_box(seg: WallSegment) -> trimesh.Trimesh | None:
    """A box for one wall piece, in Z-up plan coordinates."""
    a, b = np.asarray(seg.a), np.asarray(seg.b)
    length = float(np.linalg.norm(b - a))
    if length < 0.02:
        return None
    box = trimesh.creation.box(extents=[length, max(seg.thickness, 0.08), seg.height])
    angle = float(np.arctan2(b[1] - a[1], b[0] - a[0]))
    box.apply_transform(trimesh.transformations.rotation_matrix(angle, [0, 0, 1]))
    mid = (a + b) / 2
    box.apply_translation([mid[0], mid[1], seg.base_z + seg.height / 2])
    return box


def write_collision_glb(
    walls: list[WallSegment],
    floors: list[tuple[Polygon, float]],
    dest: Path,
    fixtures: np.ndarray | None = None,
) -> dict[str, int]:
    """collision.glb, Y-up: `walls` (boxes, door gaps cut out), `floor` (slabs) and, when given, `fixtures`
    (scan triangles (n, 3, 3) of counters, half walls, tubs: things players must not walk through).
    Returns triangle counts."""
    scene = trimesh.Scene()
    counts: dict[str, int] = {}
    boxes = [m for m in (wall_box(w) for w in walls) if m is not None]
    if boxes:
        w = trimesh.util.concatenate(boxes)
        w.apply_transform(Z_UP_TO_Y_UP)
        scene.add_geometry(w, node_name="walls", geom_name="walls")
        counts["walls"] = len(w.faces)
    slabs = []
    for poly, floor_z in floors:
        polys = [poly] if poly.geom_type == "Polygon" else list(poly.geoms)
        for p in polys:
            slab = trimesh.creation.extrude_polygon(p, height=0.1)
            slab.apply_translation([0, 0, floor_z - 0.1])
            slabs.append(slab)
    if slabs:
        f = trimesh.util.concatenate(slabs)
        f.apply_transform(Z_UP_TO_Y_UP)
        scene.add_geometry(f, node_name="floor", geom_name="floor")
        counts["floor"] = len(f.faces)
    if fixtures is not None and len(fixtures):
        verts = fixtures.reshape(-1, 3)
        fx = trimesh.Trimesh(verts, np.arange(len(verts)).reshape(-1, 3), process=False)
        fx.merge_vertices()
        fx.apply_transform(Z_UP_TO_Y_UP)
        scene.add_geometry(fx, node_name="fixtures", geom_name="fixtures")
        counts["fixtures"] = len(fx.faces)
    dest.parent.mkdir(parents=True, exist_ok=True)
    scene.export(dest)
    return counts
