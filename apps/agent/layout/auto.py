"""Place a whole pick list in one pass (the layout planner's fast path). Pure: no I/O.

The curator chose each piece, its rule and what it is placed relative to; this runs propose_poses for each
piece in order and takes the best pose, so one tool call does what took two model turns per piece. Every
pose is conflict-free against what is already placed, so the result normally validates first time. Pieces
with no valid pose are dropped with the reason, for the planner to recover or report.

Regenerate:
- avoid: anchor poses of earlier layouts. The anchor (the first piece placed without relative_to, e.g. the
  TV stand) takes its best pose that is clearly different from all of them, so the room visibly changes
  (the pieces placed relative to it follow).
- used: asset ids of earlier layouts. With swap=True each pick uses its first alternate not used before.
"""

from __future__ import annotations

import math
from collections.abc import Callable

from layout.geometry import RoomGeometry
from layout.propose import DEFAULT_RULE, TARGET_NEEDED, propose_poses
from layout.validate import sits_at

DIFFERENT_M = 1.0  # an anchor pose this far from an earlier one, or turned this much, counts as new
DIFFERENT_DEG = 45.0
SEAT_LOOKAHEAD = 40  # table poses tried for one where its chair still fits


def _seat_fits(
    geo: RoomGeometry, table: dict, pose: dict, seat: tuple[dict, dict], pairs: list[tuple[dict, dict]]
) -> bool:
    """Whether the seat pick can still be pulled up to the table standing at `pose`."""
    obj = {
        "instance_id": "_table",
        "asset_id": table["id"],
        **{k: pose[k] for k in ("x", "y", "z", "rot_deg")},
    }
    pick, seat_asset = seat
    trial = propose_poses(geo, seat_asset, pick.get("rule") or "facing", "_table", [*pairs, (obj, table)], 1)
    return bool(trial["poses"])


def _differs(pose: dict, earlier: list[dict]) -> bool:
    for e in earlier:
        turn = abs((pose["rot_deg"] - e["rot_deg"] + 180) % 360 - 180)
        if math.dist((pose["x"], pose["z"]), (e["x"], e["z"])) < DIFFERENT_M and turn < DIFFERENT_DEG:
            return False
    return True


def _ordered(picks: list[dict]) -> list[tuple[int, dict]]:
    """Priority order, but a piece comes after the piece it is placed relative to."""
    by_prio = sorted(enumerate(picks), key=lambda ip: (ip[1].get("priority", 2), ip[0]))
    done: set[str] = set()
    out: list[tuple[int, dict]] = []
    pending = by_prio
    while pending:
        rest = []
        for i, p in pending:
            rel = p.get("relative_to")
            if rel and rel not in done and any(q["asset_id"] == rel for q in picks):
                rest.append((i, p))
            else:
                out.append((i, p))
                done.add(p["asset_id"])
        if len(rest) == len(pending):  # a cycle: place the rest without waiting
            out += rest
            break
        pending = rest
    return out


