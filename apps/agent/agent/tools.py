"""Agent tools (AGENTS.md section 10). Lane B owns this file.

Every number comes from deterministic code: the model picks intents (rule, pose_id), never coordinates.
Tools read session_id / player_id / block_id from runtime context (agent/context.py), never from the model.

The working plan for a room lives in the agent's virtual filesystem (Deep Agents StateBackend, one per
thread) as a Placement plan (contracts/placement_plan.schema.json):

  /rooms/<room_id>.json          get_room_geometry: the computed geometry summary
  /plans/<room_id>.json          the draft plan; only the tools below write it (PLAN_PERMISSIONS)
  /plans/<room_id>.poses.json    last propose_poses result per asset, so place_asset can take a pose_id
  /plans/<room_id>.picks.json    save_picks: the asset-curator's choices, read by the layout planner
  /plans/<room_id>.history.json  place_picks: earlier layouts (anchor pose, assets), so regenerate differs
  /search/results.json           search_listings: ranked matches with evidence, read by the auditors

The model may read every file. Built-in write_file/edit_file are denied on /plans/** so coordinates are
only ever written by place_asset. Expected failures come back as {"error": ...} so the model can recover.
"""

import json
import re
import uuid
from collections.abc import Callable
from typing import Any, Literal, Protocol

import httpx
from deepagents import FilesystemPermission
from deepagents.backends import StateBackend
from langchain.tools import ToolRuntime, tool
from pydantic import BaseModel, Field
from shapely.geometry import Point, Polygon

import settings
from agent import game_api, plain, search
from agent.context import AgentContext
from data import packs
from layout import DEFAULT_RULE, RULES, room_geometry
from layout import propose_poses as _propose
from layout import validate_layout as _validate
from layout.auto import auto_place_all
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


def picks_path(room_id: str) -> str:
    return f"/plans/{room_id}.picks.json"


def history_path(room_id: str) -> str:
    return f"/plans/{room_id}.history.json"


SEARCH_PATH = "/search/results.json"


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


def rooms_of(listing_id: str) -> list[dict]:
    try:
        return packs.get_rooms(listing_id)
    except KeyError as e:
        raise ToolError(f"{listing_id} has no scanned interior on a built block") from e


def load_room(room_id: str) -> dict:
    for r in rooms_of(listing_of(room_id)):
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
    _track_uncommitted(plan)


# Plans written this turn but not yet committed, per thread (= session) and room. The runner commits a
# validated one left behind when the planner reports without calling commit_placement (it happened: the
# player is told "the playroom is set up" and the old layout stays in the world).
UNCOMMITTED: dict[str, dict[str, dict]] = {}


def _thread_id() -> str | None:
    try:
        from langgraph.config import get_config

        return get_config().get("configurable", {}).get("thread_id")
    except RuntimeError:  # outside a graph run (unit tests)
        return None


def _track_uncommitted(plan: dict) -> None:
    thread = _thread_id()
    if thread is None:
        return
    rooms = UNCOMMITTED.setdefault(thread, {})
    if plan.get("status") == "placed" or not plan.get("objects"):
        rooms.pop(plan["room_id"], None)
    else:
        rooms[plan["room_id"]] = json.loads(json.dumps(plan))


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


