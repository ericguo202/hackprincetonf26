// Lane C: movement feel. Everything tunable lives here, for whoever owns
// character feel. Kept out of Player.tsx so fast refresh keeps working.

export const MOVEMENT = {
  /**
   * Radius 0.25 m (0.5 m wide) so every bathroom door in both Navidad houses
   * fits (narrowest 0.57 m; HANDOFF 16:35, Lane A: bathrooms must be
   * reachable). Half-height 0.40 keeps the capsule, and so the model, 1.3 m
   * tall: 2 * (0.40 + 0.25).
   */
  capsuleHalfHeight: 0.4,
  capsuleRadius: 0.25,
  maxWalkVel: 2.6,
  maxRunVel: 4.8,
  jumpVel: 4.0,
  floatHeight: 0.3,
} as const;

/**
 * Crouch (contracts v2.4.0 "crouch"). The kit has no crouch clip; its Duck
 * clip drops into a held low pose 0.97 m tall at our 1.3 m scale (measured
 * from the model: Duck 0.33-1.0 s), and the character plays Duck trimmed to
 * that hold (clips.ts CLIP_TRIM). The capsule shrinks to match: top at
 * topM with the same float gap, so half-height = (topM - float) / 2 - radius.
 * The camera eye is the body centre + ARM.headOffsetY, so it drops with the
 * capsule centre (0.315 m, against the model's 0.33 m) with no camera change.
 */
export const CROUCH = {
  /** Top of the crouched model (and capsule) above the floor, metres. */
  topM: 0.97,
  halfHeight: (0.97 - MOVEMENT.floatHeight) / 2 - MOVEMENT.capsuleRadius,
  /** Walk only, no run, no jump while crouched. */
  maxVel: 1.3,
} as const;

/** Body centre above the floor when standing, and when crouched (ecctrl floats the capsule). */
export const CENTRE_ABOVE_FLOOR_M = {
  standing: MOVEMENT.floatHeight + MOVEMENT.capsuleRadius + MOVEMENT.capsuleHalfHeight,
  crouched: MOVEMENT.floatHeight + MOVEMENT.capsuleRadius + CROUCH.halfHeight,
} as const;

// Camera arm settings live in armMath.ts (ARM).
