// Lane D: the shared world for one block (AGENTS.md section 8, contracts/messages.md).
// Join/leave, zones, clamped movement relay (with a teleport grace window), mode toggle, foam-fight hits.
import { type Client, type Delayed, Room } from "colyseus";
import { ANIM_STATES, House, Placed, Player, RoomState } from "../../../../contracts/game-state.ts";
import {
  AIM_TOLERANCE_M,
  DAMAGE,
  distance,
  FULL_HP,
  MAX_ORIGIN_OFFSET_M,
  MAX_RANGE_M,
  MIN_FIRE_INTERVAL_MS,
  normalize,
  parseVec3,
  parseWeapon,
  RESPAWN_MS,
  rayPointDistance,
  type Vec3,
} from "./fire.ts";

export * from "./fire.ts";

export const MAX_STEP_M = 3;
/**
 * After a join, reconnect, zone change or agent teleport, accept any move for this long.
 * A window, not a one-shot flag: small moves from the old position can arrive before the
 * jump (initial [0,0,0] before the spawn is placed; 20 Hz moves before the focus patch lands).
 */
export const TELEPORT_GRACE_MS = 1500;

/** Live rooms by block_id. Safe because exactly one game instance runs (section 8). */
export const roomsByBlock = new Map<string, NeighborhoodRoom>();

type JoinOptions = { block_id?: string; name?: string };
type MovePayload = { x: number; y: number; z: number; yaw: number; anim: string; weapon?: unknown };
type FirePayload = { origin?: unknown; dir?: unknown; target_id?: unknown; weapon?: unknown };
type FurniturePayload = {
  listing_id?: unknown;
  room_id?: unknown;
  instance_id?: unknown;
  asset_id?: unknown;
  x?: unknown;
  y?: unknown;
  z?: unknown;
  rot_deg?: unknown;
};

/** Manual furnishing limits (contracts/messages.md v2.6.0). */
export const FURNITURE_EDIT_INTERVAL_MS = 100;
export const MAX_PIECES_PER_HOUSE = 150;
export const MAX_HORIZONTAL_M = 200;
export const MIN_Y_M = -20;
export const MAX_Y_M = 50;
const ASSET_ID = /^[A-Za-z0-9_.-]{1,96}$/;

/** A pose from a furniture message, or null if any field is missing, non-finite or out of range. */
export function parsePose(m: FurniturePayload): { x: number; y: number; z: number; rot_deg: number } | null {
  const { x, y, z, rot_deg } = m;
  if (![x, y, z, rot_deg].every((v) => typeof v === "number" && Number.isFinite(v))) return null;
  const [px, py, pz, r] = [x, y, z, rot_deg] as number[];
  if (Math.abs(px) > MAX_HORIZONTAL_M || Math.abs(pz) > MAX_HORIZONTAL_M || py < MIN_Y_M || py > MAX_Y_M) return null;
  return { x: px, y: py, z: pz, rot_deg: ((r % 360) + 360) % 360 };
}

/**
 * Agents number pieces o1, o2... per plan, so ids collide across rooms of one house.
 * The game prefixes them with the room: "hou_x.r3/o1" (idempotent).
 */
export function roomInstanceId(roomId: string, instanceId: string): string {
  return instanceId.startsWith(`${roomId}/`) ? instanceId : `${roomId}/${instanceId}`;
}

export type PlanObject = {
  instance_id: string;
  asset_id: string;
  x: number;
  y: number;
  z: number;
  rot_deg: number;
  reason: string;
};

export class NeighborhoodRoom extends Room<{ state: RoomState }> {
  maxClients = 16;
  state = new RoomState();
  /** sessionId -> time (ms) until which moves skip the step check. Server-only, not in state. */
  private graceUntil = new Map<string, number>();
  /** sessionId -> Date.now() of the last accepted fire (any weapon). Server-only. */
  private lastFire = new Map<string, number>();
  /** listing_id -> room_id -> that room's placed objects. Server-only; House.objects is rebuilt from it. */
  private roomPlans = new Map<string, Map<string, (PlanObject & { by_player: string })[]>>();
  /** sessionId -> pending respawn timer. Server-only. */
  private respawns = new Map<string, Delayed>();
  /** sessionId -> Date.now() of the last accepted furniture edit. Server-only. */
  private lastEdit = new Map<string, number>();
  /** Next number for a hand-placed piece's instance_id ("<room_id>/m<n>"), unique per room instance. */
  private manualSeq = 0;

