// Lane C: other players' shots (store.fx), pure parts. No React, no three.js;
// tested in remoteFx.test.ts. RemoteShots.tsx draws what these let through.
//
// The server's `fx` broadcast is {kind: weapon, origin, dir}: it goes to
// everyone but the shooter, in every zone, with no shooter id and no zone.
// origin is the shooter's head in the shooter's zone frame. So the zone test
// is geometric: keep an event only if its origin is near a remote player who
// is drawn in my zone right now.

import type { FxEvent } from "../store";
import type { Vec3 } from "./remoteInterp";

/** An origin this close (3D) to a drawn remote body centre counts as that player's shot. */
export const FX_MATCH_M = 1.5;

/**
 * Keep only events fired by a remote player in my zone: the origin within
 * FX_MATCH_M of one of `remotes` (interpolated body centres, as drawn). A shot
 * from another zone is in another frame, so it matches nobody and is dropped.
 */
export function fxInMyZone(fx: FxEvent, remotes: readonly Vec3[]): boolean {
  const [x, y, z] = fx.origin;
  return remotes.some((p) => Math.hypot(p[0] - x, p[1] - y, p[2] - z) <= FX_MATCH_M);
}

/** Every id already in the list: mark them seen on mount, so a reload never replays old shots. */
export function seenIds(fx: readonly FxEvent[]): Set<number> {
  return new Set(fx.map((e) => e.id));
}

/**
 * Events not played yet, in list order; marks them seen. `seen` is trimmed to
 * ids still in the list (ids are never reused and the list only drops its
 * oldest), so it stays at most the list's size.
 */
export function takeUnseen(fx: readonly FxEvent[], seen: Set<number>): FxEvent[] {
  const fresh = fx.filter((e) => !seen.has(e.id));
  const live = new Set(fx.map((e) => e.id));
  for (const id of seen) if (!live.has(id)) seen.delete(id);
  for (const e of fresh) seen.add(e.id);
  return fresh;
}

/** Unit vector, or null for a zero or non-finite direction. */
export function unitDir(d: Vec3): Vec3 | null {
  const n = Math.hypot(d[0], d[1], d[2]);
  if (!Number.isFinite(n) || n < 1e-6) return null;
  return [d[0] / n, d[1] / n, d[2] / n];
}

/**
 * Yaw (rotation about +Y) that turns local +X onto dir's horizontal part. The
 * swoosh arc is built centred on +X, so this centres it on the swing. Null if
 * dir is (nearly) vertical.
 */
export function swooshYaw(dir: Vec3): number | null {
  if (Math.hypot(dir[0], dir[2]) < 1e-6) return null;
  return Math.atan2(-dir[2], dir[0]);
}
