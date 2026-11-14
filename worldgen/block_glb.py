"""Small glTF 2.0 (GLB) writer for the procedural block: meshes with normals, UVs and vertex colours,
PBR or unlit materials, embedded JPEG/PNG images, named node hierarchy.

Compression (meshopt, WebP) happens afterwards with gltf-transform (see `compress`).
"""

from __future__ import annotations

import io
import json
import shutil
import struct
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

WORLDGEN = Path(__file__).resolve().parent


class MeshBuf:
    """Triangle soup accumulator. Triangles are flipped as needed so their geometric normal agrees
    with the requested facing direction (`up` hint) when one is given."""

    def __init__(self) -> None:
        self.p: list[np.ndarray] = []
        self.n: list[np.ndarray] = []
        self.uv: list[np.ndarray] = []
        self.c: list[np.ndarray] = []
        self.uv2: list[np.ndarray] = []
        self.idx: list[np.ndarray] = []
        self.count = 0

    def add(
        self,
        pos: np.ndarray,
        tris: np.ndarray,
        uv: np.ndarray | None = None,
        normals: np.ndarray | None = None,
        color: np.ndarray | None = None,
        facing: np.ndarray | None = None,
        uv2: np.ndarray | None = None,
    ) -> None:
        pos = np.asarray(pos, np.float32).reshape(-1, 3)
        tris = np.asarray(tris, np.int64).reshape(-1, 3)
        if len(tris) == 0:
            return
        if facing is not None:
            f = np.asarray(facing, np.float32)
            a, b, c = pos[tris[:, 0]], pos[tris[:, 1]], pos[tris[:, 2]]
            fn = np.cross(b - a, c - a)
            want = f if f.ndim == 1 else f
            flip = (fn * want).sum(-1) < 0
            tris = tris.copy()
            tris[flip] = tris[flip][:, ::-1]
        if normals is None:
            normals = vertex_normals(pos, tris)
        normals = np.broadcast_to(np.asarray(normals, np.float32), pos.shape)
        if uv is None:
            uv = np.zeros((len(pos), 2), np.float32)
        if color is None:
            color = np.ones((len(pos), 4), np.float32)
        color = np.asarray(color, np.float32)
        if color.shape[-1] == 3:
            color = np.concatenate([color, np.ones(color.shape[:-1] + (1,), np.float32)], -1)
        color = np.broadcast_to(color, (len(pos), 4))
        self.p.append(pos)
        self.n.append(np.asarray(normals, np.float32))
        self.uv.append(np.asarray(uv, np.float32).reshape(-1, 2))
        self.c.append(color)
        self.uv2.append(
            np.asarray(uv2 if uv2 is not None else np.zeros((len(pos), 2)), np.float32).reshape(-1, 2)
        )
        self.idx.append(tris + self.count)
        self.count += len(pos)

    def quad(self, a, b, c, d, uv=None, color=None, facing=None) -> None:
        """Quad a-b-c-d (in order around the edge)."""
        pos = np.array([a, b, c, d], np.float32)
        n = np.cross(pos[1] - pos[0], pos[3] - pos[0])
        nn = np.linalg.norm(n)
        n = n / nn if nn > 1e-9 else np.array([0, 1, 0], np.float32)
        if facing is not None and np.dot(n, facing) < 0:
            n = -n
        self.add(pos, [[0, 1, 2], [0, 2, 3]], uv=uv, normals=n, color=color, facing=n)

    def empty(self) -> bool:
        return self.count == 0

    def arrays(self) -> dict[str, np.ndarray]:
        return {
            "POSITION": np.concatenate(self.p),
            "NORMAL": np.concatenate(self.n),
            "TEXCOORD_0": np.concatenate(self.uv),
            "COLOR_0": np.concatenate(self.c),
            "TEXCOORD_1": np.concatenate(self.uv2),
            "indices": np.concatenate(self.idx),
        }

    def tri_count(self) -> int:
        return sum(len(i) for i in self.idx)


def vertex_normals(pos: np.ndarray, tris: np.ndarray) -> np.ndarray:
    a, b, c = pos[tris[:, 0]], pos[tris[:, 1]], pos[tris[:, 2]]
    fn = np.cross(b - a, c - a)
    vn = np.zeros_like(pos)
    for k in range(3):
        np.add.at(vn, tris[:, k], fn)
    ln = np.linalg.norm(vn, axis=1, keepdims=True)
    vn = np.where(ln > 1e-12, vn / np.maximum(ln, 1e-12), np.array([0, 1, 0], np.float32))
    return vn.astype(np.float32)


