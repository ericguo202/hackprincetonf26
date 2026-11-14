// Lane C: the six wire animation states mapped to the character pack's exact
// clip names (AGENTS.md section 11: record these before writing animation code).
//
// Pack: Quaternius "Toon Shooter Game Kit" (Dec 2022), Characters/glTF/
// Character_Soldier.gltf. The names below were printed from that file's
// animations[] on 2026-11-14; Character_Enemy and Character_Hazmat carry the
// identical 17 clips. The animations ship in the model: nothing is retargeted
// (AGENTS.md section 5).
import type { AnimState } from "../../../../contracts/game-state";

/** Every animations[].name in Character_Soldier.gltf, in file order. */
export const PACK_CLIPS = [
  "Death",
  "Duck",
  "HitReact",
  "Idle",
  "Idle_Shoot",
  "Jump",
  "Jump_Idle",
  "Jump_Land",
  "No",
  "Punch",
  "Run",
  "Run_Gun",
  "Run_Shoot",
  "Walk",
  "Walk_Shoot",
  "Wave",
  "Yes",
] as const;

export type PackClip = (typeof PACK_CLIPS)[number];

/**
 * State -> clip. run uses Run_Gun (the gun stays raised while running, so no
 * upper/lower body masking); no strafe clips exist or are used.
 */
export const CLIPS: Record<AnimState, PackClip> = {
  idle: "Idle",
  run: "Run_Gun",
  jump: "Jump",
  shoot: "Idle_Shoot",
  hit: "HitReact",
  eliminated: "Death",
};

/**
 * shoot while moving: Run_Shoot keeps the legs running, so holding the
 * trigger on the move does not slide a standing pose across the floor.
 * Needs the optional `moving` input; without it, shoot is Idle_Shoot.
 */
export const MOVING_SHOOT: PackClip = "Run_Shoot";

/**
 * One-shot clips: play once and hold the last frame, or hand over to `then`.
 * Jump is a 0.3 s takeoff; the jump state lasts the whole airtime, so it
 * continues into the Jump_Idle loop. Everything else loops.
 */
export const ONE_SHOT: Partial<Record<PackClip, { then?: PackClip }>> = {
  Jump: { then: "Jump_Idle" },
  HitReact: {},
  Death: {},
};

export const CROSSFADE_S = 0.2;

export type ClipPlan = { clip: PackClip; once: boolean; then?: PackClip };

/** Which clip to play for a wire state (pure; Character.tsx does the mixing). */
export function clipFor(state: AnimState, moving = false): ClipPlan {
  const clip = state === "shoot" && moving ? MOVING_SHOOT : CLIPS[state];
  const shot = ONE_SHOT[clip];
  return shot ? { clip, once: true, ...(shot.then ? { then: shot.then } : {}) } : { clip, once: false };
}
