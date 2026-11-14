# AGENTS.md — Built World Sandbox (HackPrinceton Fall 2026)

Instructions for every coding agent in this repo. Read the whole file before writing code. The humans own the schedule (team schedule doc); this file owns the technical rules. Contracts version: **v2** (multiplayer revision).

## 1. North star

**Know a house the way GTA 5 players know Los Santos.** People who have played GTA 5 know its map like the back of their hand, and through it, the real city it is based on. They learned it by living in it: walking driving, messing around. We want home buyers to know a house and its block the same way before they buy. A Matterport tour shows the rooms. It does not show what it is like to walk up and down your street, to see the place furnished the way you want, or to run around it with your family.

So the product is a game that is also a house tour:

1. **Explore.** Load into a real block built from Vexcel data and walk into a real home built from its Matterport scan.
2. **Furnish.** Tell the agent how you want a room, and watch it plan, pick real 3D objects and place them in the real room shape. Walk around it again.
3. **Move in.** Bring your family in (multiplayer). Have a nerf war in your future living room. The couch the agent just placed is now cover.
4. **Repeat.** Every furnishing pass changes the map. Play to explore it, furnish to change it, and by the end you know what living there feels like.

**Every decision serves this demo path.** When a choice is unclear, pick the option that makes this loop work on stage, on the deployed build, with two players.

### The demo (3 minutes, two laptops)

| Time      | On screen                                                                                                                                                              | What it proves                                       |
| --------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------- |
| 0:00–0:25 | Load in on a real block; walk the sidewalk; enter the house                                                                                                            | Vexcel neighborhood + Matterport interior, one world |
| 0:25–0:45 | (Optional for CoStar and LangChain judges) "We're new parents, find us a home under 450k": parallel auditors, ranked pick, a contradiction caught, beacon on the house | CoStar data, agentic depth                           |
| 0:45–1:30 | "Furnish the living room for a family that watches movies together": live plan, subagent feed, furniture drops in; walk through it                                     | Reason, plan, act; real room geometry                |
| 1:30–2:15 | Second player joins; switch to foam mode; nerf war around the new couch and TV                                                                                         | Multiplayer; furniture is cover                      |
| 2:15–2:40 | "Make it a playroom instead": the layout changes mid-match and so does the fight                                                                                       | The map changes with every furnishing pass           |
| 2:40–3:00 | Close: "Know your home before you buy it"                                                                                                                              | —                                                    |

### What each judge hears

| Track                | Emphasis                                                                                                                                                                    |
| -------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Entertainment (main) | The FPS: a nerf war in a real house and on a real block, with friends, on a map that you rebuild by talking to an agent                                                     |
| CoStar / Matterport  | Every data facet: CoStar price, tax, schools, features; Matterport rooms, floors, wall and door vectors; Vexcel terrain, buildings, yards; the audit that cross-checks them |
| LangChain            | Deep Agents stack: planning, parallel subagents, skills, layout loop with self-checks, LangSmith trace shown live                                                           |
| Amazon               | Target user (a family deciding on a home), a working deployed prototype, docs, UX                                                                                           |

## 2. What we are building

A multiplayer browser game made from real CoStar, Matterport and Vexcel data. Players walk a real Houston or Los Angeles block together, enter homes that are for sale or for rent, and talk to an agent that:

1. **Furnishes the house** from a library of real 3D objects, based on what the player asks for. Every player in that house sees the furniture appear, and it becomes part of the playable map (cover, obstacles, routes).
2. **Finds a home** that fits their needs, using price, features and the scanned interior ("new parents with an infant" → no stairs, a small bedroom next to the primary, a fenced yard, inside budget).
3. **Catches contradictions** in the listing data (a staircase scanned in a home listed as one story, 3 beds listed but 2 bedrooms scanned, listed area far from scanned area).

The play layer: a casual multiplayer foam-blaster mode (nerf darts or snowballs) indoors and on the block. No realistic guns, no blood.

