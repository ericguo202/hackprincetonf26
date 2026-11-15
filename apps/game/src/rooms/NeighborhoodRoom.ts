// Lane D: the shared world for one block (AGENTS.md section 8, contracts/messages.md).
// Join/leave, zones, clamped movement relay (with a teleport grace window), mode toggle. Hit validation is TODO.
import { type Client, Room } from "colyseus";
import { ANIM_STATES, House, Placed, Player, RoomState } from "../../../../contracts/game-state.ts";

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
type MovePayload = { x: number; y: number; z: number; yaw: number; anim: string };

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
    },
    enter_zone: (client: Client, m: { zone: string }) => {
      const p = this.state.players.get(client.sessionId);
      if (!p || typeof m.zone !== "string" || m.zone === p.zone) return;
      p.zone = m.zone;
      this.allowTeleport(client.sessionId); // block frame <-> house-local frame is always a jump
    },
    set_mode: (_client: Client, m: { mode: string }) => {
      if (m.mode === "explore" || m.mode === "foam") this.state.mode = m.mode;
    },
    chat: (client: Client, m: { text: string }) => {
      const p = this.state.players.get(client.sessionId);
      this.broadcast("toast", { text: `${p?.name ?? "?"}: ${String(m.text).slice(0, 200)}` });
    },
    fire: (_client: Client, _m: unknown) => {
      // TODO(lane-d): validate per AGENTS.md section 11 and broadcast hit / eliminated / fx.
    },
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

  /** Internal API: replace one house's furniture with a validated plan. */
  applyPlan(listingId: string, planId: string, objects: PlanObject[], byPlayer: string) {
    const house = new House();
    house.listing_id = listingId;
    house.plan_id = planId;
    for (const o of objects) {
      const placed = new Placed();
      Object.assign(placed, o, { by_player: byPlayer });
      house.objects.push(placed);
    }
    this.state.houses.set(listingId, house);
  }
}
