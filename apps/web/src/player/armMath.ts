// Lane C: over-the-shoulder camera arm. Pure math, no three.js, so it is unit
// tested (armMath.test.ts). Player.tsx does the raycast and moves the camera.
//
// Frame: three.js world, Y up. yaw is radians about +Y (0 looks down -Z, the
// three.js camera default); pitch is radians, positive looks up.
//
// The arm: from the head, back along the view direction by `back` and right
// by `side`. Every frame Player.tsx casts a ray from the head to that point
// against zone collision only, and the camera sits at the hit, pulled in a
// little. The camera always looks parallel to the view direction, so the
// screen centre (crosshair) is exactly the view ray from the camera.
//
// For the blaster (AGENTS.md section 11), so shots never look sideways:
//   - AIM: cast the camera ray through the crosshair (origin = camera,
//     direction = viewDir(yaw, pitch)) to find the aim point: what is under
//     the crosshair. Ignore hits closer to the camera than the head is, so
//     anything between the camera and the player (or the player's own
//     capsule) cannot eat the shot.
//   - SHOT: origin = the HEAD (body centre + ARM.headOffsetY), direction =
//     normalize(aimPoint - head). Raycast that against hitboxes, zone
//     collision and furniture; it decides the hit. Using the head (not the
//     camera) also stops the over-the-shoulder camera from shooting around a
//     corner the body cannot see past.
//   - NETWORK: send `fire {origin: head, dir}`. Never send the camera
//     position: the server rejects an origin more than 1.5 m from the
//     shooter, and the arm reaches hypot(ARM.maxBack, ARM.side), about 3.5 m,
//     so camera-as-origin would get every shot rejected once the arm extends.
//     The head is always within the 1.5 m check.
//   - TRACER: spawns at the muzzle and is drawn to the shot's hit point (or
//     head + dir * max range on a miss), not along the muzzle's forward axis.

import { MOVEMENT } from "./movement.ts";

export type V3 = [number, number, number];

export const ARM = {
  /** Head above the body's centre (capsule centre), metres. Camera sits at this height. */
  headOffsetY: 0.5,
  /**
   * Default distance behind the head on the block. Must frame the whole body:
   * at 50 deg vertical FOV the view is 2 * d * tan(25 deg) tall, and the
   * capsule is 1.3 m, so anything under about 1.4 m crops it. Indoors the
   * default is first person instead (armDefaults).
   */
  back: 3.0,
  /** To the right of the head. */
  side: 0.4,
  /** Scroll range for `back`. */
  minBack: 0.6,
  maxBack: 3.5,
  /** Pull the camera this far in from whatever the arm hit. */
  pullIn: 0.15,
  /**
   * Below this arm length the local body is hidden (reads as first person).
   * A floor: hideThreshold() raises it on narrow screens. See hideThreshold.
   */
  hideBodyBelow: 1.6,
  /** Arm grows back out at this rate (m/s) after an obstruction clears; shrinks instantly. */
  extendRate: 4,
  /** Mouse look, radians per pixel. */
  sensitivity: 0.0022,
  /** Pitch limit, radians (about 85 degrees). */
  maxPitch: 1.48,
} as const;

/** Unit view direction for yaw/pitch. */
export function viewDir(yaw: number, pitch: number): V3 {
  const c = Math.cos(pitch);
  return [-Math.sin(yaw) * c, Math.sin(pitch), -Math.cos(yaw) * c];
}

/** Unit right vector (horizontal) for yaw. */
export function rightDir(yaw: number): V3 {
  return [Math.cos(yaw), 0, -Math.sin(yaw)];
}

/** Offset from the head to the desired camera point: back along the view, then right. */
export function armOffset(yaw: number, pitch: number, back: number, side: number): V3 {
  const f = viewDir(yaw, pitch);
  const r = rightDir(yaw);
  return [-f[0] * back + r[0] * side, -f[1] * back, -f[2] * back + r[2] * side];
}

