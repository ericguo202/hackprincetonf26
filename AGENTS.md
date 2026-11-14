# AGENTS.md — Built World Sandbox (HackPrinceton Fall 2026)

Instructions for every coding agent working in this repo. Read this whole file before writing code. Humans own the schedule (see the team schedule doc); this file owns the technical rules.

## 1. What we are building

A browser game built from real CoStar, Matterport and Vexcel data. The player walks a real neighborhood in third person, enters a house that is for sale or for rent, and talks to an agent that (a) finds a home that fits their needs and (b) furnishes rooms with 3D objects.

Hero user: a home buyer or renter. Hero prompt: "We're new parents with an infant. Find us a home." Houses come first. Daycare and other commercial uses are stretch work after the house loop is done.

The demo loop every lane serves:

1. Player walks the block and sees for-sale / for-rent markers.
2. Player tells the agent what they need. The agent plans, queries the data, ranks listings, explains its pick with cited data fields, and marks the house in the world.
3. Player walks in. Player says "furnish this room as a nursery" (or living room, bedroom).
4. Subagents pick the room, pick assets from the library, compute a layout, validate it, and place it.
5. Furniture appears in the room. The player can bump into it. Each object has a reason label.

Tracks: Entertainment (main), CoStar "Inside and Out", LangChain (Deep Agents), Amazon (Best Practical AI Innovation). Nothing else.

## 2. Rules for coding agents

1. **Stay in your lane's directories** (section 4). To change another lane's code, leave a note in `HANDOFF.md` for that lane's human instead of editing.
2. **`contracts/` is law.** Never change a schema, an event name or a fixture on your own. Propose the change to your human; all four humans agree before it merges. A contract change bumps `contracts/VERSION` and updates fixtures in the same commit.
3. **Build against fixtures first.** Every lane must run with `USE_FIXTURES=1` and no other lane's code working. Swap to real implementations behind the same interface.
4. **The model decides and explains; deterministic code computes every number.** No arithmetic, geometry, areas, distances, scores or coordinates produced by the LLM. Those come from tools.
5. **The agent must reason, plan and act.** Not a chatbot, not a summarizer. Every user request goes: plan (todo list) -> tool calls that fetch real data -> deterministic computation -> an action in the world -> self-check -> a short answer citing the data fields used. A response that only restates data is a bug.
6. **`main` always runs.** Small commits, merge at least every 2 hours. Before every commit run the lane's checks (section 10). Never commit a red build.
7. **One git worktree per coding agent**, branch `lane-<a|b|c|d>/<topic>`. Rebase on `main` before merging.
8. **Secrets** live in `.env` (git-ignored). Never print keys, never send them to the browser, never put them in prompts or traces.
9. **Matterport keys are admin keys that can buy things.** All Matterport calls go through `apps/server/data/matterport_client.py`, which rejects any GraphQL mutation. Server side only.
10. **No branded content** in the world or assets (no real logos, no licensed characters). Every asset has a recorded license.
11. **Do not add scope.** If a task is not in your lane brief (section 8), ask your human. Stretch items are listed and stay off until the human says go.
12. When blocked for more than 15 minutes on another lane, stub it against the fixture, write the blocker in `HANDOFF.md`, and keep moving.

## 3. Stack

| Part     | Choice                                                                                                                                               |
| -------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| Web app  | Vite + React + TypeScript, React Three Fiber (three.js), `@react-three/rapier` physics, Zustand store                                                |
| Server   | Python 3.11+, FastAPI, one WebSocket per session, `uv` for packages                                                                                  |
| Agent    | LangChain Deep Agents (`deepagents`, Python), LangSmith tracing                                                                                      |
| Geometry | `shapely` for room polygons, overlap and clearance checks                                                                                            |
| Data     | Pre-extracted JSON and `.glb` files in `data/` and `assets/`, served statically. Live sponsor APIs are optional and always behind the same interface |
| Model    | Set by `MODEL` env var as a `provider:model` string. Do not hard-code a model                                                                        |