@dataclass
class Material:
    name: str
    color: tuple[float, float, float, float] = (1, 1, 1, 1)
    texture: str | None = None  # key into images
    normal_texture: str | None = None
    normal_scale: float = 1.0
    normal_texcoord: int = 0
    occlusion_texture: str | None = None
    occlusion_strength: float = 1.0
    roughness: float = 0.9
    metallic: float = 0.0
    unlit: bool = False
    double_sided: bool = False
    alpha_mask: float | None = None  # alphaCutoff when MASK
    blend: bool = False
    vertex_colors: bool = False  # informational; COLOR_0 is always written
    sampler_wrap: int = 10497  # REPEAT; 33071 CLAMP


@dataclass
class Node:
    name: str
    mesh: list[tuple[MeshBuf, str]] = field(default_factory=list)  # (buffer, material name) primitives
    children: list[Node] = field(default_factory=list)
    extras: dict | None = None


def _img_bytes(img: Image.Image | Path | str) -> tuple[bytes, str]:
    if isinstance(img, (str, Path)):
        p = Path(img)
        data = p.read_bytes()
        return data, "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
    buf = io.BytesIO()
    if img.mode == "RGBA":
        img.save(buf, "PNG", optimize=True)
        return buf.getvalue(), "image/png"
    img.convert("RGB").save(buf, "JPEG", quality=92)
    return buf.getvalue(), "image/jpeg"


