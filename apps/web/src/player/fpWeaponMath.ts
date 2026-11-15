// Lane C: first-person view model, pure parts (FirstPersonWeapon.tsx draws it).
// When the camera is so close that the body is hidden (armMath
// ARM.hideBodyBelow), the held weapon is drawn on the camera instead, lower
// right. Tested in fpWeaponMath.test.ts.

import type { Weapon } from "../../../../contracts/game-state";

/** Rest offset from the camera, metres in camera space (+X right, +Y up, -Z forward). */
export const FP_OFFSET = { right: 0.25, down: 0.22, forward: 0.45 } as const;

/** Blaster kick on a shot: back toward the camera, nose up, over FP_KICK_S. */
export const FP_KICK_S = 0.12;
export const FP_KICK_BACK_M = 0.06;
export const FP_KICK_PITCH = 0.12; // radians
/** Noodle swing: a short sweep to the left and back over FP_SWEEP_S. */
export const FP_SWEEP_S = 0.28;
export const FP_SWEEP_M = 0.22;
export const FP_SWEEP_YAW = 0.6; // radians
/** Idle sway: a slow figure-of-eight, millimetres. */
export const FP_SWAY_M = { x: 0.004, y: 0.003 } as const;

export type FpVisibility = {
  /** The camera arm hid the body (first person, or a wall pushed the camera in). */
  bodyHidden: boolean;
  /** The held prop: null in explore mode (foamMode.heldWeaponProp). */
  weapon: Weapon | null;
  alive: boolean;
};

/** Draw the view model only in first person, holding a weapon (foam mode), while alive. */
export function fpVisible(v: FpVisibility): boolean {
  return v.bodyHidden && v.weapon !== null && v.alive;
}

/** Kick strength `age` s after a shot: 1 at the shot, easing to 0 at FP_KICK_S; 0 outside. */
export function kickCurve(age: number): number {
  if (!(age >= 0) || age >= FP_KICK_S) return 0;
  const f = 1 - age / FP_KICK_S;
  return f * f;
}

/** Sweep amount `age` s after a swing: 0 -> 1 (fully left) -> 0 over FP_SWEEP_S; 0 outside. */
export function sweepCurve(age: number): number {
  if (!(age >= 0) || age >= FP_SWEEP_S) return 0;
  return Math.sin((Math.PI * age) / FP_SWEEP_S);
}

/** Idle sway at time t (s), metres: x and y. Written into `out`. */
export function sway(t: number, out: { x: number; y: number }): { x: number; y: number } {
  out.x = FP_SWAY_M.x * Math.sin(t * 1.3);
  out.y = FP_SWAY_M.y * Math.sin(t * 2.6);
  return out;
}

export type FpPose = { x: number; y: number; z: number; pitch: number; yaw: number };

/**
 * View model offset (camera space) and extra rotation for this frame: the rest
 * offset, idle sway, and the last attack's kick (blaster) or sweep (noodle).
 * `attackAge` is seconds since the last attack with `attackWeapon`, or null.
 */
export function fpPose(
  weapon: Weapon,
  t: number,
  attackWeapon: Weapon | null,
  attackAge: number | null,
  out: FpPose,
  tmp: { x: number; y: number } = { x: 0, y: 0 },
): FpPose {
  sway(t, tmp);
  out.x = FP_OFFSET.right + tmp.x;
  out.y = -FP_OFFSET.down + tmp.y;
  out.z = -FP_OFFSET.forward;
  out.pitch = 0;
  out.yaw = 0;
  if (attackAge === null || attackWeapon !== weapon) return out;
  if (weapon === "blaster") {
    const k = kickCurve(attackAge);
    out.z += FP_KICK_BACK_M * k;
    out.pitch += FP_KICK_PITCH * k;
  } else {
    const s = sweepCurve(attackAge);
    out.x -= FP_SWEEP_M * s;
    out.yaw += FP_SWEEP_YAW * s;
  }
  return out;
}