Tracks: Entertainment (main), CoStar "Inside and Out", LangChain (Deep Agents), Amazon (Best Practical AI Innovation). Nothing else.

## 3. Rules for coding agents

1. **Stay in your lane's directories** (section 6). Need a change in another lane? Write it in `HANDOFF.md` for that lane's human.
2. **`contracts/` is law.** Never change a schema, message name or fixture on your own. All four humans agree, then bump `contracts/VERSION` and update fixtures in the same commit.
3. **World generation and world playing are separate programs.** `worldgen/` runs offline and writes world packs. The game reads packs and never calls sponsor APIs. The agent reads the same packs plus the listings index. Nothing at runtime converts meshes or parses CoStar CSVs.
4. **Build against fixtures first.** Every service must run with `USE_FIXTURES=1` and no other lane working.
5. **The model decides and explains; deterministic code computes every number.** Areas, prices per square meter, distances, poses, overlaps, scores and contradiction checks come from tools, never from the model's head.
6. **The agent must reason, plan and act**, not chat or summarize: plan (todo list) → tool calls on real data → deterministic computation → an action in the world → self-check → a short answer citing the data fields used.
7. **`main` always runs and always deploys.** Small commits, merge at least every 2 hours, run your lane's checks (section 13) before every commit.
8. **One git worktree per coding agent**, branch `lane-<a|b|c|d>/<topic>`. Rebase on `main` before merging.
9. **Secrets** live in `.env` locally and in Render environment variables in production. Never commit them, never send them to the browser, never put them in prompts or traces. Matterport API keys can buy things: all Matterport calls go through `worldgen/sources/matterport.py`, which rejects GraphQL mutations.
10. **No branded content** in the world or assets. Every asset has a recorded license.
11. **No new scope** without your human. When blocked over 15 minutes on another lane, stub against the fixture, note it in `HANDOFF.md`, keep moving.

## 4. Architecture

```
 OFFLINE (worldgen/, Python)                        RUNTIME (Render)
 ───────────────────────────                        ─────────────────────────────────────────────
 CoStar JSON/CSV ──► listings index ─┐              ┌──────────── web (static site) ────────────┐
 Matterport API ──► interior .glb    │  world packs │ React + R3F game client                    │
                   rooms + graph     ├─────────────►│  loads packs, renders players + furniture  │
 Vexcel API ─────► block .glb        │  (S3 bucket  └───┬──────────────────────────┬────────────┘
                   collision, spawns │  or static)      │ Colyseus WebSocket       │ HTTPS + SSE
 Furniture packs ► normalized .glb   │                  ▼                          ▼
                   catalog.json      │      ┌──── game (Node, Colyseus) ──┐  ┌── agent (Python) ──────┐
 Data audit ─────► audit.json ───────┘      │ authoritative shared state: │◄─┤ FastAPI + Deep Agents  │
                                            │ players, zones, furniture,  │  │ search, audit, furnish │
                                            │ focus beacon, foam fight    │  │ POSTs placements to    │
                                            └─────────────────────────────┘  │ game (shared secret)   │
                                                                             └────────────────────────┘
```

Three runtime services, one direction of authority:

- **game** owns everything every player must agree on: who is where, which zone they are in, which furniture is in which house, the focus beacon, hits and scores. It is the only writer of shared state.
- **agent** owns reasoning. It never talks to browsers about world state. When it acts on the world, it calls the game service's internal HTTP API, and the game broadcasts the change to everyone in the room.
- **web** renders. The chat panel streams the agent's plan and text straight from the agent service (per player), but furniture and beacons arrive only through the game state, so all players see the same thing.

## 5. Stack