def auto_place(
    geo: RoomGeometry,
    picks: list[dict],
    get_asset: Callable[[str], dict],
    avoid: list[dict] | None = None,
    used: set[str] | None = None,
    swap: bool = False,
    reason: Callable[[str], str] = lambda s: s,
) -> dict:
    """{objects, placed, dropped, anchor, poses, swapped, notes} for a fresh plan in this room."""
    objects: list[dict] = []
    pairs: list[tuple[dict, dict]] = []
    placed_info: list[dict] = []
    dropped: list[dict] = []
    poses_by_asset: dict[str, dict] = {}
    swapped: list[dict] = []
    notes: list[str] = []
    instance_of: dict[str, str] = {}  # pick's original asset_id -> first instance placed for it
    anchor: dict | None = None
    used = used or set()

    for _, pick in _ordered(picks):
        asset_id = pick["asset_id"]
        if swap:
            fresh = [a for a in pick.get("alternates") or [] if a not in used]
            if fresh:
                swapped.append({"from": asset_id, "to": fresh[0]})
                asset_id = fresh[0]
        a = get_asset(asset_id)
        rel_iid = instance_of.get(pick.get("relative_to") or "")
        rule = pick.get("rule") or DEFAULT_RULE.get(a.get("placement") or "", "center")
        rel_pick = next((q for q in picks if q["asset_id"] == pick.get("relative_to")), None)
        seat_of_table = rel_pick is not None and sits_at(a, get_asset(rel_pick["asset_id"]))
        if seat_of_table and rel_iid is None:
            # A desk chair without its desk is not a work corner: never float it somewhere else.
            dropped.append(
                {"asset_id": asset_id, "role": pick.get("role"), "why": "its table was not placed"}
            )
            continue
        # Seats that belong at this table (office chair at a desk): the table takes its best pose that
        # leaves room for the first of them, not a spot where the chair cannot pull up.
        seat = next(
            (
                (q, get_asset(q["asset_id"]))
                for q in picks
                if q.get("relative_to") == pick["asset_id"] and sits_at(get_asset(q["asset_id"]), a)
            ),
            None,
        )
        if rule in TARGET_NEEDED and rel_iid is None:
            rule = DEFAULT_RULE.get(a.get("placement") or "", "center")
            if rule in TARGET_NEEDED:
                rule = "center"
        for _ in range(int(pick.get("count", 1))):
            is_anchor = anchor is None and rel_iid is None
            limit = 10 if is_anchor and avoid else 5
            out = propose_poses(geo, a, rule, rel_iid, pairs, limit=SEAT_LOOKAHEAD if seat else limit)
            if seat and out["poses"]:
                fits = [p for p in out["poses"] if _seat_fits(geo, a, p, seat, pairs)]
                out["poses"] = (fits or out["poses"])[:limit]
            fallback = DEFAULT_RULE.get(a.get("placement") or "", "center")
            if seat_of_table:
                fallback = rule  # pulled up to its table or dropped, never placed elsewhere
            if not out["poses"] and rule != fallback and fallback not in TARGET_NEEDED:
                out = propose_poses(geo, a, fallback, None, pairs, limit=limit)
                rule = fallback
            if not out["poses"]:
                dropped.append({"asset_id": asset_id, "role": pick.get("role"), "why": out["note"]})
                break
            poses = out["poses"]
            pose = poses[0]
            if is_anchor and avoid:
                pose = next((p for p in poses if _differs(p, avoid)), None)
                if pose is None:
                    pose = poses[0]
                    notes.append(f"{a['name']}: no spot clearly different from the earlier layouts")
            iid = f"o{len(objects) + 1}"
            obj = {
                "instance_id": iid,
                "asset_id": asset_id,
                "x": pose["x"],
                "y": pose["y"],
                "z": pose["z"],
                "rot_deg": pose["rot_deg"],
                "reason": reason(pick.get("role") or pose["reason"]),
            }
            objects.append(obj)
            pairs.append((obj, a))
            instance_of.setdefault(pick["asset_id"], iid)
            if is_anchor:
                anchor = {"asset_id": asset_id, "x": pose["x"], "z": pose["z"], "rot_deg": pose["rot_deg"]}
            poses_by_asset[asset_id] = {"rule": rule, "relative_to": rel_iid, "moving": None, "poses": poses}
            placed_info.append(
                {
                    "instance_id": iid,
                    "asset_id": asset_id,
                    "name": a["name"],
                    "rule": rule,
                    "relative_to": rel_iid,
                    "pose_id": pose["pose_id"],
                    "where": pose["reason"],
                    "alternatives": [
                        {"pose_id": p["pose_id"], "where": p["reason"]} for p in poses if p is not pose
                    ][:3],
                }
            )
    return {
        "objects": objects,
        "placed": placed_info,
        "dropped": dropped,
        "anchor": anchor,
        "poses": poses_by_asset,
        "swapped": swapped,
        "notes": notes,
    }


ANCHOR_TRIES = 6  # anchor spots tried when a must-have piece does not fit around the first one


def _must_haves_dropped(result: dict, picks: list[dict]) -> int:
    prio = {}
    for p in picks:
        for a in [p["asset_id"], *(p.get("alternates") or [])]:
            prio.setdefault(a, p.get("priority", 2))
    return sum(1 for d in result["dropped"] if prio.get(d["asset_id"], 2) == 1)


def auto_place_all(geo: RoomGeometry, picks: list[dict], get_asset: Callable[[str], dict], **kw) -> dict:
    """auto_place, retried with the anchor in other spots while a priority-1 piece is dropped.

    The anchor's best spot can leave no room for a must-have (a queen bed centred on the long wall leaves
    no wall for the desk in a 15 m2 bedroom); the first layout that keeps every must-have wins, else the
    one that drops the fewest. Regenerate's `avoid` still applies to every try.
    """
    avoid = list(kw.pop("avoid", None) or [])
    tried: list[dict] = []
    best, best_miss = None, None
    for _ in range(ANCHOR_TRIES):
        r = auto_place(geo, picks, get_asset, avoid=(avoid + tried) or None, **kw)
        miss = _must_haves_dropped(r, picks)
        if best is None or miss < best_miss:
            best, best_miss = r, miss
        if miss == 0 or r["anchor"] is None or any(not _differs(r["anchor"], [t]) for t in tried):
            break
        tried.append(r["anchor"])
    if tried and best is not None and best["anchor"] and _differs(best["anchor"], tried[:1]):
        best["notes"].append("anchor moved from its first-choice spot so every must-have piece fits")
    return best
