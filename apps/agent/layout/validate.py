"""validate_layout: deterministic checks on a placement plan (AGENTS.md sections 9 and 10).

Conflict types (contracts/placement_plan.schema.json): outside_room, overlap, blocks_opening, clearance.
The model never decides whether a plan is valid; this module does. commit_placement refuses any plan
this returns with conflicts.

Rules, in plain words:
  outside_room    the footprint must lie inside the room polygon (2 cm tolerance for scan noise)
  overlap         two footprints must not intersect; flat pieces (rug, play mat) may lie under others,
                  and on_surface pieces (table lamp) stand on others
  blocks_opening  nothing but flat pieces in a door's swing zone; nothing taller than 1.0 m against a window
  clearance       each keep-clear zone from the catalog (clearance_m: front/back/left/right) must stay
                  inside the room and free of other furniture; low pieces that belong in front of seating
                  (coffee table, ottoman) may sit in another piece's front clearance
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping

from layout.geometry import RoomGeometry, clearance_zones, footprint, room_geometry

TOL_M = 0.02
MIN_AREA_M2 = 1e-3
FLAT = {"rug", "play_mat"}
OK_IN_FRONT_CLEARANCE = FLAT | {"coffee_table", "ottoman"}
WINDOW_MAX_H_M = 1.0

AssetLookup = Mapping[str, dict] | Callable[[str], dict | None]


def lookup(assets: AssetLookup, asset_id: str) -> dict:
    a = assets(asset_id) if callable(assets) else assets.get(asset_id)
    if a is None:
        raise KeyError(f"unknown asset_id {asset_id!r}")
    return a


def is_flat(asset: dict) -> bool:
    return asset["category"] in FLAT


def stands_on_others(asset: dict) -> bool:
    return asset.get("placement") == "on_surface"


def object_conflicts(
    geo: RoomGeometry,
    obj: dict,
    asset: dict,
    others: list[tuple[dict, dict]],
    check_reverse: bool = False,
) -> list[dict]:
    """Conflicts for one object against the room and the `others` already placed: [(obj, asset)].

    check_reverse also flags this object standing in the clearance zones of the others. validate_layout
    leaves it off (it visits every object, so both directions are covered); propose_poses turns it on.
    """
    out: list[dict] = []
    iid = obj["instance_id"]
    fp = footprint(obj["x"], obj["z"], asset["size_m"], obj["rot_deg"])
    room = geo.poly.buffer(TOL_M)

    if not room.contains(fp):
        outside = fp.difference(geo.poly).area
        out.append(_c(iid, "outside_room", f"{asset['name']} extends {outside:.2f} m2 outside the room"))

    if not is_flat(asset):
        for o in geo.openings:
            if o.type == "door" and fp.intersection(o.keep_clear).area > MIN_AREA_M2:
                where = f"door to {o.to_room}" if o.to_room else "outside door"
                out.append(_c(iid, "blocks_opening", f"{asset['name']} is in the swing of the {where}"))
            elif (
                o.type == "window"
                # top edge above the floor: a wall-mounted TV (mount_y_m) covers a window even though short
                and asset["size_m"]["h"] + float(asset.get("mount_y_m") or 0.0) > WINDOW_MAX_H_M
                and fp.intersection(o.keep_clear).area > MIN_AREA_M2
            ):
                out.append(
                    _c(
                        iid,
                        "blocks_opening",
                        f"{asset['name']} ({asset['size_m']['h']:.2f} m tall) covers a window",
                    )
                )

    if not stands_on_others(asset):
        for other, other_asset in others:
            if stands_on_others(other_asset) or is_flat(asset) != is_flat(other_asset):
                continue  # rugs only collide with rugs; lamps on tables never collide
            ofp = footprint(other["x"], other["z"], other_asset["size_m"], other["rot_deg"])
            area = fp.intersection(ofp).area
            if area > MIN_AREA_M2:
                c = _c(
                    iid,
                    "overlap",
                    f"{asset['name']} overlaps {other['instance_id']} {other_asset['name']} by {area:.2f} m2",
                )
                c["_with"] = other["instance_id"]
                out.append(c)

    for side, zone in clearance_zones(
        obj["x"], obj["z"], asset["size_m"], obj["rot_deg"], asset.get("clearance_m")
    ).items():
        if not room.contains(zone):
            out.append(_c(iid, "clearance", f"{asset['name']} {side} clearance runs into a wall"))
            continue
        for other, other_asset in others:
            if is_flat(other_asset) or stands_on_others(other_asset):
                continue
            if side == "front" and other_asset["category"] in OK_IN_FRONT_CLEARANCE:
                continue
            ofp = footprint(other["x"], other["z"], other_asset["size_m"], other["rot_deg"])
            if zone.intersection(ofp).area > MIN_AREA_M2:
                out.append(
                    _c(
                        iid,
                        "clearance",
                        f"{other['instance_id']} {other_asset['name']} is in the {side} clearance "
                        f"of {asset['name']}",
                    )
                )

    if check_reverse and not is_flat(asset) and not stands_on_others(asset):
        for other, other_asset in others:
            for side, zone in clearance_zones(
                other["x"],
                other["z"],
                other_asset["size_m"],
                other["rot_deg"],
                other_asset.get("clearance_m"),
            ).items():
                if side == "front" and asset["category"] in OK_IN_FRONT_CLEARANCE:
                    continue
                if zone.intersection(fp).area > MIN_AREA_M2:
                    out.append(
                        _c(
                            iid,
                            "clearance",
                            f"{asset['name']} is in the {side} clearance of {other['instance_id']} "
                            f"{other_asset['name']}",
                        )
                    )
    return out


def validate_layout(plan: dict, room: dict | RoomGeometry, assets: AssetLookup) -> dict:
    """Return a copy of `plan` with `conflicts` filled and status validated (none) or draft (some).

    Clearance is checked in both directions: each object's zones against every other object, so a
    piece placed later in a zone of an earlier one is caught.
    """
    geo = room if isinstance(room, RoomGeometry) else room_geometry(room)
    if plan["room_id"] != geo.id:
        raise ValueError(f"plan is for {plan['room_id']}, geometry is for {geo.id}")
    objs = [(o, lookup(assets, o["asset_id"])) for o in plan["objects"]]
    ids = [o["instance_id"] for o, _ in objs]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate instance_id in plan")

    conflicts: list[dict] = []
    seen: set[frozenset[str]] = set()
    for i, (obj, asset) in enumerate(objs):
        others = objs[:i] + objs[i + 1 :]
        for c in object_conflicts(geo, obj, asset, others):
            other_id = c.pop("_with", None)
            if other_id is not None:  # report each overlapping pair once
                key = frozenset((c["instance_id"], other_id))
                if key in seen:
                    continue
                seen.add(key)
            conflicts.append(c)

    out = copy.deepcopy(plan)
    out["conflicts"] = conflicts
    out["status"] = "draft" if conflicts else "validated"
    return out


def _c(instance_id: str, type_: str, detail: str) -> dict:
    return {"instance_id": instance_id, "type": type_, "detail": detail}