Engine decision: web, not Unreal. It gives a shareable link, puts the agent UI on the same page, and coding agents can build and test it headlessly.

## 4. Repo layout and ownership

```
AGENTS.md
HANDOFF.md                 cross-lane notes and blockers (append only)
contracts/                 ALL lanes, change by agreement only
  VERSION
  listing.schema.json  room.schema.json  asset.schema.json
  placement_plan.schema.json  events.md
  fixtures/                one valid example per schema + a full event transcript
apps/web/
  src/world/               Lane A  scene, player, houses, physics, furniture rendering, sim toggles
  src/ui/                  Lane C  chat panel, plan view, listing cards, HUD
  src/bridge/              Lane C  WebSocket client, typed events, Zustand store
apps/server/
  agent/                   Lane B  deep agent, subagents, prompts, tool wrappers
  skills/                  Lane B  SKILL.md folders
  api/                     Lane C  FastAPI app, WebSocket, sessions, event emit
  layout/                  Lane C  pose proposals, validator
  data/                    Lane D  loaders, search, site metrics, Matterport client
data/                      Lane D  listings.json, houses/<id>/{rooms.json, interior.glb}, block/
assets/furniture/          Lane D  *.glb + catalog.json
evals/                     Lane B writes the runner, Lane D writes the cases
docs/                      Lane D  README assets, demo script, Devpost text
```

The shared seam inside the web app is the Zustand store in `src/bridge/store.ts`. Lane C writes to it from events; Lane A reads from it to render. Lane A never opens a socket. Lane C never touches three.js objects.

## 5. Conventions

- **Units:** meters and degrees everywhere in code and contracts. Square feet and dollars only in display strings.
- **World frame (block):** origin at the block center at ground level. +X east, +Y up, +Z south (north is -Z). Right-handed, three.js default.
- **House-local frame:** each house interior has its own frame: Y up, meters, origin at the converted interior mesh origin. Room polygons, openings and placement plans are all in house-local coordinates. The listing's `world.position` and `world.heading_deg` place the house group in the world. Furniture is parented to the house group, so a misaligned house never breaks a layout.
- **2D points** are `[x, z]` in the house-local floor plane.
- **Rotation:** `rot_deg` is rotation about +Y, counterclockwise seen from above. At `rot_deg = 0` an asset's front faces +Z.
- **Asset normalization (Lane D guarantees):** `.glb`, Y up, meters, origin at the bottom center of the footprint, front facing +Z, under 5 MB each.
- **Matterport source data is Z-up.** Convert once at ingest in `apps/server/data/`. Nothing downstream sees Z-up.
- **IDs:** listings `house_a`; rooms `house_a.r3`; assets `crib_01`; placed instances `o1`, `o2`; sessions are UUIDs.

## 6. Contracts

JSON Schemas in `contracts/` are the source of truth. The shapes below are the agreed v1. Generate TypeScript types and Pydantic models from the schemas; do not hand-write divergent copies.

### Listing

```json
{
  "id": "house_a",
  "address": "123 Example St, Houston, TX",
  "lat": 29.76,
  "lon": -95.36,
  "status": "for_sale",
  "price_usd": 425000,
  "rent_usd_month": null,
  "beds": 3,
  "baths": 2,
  "area_m2": 172,
  "stories": 1,
  "year_built": 1998,
  "property_type": "single_family",
  "features": ["fenced_yard", "garage"],
  "site": {
    "yard_area_m2": 140,
    "parking_spaces": 2,
    "street_class": "residential"
  },
  "has_interior": true,
  "interior_mesh": "data/houses/house_a/interior.glb",
  "world": {
    "position": [12.0, 0.0, -30.5],
    "heading_deg": 90,
    "door": [1.2, 0.0, 0.0]
  },
  "source": {
    "costar_id": "...",
    "matterport_model_id": "...",
    "vexcel": "..."
  }
}
```

