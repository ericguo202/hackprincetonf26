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

// Camera arm settings live in armMath.ts (ARM).