| Part               | Choice                                                                                                                                                                                                                     | Notes                                                                                  |
| ------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------- |
| Client             | Vite + React + TypeScript, React Three Fiber, `@react-three/rapier`, `ecctrl` character controller, Zustand                                                                                                                | three.js has the larger example base for 3D Tiles and shooters                         |
| Multiplayer server | Node 20+, TypeScript, Colyseus 0.18 (`npm create colyseus-app@latest`), `@colyseus/schema`                                                                                                                                 | Pin exact versions in `package.json`; client SDK must match the server's minor version |
| Agent service      | Python 3.11+, FastAPI, `deepagents`, LangSmith, `shapely`, `networkx`, `uv`                                                                                                                                                | Model from `MODEL` env var (`provider:model`)                                          |
| Worldgen           | Python 3.11+, `trimesh`, `pygltflib` or Blender CLI, `gltf-transform` (Node CLI) for Draco/meshopt compression, `pandas`                                                                                                   | Runs on laptops, not on Render                                                         |
| Characters         | One Quaternius pack whose file already contains the animations (Toon Shooter Game Kit or Universal Base Characters + Universal Animation Library)                                                                          | Never retarget animations at the hackathon                                             |
| Furniture          | CC0 glTF packs first (Kenney Furniture Kit, Quaternius, Poly Haven); OBJ/STL only to fill gaps                                                                                                                             | STL carries no color or UVs: give it a flat material at normalize time                 |
| Hosting            | Render: static site `web` (free), web services `game` and `agent` on the Starter plan ($7/month each, 512 MB RAM, 0.5 CPU, always on). Packs in an S3 bucket (public read, CORS) or the static site if under ~100 MB total | `render.yaml` at repo root, `plan: starter` on both web services                       |

Units everywhere: meters and degrees. Display strings only use square feet and dollars.

## 6. Repo layout and ownership

```
AGENTS.md  HANDOFF.md  render.yaml
contracts/                    ALL, by agreement: schemas, Colyseus state, messages, fixtures
worldgen/                     Lane A
  sources/ costar.py matterport.py vexcel.py furniture.py
  build_block.py build_house.py build_catalog.py audit.py
  out/packs/<block_id>/       gitignored; uploaded by `worldgen publish`
apps/web/src/
  world/                      Lane C  pack loading, block, interiors, furniture, weather
  player/                     Lane C  character, animation, camera, blaster (local side)
  net/                        Lane D  Colyseus client, interpolation, remote players
  ui/                         Lane D  chat panel, plan feed, listing cards, HUD, scoreboard
  store.ts                    Lane D  (shape agreed with C)
apps/game/src/                Lane D  rooms, schema, movement relay, hit validation, internal API
apps/agent/
  agent/  skills/             Lane B  deep agent, subagents, prompts, tool wrappers
  layout/                     Lane B  pose proposals, validator
  data/                       Lane A  pack + index readers behind the tool signatures
evals/                        Lane B runner, Lane A cases
docs/                         Lane A  README assets, demo script, Devpost text
```

The client seam between Lanes C and D is `store.ts`: D writes network and agent state into it; C reads it to render. C never opens a socket. D never touches three.js objects.

## 7. World generation (Lane A)

Output is one **world pack** per block. A pack is immutable once published; republish with a new version.

```
packs/<block_id>/v<N>/
  manifest.json
  block/render.glb            neighborhood: terrain, buildings, trees (Vexcel 3D tiles converted, or extruded footprints)
  block/collision.glb         simplified: terrain heightfield + building boxes, under 20k triangles
  houses/<listing_id>/interior.glb     Matterport mesh, Y-up, decimated, compressed, under 25 MB
  houses/<listing_id>/collision.glb    walls extruded from floor-plan vectors, door gaps, floor planes
  houses/<listing_id>/rooms.json       room polygons, openings, labels, floors (house-local)
  houses/<listing_id>/graph.json       rooms as nodes, doors and stairs as edges
  houses/<listing_id>/audit.json       contradictions found between data layers
  furniture/*.glb  furniture/catalog.json
listings/index.json                    every listing in the CoStar folder (not just ones with interiors)
```

Pipeline steps, each a separate command so they parallelize:

