// Lane C: Pure logic for player door interaction.
// - Distance calculations in the house frame (XZ plane)
// - Finding nearest door in range (1.5 m from midpoint)
// - Determining if a door can be closed (player not within 0.4 m of door line)
// - Prompt text ("E  Open door" / "E  Close door")

import type { Point2 } from "../contracts.gen";
import type { OpenedDoor } from "./doorSplit";

export const DOOR_INTERACT_RADIUS_M = 1.5;
export const DOOR_CLOSE_CLEARANCE_M = 0.4;

/** Distance from point p to 2D line segment [a, b]. */
export function distanceToSegment(p: Point2, a: Point2, b: Point2): number {
  const dx = b[0] - a[0];
  const dz = b[1] - a[1];
  const l2 = dx * dx + dz * dz;
  if (l2 < 1e-8) {
    return Math.hypot(p[0] - a[0], p[1] - a[1]);
  }
  const t = Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dz) / l2));
  const qx = a[0] + t * dx;
  const qz = a[1] + t * dz;
  return Math.hypot(p[0] - qx, p[1] - qz);
}

/**
 * Finds the nearest door whose midpoint is within maxDistanceM (default 1.5 m)
 * of the player's 2D position.
 */
export function nearestDoorInRange(
  playerXZ: Point2,
  doors: readonly OpenedDoor[],
  maxDistanceM = DOOR_INTERACT_RADIUS_M,
): { door: OpenedDoor; distance: number } | null {
  let nearest: OpenedDoor | null = null;
  let minDistance = Infinity;

  for (const door of doors) {
    const midX = (door.a[0] + door.b[0]) / 2;
    const midZ = (door.a[1] + door.b[1]) / 2;
    const dist = Math.hypot(playerXZ[0] - midX, playerXZ[1] - midZ);
    if (dist <= maxDistanceM && dist < minDistance) {
      minDistance = dist;
      nearest = door;
    }
  }

  if (!nearest) return null;
  return { door: nearest, distance: minDistance };
}

/**
 * A door cannot be closed if the player is standing within 0.4 m of the door line
 * to prevent getting stuck inside the collider.
 */
export function canCloseDoor(
  playerXZ: Point2,
  door: OpenedDoor,
  minDistanceM = DOOR_CLOSE_CLEARANCE_M,
): boolean {
  return distanceToSegment(playerXZ, door.a, door.b) >= minDistanceM;
}

/**
 * Returns prompt text: "E  Close door" when open, "E  Open door" when closed.
 */
export function doorPromptText(isOpen: boolean): string {
  return isOpen ? "E  Close door" : "E  Open door";
}
