// Lane D: Colyseus client. Joins NeighborhoodRoom for a block, mirrors state into the store,
// sends `move` at 20 Hz from store.me. SOLO=1 (VITE_SOLO) skips the server entirely.
import { Client, type Room } from "@colyseus/sdk";
import type { GameMode } from "../../../../contracts/game-state";
import type { FirePayload } from "../player/blasterMath";
import { useWeaponState } from "../player/weaponState";
import { useStore } from "../store";
import { applyBroadcast, BROADCAST_TYPES } from "./broadcasts";
import { applyLocalEdit, type FurnitureEdit } from "./furnitureEdits";
import { mirrorState, type StateJSON } from "./mirror";

const GAME_URL = import.meta.env.VITE_GAME_URL ?? "ws://localhost:2567";
const SOLO = import.meta.env.VITE_SOLO === "1";
const MOVE_HZ = 20;

let room: Room | null = null;

export async function joinBlock(blockId: string, name: string): Promise<void> {
  const s = useStore.getState();
  s.set({ blockId });
  if (SOLO) return;

  room = await new Client(GAME_URL).joinOrCreate("neighborhood", { block_id: blockId, name });
  s.setMe({ id: room.sessionId });
  s.set({ connected: true });

  room.onStateChange((state) => {
    const j = (state as { toJSON(): StateJSON }).toJSON();
    const s = useStore.getState();
    const patch = mirrorState(s, j);
    if (patch) s.set(patch);
  });
  for (const type of BROADCAST_TYPES) room.onMessage(type, (data: unknown) => applyBroadcast(type, data));
  room.onLeave(() => useStore.getState().set({ connected: false }));

  // The server starts every player in "block"; if we join already inside a house, the first tick tells it.
  let lastZone = "block";
  setInterval(() => {
    const me = useStore.getState().me;
    if (!room) return;
    if (me.zone !== lastZone) {
      room.send("enter_zone", { zone: me.zone });
      lastZone = me.zone;
    }
    const weapon = useWeaponState.getState().weapon;
    room.send("move", { x: me.pos[0], y: me.pos[1], z: me.pos[2], yaw: me.yaw, anim: me.anim, weapon });
  }, 1000 / MOVE_HZ);
}

/** Player.onFire: send `fire` as the blaster builds it. Dropped while eliminated or offline. */
export function sendFire(p: FirePayload) {
  const s = useStore.getState();
  if (!room || !s.me.id || s.players[s.me.id]?.alive === false) return;
  room.send("fire", p);
}

export function setMode(mode: GameMode) {
  if (room) room.send("set_mode", { mode });
  else useStore.getState().set({ mode });
}

/**
 * Manual furnishing (messages.md v2.6.0): one place / move / remove for the
 * house I am in. The piece reaches every player (me included) through the
 * next state patch. Without a game server (SOLO, or offline in dev) the same
 * edit is applied to the local store instead.
 */
export function sendFurnitureEdit(edit: FurnitureEdit) {
  const s = useStore.getState();
  if (room) {
    room.send(edit.type, edit.payload);
    return;
  }
  const houses = applyLocalEdit(s.houses, edit, s.me.id ?? "solo");
  if (houses) s.set({ houses });
}