def write_glb(
    path: Path, roots: list[Node], materials: dict[str, Material], images: dict[str, Image.Image | Path | str]
) -> dict:
    bin_ = bytearray()
    gl: dict = {
        "asset": {"version": "2.0", "generator": "worldgen.block_procedural"},
        "scene": 0,
        "scenes": [{"nodes": []}],
        "nodes": [],
        "meshes": [],
        "materials": [],
        "accessors": [],
        "bufferViews": [],
        "buffers": [],
        "textures": [],
        "images": [],
        "samplers": [
            {"magFilter": 9729, "minFilter": 9987, "wrapS": 10497, "wrapT": 10497},
            {"magFilter": 9729, "minFilter": 9987, "wrapS": 33071, "wrapT": 33071},
            {"magFilter": 9729, "minFilter": 9987, "wrapS": 33648, "wrapT": 33648},
        ],
    }
    used_ext: set[str] = set()

    def view(data: bytes, target: int | None = None) -> int:
        while len(bin_) % 4:
            bin_.append(0)
        off = len(bin_)
        bin_.extend(data)
        v = {"buffer": 0, "byteOffset": off, "byteLength": len(data)}
        if target:
            v["target"] = target
        gl["bufferViews"].append(v)
        return len(gl["bufferViews"]) - 1

    def accessor(arr: np.ndarray, kind: str) -> int:
        if kind == "indices":
            a = arr.astype(np.uint32).ravel()
            ctype, typ, target = 5125, "SCALAR", 34963
        else:
            a = arr.astype(np.float32)
            ctype, target = 5126, 34962
            typ = {2: "VEC2", 3: "VEC3", 4: "VEC4"}[a.shape[1]]
        acc = {
            "bufferView": view(a.tobytes(), target),
            "componentType": ctype,
            "count": int(len(a)),
            "type": typ,
        }
        if kind == "POSITION":
            acc["min"] = a.min(0).tolist()
            acc["max"] = a.max(0).tolist()
        gl["accessors"].append(acc)
        return len(gl["accessors"]) - 1

    tex_index: dict[tuple[str, int], int] = {}
    img_index: dict[str, int] = {}

    def texture(key: str, wrap: int) -> int:
        if (key, wrap) in tex_index:
            return tex_index[(key, wrap)]
        if key not in img_index:
            data, mime = _img_bytes(images[key])
            gl["images"].append({"name": key, "bufferView": view(data), "mimeType": mime})
            img_index[key] = len(gl["images"]) - 1
        gl["textures"].append({"source": img_index[key], "sampler": {10497: 0, 33071: 1, 33648: 2}[wrap]})
        tex_index[(key, wrap)] = len(gl["textures"]) - 1
        return tex_index[(key, wrap)]

    mat_index: dict[str, int] = {}
    uses_uv2: set[str] = set()

    def material(name: str) -> int:
        if name in mat_index:
            return mat_index[name]
        m = materials[name]
        pbr: dict = {
            "baseColorFactor": list(m.color),
            "metallicFactor": m.metallic,
            "roughnessFactor": m.roughness,
        }
        if m.texture:
            pbr["baseColorTexture"] = {"index": texture(m.texture, m.sampler_wrap)}
        md: dict = {"name": m.name, "pbrMetallicRoughness": pbr}
        if m.normal_texture:
            md["normalTexture"] = {
                "index": texture(m.normal_texture, 10497 if m.normal_texcoord else m.sampler_wrap),
                "scale": m.normal_scale,
                "texCoord": m.normal_texcoord,
            }
            if m.normal_texcoord:
                uses_uv2.add(name)
        if m.occlusion_texture:
            md["occlusionTexture"] = {
                "index": texture(m.occlusion_texture, 10497),
                "texCoord": 1,
                "strength": m.occlusion_strength,
            }
            uses_uv2.add(name)
        if m.unlit:
            md["extensions"] = {"KHR_materials_unlit": {}}
            used_ext.add("KHR_materials_unlit")
        if m.double_sided:
            md["doubleSided"] = True
        if m.blend:
            md["alphaMode"] = "BLEND"
        if m.alpha_mask is not None:
            md["alphaMode"] = "MASK"
            md["alphaCutoff"] = m.alpha_mask
        gl["materials"].append(md)
        mat_index[name] = len(gl["materials"]) - 1
        return mat_index[name]

    tris_total = 0

    def node(nd: Node) -> int:
        nonlocal tris_total
        out: dict = {"name": nd.name}
        prims = []
        for buf, mname in nd.mesh:
            if buf.empty():
                continue
            a = buf.arrays()
            attrs = {k: accessor(a[k], k) for k in ("POSITION", "NORMAL", "TEXCOORD_0")}
            material(mname)
            if mname in uses_uv2:
                attrs["TEXCOORD_1"] = accessor(a["TEXCOORD_1"], "TEXCOORD_1")
            if not np.allclose(a["COLOR_0"], 1.0):
                attrs["COLOR_0"] = accessor(a["COLOR_0"], "COLOR_0")
            prims.append(
                {
                    "attributes": attrs,
                    "indices": accessor(a["indices"], "indices"),
                    "material": material(mname),
                    "mode": 4,
                }
            )
            tris_total += len(a["indices"])
        if prims:
            gl["meshes"].append({"name": nd.name, "primitives": prims})
            out["mesh"] = len(gl["meshes"]) - 1
        if nd.extras:
            out["extras"] = nd.extras
        gl["nodes"].append(out)
        i = len(gl["nodes"]) - 1
        kids = [node(c) for c in nd.children]
        if kids:
            gl["nodes"][i]["children"] = kids
        return i

    gl["scenes"][0]["nodes"] = [node(r) for r in roots]
    if used_ext:
        gl["extensionsUsed"] = sorted(used_ext)
    for k in ("textures", "images", "materials", "meshes"):
        if not gl[k]:
            del gl[k]
    while len(bin_) % 4:
        bin_.append(0)
    gl["buffers"] = [{"byteLength": len(bin_)}]
    js = json.dumps(gl, separators=(",", ":")).encode()
    while len(js) % 4:
        js += b" "
    out = struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(js) + 8 + len(bin_))
    out += struct.pack("<II", len(js), 0x4E4F534A) + js
    out += struct.pack("<II", len(bin_), 0x004E4942) + bytes(bin_)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(out)
    return {"triangles": tris_total, "bytes": len(out)}


def gltf_transform() -> list[str]:
    local = WORLDGEN / "node_modules" / ".bin" / "gltf-transform"
    if local.exists():
        return [str(local)]
    exe = shutil.which("gltf-transform")
    if exe:
        return [exe]
    return ["npx", "-y", "@gltf-transform/cli@4.5.1"]


def compress(src: Path, dst: Path, webp_quality: int = 82) -> None:
    """WebP textures + meshopt geometry. Node names and hierarchy are kept (no join/flatten)."""
    tmp = dst.with_suffix(".tmp.glb")
    gt = gltf_transform()
    subprocess.run(
        [*gt, "webp", str(src), str(tmp), "--quality", str(webp_quality)], check=True, capture_output=True
    )
    subprocess.run([*gt, "meshopt", str(tmp), str(dst), "--level", "medium"], check=True, capture_output=True)
    tmp.unlink(missing_ok=True)
