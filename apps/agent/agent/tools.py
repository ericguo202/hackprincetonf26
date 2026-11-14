"""Agent tools (AGENTS.md section 10). Lane B owns this file.

Every number comes from deterministic code: the model picks intents (rule, pose_id), never coordinates.
Tools read session_id / player_id / block_id from runtime context (agent/context.py), never from the model.

The working plan for a room lives in the agent's virtual filesystem (Deep Agents StateBackend, one per
thread) as a Placement plan (contracts/placement_plan.schema.json):

  /rooms/<room_id>.json          get_room_geometry: the computed geometry summary
  /plans/<room_id>.json          the draft plan; only the tools below write it (PLAN_PERMISSIONS)
  /plans/<room_id>.poses.json    last propose_poses result per asset, so place_asset can take a pose_id

The model may read every file. Built-in write_file/edit_file are denied on /plans/** so coordinates are
only ever written by place_asset. Expected failures come back as {"error": ...} so the model can recover.
"""

import json
import re
import uuid
from collections.abc import Callable
from typing import Any, Protocol

import httpx
from deepagents import FilesystemPermission
from deepagents.backends import StateBackend
from langchain.tools import ToolRuntime, tool

import settings
from agent import game_api
from agent.context import AgentContext
from data import packs
from layout import DEFAULT_RULE, RULES, room_geometry
from layout import propose_poses as _propose
from layout import validate_layout as _validate
from layout.validate import object_conflicts

PLAN_PERMISSIONS = [FilesystemPermission(operations=["write"], paths=["/plans/**"], mode="deny")]
MAX_REASON_WORDS = 12


class ToolError(Exception):
    """An expected failure the model should read and act on (bad id, nothing fits, game down)."""


# --- virtual filesystem ------------------------------------------------------------------------------


class Files(Protocol):
    def read_json(self, path: str) -> Any | None: ...
    def write_json(self, path: str, obj: Any) -> None: ...


class StateFiles:
    """JSON files in the Deep Agents StateBackend (the same files the agent's ls/read_file tools see).

    StateBackend finds the running graph through LangGraph's config, so this works inside any tool call.
    """

    def __init__(self) -> None:
        self.backend = StateBackend()

    def read_json(self, path: str) -> Any | None:
        r = self.backend.read(path, offset=0, limit=1_000_000)
        if r.error or r.file_data is None:
            return None
        return json.loads(r.file_data["content"])

    def write_json(self, path: str, obj: Any) -> None:
        r = self.backend.write(path, json.dumps(obj, indent=1))
        if r.error:
            raise ToolError(f"could not write {path}: {r.error}")


FILES: Files = StateFiles()  # tests swap in a dict-backed fake


def room_path(room_id: str) -> str:
    return f"/rooms/{room_id}.json"


def plan_path(room_id: str) -> str:
    return f"/plans/{room_id}.json"


def poses_path(room_id: str) -> str:
    return f"/plans/{room_id}.poses.json"


# --- helpers -----------------------------------------------------------------------------------------


