// Lane C: Split wall collision boxes across closed doors in house interiors.
// Doors that were closed during Matterport scans were baked into collision.glb
// as continuous solid wall boxes. Here we split those boxes around the door
// openings so they can be opened.

import type { BufferAttribute, BufferGeometry, Mesh, Object3D } from "three";
import type { Point2, Room } from "../contracts.gen";

export interface WallBox {
  minX: number;
  maxX: number;
  minY: number;
  maxY: number;
  minZ: number;
  maxZ: number;
}

export interface MinMaxBox {
  min: [number, number, number];
  max: [number, number, number];
}

export interface OpenedDoor {
  id: string;
  a: Point2;
  b: Point2;
  floorY: number;
  width: number;
}

export interface SplitResult {
  boxes: WallBox[];
  doors: OpenedDoor[];
}

export type FrontDoorRef =
  | Point2
  | [number, number, number]
  | { x: number; z: number }
  | null
  | undefined;

/**
 * Extracts axis-aligned wall boxes from a loaded Three.js mesh, geometry, or scene.
 * In collision.glb, the "walls" mesh consists of boxes of 8 vertices / 36 indices each.
 */
export function extractWallBoxes(
  rootOrMesh: Object3D | Mesh | BufferGeometry,
): WallBox[] {
  let positionAttr: BufferAttribute | null = null;

  if ("isBufferGeometry" in rootOrMesh && rootOrMesh.isBufferGeometry) {
    const attr = (rootOrMesh as BufferGeometry).attributes.position;
    if (attr) positionAttr = attr as BufferAttribute;
  } else if ("isObject3D" in rootOrMesh && rootOrMesh.isObject3D) {
    rootOrMesh.traverse((obj) => {
      if (positionAttr) return;
      if (obj.name === "walls" && "isMesh" in obj && obj.isMesh) {
        const mesh = obj as Mesh;
        const attr = mesh.geometry?.attributes?.position;
        if (attr) positionAttr = attr as BufferAttribute;
      }
    });
    if (!positionAttr && "isMesh" in rootOrMesh && (rootOrMesh as Mesh).isMesh) {
      const mesh = rootOrMesh as Mesh;
      const attr = mesh.geometry?.attributes?.position;
      if (attr) positionAttr = attr as BufferAttribute;
    }
  }

  if (!positionAttr) return [];

  const boxes: WallBox[] = [];
  const numBoxes = Math.floor(positionAttr.count / 8);
  for (let b = 0; b < numBoxes; b++) {
    let minX = Infinity;
    let maxX = -Infinity;
    let minY = Infinity;
    let maxY = -Infinity;
    let minZ = Infinity;
    let maxZ = -Infinity;
    for (let v = 0; v < 8; v++) {
      const i = b * 8 + v;
      const x = positionAttr.getX(i);
      const y = positionAttr.getY(i);
      const z = positionAttr.getZ(i);
      if (x < minX) minX = x;
      if (x > maxX) maxX = x;
      if (y < minY) minY = y;
      if (y > maxY) maxY = y;
      if (z < minZ) minZ = z;
      if (z > maxZ) maxZ = z;
    }
    boxes.push({ minX, maxX, minY, maxY, minZ, maxZ });
  }
  return boxes;
}

function isWallBox(b: unknown): b is WallBox {
  return (
    typeof b === "object" &&
    b !== null &&
    "minX" in b &&
    "maxX" in b &&
    "minY" in b &&
    "maxY" in b &&
    "minZ" in b &&
    "maxZ" in b &&
    typeof (b as WallBox).minX === "number"
  );
}

function isMinMaxBox(b: unknown): b is MinMaxBox {
  return (
    typeof b === "object" &&
    b !== null &&
    "min" in b &&
    "max" in b &&
    Array.isArray((b as MinMaxBox).min) &&
    Array.isArray((b as MinMaxBox).max)
  );
}

/**
 * Normalizes input box format if passed as `{ min: [x,y,z], max: [x,y,z] }`.
 */
function normalizeBox(b: unknown): WallBox {
  if (isWallBox(b)) {
    return b;
  }
  if (isMinMaxBox(b)) {
    return {
      minX: b.min[0],
      maxX: b.max[0],
      minY: b.min[1],
      maxY: b.max[1],
      minZ: b.min[2],
      maxZ: b.max[2],
    };
  }
  throw new Error("Invalid WallBox structure");
}