def _no_score(pose: dict) -> dict:
    return {k: v for k, v in pose.items() if k != "score"}


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
def get_rooms(listing_id: str) -> str:
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
            for r in rooms_of(listing_id)
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

    Each warn or conflict finding carries `say`, a plain sentence in square feet to quote to the buyer.

    Args:
        listing_id: Listing id.
    """
    return _run(lambda: plain.with_sentences(packs.get_audit(listing_id), packs.get_listing(listing_id)))


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


class Need(BaseModel):
    need: Literal[
        "single_story", "no_pool", "fenced_yard", "garage", "small_bedroom_near_primary", "elementary_school"
    ]
    weight: Literal["high", "medium", "low"] = "medium"


@tool(parse_docstring=True)
def search_listings(
    needs: list[Need],
    runtime: ToolRuntime[AgentContext],
    status: Literal["for_sale", "for_rent"] | None = None,
    property_type: str | None = None,
    max_price_usd: float | None = None,
    max_rent_usd_month: float | None = None,
    min_beds: int | None = None,
    min_baths: float | None = None,
    limit: int = 3,
) -> str:
    """Search homes the player can walk into on this block: hard criteria filter, soft needs rank.

    Only listings with a scanned interior on the player's block are ranked. `market` counts the whole
    listings index under the same hard criteria (passed_hard, median price and price per m2 / sq ft), and
    each match has price_per_sqft_usd and vs_market_pct against that median. A field the data does not
    have is reported as passed=null (unknown), never as failed. Saved to /search/results.json for auditors.

    Args:
        needs: Soft needs with weights, e.g. [{"need": "single_story", "weight": "high"}].
        status: for_sale or for_rent.
        property_type: e.g. single_family.
        max_price_usd: Budget for a purchase.
        max_rent_usd_month: Budget for a rental.
        min_beds: Minimum bedrooms.
        min_baths: Minimum bathrooms.
        limit: Maximum matches (1 to 5).
    """
    hard = {
        "status": status,
        "property_type": property_type,
        "max_price_usd": max_price_usd,
        "max_rent_usd_month": max_rent_usd_month,
        "min_beds": min_beds,
        "min_baths": min_baths,
    }

    def run() -> dict:
        block = runtime.context.block_id
        every = packs.listings()
        homes = [x for x in every if x.get("block_id") == block and x.get("has_interior")]
        if not homes:
            raise ToolError(f"no listings with a scanned interior on block {block}")
        hard_set = {k: v for k, v in hard.items() if v is not None}
        out = search.search(
            homes,
            hard_set,
            [n.model_dump() if isinstance(n, BaseModel) else dict(n) for n in needs],
            _house_data,
            limit=max(1, min(int(limit), 5)),
        )
        # Whole-index context as counts and medians only: the model never sees more than the walkable matches.
        out["market"] = search.market(every, hard_set)
        search.add_pricing(
            out["matches"], {x["id"]: x for x in homes}, out["market"]["median_price_per_m2_usd"]
        )
        for m in out["matches"]:
            m["contradictions"] = _contradictions(m["listing_id"])
        FILES.write_json(SEARCH_PATH, out)
        return out

    return _run(run)


def _contradictions(listing_id: str) -> list[str]:
    """Audit `say` sentences for one home, conflicts first; [] when every check is consistent or no audit."""
    try:
        audit = plain.with_sentences(packs.get_audit(listing_id), packs.get_listing(listing_id))
    except Exception:  # noqa: BLE001  a house without an audit file has nothing to report
        return []
    rank = {"conflict": 0, "warn": 1}
    flagged = sorted((f for f in audit["findings"] if "say" in f), key=lambda f: rank.get(f["severity"], 2))
    return [f["say"] for f in flagged]


def _house_data(listing_id: str) -> tuple[dict | None, list[dict] | None]:
    try:
        return packs.get_house_graph(listing_id), packs.get_rooms(listing_id)
    except (KeyError, packs.PackFileMissing):
        return None, None


class Pick(BaseModel):
    asset_id: str
    count: int = Field(default=1, ge=1, le=4)
    role: str = Field(description="Why it is there, 12 words or fewer; shown in the world as its reason")
    priority: int = Field(ge=1, le=3, description="1 = must have, 3 = nice to have")
    rule: Literal["against_wall", "corner", "center", "facing", "beside"] | None = Field(
        default=None, description="Placement rule from the skill; default: the asset's own placement"
    )
    relative_to: str | None = Field(
        default=None, description="asset_id of another pick this one faces, sits beside or in front of"
    )
    alternates: list[str] = Field(
        default_factory=list, max_length=2, description="Runner-up asset_ids from the same search"
    )


SEATING = ("sofa", "sectional", "loveseat")
# The priority-1 pieces of each furnishing skill (the tables in skills/<name>/SKILL.md), as groups of
# categories: one pick from every group. A skill with several lists (furnish-for-play: nerf war or
# playroom) is met by any one of them. save_picks refuses picks that miss a group, unless nothing in the
# catalog for that group fits the room. A wall-mounted TV stands in for the TV stand (the skills say so).
REQUIRED: dict[str, list[list[tuple[str, ...]]]] = {
    "furnish-living-room": [[("tv_stand",), ("tv",), SEATING, ("coffee_table",)]],
    "furnish-game-room": [[("tv_stand",), ("tv",), SEATING, ("coffee_table",)]],
    "furnish-open-plan": [
        [("tv_stand",), ("tv",), SEATING, ("coffee_table",), ("dining_table",), ("dining_chair",)]
    ],
    "furnish-for-play": [[SEATING, ("bookshelf",)], [("play_mat",), ("toy_storage",)]],
    "furnish-bedroom": [[("bed",), ("nightstand",)]],
    "furnish-kids-room": [[("bed",), ("toy_storage",)]],
    "furnish-nursery": [[("crib",), ("dresser",)]],
    "furnish-home-office": [[("desk",), ("office_chair",)]],
    "furnish-dining-room": [[("dining_table",), ("dining_chair",)]],
}
FurnishSkill = Literal[
    "furnish-living-room",
    "furnish-game-room",
    "furnish-open-plan",
    "furnish-for-play",
    "furnish-bedroom",
    "furnish-kids-room",
    "furnish-nursery",
    "furnish-home-office",
    "furnish-dining-room",
]
_SKILL_RE = re.compile(r"furnish-[a-z]+(?:-[a-z]+)*")


def _brief_skill(room_id: str) -> str | None:
    """The furnishing skill the concierge's brief names, when save_picks was not told."""
    try:
        r = StateFiles().backend.read(f"/briefs/{listing_of(room_id)}.md", offset=0, limit=10_000)
    except Exception:  # no graph run (tests) or no brief
        return None
    if r.error or r.file_data is None:
        return None
    found = [s for s in _SKILL_RE.findall(r.file_data["content"]) if s in REQUIRED]
    return found[0] if found else None