`status` is `for_sale` or `for_rent`. `site` comes from Vexcel measurements. Only some listings have `has_interior: true`; search runs over all of them, furnishing only works where an interior exists. `world.door` is house-local.

### Room

```json
{
  "id": "house_a.r3",
  "listing_id": "house_a",
  "label": "bedroom",
  "label_confidence": 0.9,
  "floor": 0,
  "floor_y": 0.0,
  "height_m": 2.6,
  "area_m2": 14.2,
  "polygon": [
    [0, 0],
    [4.1, 0],
    [4.1, 3.5],
    [0, 3.5]
  ],
  "openings": [
    { "type": "door", "a": [1.0, 0], "b": [1.9, 0], "to_room": "house_a.r1" },
    { "type": "window", "a": [4.1, 1.0], "b": [4.1, 2.4], "to_room": null }
  ]
}
```

`label` is one of: `living_room, bedroom, primary_bedroom, kitchen, dining_room, bathroom, hallway, office, closet, garage, utility, other`. `area_m2` is computed from the polygon, never taken from a null API field. `to_room` gives adjacency.

### Asset (one entry of `assets/furniture/catalog.json`)

```json
{
  "id": "crib_01",
  "name": "Crib",
  "category": "crib",
  "tags": ["nursery", "infant", "sleep"],
  "file": "assets/furniture/crib_01.glb",
  "size_m": { "w": 0.75, "d": 1.35, "h": 0.95 },
  "placement": "wall",
  "clearance_m": { "front": 0.6 },
  "license": "CC0",
  "source": "Poly Haven"
}
```

`placement` is `wall`, `corner`, `free` or `on_surface`. `w` runs along local X, `d` along local Z. Minimum library for the MVP: couch, armchair, coffee table, TV + stand, rug, bookshelf, floor lamp, bed (double), nightstand, dresser, desk, chair, dining table, dining chair, crib, changing table, rocking chair, toy storage, plant.

### Placement plan

```json
{
  "plan_id": "p_01",
  "listing_id": "house_a",
  "room_id": "house_a.r3",
  "status": "validated",
  "objects": [
    {
      "instance_id": "o1",
      "asset_id": "crib_01",
      "x": 2.4,
      "y": 0.0,
      "z": 3.1,
      "rot_deg": 90,
      "reason": "Against the interior wall, away from the window"
    }
  ],
  "conflicts": []
}
```

`status` is `draft`, `validated` or `placed`. A conflict is `{"instance_id", "type", "detail"}` with `type` one of `outside_room, overlap, blocks_opening, clearance`. Every object needs a `reason` of 12 words or fewer; the world shows it as a label.

### WebSocket events

One socket per session at `/ws/{session_id}`. Envelope: `{"type": "...", "data": {...}}`. Full transcript fixture: `contracts/fixtures/events.session.jsonl`.

| Direction     | Type               | Data                                                                                     |
| ------------- | ------------------ | ---------------------------------------------------------------------------------------- |
| web -> server | `user.message`     | `{text, context: {listing_id, room_id, player_pos}}` (context fields null when outdoors) |
| web -> server | `user.decision`    | `{decision: "approve" or "reject", note}` answer to `agent.approval`                     |
| server -> web | `agent.todos`      | `{todos: [{content, status}]}` the agent's current plan                                  |
| server -> web | `agent.subagent`   | `{name, status: "started" or "finished", summary}`                                       |
| server -> web | `agent.tool`       | `{agent, tool, status, summary}` one line per tool call                                  |
| server -> web | `agent.message`    | `{text, final}` streamed assistant text                                                  |
| server -> web | `agent.approval`   | `{plan_id, question}` human-in-the-loop pause before placing                             |
| server -> web | `listings.results` | `{matches: [{listing_id, score, evidence: [{criterion, passed, value, source}]}]}`       |
| server -> web | `world.focus`      | `{listing_id}` show a beacon on that house                                               |
| server -> web | `placement.plan`   | a placement plan with `status: "placed"`                                                 |
| server -> web | `error`            | `{message}`                                                                              |

