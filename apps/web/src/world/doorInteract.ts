// Lane C: Pure logic for player door interaction.
// - Distance calculations in the house frame (XZ plane)
// - Finding nearest door in range (1.5 m from midpoint)
// - Determining if a door can be closed (player not within 0.4 m of door line)
// - Prompt text ("E  Open door" / "E  Close door")

import type { Point2 } from "../contracts.gen";
import type { OpenedDoor } from "./doorSplit";
import { frontDoorInRange, type Door, type Vec3 } from "./pack";

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

/** What E does here: toggle an interior door, go through the front door, or nothing. */
export type DoorAction = "interior" | "front" | null;

/**
 * Interior door vs front door, both already in their own range (distances in
 * metres, null when out of range). The front door wins whenever it is in range,
 * so E at the front door always leaves (or enters) instead of toggling a leaf.
 */
export function pickDoorAction(interiorDist: number | null, frontDist: number | null): DoorAction {
  if (frontDist !== null) return "front";
  return interiorDist === null ? null : "interior";
}

/** "E  Enter <address>" on the block (the listing id if it has no address), "E  Leave house" inside. */
export function frontDoorPromptText(zone: string, listingId: string, address?: string | null): string {
  if (zone !== "block") return "E  Leave house";
  const name = address?.trim() ? address.trim() : listingId;
  return `E  Enter ${name}`;
}

export type DoorTarget =
  | { kind: "interior"; door: OpenedDoor; dist: number }
  | { kind: "front"; door: Door; dist: number }
  | null;

/**
 * The door E acts on at `pos` (body centre, current zone's frame): the nearest
 * interior door within DOOR_INTERACT_RADIUS_M (indoors only) or the front door
 * within FRONT_DOOR_KEY_M, by pickDoorAction.
 */
export function doorTargetAt(
  zone: string,
  pos: Vec3,
  interiorDoors: readonly OpenedDoor[],
  frontDoors: Door[],
): DoorTarget {
  const interior = zone === "block" ? null : nearestDoorInRange([pos[0], pos[2]], interiorDoors);
  const front = frontDoorInRange(zone, pos, frontDoors);
  const action = pickDoorAction(interior?.distance ?? null, front?.dist ?? null);
  if (action === "interior" && interior) return { kind: "interior", door: interior.door, dist: interior.distance };
  if (action === "front" && front) return { kind: "front", door: front.door, dist: front.dist };
  return null;
}