def missing_required(skill: str, chosen: list[dict], fits: Callable[[dict], bool]) -> list[str]:
    """Required category groups of `skill` the chosen assets miss (empty when one of its lists is met).
    A group is waived when no catalog asset in it fits the room."""
    cats = {a["category"] for a in chosen}
    wall_tv = any(a["category"] == "tv" and a.get("placement") == "wall" for a in chosen)

    def met(group: tuple[str, ...]) -> bool:
        if cats & set(group) or (group == ("tv_stand",) and wall_tv):
            return True
        return not any(a["category"] in group and fits(a) for a in packs.catalog())

    best: list[str] | None = None
    for option in REQUIRED.get(skill, []):
        miss = [" or ".join(g) for g in option if not met(g)]
        if best is None or len(miss) < len(best):
            best = miss
    return best or []


@tool(parse_docstring=True)
def save_picks(room_id: str, items: list[Pick], skill: FurnishSkill | None = None) -> str:
    """Save the chosen furniture for a room to /plans/<room_id>.picks.json, where the layout planner reads it.

    Checks every asset_id (and alternate) is in the catalog and can fit the room, that relative_to names
    another pick, and that every required (priority 1) piece of the skill is there: a living room without a
    TV is refused. Replaces earlier picks and starts a fresh regenerate history for the room.

    Args:
        room_id: Room the picks are for.
        items: The pieces, most important first.
        skill: The furnishing skill the brief names, e.g. furnish-living-room. Always pass it.
    """

    def run() -> dict:
        if not items:
            raise ToolError("items must not be empty")
        geo = room_geometry(load_room(room_id))
        longest_wall = max((s.length for s in geo.walls), default=0.0)
        minx, minz, maxx, maxz = geo.poly.bounds
        longest_side = max(maxx - minx, maxz - minz)

        def too_big(a: dict) -> str | None:
            w, d = a["size_m"]["w"], a["size_m"]["d"]
            on_wall = a.get("placement") in ("wall", "corner")
            limit, what = (longest_wall, "longest free wall") if on_wall else (longest_side, "room")
            if (w if on_wall else max(w, d)) > limit:
                return f"{a['id']} is {w:.2f} m wide; the {what} is {limit:.2f} m. Pick a smaller one."
            return None

        def check_fits(asset_id: str) -> dict:
            a = asset(asset_id)
            why = too_big(a)
            if why:
                raise ToolError(why)
            return a

        picks = [raw if isinstance(raw, Pick) else Pick.model_validate(raw) for raw in items]
        ids = {p.asset_id for p in picks}
        saved = []
        for p in picks:
            a = check_fits(p.asset_id)
            for alt in p.alternates:
                check_fits(alt)
            if p.relative_to and p.relative_to not in ids:
                raise ToolError(f"{p.asset_id}: relative_to {p.relative_to} is not one of the picks")
            saved.append(
                {**p.model_dump(), "name": a["name"], "category": a["category"], "size_m": a["size_m"]}
            )
        which = skill or _brief_skill(room_id)
        if which:
            chosen = [asset(p.asset_id) for p in picks]
            miss = missing_required(which, chosen, lambda a: too_big(a) is None)
            if miss:
                raise ToolError(
                    f"{which} requires {', '.join(miss)}, and the picks have none. search_assets for "
                    "it and call save_picks again with every piece."
                )
        out = {"room_id": room_id, "items": saved}
        FILES.write_json(picks_path(room_id), out)
        FILES.write_json(history_path(room_id), [])
        return {"path": picks_path(room_id), "items": len(saved)}

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
        summary = room_geometry(load_room(room_id)).summary()
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
        load_room(room_id)  # fail early on a bad id
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
def place_picks(
    room_id: str,
    runtime: ToolRuntime[AgentContext],
    new_layout: bool = False,
    new_furniture: bool = False,
) -> str:
    """Lay out every piece in /plans/<room_id>.picks.json in one pass, as a new plan (replaces the old one).

    Each piece takes the best valid pose for its rule and relative_to, in priority order. Returns each
    placed piece with up to 3 alternative pose_ids (use place_asset with instance_id to switch one), and the
    pieces dropped because no pose fits, with the reason. Regenerate: new_layout puts the anchor piece (e.g.
    the TV stand) somewhere clearly different from earlier layouts; new_furniture swaps in the curator's
    alternates not used before. Both may be set.

    Args:
        room_id: Room with saved picks.
        new_layout: Regenerate: a different arrangement from the earlier layouts.
        new_furniture: Regenerate: different furniture (the picks' unused alternates).
    """

    def run() -> dict:
        picks = FILES.read_json(picks_path(room_id))
        if not picks or not picks.get("items"):
            raise ToolError(f"no picks for {room_id}; the asset-curator saves them with save_picks")
        history = FILES.read_json(history_path(room_id)) or []
        room = load_room(room_id)
        result = auto_place_all(
            room_geometry(room),
            picks["items"],
            asset,
            avoid=[h["anchor"] for h in history if h.get("anchor")] if new_layout else None,
            used={a for h in history for a in h.get("assets", [])} if new_furniture else None,
            swap=new_furniture,
            reason=short_reason,
        )
        plan = {
            "plan_id": f"p_{uuid.uuid4().hex[:8]}",
            "listing_id": listing_of(room_id),
            "room_id": room_id,
            "status": "draft",
            "objects": result["objects"],
            "conflicts": [],
        }
        if plan["objects"]:
            plan = _validate(plan, room, asset)
        save_plan(plan)
        FILES.write_json(poses_path(room_id), result["poses"])
        history.append(
            {
                "plan_id": plan["plan_id"],
                "anchor": result["anchor"],
                "assets": sorted({o["asset_id"] for o in plan["objects"]}),
            }
        )
        FILES.write_json(history_path(room_id), history)
        return {
            "plan_id": plan["plan_id"],
            "status": plan["status"],
            "layout_number": len(history),
            "placed": result["placed"],
            "dropped": result["dropped"],
            "swapped": result["swapped"],
            "notes": result["notes"],
            "conflicts": plan["conflicts"],
        }

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
    Every returned pose already passes validation against the room and the placed pieces. When none does,
    "blocked" lists poses that fit the room but not the pieces in their blocked_by; place_asset on one
    takes those pieces out of the plan first, so you can put them back elsewhere.

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
        geo = room_geometry(load_room(room_id))
        out = _propose(geo, a, r, relative_to, placed_pairs(plan, skip=moving))
        stored = FILES.read_json(poses_path(room_id)) or {}
        blocked = out.get("blocked", [])
        stored[asset_id] = {
            "rule": r,
            "relative_to": relative_to,
            "moving": moving,
            "poses": out["poses"] + blocked,
        }
        FILES.write_json(poses_path(room_id), stored)
        result = {"asset_id": asset_id, "rule": r, "poses": [_no_score(p) for p in out["poses"]]}
        if blocked:
            result["blocked"] = [_no_score(p) for p in blocked]
        return {**result, "note": out["note"]}

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

    A blocked pose (b1, b2, ...) first removes the pieces in its blocked_by and returns them in "removed":
    put each back with propose_poses and place_asset (new instance), or leave it out and say so.

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
        blockers = set(pose.get("blocked_by", [])) - {instance_id}
        removed = [
            {"instance_id": o["instance_id"], "asset_id": o["asset_id"], "name": asset(o["asset_id"])["name"]}
            for o in plan["objects"]
            if o["instance_id"] in blockers
        ]
        plan["objects"] = [o for o in plan["objects"] if o["instance_id"] not in blockers]
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
        geo = room_geometry(load_room(room_id))
        others = placed_pairs(plan, skip=obj["instance_id"])
        conflicts = object_conflicts(geo, obj, asset(asset_id), others, check_reverse=True)
        if conflicts:
            details = "; ".join(c["detail"] for c in conflicts)
            raise ToolError(f"{pose_id} no longer fits ({details}); call propose_poses again")
        plan["objects"] = [o for o in plan["objects"] if o["instance_id"] != obj["instance_id"]] + [obj]
        plan["status"], plan["conflicts"] = "draft", []
        save_plan(plan)
        out = {"placed": obj["instance_id"], "plan": plan_view(plan)}
        return {**out, "removed": removed} if removed else out

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
        plan = _validate(load_plan(room_id), load_room(room_id), asset)
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
        plan = load_plan(room_id)
        try:
            return await send_plan(ctx, plan)
        finally:
            save_plan(plan)  # with its conflicts, or status placed

    return await _arun(run)