def _ok(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"))


def _run(fn: Callable[[], Any]) -> str:
    try:
        return _ok(fn())
    except (ToolError, KeyError, ValueError, packs.PackFileMissing) as e:
        return _ok({"error": str(e).strip("'\"")})


async def _arun(fn: Callable[[], Any]) -> str:
    try:
        return _ok(await fn())
    except httpx.HTTPError as e:
        return _ok({"error": f"game service unreachable or refused: {e}"})
    except (ToolError, KeyError, ValueError, packs.PackFileMissing) as e:
        return _ok({"error": str(e).strip("'\"")})


def listing_of(room_id: str) -> str:
    if ".r" not in room_id:
        raise ToolError(f"{room_id!r} is not a room id (expected <listing_id>.r<N>)")
    return room_id.rsplit(".r", 1)[0]


def rooms_of(listing_id: str, block_id: str) -> list[dict]:
    try:
        return packs.get_rooms(listing_id)
    except KeyError:
        # Listing not in listings/index.json yet (worldgen costar.py is a stub): read the house from the
        # session's block directly. See HANDOFF.md lane-b -> lane-a.
        return packs._read_pack(f"{packs.pack_root(block_id)}/houses/{listing_id}/rooms.json")


def load_room(room_id: str, block_id: str) -> dict:
    for r in rooms_of(listing_of(room_id), block_id):
        if r["id"] == room_id:
            return r
    raise ToolError(f"no room {room_id}")


def asset(asset_id: str) -> dict:
    a = packs.get_asset(asset_id)
    if a is None:
        raise ToolError(f"unknown asset_id {asset_id}; use an id from search_assets")
    return a


def load_plan(room_id: str) -> dict:
    plan = FILES.read_json(plan_path(room_id))
    if plan is None:
        raise ToolError(f"no plan for {room_id}; call start_plan first")
    return plan


def save_plan(plan: dict) -> None:
    FILES.write_json(plan_path(plan["room_id"]), plan)


def placed_pairs(plan: dict, skip: str | None = None) -> list[tuple[dict, dict]]:
    return [(o, asset(o["asset_id"])) for o in plan["objects"] if o["instance_id"] != skip]


def plan_view(plan: dict) -> dict:
    """Compact plan for the model: names instead of full assets."""
    return {
        "plan_id": plan["plan_id"],
        "room_id": plan["room_id"],
        "status": plan["status"],
        "objects": [
            {
                "instance_id": o["instance_id"],
                "asset_id": o["asset_id"],
                "name": asset(o["asset_id"])["name"],
                "x": o["x"],
                "z": o["z"],
                "rot_deg": o["rot_deg"],
                "reason": o["reason"],
            }
            for o in plan["objects"]
        ],
        "conflicts": plan["conflicts"],
    }


def short_reason(text: str) -> str:
    words = text.split()
    if not words:
        raise ToolError("reason must not be empty")
    return " ".join(words[:MAX_REASON_WORDS])


def next_instance_id(plan: dict) -> str:
    used = [int(o["instance_id"][1:]) for o in plan["objects"] if re.fullmatch(r"o\d+", o["instance_id"])]
    return f"o{max(used, default=0) + 1}"


def compact_asset(a: dict) -> dict:
    keys = ("id", "name", "category", "style", "color", "size_m", "cover", "placement")
    return {k: a.get(k) for k in keys}


# --- data reads (Lane A readers in data/packs.py behind the section 10 signatures) --------------------


@tool(parse_docstring=True)
def get_listing(listing_id: str) -> str:
    """Get one listing from the listings index: price, beds, baths, area, stories, features, schools.

    Args:
        listing_id: Listing id, e.g. hou_236595179.
    """

    def run() -> dict:
        listing = packs.get_listing(listing_id)
        if listing is None:
            raise ToolError(f"no listing {listing_id}")
        return listing

    return _run(run)


@tool(parse_docstring=True)
def get_rooms(listing_id: str, runtime: ToolRuntime[AgentContext]) -> str:
    """List the scanned rooms of a house: id, label, floor, area, doors, windows. Details: get_room_geometry.

    Args:
        listing_id: Listing id of a house with a scanned interior.
    """

    def run() -> list[dict]:
        return [
            {
                "id": r["id"],
                "label": r["label"],
                "floor": r["floor"],
                "area_m2": r["area_m2"],
                "doors": sum(o["type"] == "door" for o in r["openings"]),
                "windows": sum(o["type"] == "window" for o in r["openings"]),
            }
            for r in rooms_of(listing_id, runtime.context.block_id)
        ]

    return _run(run)


@tool(parse_docstring=True)
def get_house_graph(listing_id: str) -> str:
    """Get the house graph: rooms as nodes (label, area, floor, has_window), doors and stairs as edges.

    Args:
        listing_id: Listing id of a house with a scanned interior.
    """
    return _run(lambda: packs.get_house_graph(listing_id))


@tool(parse_docstring=True)
def get_audit(listing_id: str) -> str:
    """Get the data audit of a house: contradictions between the listing, the scan and the aerial data.

    Args:
        listing_id: Listing id.
    """
    return _run(lambda: packs.get_audit(listing_id))


@tool(parse_docstring=True)
def search_assets(
    query: str,
    category: str | None = None,
    style: str | None = None,
    color: str | None = None,
    max_w: float | None = None,
    max_d: float | None = None,
    limit: int = 8,
) -> str:
    """Search the furniture catalog. Hard filters: category and size. Ranked by tag and keyword match.

    Args:
        query: What the piece is for, e.g. "comfortable seating for movie night".
        category: Exact category, e.g. sofa, tv_stand, coffee_table, rug, bookshelf, crib.
        style: Preferred style (soft match).
        color: Preferred color (soft match).
        max_w: Maximum width in meters.
        max_d: Maximum depth in meters.
        limit: Maximum results (1 to 8).
    """

    def run() -> dict:
        catalog = packs.catalog()
        cats = sorted({a["category"] for a in catalog})
        cat = category.strip().lower().replace(" ", "_") if category else None
        if cat and cat not in cats:
            raise ToolError(f"unknown category {category!r}; categories: {', '.join(cats)}")
        tokens = set(re.findall(r"[a-z0-9]+", query.lower()))
        scored = []
        for a in catalog:
            if cat and a["category"] != cat:
                continue
            if max_w is not None and a["size_m"]["w"] > max_w:
                continue
            if max_d is not None and a["size_m"]["d"] > max_d:
                continue
            tags = {t.lower() for t in a.get("tags", [])}
            words = set(
                re.findall(
                    r"[a-z0-9]+",
                    " ".join(
                        str(a.get(k) or "") for k in ("name", "category", "style", "color", "material")
                    ).lower(),
                )
            )
            words |= {w for t in tags for w in t.split("_")}
            score = sum(2 if t in tags else 1 if t in words else 0 for t in tokens)
            if style and style.lower() in str(a.get("style") or "").lower():
                score += 2
            if color and color.lower() in str(a.get("color") or "").lower():
                score += 2
            scored.append((-score, a["id"], a))
        scored.sort(key=lambda s: (s[0], s[1]))
        n = max(1, min(int(limit), 8))
        return {"results": [compact_asset(a) for _, _, a in scored[:n]], "total_matches": len(scored)}

    return _run(run)


# --- layout (layout/ behind the section 10 signatures, plan kept in /plans/) --------------------------


@tool(parse_docstring=True)
def get_room_geometry(room_id: str, runtime: ToolRuntime[AgentContext]) -> str:
    """Get a room's computed geometry: area, polygon, openings, and free wall segments (longest first).

    Also saved to /rooms/<room_id>.json for other subagents.

    Args:
        room_id: Room id, e.g. hou_236595179.r3.
    """

    def run() -> dict:
        summary = room_geometry(load_room(room_id, runtime.context.block_id)).summary()
        FILES.write_json(room_path(room_id), summary)
        return summary

    return _run(run)


@tool(parse_docstring=True)
def start_plan(room_id: str, runtime: ToolRuntime[AgentContext]) -> str:
    """Start a new, empty furniture plan for a room. Re-furnishing replaces the room's previous plan.

    Do not call this to edit an existing plan; use propose_poses, place_asset and remove_asset on it.

    Args:
        room_id: Room id to furnish.
    """

    def run() -> dict:
        load_room(room_id, runtime.context.block_id)  # fail early on a bad id
        plan = {
            "plan_id": f"p_{uuid.uuid4().hex[:8]}",
            "listing_id": listing_of(room_id),
            "room_id": room_id,
            "status": "draft",
            "objects": [],
            "conflicts": [],
        }
        save_plan(plan)
        FILES.write_json(poses_path(room_id), {})
        return {"plan_id": plan["plan_id"], "path": plan_path(room_id)}

    return _run(run)


@tool(parse_docstring=True)
def propose_poses(
    room_id: str,
    asset_id: str,
    runtime: ToolRuntime[AgentContext],
    rule: str | None = None,
    relative_to: str | None = None,
    moving: str | None = None,
) -> str:
    """Propose up to 5 valid poses for one asset in the room, given what is already in the plan.

    Rules: against_wall (back to a wall), corner, center (open floor; with relative_to: in front of that
    piece, or on top of it for table lamps), facing (front toward relative_to), beside (next to relative_to).
    Every returned pose already passes validation against the room and the placed pieces.

    Args:
        room_id: Room id with a started plan.
        asset_id: Catalog asset id from search_assets.
        rule: One of against_wall, corner, center, facing, beside. Defaults to the asset's own placement.
        relative_to: instance_id already in the plan; required for facing and beside.
        moving: instance_id being moved, so its current pose is not treated as an obstacle.
    """

    def run() -> dict:
        plan = load_plan(room_id)
        a = asset(asset_id)
        r = rule or DEFAULT_RULE.get(a.get("placement") or "", "center")
        if r not in RULES:
            raise ToolError(f"rule must be one of {', '.join(RULES)}")
        geo = room_geometry(load_room(room_id, runtime.context.block_id))
        out = _propose(geo, a, r, relative_to, placed_pairs(plan, skip=moving))
        stored = FILES.read_json(poses_path(room_id)) or {}
        stored[asset_id] = {"rule": r, "relative_to": relative_to, "moving": moving, "poses": out["poses"]}
        FILES.write_json(poses_path(room_id), stored)
        poses = [{k: v for k, v in p.items() if k != "score"} for p in out["poses"]]
        return {"asset_id": asset_id, "rule": r, "poses": poses, "note": out["note"]}

    return _run(run)


@tool(parse_docstring=True)
def place_asset(
    room_id: str,
    asset_id: str,
    pose_id: str,
    runtime: ToolRuntime[AgentContext],
    reason: str | None = None,
    instance_id: str | None = None,
) -> str:
    """Put an asset into the plan at a pose from the latest propose_poses call for that asset.

    Args:
        room_id: Room id with a started plan.
        asset_id: The asset the pose was proposed for.
        pose_id: Pose id from propose_poses, e.g. p1.
        reason: Why it goes there, 12 words or fewer. Defaults to the pose's reason.
        instance_id: To move a piece already in the plan, its instance_id. Omit to add a new piece.
    """

    def run() -> dict:
        plan = load_plan(room_id)
        entry = (FILES.read_json(poses_path(room_id)) or {}).get(asset_id)
        pose = next((p for p in (entry or {}).get("poses", []) if p["pose_id"] == pose_id), None)
        if pose is None:
            raise ToolError(f"no pose {pose_id} for {asset_id}; call propose_poses for it first")
        if instance_id is not None and instance_id not in {o["instance_id"] for o in plan["objects"]}:
            raise ToolError(f"{instance_id} is not in the plan")
        obj = {
            "instance_id": instance_id or next_instance_id(plan),
            "asset_id": asset_id,
            "x": pose["x"],
            "y": pose["y"],
            "z": pose["z"],
            "rot_deg": pose["rot_deg"],
            "reason": short_reason(reason or pose["reason"]),
        }
        # The plan may have changed since the proposal: check this piece against it again.
        geo = room_geometry(load_room(room_id, runtime.context.block_id))
        others = placed_pairs(plan, skip=obj["instance_id"])
        conflicts = object_conflicts(geo, obj, asset(asset_id), others, check_reverse=True)
        if conflicts:
            details = "; ".join(c["detail"] for c in conflicts)
            raise ToolError(f"{pose_id} no longer fits ({details}); call propose_poses again")
        plan["objects"] = [o for o in plan["objects"] if o["instance_id"] != obj["instance_id"]] + [obj]
        plan["status"], plan["conflicts"] = "draft", []
        save_plan(plan)
        return {"placed": obj["instance_id"], "plan": plan_view(plan)}

    return _run(run)


@tool(parse_docstring=True)
def remove_asset(room_id: str, instance_id: str) -> str:
    """Remove one piece from the room's plan.

    Args:
        room_id: Room id with a started plan.
        instance_id: The piece to remove, e.g. o3.
    """

    def run() -> dict:
        plan = load_plan(room_id)
        if instance_id not in {o["instance_id"] for o in plan["objects"]}:
            raise ToolError(f"{instance_id} is not in the plan")
        plan["objects"] = [o for o in plan["objects"] if o["instance_id"] != instance_id]
        plan["status"], plan["conflicts"] = "draft", []
        save_plan(plan)
        return {"removed": instance_id, "plan": plan_view(plan)}

    return _run(run)


@tool(parse_docstring=True)
def validate_layout(room_id: str, runtime: ToolRuntime[AgentContext]) -> str:
    """Check the room's plan for conflicts: outside_room, overlap, blocks_opening, clearance.

    Saves the conflicts into the plan. A plan with conflicts cannot be committed.

    Args:
        room_id: Room id with a started plan.
    """

    def run() -> dict:
        plan = _validate(load_plan(room_id), load_room(room_id, runtime.context.block_id), asset)
        save_plan(plan)
        return {"status": plan["status"], "objects": len(plan["objects"]), "conflicts": plan["conflicts"]}

    return _run(run)


@tool(parse_docstring=True)
async def commit_placement(room_id: str, runtime: ToolRuntime[AgentContext]) -> str:
    """Place the room's validated plan in the shared world, so every player in the house sees it.

    Re-validates first and refuses a plan with conflicts.

    Args:
        room_id: Room id with a plan that passed validate_layout.
    """
    ctx = runtime.context

    async def run() -> dict:
        room = load_room(room_id, ctx.block_id)
        plan = _validate(load_plan(room_id), room, asset)
        if plan["conflicts"]:
            save_plan(plan)
            raise ToolError(
                f"plan has {len(plan['conflicts'])} conflicts; fix them first: {plan['conflicts']}"
            )
        if not plan["objects"]:
            raise ToolError("plan is empty")
        sent = {**plan, "status": "placed"}
        if settings.FAKE_PLACEMENT:  # AGENTS.md section 12: hand-authored plan, same ids
            fake = packs._read_pack(f"{packs.pack_root(ctx.block_id)}/plans/{room['label']}.json")
            sent = {
                **fake,
                "plan_id": plan["plan_id"],
                "listing_id": plan["listing_id"],
                "room_id": room_id,
                "status": "placed",
                "conflicts": [],
            }
        result = await game_api.post_plan(ctx.block_id, ctx.player_id, sent)
        plan["status"] = "placed"
        save_plan(plan)
        return {"plan_id": plan["plan_id"], "placed": result.get("placed", len(sent["objects"]))}

    return await _arun(run)


# --- game (Lane D internal API) ----------------------------------------------------------------------


@tool(parse_docstring=True)
async def focus_listing(listing_id: str, runtime: ToolRuntime[AgentContext]) -> str:
    """Put a beacon on a house so the player (and everyone in the block) can find it.

    Args:
        listing_id: Listing id of the chosen home.
    """
    ctx = runtime.context

    async def run() -> dict:
        if packs.get_listing(listing_id) is None:
            raise ToolError(f"no listing {listing_id}")
        await game_api.post_focus(ctx.block_id, ctx.player_id, listing_id)
        return {"focused": listing_id}

    return await _arun(run)


@tool
async def get_player_context(runtime: ToolRuntime[AgentContext]) -> str:
    """Where the player is now: zone ("block" or the listing_id of the house they are in) and position."""
    ctx = runtime.context

    async def run() -> dict:
        p = await game_api.get_player(ctx.block_id, ctx.player_id)
        zone = p.get("zone", "block")
        return {**p, "block_id": ctx.block_id, "in_house": zone if zone != "block" else None}

    return await _arun(run)


# --- tool sets per agent (AGENTS.md section 10 table) ------------------------------------------------

CONCIERGE_TOOLS = [get_listing, focus_listing, get_player_context]
AUDITOR_TOOLS = [get_listing, get_house_graph, get_rooms, get_audit]
ROOM_SCOUT_TOOLS = [get_rooms, get_house_graph, get_room_geometry]
CURATOR_TOOLS = [search_assets]
LAYOUT_TOOLS = [
    get_room_geometry,
    start_plan,
    propose_poses,
    place_asset,
    remove_asset,
    validate_layout,
    commit_placement,
]
ALL_TOOLS = list(
    {
        t.name: t
        for t in (*CONCIERGE_TOOLS, *AUDITOR_TOOLS, *ROOM_SCOUT_TOOLS, *CURATOR_TOOLS, *LAYOUT_TOOLS)
    }.values()
)
