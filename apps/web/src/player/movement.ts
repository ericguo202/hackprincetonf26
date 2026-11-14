// Lane C: movement feel. Everything tunable lives here, for whoever owns
// character feel. Kept out of Player.tsx so fast refresh keeps working.

export const MOVEMENT = {
  capsuleHalfHeight: 0.35,
  capsuleRadius: 0.3,
  maxWalkVel: 2.6,
  maxRunVel: 4.8,
  jumpVel: 4.0,
  floatHeight: 0.3,
} as const;

/** Follow camera: offset from the player at spawn, and zoom limits. */
export const CAMERA = {
  offset: [0, 2.4, 5] as const,
  lookHeight: 0.8,
  minDistance: 2,
  maxDistance: 10,
  smoothTime: 0.12,
} as const;