async def send_plan(ctx: AgentContext, plan: dict) -> dict:
    """Re-validate a plan and post it to the game. Updates `plan` in place (conflicts, status placed).
    Shared by commit_placement and the runner's safety net for a plan left uncommitted."""
    room = load_room(plan["room_id"])
    plan.update(_validate(plan, room, asset))
    if plan["conflicts"]:
        raise ToolError(f"plan has {len(plan['conflicts'])} conflicts; fix them first: {plan['conflicts']}")
    if not plan["objects"]:
        raise ToolError("plan is empty")
    sent = {**plan, "status": "placed"}
    if settings.FAKE_PLACEMENT:  # AGENTS.md section 12: hand-authored plan, same ids
        fake = packs._read_pack(f"{packs.pack_root(ctx.block_id)}/plans/{room['label']}.json")
        sent = {
            **fake,
            "plan_id": plan["plan_id"],
            "listing_id": plan["listing_id"],
            "room_id": plan["room_id"],
            "status": "placed",
            "conflicts": [],
        }
    result = await game_api.post_plan(ctx.block_id, ctx.player_id, sent, room_label=room.get("label"))
    plan["status"] = "placed"
    return {"plan_id": plan["plan_id"], "placed": result.get("placed", len(sent["objects"]))}


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


def _room_to_clear(listing_id: str, room: str, player: dict | None) -> dict:
    """The one room a clear request means: a room id, "here" (where the player stands) or a label.

    A label shared by several rooms (two bedrooms) means the one this session furnished, else the one the
    player stands in; otherwise the caller has to say which."""
    rooms = rooms_of(listing_id)
    by_id = {r["id"]: r for r in rooms}
    if room in by_id:
        return by_id[room]

    def standing_in(candidates: list[dict]) -> dict | None:
        if not player or player.get("zone") != listing_id:
            return None
        at = Point(player["x"], player["z"])
        return next((r for r in candidates if Polygon(r["polygon"]).buffer(0.05).contains(at)), None)

    key = room.strip().lower().replace("-", " ").replace("_", " ")
    if key in ("here", "this room", "this", "current room"):
        found = standing_in(rooms)
        if found is None:
            raise ToolError("the player is not standing in a room of this house; name the room instead")
        return found
    label = "_".join(key.removeprefix("the ").split())
    matches = [r for r in rooms if r["label"] == label]
    if not matches:
        labels = sorted({r["label"] for r in rooms})
        raise ToolError(f"no room {room!r} in {listing_id}; its rooms are: {', '.join(labels)}")
    if len(matches) > 1:
        furnished = [r for r in matches if (FILES.read_json(plan_path(r["id"])) or {}).get("objects")]
        here = standing_in(matches)
        if len(furnished) == 1:
            return furnished[0]
        if here is not None:
            return here
        options = ", ".join(f"{r['id']} ({r['area_m2']} m2)" for r in matches)
        raise ToolError(
            f"{len(matches)} rooms are labelled {label}: {options}. Pass the room id of the one meant"
        )
    return matches[0]


