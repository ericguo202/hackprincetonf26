// Lane C: first-person view model, pure parts (FirstPersonWeapon.tsx draws it).
// When the camera is so close that the body is hidden (armMath
// ARM.hideBodyBelow), the held weapon is drawn on the camera instead, held up
// in the lower-right corner like an item in a block game: small, at a fixed
// angle pointing up-left toward the screen centre, never crossing the centre
// third (the crosshair area). Tested in fpWeaponMath.test.ts, including the
// screen coverage at 16:9 and 16:10.

import type { Weapon } from "../../../../contracts/game-state";

/**
 * Rest offset from the camera, metres in camera space (+X right, +Y up, -Z
 * forward). Started at 0.32 / 0.30 / 0.55 with 25 deg yaw; at that the blaster's
 * tip reached 0.05 NDC into the centre third, so it sits a little further
 * right and down, turned 20 deg (screen coverage test, fpWeaponMath.test.ts).
 */
export const FP_OFFSET = { right: 0.36, down: 0.31, forward: 0.55 } as const;
/** View model scale (the props are kept at their world size times this). */
export const FP_SCALE = 0.6;
/** The noodle is this much of its length in first person, so it doesn't fill the screen. */
export const FP_NOODLE_LENGTH = 0.6;
/**
 * The noodle's own direction in the held item's frame (before FP_TILT): up and
 * forward. The aim pose held it pointing up-right, off the screen edge; this
 * puts its tip at about NDC (0.48, -0.04), pointing up-left toward the centre
 * without reaching the centre third (coverage test).
 */
export const FP_NOODLE_DIR: [number, number, number] = [0, 0.6, -0.8];
/** Fixed hold angle (radians): yaw inward (toward the centre), pitch up, a slight roll. Euler order YXZ. */
export const FP_TILT = { yaw: (20 * Math.PI) / 180, pitch: (15 * Math.PI) / 180, roll: (-8 * Math.PI) / 180 } as const;

/** Blaster kick on a shot: back toward the camera, nose up, over FP_KICK_S. */
export const FP_KICK_S = 0.08;
export const FP_KICK_BACK_M = 0.035;
export const FP_KICK_PITCH = 0.07; // radians
/** Noodle swing: a quick diagonal swipe down-left and back over FP_SWIPE_S. */
export const FP_SWIPE_S = 0.2;
export const FP_SWIPE_LEFT_M = 0.12;
export const FP_SWIPE_DOWN_M = 0.08;
export const FP_SWIPE_ROLL = 0.5; // radians, top of the item tips left
export const FP_SWIPE_PITCH = 0.35; // radians, tips down
/** Idle sway, metres. */
export const FP_SWAY_M = { x: 0.002, y: 0.0015 } as const;
/** Walk bob: amplitudes at full run speed (metres), and steps per metre walked. */
export const FP_BOB_M = { x: 0.008, y: 0.012 } as const;
export const FP_BOB_PER_M = 1.6;
/** Speed (m/s) that gives the full bob: the run speed (movement.ts maxRunVel). */
export const FP_BOB_FULL_SPEED = 4.8;
/** The game camera's vertical field of view (World.tsx Canvas). */
export const FP_CAMERA_FOV_DEG = 50;

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

/** Swipe amount `age` s after a swing: 0 -> 1 (down-left) -> 0 over FP_SWIPE_S; 0 outside. */
export function swipeCurve(age: number): number {
  if (!(age >= 0) || age >= FP_SWIPE_S) return 0;
  return Math.sin((Math.PI * age) / FP_SWIPE_S);
}

/** Idle sway at time t (s), metres. Written into `out`. */
export function sway(t: number, out: { x: number; y: number }): { x: number; y: number } {
  out.x = FP_SWAY_M.x * Math.sin(t * 1.3);
  out.y = FP_SWAY_M.y * Math.sin(t * 2.6);
  return out;
}

/** Bob strength for a horizontal speed (m/s): 0 when still, 1 at FP_BOB_FULL_SPEED and above. */
export function bobStrength(speed: number): number {
  if (!(speed > 0.05)) return 0;
  return Math.min(1, speed / FP_BOB_FULL_SPEED);
}

/**
 * Walk bob for a step phase (radians, advanced by distance walked) and a
 * strength (bobStrength): side to side once per two steps, up and down once per step.
 */
export function bob(phase: number, strength: number, out: { x: number; y: number }): { x: number; y: number } {
  out.x = FP_BOB_M.x * strength * Math.sin(phase);
  out.y = -FP_BOB_M.y * strength * Math.abs(Math.sin(phase));
  return out;
}

/** Advance the bob phase by a distance walked (metres). */
export function bobPhase(phase: number, distance: number): number {
  return (phase + distance * FP_BOB_PER_M * Math.PI) % (2 * Math.PI);
}

export type FpPose = { x: number; y: number; z: number; pitch: number; yaw: number; roll: number };

const tmpSway = { x: 0, y: 0 };
const tmpBob = { x: 0, y: 0 };

/**
 * View model offset (camera space) and rotation for this frame: the fixed
 * hold, idle sway, walk bob, and the last attack's kick (blaster) or swipe
 * (noodle). `attackAge` is seconds since the last attack with
 * `attackWeapon`, or null. Allocation-free: writes into `out`.
 */
export function fpPose(
  weapon: Weapon,
  t: number,
  attackWeapon: Weapon | null,
  attackAge: number | null,
  out: FpPose,
  walk: { phase: number; strength: number } = { phase: 0, strength: 0 },
): FpPose {
  sway(t, tmpSway);
  bob(walk.phase, walk.strength, tmpBob);
  out.x = FP_OFFSET.right + tmpSway.x + tmpBob.x;
  out.y = -FP_OFFSET.down + tmpSway.y + tmpBob.y;
  out.z = -FP_OFFSET.forward;
  out.yaw = FP_TILT.yaw;
  out.pitch = FP_TILT.pitch;
  out.roll = FP_TILT.roll;
  if (attackAge === null || attackWeapon !== weapon) return out;
  if (weapon === "blaster") {
    const k = kickCurve(attackAge);
    out.z += FP_KICK_BACK_M * k;
    out.pitch += FP_KICK_PITCH * k;
  } else {
    const s = swipeCurve(attackAge);
    out.x -= FP_SWIPE_LEFT_M * s;
    out.y -= FP_SWIPE_DOWN_M * s;
    out.roll += FP_SWIPE_ROLL * s;
    out.pitch -= FP_SWIPE_PITCH * s;
  }
  return out;
}

/**
 * Normalised device coordinates (-1..1, +y up) of a camera-space point, for a
 * perspective camera with vertical fov `fovDeg` and `aspect` (width / height).
 * Null behind the camera.
 */
export function toNdc(p: [number, number, number], fovDeg: number, aspect: number): [number, number] | null {
  if (!(p[2] < -1e-6)) return null;
  const f = 1 / Math.tan((fovDeg * Math.PI) / 360);
  return [(f / aspect) * (p[0] / -p[2]), f * (p[1] / -p[2])];
}
