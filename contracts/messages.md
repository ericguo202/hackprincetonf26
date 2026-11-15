# Game service messages (contracts v2.4.5)

Colyseus 0.18, one room type `NeighborhoodRoom` keyed by `block_id`, up to 16 players.
Shared state schema: `game-state.ts`. Positions are in the player's current zone frame
(block frame when `zone` is `"block"`, house-local frame inside a listing).

## Client -> game

| Message      | Payload                                        | Server rule                                                   |
| ------------ | ---------------------------------------------- | ------------------------------------------------------------- |
| `move`       | `{x, y, z, yaw, anim}`                         | 20 Hz; reject a jump over 3 m since the last accepted move, except in a teleport window (below); always reject non-finite values |
| `enter_zone` | `{zone}` (`"block"` or a `listing_id`)         | Sets `Player.zone`; a real change opens a teleport window     |
| `fire`       | `{origin: [x,y,z], dir: [x,y,z], target_id?, weapon?}` | Foam mode only. Hit counts only if both alive, same zone, under the weapon's fire rate, in range, origin within 1.5 m of last position (rules below) |
| `set_mode`   | `{mode: "explore" or "foam"}`                  | Host toggle                                                   |
| `chat`       | `{text}`                                       | Relayed to the room                                           |

Teleport window (v2.4.1): for 1.5 s (`TELEPORT_GRACE_MS` in `apps/game`) after
a join, a reconnect, an `enter_zone` that changes the zone, or an agent `focus`
that changes the player's beacon, `move` accepts any finite position. Each of
these is a legitimate jump (spawn point, block frame to house-local frame, walk
to the focused house's door). Small moves sent before the jump are fine; the
window is time-based, not one-shot. Send `enter_zone` before the first `move`
in the new frame.

`anim` is one of `idle, run, jump, shoot, hit, eliminated, crouch` (`ANIM_STATES` in `game-state.ts`).
`crouch` (v2.4.0): the player is crouched. Their body is lower: the client's
crouched capsule top is about 0.97 m above the floor instead of 1.3 m, so a
server-side hit check that models the body should use the lower height.

`weapon` (v2.4.0) is one of `blaster, noodle` (`WEAPONS` in `game-state.ts`).
Absent means `blaster`, so older payloads stay valid.

Fire rules (v2.4.3). `Player.hp` is 3 when full (the schema default).

| Weapon    | Damage per hit | Hits to eliminate | Client cooldown | Server min interval | Range (origin to target body centre)             |
| --------- | -------------- | ----------------- | --------------- | ------------------- | ------------------------------------------------ |
| `blaster` | 1              | 3                 | 0.25 s          | 0.2 s               | under 40 m, and the ray passes within 1 m of the target |
| `noodle`  | 2              | 2                 | 0.8 s           | 0.64 s              | under 2.9 m (2.0 m reach + body radius + lag slack); no aim or line-of-sight check |

Every accepted `fire` counts toward the rate limit, misses included, and is
sent to the other players (not the shooter) as `fx {kind: weapon, origin, dir}`.
A rejected hit does no damage but the accepted shot still sends `fx`. Line of
sight through walls and furniture is not checked on the server (stretch).

## Game -> client broadcasts (besides state patches)

| Message      | Payload                 |
| ------------ | ----------------------- |
| `hit`        | `{shooter, target, hp}` |
| `eliminated` | `{target, by}`          |
| `fx`         | `{kind, origin, dir}`   |
| `toast`      | `{text}`                |

`hp` reaching 0 eliminates: `alive` false, `anim` "eliminated", the shooter's
`score` +1, then `eliminated {target, by}`. The server ignores `move` and
`fire` from an eliminated player. After 3 s it sets `hp` 3, `alive` true and
opens that player's teleport window; the client then moves itself to a spawn
point for its zone (indoors: free floor after the latest plan, never inside
furniture), because only the client knows the current free floor. Switching
the room to `explore` revives everyone; switching to `foam` resets scores.

## Agent -> game internal API

HTTP with header `X-Internal-Secret: $GAME_INTERNAL_SECRET`. Private network first
(`http://game:<port>`), public URL as the fallback.

| Method | Path                                                  | Body                                                                 | Effect                                                                                     |
| ------ | ----------------------------------------------------- | -------------------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| `POST` | `/internal/rooms/{block_id}/houses/{listing_id}/plan` | `{player_id, plan, room_label?}` (plan: `placement_plan.schema.json`, no conflicts) | Replace that room's furniture (rules below); broadcast `toast` "{name}'s agent furnished the {room}" |
| `POST` | `/internal/rooms/{block_id}/focus`                    | `{player_id, listing_id}`                                            | Beacon on a house; a changed beacon opens that player's teleport window                   |
| `GET`  | `/internal/rooms/{block_id}/players/{player_id}`      | none                                                                 | `{zone, x, y, z}` for agent context                                                        |

Per-room furniture (v2.4.5): the game keeps furniture per `plan.room_id` and
rebuilds `House.objects` from every furnished room of the house, rooms in the
order they were first furnished. A plan replaces only its own room, so
furnishing a second room keeps the first; the same room again replaces it (last
write wins); a plan with no objects clears that room. `plan.room_id` must be a
room of the listing in the path (`<listing_id>.r<n>`), else 400. Each
`instance_id` in `House.objects` is prefixed with its room id
(`"hou_236595179.r3/o1"`), because agents number pieces `o1, o2...` per plan.
`House.plan_id` is the latest plan's id for that house.

`room_label` (v2.4.2, optional) is the room's `label` from `room.schema.json`
(for example `living_room`). The toast shows it in words ("the living room");
without it the toast says "a room", never the raw `room_id`.

Changing anything here needs all four humans, a bump of `VERSION`, and updated fixtures in the same commit.