/**
 * Openable interior door leaves (HouseDoors, the E toggle and its hint). Off:
 * collision.glb's walls are used as the pack ships them (doors closed in the
 * scan stay solid, like a closed leaf; the pack's own openings stay open), and
 * no doors register, so no leaf, no collider, no "E  Open door". Set true to
 * bring them back. The front door (E to enter/leave) does not depend on this.
 */
export const INTERIOR_DOOR_LEAVES = false;

/** splitClosedDoors when INTERIOR_DOOR_LEAVES is on; otherwise the walls as-is and no doors. */
export function houseWalls(
  wallBoxes: readonly (WallBox | MinMaxBox)[],
  rooms: readonly Room[],
  frontDoor?: FrontDoorRef,
  leaves: boolean = INTERIOR_DOOR_LEAVES,
): SplitResult {
  if (leaves) return splitClosedDoors(wallBoxes, rooms, frontDoor);
  return { boxes: wallBoxes.map(normalizeBox), doors: [] };
}

/**
 * Pure function: for each closed door in rooms except the front door,
 * replace the box that spans it with:
 * - solid pieces between and outside the openings
 * - a lintel piece above each opening from y = 2.03 up to box's top
 *
 * Returns the new boxes and the list of opened doors: { id, a, b, floorY, width }.
 */