  messages = {
    move: (client: Client, m: MovePayload) => {
      const p = this.state.players.get(client.sessionId);
      if (!p || !p.alive) return;
      const step = Math.hypot(m.x - p.x, m.y - p.y, m.z - p.z);
      if (!Number.isFinite(step) || !Number.isFinite(m.yaw)) return;
      const inGrace = Date.now() < (this.graceUntil.get(client.sessionId) ?? 0);
      if (!inGrace && step > MAX_STEP_M) return; // reject teleports
      p.x = m.x;
      p.y = m.y;
      p.z = m.z;
      p.yaw = m.yaw;
      if ((ANIM_STATES as readonly string[]).includes(m.anim)) p.anim = m.anim;
      // Absent keeps the current weapon (pre-2.7 clients); an unknown value is ignored.
      if (m.weapon !== undefined) {
        const weapon = parseWeapon(m.weapon);
        if (weapon) p.weapon = weapon;
      }
    },
    enter_zone: (client: Client, m: { zone: string }) => {
      const p = this.state.players.get(client.sessionId);
      if (!p || typeof m.zone !== "string" || m.zone === p.zone) return;
      p.zone = m.zone;
      this.allowTeleport(client.sessionId); // block frame <-> house-local frame is always a jump
    },
    set_mode: (_client: Client, m: { mode: string }) => {
      if (m.mode !== "explore" && m.mode !== "foam") return;
      const prev = this.state.mode;
      this.state.mode = m.mode;
      if (m.mode === "explore") {
        for (const t of this.respawns.values()) t.clear();
        this.respawns.clear();
        this.state.players.forEach((p, id) => this.revive(id, p));
      } else if (prev === "explore") {
        this.state.players.forEach((p) => {
          p.score = 0;
          p.hp = FULL_HP;
        });
      }
    },
    chat: (client: Client, m: { text: string }) => {
      const p = this.state.players.get(client.sessionId);
      this.broadcast("toast", { text: `${p?.name ?? "?"}: ${String(m.text).slice(0, 200)}` });
    },
    fire: (client: Client, m: FirePayload) => this.fire(client, m ?? {}),
    furniture_place: (client: Client, m: FurniturePayload) => this.furnitureEdit(client, "place", m ?? {}),
    furniture_move: (client: Client, m: FurniturePayload) => this.furnitureEdit(client, "move", m ?? {}),
    furniture_remove: (client: Client, m: FurniturePayload) => this.furnitureEdit(client, "remove", m ?? {}),
  };

  onCreate(options: JoinOptions) {
    this.state.block_id = options.block_id ?? "hou_navidad_01";
    roomsByBlock.set(this.state.block_id, this);
    // TODO(lane-d): seed the hero house's furniture from packs/.../plans/ (section 8).
  }

  onJoin(client: Client, options: JoinOptions) {
    const p = new Player();
    p.id = client.sessionId;
    p.name = (options.name ?? "player").slice(0, 24);
    this.state.players.set(client.sessionId, p);
    this.allowTeleport(client.sessionId); // the spawn point is anywhere relative to (0, 0, 0)
  }

  onLeave(client: Client) {
    this.state.players.delete(client.sessionId);
    this.state.focus.delete(client.sessionId);
    this.graceUntil.delete(client.sessionId);
    this.lastFire.delete(client.sessionId);
    this.lastEdit.delete(client.sessionId);
    this.respawns.get(client.sessionId)?.clear();
    this.respawns.delete(client.sessionId);
  }

  onDispose() {
    if (roomsByBlock.get(this.state.block_id) === this) roomsByBlock.delete(this.state.block_id);
  }

  onDrop(client: Client) {
    // Hold the seat so a network blip or a deploy reconnect keeps the player and zone.
    this.allowReconnection(client, 30).catch(() => {});
  }

  onReconnect(client: Client) {
    // The client kept simulating while disconnected, so it may be far from its last accepted move.
    this.allowTeleport(client.sessionId);
  }