## 7. Agent architecture (Lane B owns, everyone must understand)

Built on Deep Agents. Docs: https://docs.langchain.com/oss/python/deepagents/overview, subagents: https://docs.langchain.com/oss/python/deepagents/subagents, customization: https://docs.langchain.com/oss/python/deepagents/customization. Check the docs for exact signatures before coding; do not guess parameters.

Facts from the docs that shape our design:

- `create_deep_agent(model=, tools=, system_prompt=, subagents=, skills=, backend=, checkpointer=, interrupt_on=, ...)`. Planning (todo list), a virtual filesystem and the `task` tool for delegation are built in.
- A subagent is a dict: `name`, `description`, `system_prompt` required; `tools`, `model`, `skills`, `response_format`, `interrupt_on` optional. Setting `tools` replaces the parent's tools.
- Subagents are isolated: they see only the task description, not the conversation. The orchestrator must put `listing_id`, `room_id`, the user's request and any constraints into the task description.
- The parent receives only the subagent's final result. Give each subagent a Pydantic `response_format` so that result is structured (needs `deepagents>=0.5.3`).
- Several `task` calls in one turn run in parallel.
- Skills are not inherited by custom subagents. Pass `skills=[...]` on each subagent that needs them. Skill files must exist in the backend before the agent is created.
- Human-in-the-loop (`interrupt_on`) requires a checkpointer.

### Agents

| Agent                         | Job                                                                                                 | Tools                                                                       | Returns                                                |
| ----------------------------- | --------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------- | ------------------------------------------------------ |
| `concierge` (main deep agent) | Understands the request, writes the plan, finds homes, delegates furnishing, gives the final answer | `search_listings`, `get_listing`, `get_rooms`, `focus_listing`, `task`      | Chat answer                                            |
| `room-scout` (subagent)       | Chooses which room to furnish from the request and the player's position                            | `get_rooms`, `get_room_geometry`                                            | `{room_id, label, why, usable_area_m2, constraints[]}` |
| `asset-curator` (subagent)    | Chooses the object list for that room and purpose from the library, sized to the room               | `search_assets`                                                             | `{items: [{asset_id, count, role, priority}]}`         |
| `layout-planner` (subagent)   | Turns the list into poses, validates, fixes conflicts, places                                       | `get_room_geometry`, `propose_poses`, `validate_layout`, `commit_placement` | `{plan_id, placed, dropped[], notes}`                  |
| `critic` (stretch)            | Re-checks the final plan and the home recommendation against the request                            | `validate_layout`, `get_listing`                                            | `{ok, issues[]}`                                       |

Furnishing order: `room-scout` first; then `asset-curator`; then `layout-planner`. If the user already named the room type, the concierge may run `room-scout` and `asset-curator` in parallel.

### Tools (deterministic, typed, short docstrings)

| Tool                                                  | Returns                                                                                                                                                  | Implemented in             |
| ----------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------- |
| `search_listings(criteria)`                           | Ranked matches with per-criterion evidence. `criteria` has hard filters (status, max price or rent, min beds, stories) and soft preferences with weights | `data/` (D)                |
| `get_listing(listing_id)`                             | Full listing                                                                                                                                             | `data/` (D)                |
| `get_rooms(listing_id)`                               | Rooms with label, area, floor, adjacency                                                                                                                 | `data/` (D)                |
| `get_room_geometry(room_id)`                          | Polygon, openings, height, free wall segments                                                                                                            | `data/` + `layout/` (D, C) |
| `search_assets(query, category, max_w, max_d)`        | Matching catalog entries                                                                                                                                 | `data/` (D)                |
| `propose_poses(room_id, asset_id, rule, relative_to)` | Up to 5 candidate poses. `rule`: `against_wall`, `corner`, `center`, `facing`, `beside`                                                                  | `layout/` (C)              |
| `validate_layout(plan)`                               | The plan with `conflicts` filled                                                                                                                         | `layout/` (C)              |
| `commit_placement(plan)`                              | Emits `placement.plan`, returns count placed. Refuses a plan with conflicts                                                                              | `api/` (C)                 |
| `focus_listing(listing_id)`                           | Emits `world.focus`                                                                                                                                      | `api/` (C)                 |