/** Length and unit direction of the arm. A zero arm points nowhere and has length 0. */
export function armRay(offset: V3): { dir: V3; len: number } {
  const len = Math.hypot(offset[0], offset[1], offset[2]);
  if (len < 1e-6) return { dir: [0, 0, 0], len: 0 };
  return { dir: [offset[0] / len, offset[1] / len, offset[2] / len], len };
}

/**
 * How long the arm may be this frame: the full length, or up to the hit
 * pulled in by `pullIn`, never below zero.
 */
export function clampArm(desiredLen: number, hitDist: number | null, pullIn: number = ARM.pullIn): number {
  if (hitDist === null) return desiredLen;
  return Math.max(0, Math.min(desiredLen, hitDist - pullIn));
}

/**
 * Smooth the arm length over time: snap in at once (never let the camera sit
 * inside a wall for a frame), ease back out so it does not pop.
 */
export function smoothArm(current: number, target: number, dt: number, rate: number = ARM.extendRate): number {
  if (target <= current) return target;
  return Math.min(target, current + rate * dt);
}

/** Horizontal FOV (radians) of a perspective camera from its vertical FOV (degrees) and aspect. */
export function horizontalFov(verticalFovDeg: number, aspect: number): number {
  return 2 * Math.atan(Math.tan((verticalFovDeg * Math.PI) / 360) * aspect);
}

/**
 * Arm length below which the body is hidden, for a given horizontal FOV.
 *
 * The body (a capsule of radius r) seen from distance d spans
 * 2 * atan(r / d) of the view. Hide it once that is more than half the
 * horizontal FOV, i.e. when d < r / tan(hFov / 4). The FOV comes from the
 * window shape (the camera fixes the vertical FOV), so a narrow window pushes
 * this up; ARM.hideBodyBelow is the floor for wide ones.
 */
export function hideThreshold(hFovRad: number, radius: number = MOVEMENT.capsuleRadius): number {
  return Math.max(ARM.hideBodyBelow, radius / Math.tan(hFovRad / 4));
}

export function hideBody(armLen: number, hFovRad: number): boolean {
  return armLen < hideThreshold(hFovRad);
}

/** Apply a mouse delta (pixels). Moving the mouse right turns right; up looks up. */
export function applyLook(
  yaw: number,
  pitch: number,
  dx: number,
  dy: number,
  sensitivity: number = ARM.sensitivity,
): { yaw: number; pitch: number } {
  const p = pitch - dy * sensitivity;
  return {
    yaw: yaw - dx * sensitivity,
    pitch: Math.max(-ARM.maxPitch, Math.min(ARM.maxPitch, p)),
  };
}

/** Scroll down (positive deltaY) pulls the camera back; up brings it in. */
export function nextArmBack(back: number, wheelDeltaY: number): number {
  const scaled = back * Math.exp(wheelDeltaY * 0.0015);
  return Math.max(ARM.minBack, Math.min(ARM.maxBack, scaled));
}

/** What the player has asked of the arm. `pinned` means a max length of 0: first person. */
export type ArmSetting = { back: number; pinned: boolean };

/**
 * The arm on entering a zone: first person indoors (zone is a listing_id),
 * where the ray would clamp to a wall almost every frame anyway, and the full
 * arm on the block.
 */
export function armDefaults(zone: string): ArmSetting {
  return { back: ARM.back, pinned: zone !== "block" };
}

/** V: pin to first person, or unpin back to the remembered length. */
export function toggleFirstPerson(s: ArmSetting): ArmSetting {
  return { ...s, pinned: !s.pinned };
}

/**
 * Scroll. From first person, scrolling out (positive deltaY) leaves it at the
 * shortest arm and grows from there, rather than jumping to the full length;
 * scrolling in stays in first person.
 */
export function scrollArm(s: ArmSetting, wheelDeltaY: number): ArmSetting {
  if (s.pinned) return wheelDeltaY > 0 ? { back: ARM.minBack, pinned: false } : s;
  return { back: nextArmBack(s.back, wheelDeltaY), pinned: false };
}