1. **CoStar → `listings/index.json`.** Read every JSON and CSV in the raw folder, map to the Listing schema (section 9), drop rows without coordinates, keep `property_type` (single family, multi family, apartment, commercial). Keep the raw record ID for traceability.
2. **Join layers.** Match CoStar records to Matterport models by address, then by distance under 30 m. Match to Vexcel by coordinates. Write `source` IDs on each listing. Unmatched is fine; `has_interior` stays false.
3. **Block.** Pick a local origin at the block center. Convert lat/lon to meters east/north/up from it. Vexcel 3D tiles → glTF if available for the block; otherwise extrude footprints (OpenStreetMap or Vexcel) to DSM heights over DTM terrain. Write spawn points on sidewalks and a door point per listed house.
4. **House.** The pitch is "furnish it the way you want", so the hero house must look empty. Prefer listings scanned vacant or a defurnished mesh; if the only scan is furnished, say so at hour 3 so the team picks another house or accepts it. MatterPak OBJ → glTF; convert Z-up to Y-up once here; decimate for rendering; build collision from floor-plan wall vectors (never the raw scan). Room polygons and labels from Property Intelligence; if not on our keys, hand-trace the hero house. Compute every `area_m2` from the polygon.
5. **Graph.** `graph.json`: nodes = rooms (`label`, `area_m2`, `floor`, `has_window`), edges = doors and stairs (`kind`, `width_m`). This is the "knowledge graph" the agent queries. No graph database.
6. **Furniture.** Download, normalize to `.glb`: Y-up, meters, origin at bottom center, front facing +Z, under 5 MB. Write `catalog.json` with size, tags, placement type and license.
7. **Audit.** Deterministic checks per listing, written to `audit.json` (section 9). Examples: CoStar `stories = 1` but the scan has more than one floor or a Staircase room; `beds` vs count of bedroom-labelled rooms; CoStar area vs summed scanned interior area off by more than 15%; `has_pool` true but no pool in Vexcel attributes.
8. **Publish.** `worldgen publish <block_id>` uploads to the bucket and prints the pack URL. Set it as `PACK_BASE_URL` on all three services.

## 8. Multiplayer (Lane D, with C on the client)

**Room model.** One Colyseus room type, `NeighborhoodRoom`, keyed by `block_id`, up to 16 players. Joining from the landing page puts players in the same room for that block.

**Zones, not separate rooms.** Each player has a `zone`: `"block"` or a `listing_id`. Entering a door sends `enter_zone`; the client loads that house's interior and renders only players with the same zone. Positions are in the current zone's frame (block frame outdoors, house-local frame indoors). Players never disconnect to change zones.

**Movement.** Client-authoritative for the hackathon. Each client sends `move` at 20 Hz. The server clamps speed (reject jumps over 3 m between messages) and relays through state. Remote players are interpolated about 100 ms behind. Colyseus 0.18 ships client-side prediction and lag compensation; do not adopt them until the game works without them.

**Shared world state** (the `@colyseus/schema` in `contracts/game-state.ts`):

```ts
class Player {
  id;
  name;
  zone;
  x;
  y;
  z;
  yaw;
  anim;
  hp;
  score;
  alive;
}
class Placed {
  instance_id;
  asset_id;
  x;
  y;
  z;
  rot_deg;
  reason;
  by_player;
}
class House {
  listing_id;
  plan_id;
  objects: ArraySchema<Placed>;
}
class RoomState {
  block_id;
  pack_version;
  mode: "explore" | "foam";
  players: MapSchema<Player>;
  houses: MapSchema<House>; // furniture per listing
  focus: MapSchema<string>; // player id -> listing_id beacon
}
```

**Client → game messages:** `move {x,y,z,yaw,anim}`, `enter_zone {zone}`, `fire {origin, dir, target_id?}`, `set_mode {mode}`, `chat {text}`.
**Game → client broadcasts** (besides state patches): `hit {shooter, target, hp}`, `eliminated {target, by}`, `fx {kind, origin, dir}`, `toast {text}`.

