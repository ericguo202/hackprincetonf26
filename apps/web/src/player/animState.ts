// Lane C: translate ecctrl's locomotion states into the six states the
// network contract carries (contracts/game-state.ts ANIM_STATES).
//
// ecctrl reports seven: IDLE, WALK, RUN, JUMP_START, JUMP_IDLE, JUMP_FALL,
// JUMP_LAND. The wire carries six: idle, run, jump, shoot, hit, eliminated.
// The last three are game events, not locomotion, so they arrive as an
// `action` and win over whatever the legs are doing.
//
// Pure on purpose: this is the one piece of the character that can be tested
// without a browser, and it decides what every other player sees you doing.

import type { AnimState } from "../../../../contracts/game-state";

/** ecctrl's EcctrlAnimationState, inlined so this file imports no runtime deps. */
export type LocomotionState =
  | "IDLE"
  | "WALK"
  | "RUN"
  | "JUMP_START"
  | "JUMP_IDLE"
  | "JUMP_FALL"
  | "JUMP_LAND";

/** Game events that override locomotion, most severe first. */
export type PlayerAction = "eliminated" | "hit" | "shoot" | null;

const LOCOMOTION: Record<LocomotionState, AnimState> = {
  IDLE: "idle",
  // The wire has no "walk". Collapsing walk into run keeps remote players
  // moving their legs; add a seventh state to the contract if it looks wrong.
  WALK: "run",
  RUN: "run",
  JUMP_START: "jump",
  JUMP_IDLE: "jump",
  JUMP_FALL: "jump",
  JUMP_LAND: "jump",
};

/**
 * Priority: eliminated > hit > crouch > shoot > locomotion.
 *
 * Being eliminated or hit shows whatever the legs are doing. Crouch beats
 * shoot: a crouched player firing over cover stays crouched on the wire, so
 * remote players see (and hit) the lower body that is actually there; the
 * shot itself still shows as a tracer and muzzle flash.
 */
export function toAnimState(
  locomotion: LocomotionState,
  action: PlayerAction = null,
  crouched = false,
): AnimState {
  if (action === "eliminated" || action === "hit") return action;
  if (crouched) return "crouch";
  return action ?? LOCOMOTION[locomotion];
}

/**
 * How long a one-shot action keeps overriding locomotion, in seconds.
 * `eliminated` has no duration — it ends when the player respawns.
 */
export const ACTION_HOLD_S: Record<Exclude<PlayerAction, null>, number> = {
  shoot: 0.25,
  hit: 0.35,
  eliminated: Infinity,
};

/** True when `next` is worth sending; avoids spamming the socket. */
export function animChanged(prev: AnimState, next: AnimState): boolean {
  return prev !== next;
}
