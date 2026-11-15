# Game service messages (contracts v2.4.1)

Colyseus 0.18, one room type `NeighborhoodRoom` keyed by `block_id`, up to 16 players.
Shared state schema: `game-state.ts`. Positions are in the player's current zone frame
(block frame when `zone` is `"block"`, house-local frame inside a listing).

## Client -> game

| Message      | Payload                                        | Server rule                                                   |
| ------------ | ---------------------------------------------- | ------------------------------------------------------------- |
| `move`       | `{x, y, z, yaw, anim}`                         | 20 Hz; reject a jump over 3 m since the last accepted move, except in a teleport window (below); always reject non-finite values |
| `enter_zone` | `{zone}` (`"block"` or a `listing_id`)         | Sets `Player.zone`; a real change opens a teleport window     |
| `fire`       | `{origin: [x,y,z], dir: [x,y,z], target_id?, weapon?}` | Hit counts only if both alive, same zone, under 5 shots/s, under 40 m, origin within 1.5 m of last position |
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
Absent means `blaster`, so older payloads stay valid. `noodle` (the pool
noodle) is reserved; its rules (for example a short reach instead of 40 m)
are Lane D's to define.

## Game -> client broadcasts (besides state patches)

| Message      | Payload                 |
| ------------ | ----------------------- |
| `hit`        | `{shooter, target, hp}` |
| `eliminated` | `{target, by}`          |
| `fx`         | `{kind, origin, dir}`   |
| `toast`      | `{text}`                |

Three hits eliminate; respawn after 3 s at a valid spawn point for the zone.

## Agent -> game internal API

HTTP with header `X-Internal-Secret: $GAME_INTERNAL_SECRET`. Private network first
(`http://game:<port>`), public URL as the fallback.

| Method | Path                                                  | Body                                                                 | Effect                                                                                     |
| ------ | ----------------------------------------------------- | -------------------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| `POST` | `/internal/rooms/{block_id}/houses/{listing_id}/plan` | `{player_id, plan}` (plan: `placement_plan.schema.json`, no conflicts) | Replace that house's furniture; broadcast `toast` "{name}'s agent furnished the {room}" |
| `POST` | `/internal/rooms/{block_id}/focus`                    | `{player_id, listing_id}`                                            | Beacon on a house; a changed beacon opens that player's teleport window                   |
| `GET`  | `/internal/rooms/{block_id}/players/{player_id}`      | none                                                                 | `{zone, x, y, z}` for agent context                                                        |

Changing anything here needs all four humans, a bump of `VERSION`, and updated fixtures in the same commit.
