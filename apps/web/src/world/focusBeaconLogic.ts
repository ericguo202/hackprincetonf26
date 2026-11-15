// Lane C: pure logic and constants for FocusBeacon.

import { DEV_FOCUS_TOGGLE_ON } from "./devFlags";

export const BEACON_RADIUS_M = 0.8;
export const BEACON_HEIGHT_M = 30;

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
