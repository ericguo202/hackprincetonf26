"""Amazon Berkeley Objects (ABO) furniture pipeline, AGENTS.md section 7.6.

Stages (each a subcommand, run from worldgen/):

  uv run python -m worldgen.sources.abo meta      # download 3dmodels/metadata/3dmodels.csv.gz (340 KB)
  uv run python -m worldgen.sources.abo types     # product_type counts of listings that have a 3D model
  uv run python -m worldgen.sources.abo index     # -> out/abo_index.parquet (never ships to Render)
  uv run python -m worldgen.sources.abo select    # abo_categories.yaml -> out/abo_selection.json
  uv run python -m worldgen.sources.abo fetch     # download the selected .glb files -> out/raw/abo/

The bucket is public, so plain HTTPS works and no AWS CLI is needed
(same objects as `aws s3 cp --no-sign-request s3://amazon-berkeley-objects/...`).
Never downloads the full model archive. Normalizing (meters, +Y up, front +Z,
bottom-center origin, meshopt) is build_catalog's job, not this file's.

License: CC BY 4.0, credit "Amazon Berkeley Objects, Amazon.com".
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import glob
import gzip
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import httpx
import pandas as pd
import yaml

BUCKET = "https://amazon-berkeley-objects.s3.amazonaws.com"
HERE = Path(__file__).resolve().parents[1]  # worldgen/
DEFAULT_ABO = HERE.parent / "amazon-3d-images"
OUT = HERE / "out"
INCH = 0.0254

LICENSE = "CC BY 4.0"
CREDIT = "Amazon Berkeley Objects, Amazon.com"


# ---------------------------------------------------------------- helpers


def _en(values: list[dict] | None) -> str | None:
    """First en_* value of an ABO multilingual field, else first value, else None."""
    if not values:
        return None
    for v in values:
        if str(v.get("language_tag", "")).startswith("en"):
            return str(v["value"]).strip()
    return str(values[0].get("value", "")).strip() or None


def _has_en_name(values: list[dict] | None) -> bool:
    return any(str(v.get("language_tag", "")).startswith("en") for v in values or [])


def _dims_m(d: dict | list | None) -> dict | None:
    """item_dimensions -> {h,l,w} in meters (listing text; only used to cross-check the mesh)."""
    if isinstance(d, list):
        d = d[0] if d else None
    if not d:
        return None
    out = {}
    for k, key in (("height", "h"), ("length", "l"), ("width", "w")):
        v = (d.get(k) or {}).get("normalized_value") or d.get(k) or {}
        if "value" in v:
            unit = str(v.get("unit", "inches")).lower()
            val = float(v["value"])
            out[key] = round(
                val * INCH if unit.startswith("inch") else val / 100 if unit.startswith("cent") else val, 3
            )
    return out or None


def _listing_rows(abo_dir: Path):
    files = sorted(glob.glob(str(abo_dir / "abo-listings/listings/metadata/listings_*.json.gz")))
    if not files:
        sys.exit(f"no listings found under {abo_dir}/abo-listings/listings/metadata")
    for f in files:
        with gzip.open(f, "rt", encoding="utf-8") as fh:
            for line in fh:
                r = json.loads(line)
                if r.get("3dmodel_id"):
                    yield r


def _meta_path(abo_dir: Path) -> Path:
    return abo_dir / "abo-3dmodels/metadata/3dmodels.csv.gz"


# ---------------------------------------------------------------- stages


def cmd_meta(a) -> None:
    dst = _meta_path(a.abo)
    dst.parent.mkdir(parents=True, exist_ok=True)
    r = httpx.get(f"{BUCKET}/3dmodels/metadata/3dmodels.csv.gz", timeout=60)
    r.raise_for_status()
    dst.write_bytes(r.content)
    print(f"wrote {dst} ({len(r.content):,} bytes)")


def cmd_types(a) -> None:
    c = Counter(_en(r.get("product_type")) or "?" for r in _listing_rows(a.abo))
    for k, v in c.most_common():
        print(f"{v:6d}  {k}")


def cmd_index(a) -> None:
    meta = pd.read_csv(_meta_path(a.abo))
    rows, seen = [], set()
    for r in _listing_rows(a.abo):
        mid = r["3dmodel_id"]
        # Several listings can share one model; keep the first one with an English name.
        if mid in seen:
            continue
        seen.add(mid)
        rows.append(
            {
                "3dmodel_id": mid,
                "item_id": r["item_id"],
                "item_name": _en(r.get("item_name")),
                "name_is_en": _has_en_name(r.get("item_name")),
                "brand": _en(r.get("brand")),
                "product_type": _en(r.get("product_type")),
                "color": _en(r.get("color")),
                "material": _en(r.get("material")),
                "style": _en(r.get("style")),
                "item_dimensions_m": json.dumps(_dims_m(r.get("item_dimensions"))),
                "item_keywords": "; ".join(
                    k["value"]
                    for k in r.get("item_keywords", [])
                    if str(k.get("language_tag", "")).startswith("en")
                ),
                "main_image_id": r.get("main_image_id"),
            }
        )
    df = pd.DataFrame(rows).merge(meta, on="3dmodel_id", how="inner")
    OUT.mkdir(parents=True, exist_ok=True)
    dst = OUT / "abo_index.parquet"
    df.to_parquet(dst, index=False)
    print(f"wrote {dst}: {len(df)} models")


def _family(name: str, color: str | None) -> str:
    """Name without color/size noise, so 'Bean Bag 4', Red' and '... 4', Blue' group together."""
    s = name.lower()
    if color:
        s = s.replace(color.lower(), "")
    s = re.sub(r"[\d.]+\s*(\"|'|inch|in\b|cm|x)", " ", s)
    s = re.sub(r",[^,]*$", "", s)  # trailing ', <variant>'
    return re.sub(r"\W+", " ", s).strip()


def cmd_select(a) -> None:
    df = pd.read_parquet(OUT / "abo_index.parquet").rename(columns={"3dmodel_id": "model_id"})
    for col in ("item_name", "brand", "color", "style", "material", "main_image_id"):
        df[col] = df[col].astype(object).where(df[col].notna(), None)  # NaN -> None (null in JSON)
    cfg = yaml.safe_load(Path(a.categories).read_text(encoding="utf-8"))["categories"]
    taken: set[str] = set()
    selection, report = [], []
    # Cost: faces, plus a penalty for 4K textures (big files, slow on the web).
    df = df.assign(cost=df["faces"] + (df["image_height_max"] > 2048) * 150_000)
    df = df.sort_values(["name_is_en", "cost"], ascending=[False, True])
    for cat, c in cfg.items():
        m = df["product_type"].isin(c["product_types"]) & ~df["model_id"].isin(taken)
        name = df["item_name"].fillna("")
        if c.get("include"):
            m &= name.str.contains(c["include"], case=False, regex=True)
        if c.get("exclude"):
            m &= ~name.str.contains(c["exclude"], case=False, regex=True)
        if c.get("height_m"):
            lo, hi = c["height_m"]
            m &= df["extent_y"].between(lo, hi)
        if c.get("footprint_m"):
            lo, hi = c["footprint_m"]
            m &= df[["extent_x", "extent_z"]].max(axis=1).between(lo, hi)
        cand = df[m]
        # Round-robin over product families so we get varied styles/colors, not 8 colors of one sofa.
        fams: dict[str, list] = defaultdict(list)
        for row in cand.itertuples(index=False):
            fams[_family(row.item_name or "", row.color)].append(row)
        picks, colors = [], set()
        queues = list(fams.values())
        while len(picks) < c["count"] and any(queues):
            for q in queues:
                if not q or len(picks) >= c["count"]:
                    continue
                # Within a family prefer a color we don't have yet.
                idx = next((i for i, r in enumerate(q) if (r.color or "").lower() not in colors), 0)
                r = q.pop(idx)
                picks.append(r)
                colors.add((r.color or "").lower())
        report.append((cat, len(cand), len(picks)))
        for r in picks:
            taken.add(r.model_id)
            d = r._asdict()
            selection.append(
                {
                    "category": cat,
                    "id": f"{cat}_abo_{d['item_id']}",
                    "3dmodel_id": d["model_id"],
                    "item_id": d["item_id"],
                    "path": d["path"],
                    "product_name": d["item_name"],
                    "brand": d["brand"],
                    "product_type": d["product_type"],
                    "style": d["style"],
                    "color": d["color"],
                    "material": d["material"],
                    "item_dimensions_m": json.loads(d["item_dimensions_m"]),
                    "extent_m": {"x": d["extent_x"], "y": d["extent_y"], "z": d["extent_z"]},
                    "faces": int(d["faces"]),
                    "texture_px": int(d["image_height_max"]),
                    "main_image_id": d["main_image_id"],
                    "tags": c.get("tags", []),
                    "placement": c.get("placement"),
                    "clearance_m": {"front": c.get("clearance_front_m", 0.0)},
                    "source": {
                        "dataset": "ABO",
                        "item_id": d["item_id"],
                        "license": LICENSE,
                        "credit": CREDIT,
                    },
                }
            )
    dst = OUT / "abo_selection.json"
    dst.write_text(json.dumps(selection, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"{'category':16s} {'candidates':>10s} {'picked':>6s}")
    for cat, n, k in report:
        flag = "  <-- short" if k < min(3, cfg[cat]["count"]) else ""
        print(f"{cat:16s} {n:10d} {k:6d}{flag}")
    print(f"total {len(selection)} -> {dst}")


def cmd_fetch(a) -> None:
    sel = json.loads((OUT / "abo_selection.json").read_text(encoding="utf-8"))
    dst_dir = OUT / "raw/abo"
    dst_dir.mkdir(parents=True, exist_ok=True)

    def get(item: dict) -> tuple[str, int, str]:
        dst = dst_dir / f"{item['3dmodel_id']}.glb"
        if dst.exists() and dst.stat().st_size > 0:
            return item["3dmodel_id"], dst.stat().st_size, "cached"
        url = f"{BUCKET}/3dmodels/original/{item['path']}"
        tmp = dst.with_suffix(".part")
        with httpx.stream("GET", url, timeout=120, follow_redirects=True) as r:
            r.raise_for_status()
            with tmp.open("wb") as fh:
                for chunk in r.iter_bytes(1 << 20):
                    fh.write(chunk)
        tmp.replace(dst)
        return item["3dmodel_id"], dst.stat().st_size, "ok"

    total, failed = 0, []
    with cf.ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(get, it): it for it in sel}
        for i, f in enumerate(cf.as_completed(futs), 1):
            it = futs[f]
            try:
                mid, size, status = f.result()
                total += size
                print(f"[{i:3d}/{len(sel)}] {status:6s} {size / 1e6:6.1f} MB  {it['category']:14s} {mid}")
            except Exception as e:  # noqa: BLE001 - report and keep going
                failed.append(it["3dmodel_id"])
                print(f"[{i:3d}/{len(sel)}] FAILED {it['3dmodel_id']}: {e}")
    print(
        f"done: {len(sel) - len(failed)} files, {total / 1e6:.0f} MB in {dst_dir}; failed: {failed or 'none'}"
    )
    if failed:
        raise SystemExit(1)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--abo", type=Path, default=DEFAULT_ABO, help="local ABO folder (listings + 3D metadata)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("meta").set_defaults(fn=cmd_meta)
    sub.add_parser("types").set_defaults(fn=cmd_types)
    sub.add_parser("index").set_defaults(fn=cmd_index)
    s = sub.add_parser("select")
    s.add_argument("--categories", default=str(HERE / "abo_categories.yaml"))
    s.set_defaults(fn=cmd_select)
    f = sub.add_parser("fetch")
    f.add_argument("--workers", type=int, default=6)
    f.set_defaults(fn=cmd_fetch)
    a = p.parse_args(argv)
    a.fn(a)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
