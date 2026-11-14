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

// Camera arm settings live in armMath.ts (ARM).