Lane B writes thin `@tool` wrappers in `agent/tools.py` that call these functions. Tools read `session_id` from the run's runtime context so they can emit events; never ask the model to pass it.

### What "reason, plan, act" means here

- **Home search:** turn needs into explicit criteria first (hard vs soft), then search, then open the top 2 to 3 candidates' rooms and site data to verify the things that matter (for an infant: a small bedroom adjacent to the primary bedroom, a single story or a bedroom on the main floor, a fenced yard, a quiet street class). Rank, explain with the evidence, call `focus_listing`. Ask at most one clarifying question, and only when a hard filter is missing (budget, buy vs rent).
- **Furnishing:** the layout planner loops propose -> validate -> fix. Maximum 3 rounds. If an item still conflicts, drop the lowest-priority item and say so. Never commit a plan with conflicts.
- **Edits:** "move the couch to the other wall", "swap the TV for a bookshelf" must change the existing plan, not start over.
- **Honesty:** when data is missing (no interior, unknown room label), say so and state the assumption. Never invent a listing, a room or an asset ID.

### Skills (`apps/server/skills/<name>/SKILL.md`)

`home-search` (needs -> criteria playbook, including the new-parents profile), `furnish-living-room`, `furnish-bedroom`, `furnish-nursery`. Each furnishing skill holds: required and optional objects with priorities, clearance rules (for example 0.75 m walkway, door swing clear, crib not under a window), and arrangement heuristics (couch faces TV, bed head against a wall). Judges read these files; keep them clear and cited where a rule comes from a source.

### Streaming and tracing

- Stream the run to the socket: todo updates -> `agent.todos`, each `task` delegation -> `agent.subagent`, each tool call -> `agent.tool`, text -> `agent.message`. Use LangGraph streaming with subgraph events on so subagent activity is visible; check the Deep Agents streaming page for the current arguments.
- One thread ID per session with a checkpointer, so follow-ups and edits keep context.
- LangSmith tracing on for every run (`LANGSMITH_TRACING=true`). The trace is part of the demo.
- Human-in-the-loop: `interrupt_on` for `commit_placement` -> `agent.approval`. Ships behind `HITL=1`; off by default until Gate 2 passes.

## 8. Lane briefs

Each lane lists its definition of done for the MVP. Times and gates are in the schedule doc.

### Lane A — World and play (`apps/web/src/world/`)

- Block scene: ground, streets, neighborhood buildings (Vexcel 3D tiles if available, otherwise extruded footprints), sky, sun.
- Third-person character with Rapier controller, camera follow, collision with buildings, walls and furniture.
- Listing markers: for-sale / for-rent signs from `listings.json`; beacon on `world.focus`.
- Enter a house: door trigger swaps the exterior shell for the interior `.glb`; wall colliders extruded from room polygons with gaps at door openings; floor plane per level.
- Current-room detection (point in polygon) written to the store so the UI can send `context.room_id`.
- Furniture: render `store.placement` plans as `.glb` instances parented to the house group, box colliders from `size_m`, reason labels, drop-in animation, before/after toggle.
- Day/night slider with real sun position (`suncalc`), then rain. Flood and destruction are stretch.
- Done when: with `USE_FIXTURES=1` a player can walk the block, enter `house_a`, and see the fixture placement plan as collidable furniture at 60 fps on a laptop.

### Lane B — Agent (`apps/server/agent/`, `apps/server/skills/`)

