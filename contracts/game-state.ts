// Shared Colyseus state (contracts v2, AGENTS.md section 8). Change by agreement only.
// Server: apps/game imports these classes. Web: import only the types (`import type`);
// the Colyseus SDK decodes state by reflection and does not need the classes at runtime.
//
// Positions are in the player's zone frame: block frame when zone is "block",
// house-local frame when zone is a listing_id. Meters and degrees, except yaw (radians).
import { schema, t, type SchemaType } from "@colyseus/schema";

export const ANIM_STATES = ["idle", "run", "jump", "shoot", "hit", "eliminated"] as const;
export type AnimState = (typeof ANIM_STATES)[number];
export type GameMode = "explore" | "foam";

export const Player = schema(
  {
    id: t.string(),
    name: t.string(),
    zone: t.string().default("block"),
    x: t.float32().default(0),
    y: t.float32().default(0),
    z: t.float32().default(0),
    yaw: t.float32().default(0),
    anim: t.string().default("idle"),
    hp: t.uint8().default(3),
    score: t.uint16().default(0),
    alive: t.boolean().default(true),
  },
  "Player",
);
export type Player = SchemaType<typeof Player>;

export const Placed = schema(
  {
    instance_id: t.string(),
    asset_id: t.string(),
    x: t.float32().default(0),
    y: t.float32().default(0),
    z: t.float32().default(0),
    rot_deg: t.float32().default(0),
    reason: t.string(),
    by_player: t.string(),
  },
  "Placed",
);
export type Placed = SchemaType<typeof Placed>;

export const House = schema(
  {
    listing_id: t.string(),
    plan_id: t.string(),
    objects: t.array(Placed),
  },
  "House",
);
export type House = SchemaType<typeof House>;

export const RoomState = schema(
  {
    block_id: t.string(),
    pack_version: t.uint16().default(1),
    mode: t.string().default("explore"),
    players: t.map(Player),
    houses: t.map(House), // furniture per listing_id
    focus: t.map("string"), // player id -> listing_id beacon
  },
  "RoomState",
);
export type RoomState = SchemaType<typeof RoomState>;
