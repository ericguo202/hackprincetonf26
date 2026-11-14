"""Stats for an untrusted downloaded file. Run isolated: `python -I worldgen/scout/file_stats.py <path>`.

.ply (binary little-endian Gaussian splat): vertex count, properties, bbox (all points and the 1st-99th
percentile core, to ignore floaters), up-axis guess. .obj: vertex/face counts, bbox, up-axis guess.
Prints one JSON object. Reads data only; never executes anything from the file.
"""

from __future__ import annotations

import json
import os
import sys

import numpy as np

PLY_TYPES = {
    "float": "f4",
    "float32": "f4",
    "double": "f8",
    "float64": "f8",
    "uchar": "u1",
    "uint8": "u1",
    "char": "i1",
    "int8": "i1",
    "short": "i2",
    "int16": "i2",
    "ushort": "u2",
    "uint16": "u2",
    "int": "i4",
    "int32": "i4",
    "uint": "u4",
    "uint32": "u4",
}


def bbox(xyz: np.ndarray) -> dict:
    lo, hi = xyz.min(axis=0), xyz.max(axis=0)
    p1, p99 = np.percentile(xyz, 1, axis=0), np.percentile(xyz, 99, axis=0)
    ext = hi - lo
    core = p99 - p1
    return {
        "min": [round(float(v), 3) for v in lo],
        "max": [round(float(v), 3) for v in hi],
        "extent_m": [round(float(v), 2) for v in ext],
        "core_p1": [round(float(v), 3) for v in p1],
        "core_p99": [round(float(v), 3) for v in p99],
        "core_extent_m": [round(float(v), 2) for v in core],
        "centroid": [round(float(v), 3) for v in xyz.mean(axis=0)],
        # The up axis is usually the one with the smallest core extent for a house + yard capture.
        "up_axis_guess": "xyz"[int(np.argmin(core))],
    }


def ply(path: str, box: list[float] | None = None) -> dict:
    with open(path, "rb") as f:
        header = []
        while True:
            line = f.readline()
            if not line:
                raise ValueError("no end_header")
            s = line.decode("ascii", "replace").strip()
            header.append(s)
            if s == "end_header":
                break
            if len(header) > 500:
                raise ValueError("header too long")
        offset = f.tell()
    fmt = next((h.split()[1] for h in header if h.startswith("format")), None)
    if fmt != "binary_little_endian":
        return {"format": fmt, "error": "only binary_little_endian supported"}
    count, props, in_vertex = 0, [], False
    for h in header:
        parts = h.split()
        if parts[:2] == ["element", "vertex"]:
            count, in_vertex = int(parts[2]), True
        elif parts[:1] == ["element"]:
            in_vertex = False
        elif parts[:1] == ["property"] and in_vertex and parts[1] != "list":
            props.append((parts[2], "<" + PLY_TYPES[parts[1]]))
    dt = np.dtype(props)
    data = np.memmap(path, dtype=dt, mode="r", offset=offset, shape=(count,))
    xyz = np.stack([data["x"], data["y"], data["z"]], axis=1).astype("f8")
    out = {
        "kind": "ply",
        "bytes": os.path.getsize(path),
        "points": count,
        "properties": [p[0] for p in props],
        "sh_rest_coeffs": sum(1 for p in props if p[0].startswith("f_rest_")),
        "bbox": bbox(xyz),
    }
    if "opacity" in data.dtype.names:
        op = 1 / (1 + np.exp(-data["opacity"].astype("f8")))
        out["opacity_ge_0.5_share"] = round(float((op >= 0.5).mean()), 3)
    # Splats keep far background (km away). Count points by horizontal distance from the origin and
    # give the 5-95th percentile extent of the near field (within 150 m).
    r = np.hypot(xyz[:, 0], xyz[:, 1])
    out["points_within_m"] = {str(k): int((r <= k).sum()) for k in (10, 25, 50, 100, 150, 250)}
    near = xyz[r <= 150]
    if len(near):
        p5, p95 = np.percentile(near, 5, axis=0), np.percentile(near, 95, axis=0)
        out["near150_p5"] = [round(float(v), 2) for v in p5]
        out["near150_p95"] = [round(float(v), 2) for v in p95]
    if box:
        x0, y0, x1, y1 = box
        inside = (xyz[:, 0] >= x0) & (xyz[:, 0] <= x1) & (xyz[:, 1] >= y0) & (xyz[:, 1] <= y1)
        area = max((x1 - x0) * (y1 - y0), 1e-6)
        ring = (r <= 150) & ~inside
        ring_area = np.pi * 150**2 - area
        out["box"] = [round(v, 2) for v in box]
        out["points_in_box"] = int(inside.sum())
        out["density_in_box_per_m2"] = round(float(inside.sum() / area), 1)
        out["density_rest_150m_per_m2"] = round(float(ring.sum() / ring_area), 1)
    return out


def obj(path: str) -> dict:
    vs, faces, has_vt, has_vn, mtllib = [], 0, False, False, None
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.startswith("v "):
                p = line.split()
                vs.append((float(p[1]), float(p[2]), float(p[3])))
            elif line.startswith("f "):
                n = len(line.split()) - 1
                faces += max(n - 2, 0)
            elif line.startswith("vt "):
                has_vt = True
            elif line.startswith("vn "):
                has_vn = True
            elif line.startswith("mtllib") and mtllib is None:
                mtllib = line.split(maxsplit=1)[1].strip()
    xyz = np.array(vs, dtype="f8")
    return {
        "kind": "obj",
        "bytes": os.path.getsize(path),
        "vertices": len(vs),
        "triangles": faces,
        "has_uv": has_vt,
        "has_normals": has_vn,
        "mtllib": mtllib,
        "bbox": bbox(xyz) if len(xyz) else None,
    }


def main() -> int:
    path = sys.argv[1]
    box = [float(v) for v in sys.argv[3:7]] if len(sys.argv) >= 7 and sys.argv[2] == "--box" else None
    if path.lower().endswith(".ply"):
        res = ply(path, box)
    elif path.lower().endswith(".obj"):
        res = obj(path)
    else:
        res = {"error": "unsupported file type"}
    print(json.dumps(res))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