  /** Let this player's moves skip the step check for TELEPORT_GRACE_MS (zone change, focus, respawn). */
  allowTeleport(playerId: string) {
    if (this.state.players.has(playerId)) this.graceUntil.set(playerId, Date.now() + TELEPORT_GRACE_MS);
  }

  /**
   * Foam fight (AGENTS.md section 11). Any failed check drops the shot silently.
   * Every accepted fire (hit or miss) counts against the rate limit and sends fx to the others.
   */
  private fire(client: Client, m: FirePayload) {
    const id = client.sessionId;
    const shooter = this.state.players.get(id);
    const rawOrigin = parseVec3(m.origin);
    const rawDir = parseVec3(m.dir);
    const weapon = parseWeapon(m.weapon);
    if (this.state.mode !== "foam" || !shooter?.alive || !rawOrigin || !rawDir || !weapon) return;
    const origin = rawOrigin;
    const dir = normalize(rawDir);
    if (!dir || distance(origin, [shooter.x, shooter.y, shooter.z]) > MAX_ORIGIN_OFFSET_M) return;
    const now = Date.now();
    if (now - (this.lastFire.get(id) ?? Number.NEGATIVE_INFINITY) < MIN_FIRE_INTERVAL_MS[weapon]) return;
    this.lastFire.set(id, now);
    this.broadcast("fx", { kind: weapon, origin, dir }, { except: client });

    if (typeof m.target_id !== "string" || m.target_id === id) return;
    const target = this.state.players.get(m.target_id);
    if (!target?.alive || target.zone !== shooter.zone) return;
    const centre: Vec3 = [target.x, target.y, target.z];
    if (distance(origin, centre) >= MAX_RANGE_M[weapon]) return;
    if (weapon === "blaster" && rayPointDistance(origin, dir, centre) > AIM_TOLERANCE_M) return;

    target.hp = Math.max(0, target.hp - DAMAGE[weapon]);
    if (target.hp > 0) target.anim = "hit";
    this.broadcast("hit", { shooter: id, target: target.id, hp: target.hp });
    if (target.hp > 0) return;

    target.alive = false;
    target.anim = "eliminated";
    shooter.score += 1;
    this.broadcast("eliminated", { target: target.id, by: id });
    const targetId = target.id;
    this.respawns.get(targetId)?.clear();
    this.respawns.set(
      targetId,
      this.clock.setTimeout(() => {
        this.respawns.delete(targetId);
        const p = this.state.players.get(targetId);
        if (p) this.revive(targetId, p);
      }, RESPAWN_MS),
    );
  }

  /** Full hp, alive, idle; a player coming back from dead may jump to its (client-chosen) spawn point. */
  private revive(id: string, p: Player) {
    if (!p.alive) this.allowTeleport(id);
    p.hp = FULL_HP;
    p.alive = true;
    p.anim = "idle";
  }

  /**
   * Internal API: replace ONE ROOM's furniture with a validated plan, keeping the
   * house's other rooms. Plans are validated per room polygon, so rooms never overlap.
   * Same room again = last write wins ("make it a playroom instead"); empty objects clear it.
   */
  applyPlan(listingId: string, roomId: string, planId: string, objects: PlanObject[], byPlayer: string) {
    // Map.set on an existing key keeps its position, so House.objects order is stable.
    this.roomsOf(listingId).set(
      roomId,
      objects.map((o) => ({ ...o, instance_id: roomInstanceId(roomId, o.instance_id), by_player: byPlayer })),
    );
    this.rebuildHouse(listingId, planId);
  }

  /**
   * Clear furniture (messages.md v2.6.1): every piece of one room, or of the whole house when roomId is
   * omitted, hand-placed pieces included. Returns the rooms that held something, with how many pieces.
   */
  clearFurniture(listingId: string, roomId?: string): { room_id: string; removed: number }[] {
    const rooms = this.roomsOf(listingId);
    const cleared: { room_id: string; removed: number }[] = [];
    for (const [id, list] of rooms) {
      if ((roomId === undefined || id === roomId) && list.length > 0) {
        cleared.push({ room_id: id, removed: list.length });
        rooms.set(id, []); // an existing key keeps its place, as in applyPlan
      }
    }
    if (cleared.length > 0) this.rebuildHouse(listingId);
    return cleared;
  }