**Agent → game internal API** (HTTPS on the public URL, header `X-Internal-Secret`):

| Method | Path                                                  | Effect                                                                                                        |
| ------ | ----------------------------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| `POST` | `/internal/rooms/{block_id}/houses/{listing_id}/plan` | Replace that house's furniture with a validated plan; broadcast `toast` "{name}'s agent furnished the {room}" |
| `POST` | `/internal/rooms/{block_id}/focus`                    | `{player_id, listing_id}` beacon on a house                                                                   |
| `GET`  | `/internal/rooms/{block_id}/players/{player_id}`      | Player's zone and position, for agent context                                                                 |

**Furniture is static** in multiplayer: fixed colliders, not pushable, so every client agrees where it is.

**Hosting constraints that shape this:**

- `game` and `agent` run on Render Starter instances from the first deploy: always on, no spin-down, 512 MB RAM and 0.5 CPU each.
- Run **exactly one instance** of `game`. Rooms live in memory, and Render assigns each WebSocket connection to a random instance. Never turn on autoscaling.
- **Memory budget is 512 MB per service.** `agent` keeps only `listings/index.json`, room, graph, audit and catalog JSON in memory, never meshes. `game` keeps only schema state. Watch memory in the Render dashboard after Gate 1.
- `agent` calls `game` over Render's private network (`http://game:<port>`, from `fromService` in `render.yaml`) and still sends the shared secret. The public URL plus secret is the fallback.
- A deploy closes every WebSocket. No deploys during judging. The client reconnects automatically and rejoins its zone.
- The filesystem is ephemeral. Room state is lost on restart; the demo seeds the hero house's furniture from `packs/.../plans/` on room creation.

## 9. Contracts (v2)

JSON Schemas in `contracts/` are the source of truth. Generate TypeScript types and Pydantic models from them.

### Listing (`listings/index.json` entries)

