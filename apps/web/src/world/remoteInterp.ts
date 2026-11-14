// Lane C: remote players, pure parts (AGENTS.md section 8). No React, no
// three.js; tested in remoteInterp.test.ts.
//
// Lane D's net code writes store.players from Colyseus state patches (about
// 20 Hz) with no timestamps. We stamp each patch on arrival, keep a short
// buffer per player, and render INTERP_DELAY_S behind now, lerping between
// the two snapshots around that time. So remote players move smoothly between
// patches and the rendered transform is the one everything else reads:
// hitboxes come from the same interpolated sample (the HANDOFF rule: never a
// raw, un-interpolated position).
//
// NetPlayer.pos is the body centre, the same point the local Player writes to
// me.pos. The model's feet are FOOT_BELOW_CENTRE_M below it.

import type { AnimState } from "../../../../contracts/game-state";
import type { NetPlayer } from "../store";
import { CHARACTER, FOOT_BELOW_CENTRE_M } from "../player/characterAsset";
import type { Hitbox } from "../player/blasterMath";
import { MOVEMENT } from "../player/movement";

export type Vec3 = [number, number, number];

/** Render this far behind the newest patch (AGENTS.md section 8: about 100 ms). */
export const INTERP_DELAY_S = 0.1;
/** Keep about a second of patches; older ones can never be sampled again. */
const MAX_SNAPSHOTS = 24;
/** Above this speed (m/s) a remote player counts as moving (shoot -> Run_Shoot). */
export const MOVING_SPEED = 0.5;

export type Snapshot = {
  t: number;
  pos: Vec3;
  yaw: number;
  anim: AnimState;
  zone: string;
  alive: boolean;
};

export type Sample = Omit<Snapshot, "t"> & { speed: number };

/**
 * Add a patch to a player's buffer (in time order). A zone change clears the
 * buffer: positions in different zones are in different frames, so lerping
 * across one would drag the player through the world.
 */
export function pushSnapshot(buf: Snapshot[], snap: Snapshot): Snapshot[] {
  const last = buf[buf.length - 1];
  if (last && last.zone !== snap.zone) return [snap];
  if (last && snap.t < last.t) return buf; // out of order: drop
  const next = [...buf, snap];
  return next.length > MAX_SNAPSHOTS ? next.slice(next.length - MAX_SNAPSHOTS) : next;
}

/** Shortest-way angle lerp, so a turn through +-pi does not spin the long way. */
export function lerpAngle(a: number, b: number, k: number): number {
  let d = (b - a) % (2 * Math.PI);
  if (d > Math.PI) d -= 2 * Math.PI;
  if (d < -Math.PI) d += 2 * Math.PI;
  return a + d * k;
}

/**
 * The transform at render time `t` (already delayed by the caller, or use
 * sampleDelayed). Between two patches: position and yaw lerp; anim, zone and
 * alive are discrete and come from the older patch. Before the first patch:
 * the first. After the last: the last, held (no extrapolation).
 */
export function sampleAt(buf: Snapshot[], t: number): Sample | null {
  if (buf.length === 0) return null;
  if (t <= buf[0].t) return { ...strip(buf[0]), speed: 0 };
  const last = buf[buf.length - 1];
  if (t >= last.t) return { ...strip(last), speed: 0 };
  let i = 0;
  while (i < buf.length - 2 && buf[i + 1].t <= t) i++;
  const a = buf[i];
  const b = buf[i + 1];
  const span = b.t - a.t;
  const k = span > 0 ? (t - a.t) / span : 1;
  const pos: Vec3 = [
    a.pos[0] + (b.pos[0] - a.pos[0]) * k,
    a.pos[1] + (b.pos[1] - a.pos[1]) * k,
    a.pos[2] + (b.pos[2] - a.pos[2]) * k,
  ];
  const dist = Math.hypot(b.pos[0] - a.pos[0], b.pos[2] - a.pos[2]);
  return { ...strip(a), pos, yaw: lerpAngle(a.yaw, b.yaw, k), speed: span > 0 ? dist / span : 0 };
}

export function sampleDelayed(buf: Snapshot[], now: number, delay = INTERP_DELAY_S): Sample | null {
  return sampleAt(buf, now - delay);
}

function strip(s: Snapshot): Omit<Snapshot, "t"> {
  return { pos: s.pos, yaw: s.yaw, anim: s.anim, zone: s.zone, alive: s.alive };
}

/** Other players in my zone (never me). Sorted by id so the render order is stable. */
export function remotesInZone(
  players: Record<string, NetPlayer>,
  myZone: string,
  myId: string | null,
): NetPlayer[] {
  return Object.values(players)
    .filter((p) => p.id !== myId && p.zone === myZone)
    .sort((a, b) => a.id.localeCompare(b.id));
}

/** Where the model's feet are for a body centre. The model's origin is its feet. */
export function feetFromBody(body: Vec3): Vec3 {
  return [body[0], body[1] - FOOT_BELOW_CENTRE_M, body[2]];
}

/**
 * A remote player's shot hitbox, matched to the MODEL (feet to feet +
 * CHARACTER.heightM), not to the floating capsule (feet + floatHeight up to
 * feet + floatHeight + capsule height). Otherwise a dart at the knees would
 * miss and one just over the head would hit.
 */
export function hitboxFromBody(id: string, body: Vec3): Hitbox {
  const feet = feetFromBody(body);
  const radius = MOVEMENT.capsuleRadius;
  return {
    id,
    centre: [feet[0], feet[1] + CHARACTER.heightM / 2, feet[2]],
    radius,
    halfHeight: Math.max(0, CHARACTER.heightM / 2 - radius),
  };
}