  /**
   * Manual furnishing (messages.md v2.6.0): one piece added, moved or removed by
   * a player standing in the house. Geometry is the client's job (the game has
   * no room polygons); this checks the message's shape, the zone and the limits.
   */
  private furnitureEdit(client: Client, kind: "place" | "move" | "remove", m: FurniturePayload) {
    const id = client.sessionId;
    const p = this.state.players.get(id);
    const reject = (why: string) => client.send("toast", { text: `Could not ${kind} that piece: ${why}` });
    const listingId = m.listing_id;
    if (!p || typeof listingId !== "string" || p.zone !== listingId) return reject("you are not in that house");
    const now = Date.now();
    if (now - (this.lastEdit.get(id) ?? Number.NEGATIVE_INFINITY) < FURNITURE_EDIT_INTERVAL_MS) return;
    this.lastEdit.set(id, now);

    const rooms = this.roomsOf(listingId);
    const found = typeof m.instance_id === "string" ? findPiece(rooms, m.instance_id) : null;
    if (kind !== "place" && !found) return reject("it is no longer there");

    if (kind === "remove") {
      found!.list.splice(found!.index, 1);
      return this.rebuildHouse(listingId);
    }

    const roomId = m.room_id;
    if (typeof roomId !== "string" || !roomId.startsWith(`${listingId}.`)) return reject("not a room of this house");
    const pose = parsePose(m);
    if (!pose) return reject("bad position");

    if (kind === "place") {
      if (typeof m.asset_id !== "string" || !ASSET_ID.test(m.asset_id)) return reject("unknown asset");
      let count = 0;
      for (const list of rooms.values()) count += list.length;
      if (count >= MAX_PIECES_PER_HOUSE) return reject(`a house holds at most ${MAX_PIECES_PER_HOUSE} pieces`);
      const list = rooms.get(roomId) ?? [];
      rooms.set(roomId, list);
      list.push({ instance_id: `${roomId}/m${++this.manualSeq}`, asset_id: m.asset_id, ...pose, reason: "", by_player: id });
      return this.rebuildHouse(listingId);
    }

    // move: same room keeps the id; another room re-files it under that room.
    const piece = found!.list[found!.index];
    const moved = { ...piece, ...pose, reason: "", by_player: id };
    if (found!.roomId === roomId) {
      found!.list[found!.index] = moved;
    } else {
      found!.list.splice(found!.index, 1);
      const list = rooms.get(roomId) ?? [];
      rooms.set(roomId, list);
      list.push({ ...moved, instance_id: `${roomId}/m${++this.manualSeq}` });
    }
    this.rebuildHouse(listingId);
  }

  private roomsOf(listingId: string) {
    let rooms = this.roomPlans.get(listingId);
    if (!rooms) this.roomPlans.set(listingId, (rooms = new Map()));
    return rooms;
  }

  /** House.objects from every room of the house, rooms in first-furnished order. */
  private rebuildHouse(listingId: string, planId?: string) {
    const rooms = this.roomsOf(listingId);
    const house = new House();
    house.listing_id = listingId;
    // Latest plan for this house; manual edits keep it ("manual" if there never was one).
    house.plan_id = planId ?? this.state.houses.get(listingId)?.plan_id ?? "manual";
    for (const roomObjects of rooms.values()) {
      for (const o of roomObjects) {
        const placed = new Placed();
        placed.instance_id = o.instance_id;
        placed.asset_id = o.asset_id;
        placed.x = o.x;
        placed.y = o.y;
        placed.z = o.z;
        placed.rot_deg = o.rot_deg;
        placed.reason = o.reason ?? "";
        placed.by_player = o.by_player;
        house.objects.push(placed);
      }
    }
    this.state.houses.set(listingId, house);
  }
}

type RoomPieces = Map<string, (PlanObject & { by_player: string })[]>;

function findPiece(rooms: RoomPieces, instanceId: string) {
  for (const [roomId, list] of rooms) {
    const index = list.findIndex((o) => o.instance_id === instanceId);
    if (index >= 0) return { roomId, list, index };
  }
  return null;
}
