# Built World Sandbox

**Know your home before you buy it.** A multiplayer browser game built from real CoStar, Matterport and Vexcel data: walk a real block, step into a real scanned home, have an AI agent furnish it, then have a foam-dart fight with your family around the couch it just placed.

Built at HackPrinceton Fall 2026 by **Patrick Fu, Eric Guo, Khadim Dioume and Luke Sanborn**.

---

## The problem

Buying a home is the biggest purchase most families make, and more and more of the decision happens on a screen. In a LendingTree survey, 47% of people who bought in the previous two years had made an offer on a home they never toured in person. After they move in, many are disappointed: in a 2025 Bankrate survey, 45% of homeowners had at least one regret about their purchase, and "the house is too small" was the second most common. Size regret also grows the longer people live in the home.

Listing photos and 3D tours show you the rooms. They don't tell you what living there is like: whether your couch and TV fit on the long wall, whether the nursery is close enough to your bedroom, what the street looks like from the sidewalk. They also don't tell you when the listing gets something wrong, like "one story" on a house the scan shows has a staircase.

## Our solution

People who have played GTA V know Los Santos well, and through it the real Los Angeles. They didn't study a map. They learned it by spending time there: walking, driving, messing around. We want home buyers to know a house and its block the same way.

Built World Sandbox is a game that also works as a house tour:

1. **Explore.** Spawn on a real Houston block rebuilt from Vexcel aerial data, and walk into a real home rebuilt from its Matterport scan.
2. **Furnish.** Tell the agent what you want ("furnish the living room for a family that watches movies together"). It plans, picks real 3D products and places them to fit the room's actual shape.
3. **Move in.** Invite your family in. Switch to foam mode and have a nerf war in your future living room, where the new couch works as cover.
4. **Repeat.** Say "make it a playroom instead" and the layout changes mid-match, and the fight changes with it.

## Key features

- **Real houses, real block.** Matterport scans become walkable interiors with straight walls, real doorways and room data. Vexcel imagery and elevation data become the neighborhood around them. Both are in one world, and you never hit a loading screen when you step through the front door.
- **An agent that does the work.** A LangChain Deep Agents concierge plans with a todo list and hands the work to subagents. They pick a room, choose furniture sized to fit it and lay it out. The layout is checked against the real floor plan (overlaps, blocked doors, 0.75 m walkways) and fixed until no conflicts remain.
- **Home search that catches contradictions.** "We're new parents, find us a home under 450k" searches every CoStar listing. Auditors then check the top candidates in parallel against the scanned rooms and floor plan. The agent ranks them, points a beacon at the best one in the world, and flags any mismatch between the listing and the scan.
- **Multiplayer foam fights.** Up to 16 players per block. Hits are checked on the server, and agent-placed furniture is real cover. The map rebuilds every time the agent furnishes a room.
- **Real products.** About 150 pieces of furniture from Amazon Berkeley Objects (real Amazon products), each rescaled and checked against its listed dimensions.

## How it works

```
OFFLINE: worldgen/ (Python)                          RUNTIME (Render)
────────────────────────────                         ──────────────────────────────────────────────
CoStar kit (JSONL/CSV) ─► listings/index.json ─┐     ┌─────── web (React + three.js) ───────┐
Matterport API ─────────► interior.glb,        │     │ renders block, interiors, players,   │
                          collision.glb,       │     │ furniture; chat panel + plan feed    │
                          rooms.json,          ├────►└───────┬─────────────────────┬────────┘
                          graph.json           │ world pack  │ WebSocket           │ HTTPS + SSE
Vexcel API ─────────────► block render.glb,    │ (static)    ▼                     ▼
                          block collision.glb  │  ┌── game (Colyseus) ──┐   ┌── agent (FastAPI) ──────┐
ABO furniture ──────────► normalized .glb +    │  │ shared state:       │◄──┤ Deep Agents concierge + │
                          catalog.json         │  │ players, zones,     │   │ subagents; reads packs; │
Audit (deterministic) ──► audit.json ──────────┘  │ furniture, beacons, │   │ commits validated plans │
                                                  │ hits and scores     │   │ to the game server      │
                                                  └─────────────────────┘   └─────────────────────────┘
```

Building the world and playing in it are separate programs. `worldgen/` runs offline and writes an immutable **world pack**. The game and the agent only read packs, and nothing calls a sponsor API at runtime. The game server is the only writer of shared state. When the agent acts on the world, it calls the game's internal API, and every player in the house sees the change at the same moment.

### How we used each data source

**CoStar** (`worldgen/costar.py`). We read every listing in the CoStar hackathon kit (residential, apartment and commercial JSONL/CSV) into one listings index: price, rent, tax, beds, baths, area, stories, year built, features (pool, garage, fenced yard, parking), schools and property type. Fields CoStar doesn't have stay `null` and are never made up. The agent's `search_listings` tool filters and ranks this index, and every match comes with evidence for each criterion.

