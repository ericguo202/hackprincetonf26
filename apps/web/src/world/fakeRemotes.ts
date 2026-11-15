// Lane C, dev only (never in a production build): VITE_DEV_REMOTES=1 puts two fake remote players in my
// zone, so remote rendering, interpolation and hitboxes can be exercised with
// no game server. Logic only (paths, a stand-in for the server's hit rules);
// DevRemotes.tsx feeds the results into store.players at 20 Hz.
//
// The first fake runs a circle around the room's centre. The second walks a
// line back and forth, pausing at each end and shooting while paused.
// devFire() is passed to <Player onFire> in this mode: it logs the payload and
// plays a stand-in for Lane D's server: 3 blaster or 2 noodle hits eliminate, respawn after 3 s.

import type { AnimState } from "../../../../contracts/game-state";
import type { Point2 } from "../contracts.gen";
import type { FirePayload } from "../player/blasterMath";
import { WEAPON_RULES } from "../player/weapons";

/**
 * Dev builds only. The env var alone is not enough: a production build
 * (`vite build`) replaces import.meta.env.DEV with false, so the fakes are
 * dead code there and dropped from the bundle even if VITE_DEV_REMOTES is set
 * on the host. Never deploy these. Written without `?.` so Vite can
 * replace it statically.
 */
export const DEV_REMOTES_ON = import.meta.env.DEV && Boolean(import.meta.env.VITE_DEV_REMOTES);
// Ids picked so variantFor gives two different looks (Hazmat, Red helmet), both
// unlike the local player's Soldier in SOLO (characterAsset.ts variantFor).
export const DEV_REMOTE_IDS = ["dev-remote-5", "dev-remote-2"] as const;
// Stand-in for Lane D's server: health 6, so the blaster's 3 hits and the
// noodle's 2 (WEAPON_RULES) both divide it evenly.
const FULL_HP = 6;
const RESPAWN_S = 3;
const HIT_S = 0.35;
const RUN_SPEED = 2.0;

type Fake = { hp: number; hitUntil: number; deadUntil: number };
// Built on first use, not at module load: nothing here runs in a production
// build (DEV_REMOTES_ON is false there), so the bundler can drop all of it.
let fakeState: Map<string, Fake> | null = null;
const fakes = (): Map<string, Fake> =>
  (fakeState ??= new Map(DEV_REMOTE_IDS.map((id) => [id, { hp: FULL_HP, hitUntil: 0, deadUntil: 0 }])));

export type FakePose = { pos2: Point2; yaw: number; moving: boolean; shooting: boolean };

/** yaw that turns the model's +Z onto a direction (three.js rotation-y). */
const yawTo = (dx: number, dz: number) => Math.atan2(dx, dz);

/** Scripted pose at time t (s) around `centre`, staying within `radius`. */
export function fakePose(id: string, t: number, centre: Point2, radius: number): FakePose {
  const [cx, cz] = centre;
  if (id === DEV_REMOTE_IDS[0]) {
    const w = RUN_SPEED / radius;
    const a = t * w;
    return {
      pos2: [cx + radius * Math.cos(a), cz + radius * Math.sin(a)],
      yaw: yawTo(-Math.sin(a), Math.cos(a)),
      moving: true,
      shooting: false,
    };
  }
  // Back and forth on a line through the centre: pause, walk, pause, walk back.
  const L = Math.min(radius, 1.4);
  const period = 6;
  const p = ((t % period) + period) % period;
  const A: Point2 = [cx - L, cz];
  const B: Point2 = [cx + L, cz];
  if (p < 1.5) return { pos2: A, yaw: yawTo(1, 0), moving: false, shooting: p > 0.4 && p < 1.1 };
  if (p < 3) {
    const k = (p - 1.5) / 1.5;
    return { pos2: [A[0] + (B[0] - A[0]) * k, cz], yaw: yawTo(1, 0), moving: true, shooting: false };
  }
  if (p < 4.5) return { pos2: B, yaw: yawTo(-1, 0), moving: false, shooting: p > 3.4 && p < 4.1 };
  const k = (p - 4.5) / 1.5;
  return { pos2: [B[0] + (A[0] - B[0]) * k, cz], yaw: yawTo(-1, 0), moving: true, shooting: false };
}

export function fakeAlive(id: string, t: number): boolean {
  return (fakes().get(id)?.deadUntil ?? 0) <= t;
}

/** Wire state for a fake, the way the server would derive it. */
export function fakeAnim(id: string, t: number, pose: FakePose): AnimState {
  const f = fakes().get(id);
  if (f && f.deadUntil > t) return "eliminated";
  if (f && f.hitUntil > t) return "hit";
  if (pose.shooting) return "shoot";
  return pose.moving ? "run" : "idle";
}

/** <Player onFire> in dev-remotes mode: log the payload, apply the stand-in hit rules. */
export function devFire(p: FirePayload, t = performance.now() / 1000): void {
  console.info("[dev remotes] fire", JSON.stringify(p));
  const f = p.target_id ? fakes().get(p.target_id) : undefined;
  if (!f || f.deadUntil > t) return;
  f.hp -= FULL_HP / WEAPON_RULES[p.weapon ?? "blaster"].hitsToEliminate;
  if (f.hp <= 0) {
    f.deadUntil = t + RESPAWN_S;
    f.hp = FULL_HP;
  } else {
    f.hitUntil = t + HIT_S;
  }
}

/** Tests only. */
export function resetFakes(): void {
  for (const f of fakes().values()) Object.assign(f, { hp: FULL_HP, hitUntil: 0, deadUntil: 0 });
}
