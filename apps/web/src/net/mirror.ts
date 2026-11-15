// Lane D: structural sharing for the Colyseus -> store mirror. Patches arrive ~20 Hz per player
// (every `move`); only the parts that actually changed get a new identity, so furniture, focus
// and unchanged players keep their references and their subscribers do not re-render.
import type { AnimState, GameMode } from "../../../../contracts/game-state";
import type { HouseFurniture, NetPlayer } from "../store";

export type PlayerJSON = Omit<NetPlayer, "pos"> & { x: number; y: number; z: number };
export type HouseJSON = HouseFurniture & { listing_id: string };
export type StateJSON = {
  block_id: string;
  mode: GameMode;
  players: Record<string, PlayerJSON>;
  houses: Record<string, HouseJSON>;
  focus: Record<string, string>;
};

export type MirrorPrev = {
  mode: GameMode;
  players: Record<string, NetPlayer>;
  houses: Record<string, HouseFurniture>;
  focus: Record<string, string>;
};
export type MirrorPatch = Partial<MirrorPrev>;

/** Same keys, and every value strictly equal, except keys in `skip`. */
function shallowEqual(a: object, b: object, skip?: string): boolean {
  const ra = a as Record<string, unknown>;
  const rb = b as Record<string, unknown>;
  const ka = Object.keys(ra);
  if (ka.length !== Object.keys(rb).length) return false;
  for (const k of ka) {
    if (k === skip) continue;
    if (!(k in rb) || ra[k] !== rb[k]) return false;
  }
  return true;
}

function mirrorPlayer(prev: NetPlayer | undefined, p: PlayerJSON): NetPlayer {
  const next: NetPlayer = { ...p, anim: p.anim as AnimState, pos: [p.x, p.y, p.z] };
  if (
    prev &&
    prev.pos[0] === next.pos[0] &&
    prev.pos[1] === next.pos[1] &&
    prev.pos[2] === next.pos[2] &&
    shallowEqual(prev, next, "pos")
  ) {
    return prev;
  }
  return next;
}

function mirrorHouse(prev: HouseFurniture | undefined, h: HouseJSON): HouseFurniture {
  const objects = h.objects ?? [];
  if (
    prev &&
    shallowEqual(prev, { ...h, objects }, "objects") &&
    prev.objects.length === objects.length &&
    prev.objects.every((o, i) => shallowEqual(o, objects[i]))
  ) {
    return prev;
  }
  return { ...h, objects: objects.map((o) => ({ ...o })) };
}

/** Rebuild `next` from JSON entries, reusing `prev` entries (and `prev` itself) when unchanged. */
function mirrorRecord<P, J>(
  prev: Record<string, P>,
  json: Record<string, J>,
  one: (p: P | undefined, j: J) => P,
): Record<string, P> {
  const next: Record<string, P> = {};
  let same = Object.keys(prev).length === Object.keys(json).length;
  for (const [id, j] of Object.entries(json)) {
    const p = Object.prototype.hasOwnProperty.call(prev, id) ? prev[id] : undefined;
    next[id] = one(p, j);
    if (next[id] !== p) same = false;
  }
  return same ? prev : next;
}

/**
 * Diff a Colyseus `state.toJSON()` against the current store slice. Returns only the fields whose
 * reference changed, or null when nothing did.
 */
export function mirrorState(prev: MirrorPrev, j: StateJSON): MirrorPatch | null {
  const patch: MirrorPatch = {};
  const players = mirrorRecord(prev.players, j.players ?? {}, mirrorPlayer);
  if (players !== prev.players) patch.players = players;
  const houses = mirrorRecord(prev.houses, j.houses ?? {}, mirrorHouse);
  if (houses !== prev.houses) patch.houses = houses;
  const focus = mirrorRecord(prev.focus, j.focus ?? {}, (_p: string | undefined, v: string) => v);
  if (focus !== prev.focus) patch.focus = focus;
  if (j.mode !== prev.mode) patch.mode = j.mode;
  return Object.keys(patch).length ? patch : null;
}
