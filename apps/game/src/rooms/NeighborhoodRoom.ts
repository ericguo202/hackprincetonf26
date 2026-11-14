// Lane D: the shared world for one block (AGENTS.md section 8, contracts/messages.md).
// Scaffold: join/leave, zones, clamped movement relay, mode toggle. Hit validation is TODO.
import { type Client, Room } from "colyseus";
import { ANIM_STATES, House, Placed, Player, RoomState } from "../../../../contracts/game-state.ts";

const MAX_STEP_M = 3;

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

  messages = {
    move: (client: Client, m: MovePayload) => {
      const p = this.state.players.get(client.sessionId);
      if (!p || !p.alive) return;
      const step = Math.hypot(m.x - p.x, m.y - p.y, m.z - p.z);
      if (!Number.isFinite(step) || step > MAX_STEP_M) return; // reject teleports
      p.x = m.x;
      p.y = m.y;
      p.z = m.z;
      p.yaw = m.yaw;
      if ((ANIM_STATES as readonly string[]).includes(m.anim)) p.anim = m.anim;
    },
    enter_zone: (client: Client, m: { zone: string }) => {
      const p = this.state.players.get(client.sessionId);
      if (p && typeof m.zone === "string") p.zone = m.zone;
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
    this.state.block_id = options.block_id ?? "hou_heights_01";
    roomsByBlock.set(this.state.block_id, this);
    // TODO(lane-d): seed the hero house's furniture from packs/.../plans/ (section 8).
  }

  onJoin(client: Client, options: JoinOptions) {
    const p = new Player();
    p.id = client.sessionId;
    p.name = (options.name ?? "player").slice(0, 24);
    this.state.players.set(client.sessionId, p);
  }

  onLeave(client: Client) {
    this.state.players.delete(client.sessionId);
    this.state.focus.delete(client.sessionId);
  }

  onDispose() {
    if (roomsByBlock.get(this.state.block_id) === this) roomsByBlock.delete(this.state.block_id);
  }

  onDrop(client: Client) {
    // Hold the seat so a network blip or a deploy reconnect keeps the player and zone.
    this.allowReconnection(client, 30).catch(() => {});
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