@tool(parse_docstring=True)
async def clear_furniture(
    listing_id: str, runtime: ToolRuntime[AgentContext], room: str | None = None
) -> str:
    """Remove furniture from the shared world: everything in one room, or in the whole house.

    Pieces placed by hand go too, for every player in the house. Returns how many pieces were removed and
    from which rooms; removed 0 means there was nothing to remove.

    Args:
        listing_id: The house, e.g. the in_house value of the [Player: ...] line.
        room: The room to empty: a label ("living_room"), a room id ("hou_236595179.r3"), or "here" for
            the room the player stands in. Leave it out to empty the whole house.
    """
    ctx = runtime.context

    async def run() -> dict:
        rooms = {r["id"]: r for r in rooms_of(listing_id)}
        target = None
        if room is not None:
            needs_player = room not in rooms
            player = await game_api.get_player(ctx.block_id, ctx.player_id) if needs_player else None
            target = _room_to_clear(listing_id, room, player)
        result = await game_api.clear_furniture(
            ctx.block_id,
            ctx.player_id,
            listing_id,
            room_id=target["id"] if target else None,
            room_label=target["label"] if target else None,
        )
        # Keep this session's plans in step with the world, so a later edit does not bring pieces back.
        for room_id in [target["id"]] if target else list(rooms):
            plan = FILES.read_json(plan_path(room_id))
            if plan and plan.get("objects"):
                save_plan({**plan, "objects": [], "conflicts": [], "status": "draft"})
        return {
            "scope": "room" if target else "house",
            "room_id": target["id"] if target else None,
            "label": target["label"] if target else None,
            "removed": result.get("removed", 0),
            "rooms": [
                {**r, "label": rooms.get(r["room_id"], {}).get("label")} for r in result.get("rooms", [])
            ],
        }

    return await _arun(run)


# --- tool sets per agent (AGENTS.md section 10 table) ------------------------------------------------

CONCIERGE_TOOLS = [search_listings, get_listing, focus_listing, get_player_context, clear_furniture]
AUDITOR_TOOLS = [get_listing, get_house_graph, get_rooms, get_audit]
ROOM_SCOUT_TOOLS = [get_rooms, get_house_graph, get_room_geometry]
CURATOR_TOOLS = [search_assets, save_picks]
LAYOUT_TOOLS = [
    place_picks,
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