**Matterport** (`worldgen/build_house.py`, `rebake.py`, `panos.py`). For each house we pull the defurnished mesh (so it's ready to furnish), the panoramas and the Property Intelligence room boundaries through one read-only, cached client that rejects GraphQL mutations. From these we build:
- `interior.glb`: a cleaned shell with carved doorways and textures re-baked from the panoramas, under 5 MB per house.
- `collision.glb`: walls extruded from the floor-plan vectors with door gaps, not the raw scan, so the foam fight plays well.
- `rooms.json`: labelled room polygons, with every area computed from the polygon.
- `graph.json`: rooms as nodes and doors and stairs as edges. The agent uses this as its knowledge graph ("is there a small bedroom next to the primary?").

**Vexcel** (`worldgen/sources/vexcel.py`, `block_combined.py`). Every call is cached and logged, since the API bills per unit. Near the hero house, the block is built procedurally from parcels and footprints, with wall colors sampled from the Vexcel 3D Cities mesh and ground (lawn, concrete, road) classified from the orthophoto. Farther out, the 3D Cities mesh is used as is. Trees are live oaks placed and scaled from DSM crowns, and terrain comes from the DTM. Vexcel property attributes also feed the audit.

**Audit** (`worldgen/audit.py`). Deterministic checks compare the layers: CoStar stories vs. scanned floors and staircases, listed beds vs. bedroom-labelled rooms, listed area vs. the summed area of the scanned rooms, and the CoStar pool flag vs. Vexcel. No model is involved, and every number comes from code.

### Agent design (LangChain Deep Agents)

| Agent | Job | Tools |
| --- | --- | --- |
| `concierge` (main) | Turns needs into criteria, plans, delegates, acts in the world, answers | `search_listings`, `get_listing`, `focus_listing`, `get_player_context`, `task` |
| `listing-auditor` ×3, in parallel | Checks one home against the family's needs and flags contradictions | `get_listing`, `get_house_graph`, `get_rooms`, `get_audit` |
| `room-scout` | Picks the room to furnish and its constraints | `get_rooms`, `get_house_graph`, `get_room_geometry` |
| `asset-curator` | Picks furniture sized to the room and its purpose | `search_assets` |
| `layout-planner` | Proposes poses, validates, fixes (up to 3 rounds), commits | `propose_poses`, `validate_layout`, `commit_placement` |

- **The model decides, code computes.** Areas, distances, poses, overlaps and contradiction checks all come from Python tools (`shapely`, `networkx`), never from the model. `commit_placement` refuses any plan that still has conflicts.
- **Skills** (`apps/agent/skills/`): `furnish-living-room`, `furnish-for-play`, `furnish-bedroom`, `furnish-nursery`, `home-search`, `listing-audit`. Each one holds the required pieces, clearances and arrangement rules (the couch faces the TV, the crib isn't under a window, both teams get cover).
- **Shared filesystem.** Subagents pass briefs, rooms and plans to each other through the Deep Agents virtual filesystem, with one thread and checkpointer per player session.
- **Streaming.** Todos, subagent tasks, tool calls and text are streamed to the browser over SSE, so players watch the plan happen live. Every run is traced in LangSmith, and the trace link appears in the UI.

## Tech stack

| Part | Stack |
| --- | --- |
| Client | Vite, React 19, TypeScript, React Three Fiber, three.js, `@react-three/rapier`, `ecctrl`, Zustand |
| Multiplayer | Node, Colyseus 0.18, `@colyseus/schema` |
| Agent | Python 3.11, FastAPI, LangChain Deep Agents, LangGraph, LangSmith, `shapely`, `networkx`, `uv` |
| World generation | Python, `trimesh`, `shapely`, `pandas`, `gltf-transform` (meshopt compression) |
| Data | CoStar hackathon kit, Matterport API (MatterPak + Property Intelligence), Vexcel API (3D Cities, ortho, DSM/DTM, property attributes) |
| Assets | Amazon Berkeley Objects (CC BY 4.0), Poly Haven (CC0), Kenney and KayKit (CC0), FurniMesh cribs (free license), team-made TVs (CC0), Quaternius characters |
| Hosting | Render: static site (web + world packs), two always-on web services (game, agent), defined in `render.yaml` |

Run each service locally with the commands in [`AGENTS.md` §13](AGENTS.md). Every service can also run on fixtures with `USE_FIXTURES=1`.

## Challenges we faced

_TBD_

## What we learned

_TBD_

## Next steps

- **More properties.** Run the pipeline on every CoStar listing that has a Matterport scan, not just our two hero homes on Navidad Road, and expand from one Houston block to whole neighborhoods in Houston and Los Angeles.
- **Fully automatic house builds.** Remove the remaining hand fixes (room splits, overrides) so any new scan turns into a playable house without a person in the loop.
- **Richer audits.** Add more cross-checks between data layers, such as lot size vs. Vexcel parcels, the garage flag vs. the scanned garage, and school distances vs. real walking routes.
- **Bring your own furniture.** Let families scan or pick the pieces they already own and see whether they fit before moving day.

## Credits

Furniture models: Amazon Berkeley Objects, Amazon.com (CC BY 4.0). Additional assets from Poly Haven, Kenney and KayKit (CC0), FurniMesh (cribs, AI-generated models, free commercial license) and Quaternius; the flat-screen TVs are team-made (CC0). Listing data from CoStar, interior scans from Matterport, and aerial imagery and 3D data from Vexcel.
