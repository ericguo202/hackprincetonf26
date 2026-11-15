// Lane C: pure logic and constants for FocusBeacon.

import { DEV_FOCUS_TOGGLE_ON } from "./devFlags";
import type { Door, Vec3 } from "./pack";

export const BEACON_RADIUS_M = 0.8;
export const BEACON_HEIGHT_M = 30;
/** "Your match" sits above the block door's address label (2.7 m), so they don't overlap. */
export const BEACON_LABEL_Y_M = 3.6;

/**
 * Where the beacon stands: the block door's marker point, DOOR_MARKER_M (4.2 m)
 * out from the door, in front of the real house facade, under the address
 * label. Not exitTo (7 m out, past the marker) nor the door point (inside the
 * house mesh on pack v3).
 */
export function beaconPoint(door: Door): Vec3 {
  return door.marker;
}

/**
 * Resolves the focused listing ID: dev override on VITE_SOLO takes precedence,
 * otherwise store.focus[me.id].
 */
export function effectiveFocus(
  meId: string | null,
  focus: Record<string, string>,
  devOverride: string | null,
): string | null {
  if (DEV_FOCUS_TOGGLE_ON && devOverride) return devOverride;
  if (!meId) return null;
  return focus[meId] ?? null;
}