```json
{
  "id": "hou_000123",
  "address": "...",
  "lat": 29.76,
  "lon": -95.36,
  "block_id": "hou_heights_01",
  "status": "for_sale",
  "property_type": "single_family",
  "price_usd": 425000,
  "rent_usd_month": null,
  "tax_usd_year": 8900,
  "beds": 3,
  "baths": 2,
  "area_m2": 172,
  "stories": 1,
  "year_built": 1998,
  "features": {
    "pool": false,
    "parking_spaces": 2,
    "garage": true,
    "fenced_yard": true,
    "utilities": ["gas", "electric"]
  },
  "schools": [
    { "name": "...", "level": "elementary", "distance_m": 650, "rating": 8 }
  ],
  "site": { "yard_area_m2": 140, "street_class": "residential" },
  "has_interior": true,
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

Fields the CoStar folder does not have stay `null`. Never fabricate them.

### Room, house graph, audit

```json
{
  "id": "hou_000123.r3",
  "label": "bedroom",
  "label_confidence": 0.9,
  "floor": 0,
  "floor_y": 0.0,
  "height_m": 2.6,
  "area_m2": 9.8,
  "polygon": [
    [0, 0],
    [3.2, 0],
    [3.2, 3.1],
    [0, 3.1]
  ],
  "openings": [
    { "type": "door", "a": [1.0, 0], "b": [1.9, 0], "to_room": "hou_000123.r1" }
  ]
}
```

`label`: `living_room, bedroom, primary_bedroom, kitchen, dining_room, bathroom, hallway, staircase, office, closet, garage, utility, other`.

`graph.json`: `{"nodes": [{"id", "label", "area_m2", "floor", "has_window"}], "edges": [{"a", "b", "kind": "door" | "stairs" | "open", "width_m"}]}`

`audit.json`: `{"listing_id", "findings": [{"check", "severity": "info" | "warn" | "conflict", "listed", "observed", "evidence"}]}`

### Asset (`furniture/catalog.json` entry)

```json
{
  "id": "crib_01",
  "name": "Crib",
  "category": "crib",
  "tags": ["nursery", "infant", "sleep"],
  "file": "furniture/crib_01.glb",
  "size_m": { "w": 0.75, "d": 1.35, "h": 0.95 },
  "placement": "wall",
  "clearance_m": { "front": 0.6 },
  "cover": "half",
  "license": "CC0",
  "source": "Kenney"
}
```

MVP library (at least): couch, sectional, armchair, coffee table, TV + stand, rug, bookshelf, floor lamp, double bed, single bed, nightstand, dresser, desk, chair, dining table, dining chair, crib, changing table, rocking chair, toy storage, play mat, beanbag, plant. Living-room pieces come first: they are in the demo.

Add `"cover": "full" | "half" | "none"` to each asset (full: taller than 1.2 m, such as a bookshelf; half: 0.5 to 1.2 m, such as a couch; none: a rug). The renderer and the layout planner both use it.

### Placement plan

```json
{
  "plan_id": "p_01",
  "listing_id": "hou_000123",
  "room_id": "hou_000123.r3",
  "status": "validated",
  "objects": [
    {
      "instance_id": "o1",
      "asset_id": "crib_01",
      "x": 2.4,
      "y": 0.0,
      "z": 3.1,
      "rot_deg": 90,
      "reason": "Interior wall, away from the window"
    }
  ],
  "conflicts": []
}
```

House-local frame: Y up, +Z front of asset at `rot_deg = 0`, rotation counterclockwise from above. `conflicts[].type`: `outside_room, overlap, blocks_opening, clearance`. Reasons are 12 words or fewer.

### Agent service API (per player)

| Method | Path                      | Notes                                                                                                              |
| ------ | ------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| `POST` | `/sessions`               | `{player_id, block_id}` → `{session_id}`                                                                           |
| `POST` | `/sessions/{id}/messages` | `{text}`; the agent looks up the player's zone through the game API                                                |
| `GET`  | `/sessions/{id}/stream`   | SSE: `agent.todos`, `agent.subagent`, `agent.tool`, `agent.message`, `listings.results`, `audit.findings`, `error` |

Event payloads: see `contracts/agent-events.md` and the transcript fixture `contracts/fixtures/agent.session.jsonl`.

## 10. Agent (Lane B)

Built on Deep Agents. Docs: https://docs.langchain.com/oss/python/deepagents/overview, subagents: https://docs.langchain.com/oss/python/deepagents/subagents, customization: https://docs.langchain.com/oss/python/deepagents/customization. Check signatures in the docs before coding.

Facts that shape the design:

- Subagent dicts need `name`, `description`, `system_prompt`; `tools` replaces the parent's tools when set; `skills` are not inherited by custom subagents; `response_format` gives structured results.
- Subagents see only the task description. The concierge must put listing IDs, room IDs, the user's needs and constraints into it.
- Several `task` calls in one turn run in parallel. Use this for auditing candidate homes.
- `interrupt_on` needs a checkpointer.

### Agents

| Agent                                               | Job                                                                                  | Tools                                                                           | Returns                                                                 |
| --------------------------------------------------- | ------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| `concierge` (main)                                  | Turns needs into criteria, searches, delegates, decides, acts, answers               | `search_listings`, `get_listing`, `focus_listing`, `get_player_context`, `task` | Chat answer                                                             |
| `listing-auditor` (one per top candidate, parallel) | Checks one home against the needs using rooms, graph and audit; flags contradictions | `get_listing`, `get_house_graph`, `get_rooms`, `get_audit`                      | `{listing_id, fit: [{need, met, evidence}], contradictions[], verdict}` |
| `room-scout`                                        | Picks the room to furnish                                                            | `get_rooms`, `get_house_graph`, `get_room_geometry`                             | `{room_id, label, why, usable_area_m2, constraints[]}`                  |
| `asset-curator`                                     | Picks objects for that room and purpose, sized to it                                 | `search_assets`                                                                 | `{items: [{asset_id, count, role, priority}]}`                          |
| `layout-planner`                                    | Proposes, validates, fixes, commits                                                  | `get_room_geometry`, `propose_poses`, `validate_layout`, `commit_placement`     | `{plan_id, placed, dropped[], notes}`                                   |

Home search flow: criteria (hard: status, budget, beds, property type; soft: weighted needs) → `search_listings` over the full index → top 3 with interiors → three `listing-auditor` tasks in parallel → rank with evidence → `focus_listing` → answer naming any contradictions found ("Listed as one story, but the scan shows a staircase to a second floor").

Infant profile (in the `home-search` skill): single floor or a bedroom on the entry floor; a bedroom under 12 m² adjacent (graph distance 1 to 2) to the primary bedroom; no pool, or a fenced pool; fenced yard preferred; quiet street class; nearby elementary schools as a soft plus.

Furnish is the demo's centre and is built before home search. Demo prompts that must work every time: "Furnish the living room for a family that watches movies together", "Set up the living room for a nerf war", "Make it a playroom instead". Re-furnishing the same room replaces its plan; it does not stack objects.

Furnish flow: `room-scout` → `asset-curator` → `layout-planner` loop (propose → validate → fix, maximum 3 rounds; then drop the lowest-priority conflicting item and say so). Never commit a plan with conflicts. Edits ("move the couch to the other wall") change the existing plan.

### Tools

| Tool                                                       | Returns                                                               | Owner              |
| ---------------------------------------------------------- | --------------------------------------------------------------------- | ------------------ |
| `search_listings(criteria)`                                | Ranked matches with per-criterion evidence                            | A (`agent/data`)   |
| `get_listing`, `get_rooms`, `get_house_graph`, `get_audit` | Pack and index reads                                                  | A                  |
| `search_assets(query, category, max_w, max_d)`             | Catalog entries                                                       | A                  |
| `get_room_geometry(room_id)`                               | Polygon, openings, height, free wall segments                         | B (`layout/`)      |
| `propose_poses(room_id, asset_id, rule, relative_to)`      | Up to 5 poses; `rule`: `against_wall, corner, center, facing, beside` | B                  |
| `validate_layout(plan)`                                    | Plan with conflicts                                                   | B                  |
| `commit_placement(plan)`                                   | POSTs to the game API; refuses plans with conflicts                   | B, calling D's API |
| `focus_listing(listing_id)`, `get_player_context()`        | Game API calls                                                        | B, calling D's API |

Tools read `session_id`, `player_id` and `block_id` from runtime context. Never ask the model to pass them.

### Skills (`apps/agent/skills/<name>/SKILL.md`)

`furnish-living-room` (first: it is in the demo), `furnish-for-play`, `home-search`, `listing-audit`, `furnish-bedroom`, `furnish-nursery`. Furnishing skills hold required and optional objects with priorities, clearances (0.75 m walkway, door swing clear, crib not under a window) and arrangement rules (couch faces TV, bed head against a wall).

### Streaming and tracing

Map the agent stream to SSE events: todo updates → `agent.todos`, each `task` → `agent.subagent`, each tool call → `agent.tool`, text → `agent.message`. Turn on subgraph streaming so subagent activity is visible; check the Deep Agents streaming docs for the current arguments. One thread ID per session with a checkpointer. LangSmith tracing on for every run; the trace link shows in the UI.

## 11. Foam fight (Lane C client, Lane D server)

Off by default (`mode: "explore"`). A host toggle switches the room to `foam`. **The hero fight is inside the furnished house**: the pitch is a nerf war around the couch the agent just placed. The block works too, but indoors is built and tested first.

**The map changes with every furnishing pass.** This is the core idea, not a side effect:

- Furniture colliders are part of the shot world. Darts stop on a couch, a bookshelf or a crib. Players hide behind them.
- When a new plan lands mid-match, the old furniture leaves and the new furniture drops in (0.5 s animation). Colliders rebuild in the same frame. A player who would end up inside a new object is pushed to the nearest free floor point.
- Indoor spawn points are computed from the free floor area after each plan (grid points at least 0.6 m from walls and furniture, at least 3 m apart). Never spawn inside furniture.
- The agent should treat play as a valid design goal. "Set up the living room for a nerf war" is a real request: the layout planner may favour pieces with `cover` (couch, sectional, bookshelf) and spread them so both sides have cover, while still passing every clearance rule. The `furnish-for-play` skill holds these rules.

Rules:

- **Weapon:** foam blaster with hitscan. A visible dart tracer and a hit marker sell it. Snowballs are a skin: same hitscan, an arc-shaped cosmetic trail.
- **Client (C):** raycast from the camera against remote players' capsule hitboxes, the zone's collision (`block/collision.glb` or the house's `collision.glb`) and the current furniture colliders. On a player hit, send `fire {origin, dir, target_id}`; otherwise `fire {origin, dir}` for the effect only. Play muzzle and hit effects locally right away.
- **Server (D):** accept a hit only if shooter and target are alive and in the same zone, fire rate is under 5 per second, the distance is under 40 m, and `origin` is within 1.5 m of the shooter's last position. Then apply damage (3 hits to eliminate), broadcast `hit`, respawn after 3 s at a valid spawn point for that zone. Line-of-sight checks on the server (walls plus furniture boxes from `House.objects`) are stretch.
- **Animation:** six states, `idle, run, jump, shoot, hit, eliminated`, 0.2 s crossfade. Send only the state name in `Player.anim`; each client plays the clip locally. Use clips where the character already holds the blaster while running; no upper-body masking. Record the pack's exact clip names in `apps/web/src/player/clips.ts` before writing animation code.
- **Game loop:** score per elimination, scoreboard on Tab, 3-minute round timer (stretch).

## 12. Fallback switches

| Env var            | Service | Effect                                                                                                                                     |
| ------------------ | ------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| `USE_FIXTURES=1`   | all     | Read `contracts/fixtures/` instead of packs                                                                                                |
| `MOCK_AGENT=1`     | agent   | Replay `agent.session.jsonl` and send its placement to the game API                                                                        |
| `FAKE_PLACEMENT=1` | agent   | `commit_placement` sends the hand-authored plan from `packs/.../plans/<room_label>.json`; the agent still plans, picks assets and explains |
| `SOLO=1`           | web     | Run without the game server: local state only, no other players                                                                            |
| `HITL=1`           | agent   | Approval pause before placing (after Gate 2)                                                                                               |

A faked step stays invisible in the UI and is listed honestly in the README.

## 13. Commands and checks

```
# worldgen (laptop)
cd worldgen && uv sync
uv run python -m worldgen.costar --in ../raw/costar --out out/listings/index.json
uv run python -m worldgen.build_house --listing hou_000123
uv run python -m worldgen.audit --block hou_heights_01
uv run python -m worldgen.publish hou_heights_01
uv run pytest && uv run python ../contracts/validate.py out/packs/hou_heights_01

# game server
cd apps/game && npm i && npm run dev        # ws://localhost:2567
npm test && npm run typecheck

# agent
cd apps/agent && uv sync && uv run uvicorn api.main:app --reload --port 8000
uv run pytest && uv run ruff check .
uv run python -m agent.cli "We're new parents with an infant. Find us a home to buy under 450k."

# web
cd apps/web && pnpm i && pnpm dev
pnpm typecheck && pnpm lint && pnpm test
```

Test multiplayer locally with two browser windows, one in a private window. Commit messages: `lane-d: hit validation`. Add a line to `HANDOFF.md` when you change something another lane depends on.

## 14. Out of scope unless a human says otherwise

Daycare and commercial playbooks, flood, earthquake, destruction, vehicles, building or editing terrain, physics-synced pushable objects, proper lag compensation, accounts or login, voice chat, more than one block, any track other than the four in section 2.