export function splitClosedDoors(
  wallBoxes: readonly (WallBox | MinMaxBox)[],
  rooms: readonly Room[],
  frontDoor?: FrontDoorRef,
): SplitResult {
  const normalizedBoxes = wallBoxes.map(normalizeBox);

  // 1. Resolve front door 2D coordinates (x, z)
  let fx: number | undefined;
  let fz: number | undefined;
  if (Array.isArray(frontDoor)) {
    if (frontDoor.length >= 3) {
      fx = frontDoor[0];
      fz = frontDoor[2];
    } else if (frontDoor.length === 2) {
      fx = frontDoor[0];
      fz = frontDoor[1];
    }
  } else if (frontDoor && typeof frontDoor === "object" && "x" in frontDoor && "z" in frontDoor) {
    fx = frontDoor.x;
    fz = frontDoor.z;
  }

  // 2. Collect unique doors from rooms
  interface CandidateDoor {
    id: string;
    a: Point2;
    b: Point2;
    mid: Point2;
    floorY: number;
    width: number;
  }

  const candidateDoors: CandidateDoor[] = [];
  const idCounts = new Map<string, number>();

  for (const r of rooms) {
    for (const o of r.openings ?? []) {
      if (o.type !== "door") continue;
      const mid: Point2 = [(o.a[0] + o.b[0]) / 2, (o.a[1] + o.b[1]) / 2];
      // Deduplicate if already present (e.g. reported from both connecting rooms)
      if (candidateDoors.some((d) => Math.hypot(d.mid[0] - mid[0], d.mid[1] - mid[1]) < 0.25)) {
        continue;
      }
      const width = Math.hypot(o.a[0] - o.b[0], o.a[1] - o.b[1]);
      const baseId = `${r.id}->${o.to_room ?? "outside"}`;
      const count = (idCounts.get(baseId) ?? 0) + 1;
      idCounts.set(baseId, count);
      const id = count === 1 ? baseId : `${baseId}#${count}`;

      candidateDoors.push({
        id,
        a: o.a,
        b: o.b,
        mid,
        floorY: r.floor_y ?? 0,
        width,
      });
    }
  }

  // 3. Find front door among candidates (nearest to frontDoor point)
  let frontDoorIdx = -1;
  if (fx !== undefined && fz !== undefined && candidateDoors.length > 0) {
    let minDist = Infinity;
    candidateDoors.forEach((d, idx) => {
      const dist = Math.hypot(d.mid[0] - fx!, d.mid[1] - fz!);
      if (dist < minDist) {
        minDist = dist;
        frontDoorIdx = idx;
      }
    });
  }

  // 4. Match closed doors to their spanning wall boxes
  interface BoxSplit {
    door: CandidateDoor;
    isX: boolean;
    minDoor: number;
    maxDoor: number;
  }

  const boxSplits = new Map<number, BoxSplit[]>();
  const openedDoors: OpenedDoor[] = [];

  candidateDoors.forEach((d, dIdx) => {
    // The front door must stay solid!
    if (dIdx === frontDoorIdx) return;

    const dx = Math.abs(d.b[0] - d.a[0]);
    const dz = Math.abs(d.b[1] - d.a[1]);
    const isX = dx >= dz;
    const minDoor = isX ? Math.min(d.a[0], d.b[0]) : Math.min(d.a[1], d.b[1]);
    const maxDoor = isX ? Math.max(d.a[0], d.b[0]) : Math.max(d.a[1], d.b[1]);
    const doorThin = isX ? d.mid[1] : d.mid[0];

    let doorOpened = false;

    // Find all boxes spanning across this doorway
    normalizedBoxes.forEach((b, bIdx) => {
      // Must span below y = 2.0
      if (b.minY > 1.0 || b.maxY < 1.5) return;

      if (isX) {
        // Wall box running along X (thin in Z)
        const inThin = doorThin >= b.minZ - 0.08 && doorThin <= b.maxZ + 0.08;
        const spans = b.minX <= minDoor + 0.08 && b.maxX >= maxDoor - 0.08;
        if (inThin && spans) {
          const list = boxSplits.get(bIdx) ?? [];
          list.push({ door: d, isX, minDoor, maxDoor });
          boxSplits.set(bIdx, list);
          doorOpened = true;
        }
      } else {
        // Wall box running along Z (thin in X)
        const inThin = doorThin >= b.minX - 0.08 && doorThin <= b.maxX + 0.08;
        const spans = b.minZ <= minDoor + 0.08 && b.maxZ >= maxDoor - 0.08;
        if (inThin && spans) {
          const list = boxSplits.get(bIdx) ?? [];
          list.push({ door: d, isX, minDoor, maxDoor });
          boxSplits.set(bIdx, list);
          doorOpened = true;
        }
      }
    });

    if (doorOpened) {
      openedDoors.push({
        id: d.id,
        a: d.a,
        b: d.b,
        floorY: d.floorY,
        width: d.width,
      });
    }
  });

  // 5. Construct new boxes: untouched boxes stay untouched, spanning boxes split into pieces
  const newBoxes: WallBox[] = [];

  normalizedBoxes.forEach((b, bIdx) => {
    const splits = boxSplits.get(bIdx);
    if (!splits || splits.length === 0) {
      newBoxes.push(b);
      return;
    }

    const isX = splits[0].isX;
    // Sort openings along the wall axis
    const intervals = splits
      .map((s) => ({
        start: Math.max(isX ? b.minX : b.minZ, s.minDoor),
        end: Math.min(isX ? b.maxX : b.maxZ, s.maxDoor),
      }))
      .sort((p, q) => p.start - q.start);

    let current = isX ? b.minX : b.minZ;
    for (const int of intervals) {
      // Solid piece before or between openings
      if (int.start - current > 1e-4) {
        newBoxes.push(
          isX
            ? {
                minX: current,
                maxX: int.start,
                minY: b.minY,
                maxY: b.maxY,
                minZ: b.minZ,
                maxZ: b.maxZ,
              }
            : {
                minX: b.minX,
                maxX: b.maxX,
                minY: b.minY,
                maxY: b.maxY,
                minZ: current,
                maxZ: int.start,
              },
        );
      }
      // Lintel piece above this opening (from y = 2.03 up to box's top)
      if (b.maxY > 2.03 + 1e-4 && int.end - int.start > 1e-4) {
        newBoxes.push(
          isX
            ? {
                minX: int.start,
                maxX: int.end,
                minY: Math.max(b.minY, 2.03),
                maxY: b.maxY,
                minZ: b.minZ,
                maxZ: b.maxZ,
              }
            : {
                minX: b.minX,
                maxX: b.maxX,
                minY: Math.max(b.minY, 2.03),
                maxY: b.maxY,
                minZ: int.start,
                maxZ: int.end,
              },
        );
      }
      current = Math.max(current, int.end);
    }
    const limit = isX ? b.maxX : b.maxZ;
    // Solid piece after the last opening
    if (limit - current > 1e-4) {
      newBoxes.push(
        isX
          ? {
              minX: current,
              maxX: limit,
              minY: b.minY,
              maxY: b.maxY,
              minZ: b.minZ,
              maxZ: b.maxZ,
            }
          : {
              minX: b.minX,
              maxX: b.maxX,
              minY: b.minY,
              maxY: b.maxY,
              minZ: current,
              maxZ: limit,
            },
      );
    }
  });

  return { boxes: newBoxes, doors: openedDoors };
}
