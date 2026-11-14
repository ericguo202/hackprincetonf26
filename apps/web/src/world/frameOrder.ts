// Lane C: explicit useFrame priorities. R3F runs frame callbacks in priority
// order (lower first) and, within a priority, in subscription order, which
// depends on mount order and changes when the Player remounts on a zone
// change. Anything that must run after the physics step says so here.
//
// Positive priorities are off limits: R3F stops rendering automatically as
// soon as any useFrame has a priority above 0. So physics goes below 0 and
// everything that reads its result stays at 0.

/** <Physics updatePriority>: step and write interpolated body transforms first. */
export const PHYSICS_PRIORITY = -50;

/** Camera arm: reads the interpolated body transform written by the step above. */
export const CAMERA_PRIORITY = 0;