- `build_agent()` returning the concierge deep agent with the three subagents, tools and skills from section 7.
- CLI runner `python -m agent.cli "..." --listing house_a --room house_a.r3` for testing without the web app.
- Prompts that enforce plan -> act -> check, structured subagent outputs, the 3-round layout loop, edit handling.
- Event mapping from the agent stream to section 6 events (hand `stream_events()` to Lane C).
- LangSmith tracing; eval runner in `evals/run.py` over Lane D's cases.
- Done when: against fixture tools, the hero search prompt returns a ranked pick with evidence and a `world.focus`, and "furnish this room as a nursery" ends in a committed plan with zero conflicts, both visible as multi-step traces.

### Lane C — Server, bridge, layout and UI (`apps/server/api/`, `apps/server/layout/`, `apps/web/src/ui/`, `apps/web/src/bridge/`)

- FastAPI app, `/ws/{session_id}`, session store, `emit(session_id, event)`, static serving of `data/` and `assets/`.
- A mock mode that replays `contracts/fixtures/events.session.jsonl` so Lanes A and C can work before the agent exists.
- Bridge: typed event client and the Zustand store (`listings`, `focus`, `todos`, `subagents`, `messages`, `placement`, `player`).
- UI: chat panel with example chips, live plan (todos ticking), subagent activity feed, listing cards with evidence rows, approval prompt, link to the LangSmith trace.
- Layout module: `free_wall_segments`, `propose_poses`, `validate_layout` (inside room, no overlap, openings and walkway clear) with unit tests on the fixture room.
- Done when: typing a prompt in the web app drives the real agent, the plan and subagent feed update live, and a committed plan reaches the store.

### Lane D — Data, assets, evals and pitch (`data/`, `assets/`, `apps/server/data/`, `evals/`, `docs/`)

- Pick the hero house plus two more that have all three data layers. Hour-2 deliverable.
- `listings.json`: 20 or more real listings (search needs a pool), at least 3 with `has_interior`. `site` metrics from Vexcel.
- Per house: `interior.glb` (converted, Y-up, decimated) and `rooms.json` in house-local coordinates. If Matterport room data is not on our keys, trace polygons by hand for the hero house.
- Block data for Lane A: building footprints and heights, or the 3D tiles pointer.
- Asset library: the MVP list in section 6, normalized, with `catalog.json`.
- Data functions in `apps/server/data/` behind the tool signatures in section 7, with a fixtures mode.
- 8 to 10 eval cases with hand-checked expected results; README; Devpost text; demo script; backup video.
- Done when: every file validates against `contracts/` and the hero house walks end to end with real data.

## 9. Fallback switches

| Env var            | Effect                                                                                                                                                               | Use when                                                  |
| ------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------- |
| `USE_FIXTURES=1`   | Data functions return `contracts/fixtures/`                                                                                                                          | Always available; default in tests                        |
| `MOCK_AGENT=1`     | Server replays the fixture event transcript                                                                                                                          | Agent is down; UI and world work continues                |
| `FAKE_PLACEMENT=1` | `commit_placement` loads `data/houses/<id>/plans/<room_label>.json` (hand-authored) instead of the solver's output; the agent still plans, picks assets and explains | Solver is not reliable by the fake-or-real decision point |
| `HITL=1`           | Approval pause before placing                                                                                                                                        | After Gate 2                                              |

A faked step must stay invisible in the UI and honest in the README (list what is pre-authored).

## 10. Commands and checks

```
# web
cd apps/web && pnpm install && pnpm dev
pnpm typecheck && pnpm lint && pnpm test

# server
cd apps/server && uv sync && uv run uvicorn api.main:app --reload
uv run pytest && uv run ruff check .

# contracts (run by every lane before committing data or fixtures)
uv run python contracts/validate.py

# agent, no web app needed
uv run python -m agent.cli "We're new parents with an infant. Find us a home to buy under 450k."
```

Commit messages: `lane-b: layout planner loop`. One topic per commit. If you changed behavior another lane depends on, add a line to `HANDOFF.md`.

## 11. Out of scope until a human says otherwise

Daycare and other commercial playbooks, capacity math, cost estimates, flood, earthquake, destruction, multiplayer, text-to-3D, hardware, any track other than the four in section 1.
