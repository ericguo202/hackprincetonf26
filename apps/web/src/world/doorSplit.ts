// Lane C: Split wall collision boxes across closed doors in house interiors.
// Doors that were closed during Matterport scans were baked into collision.glb
// as continuous solid wall boxes. Here we split those boxes around the door
// openings so they can be opened.

import type { Point2, Room } from "../contracts.gen";

export interface WallBox {
  minX: number;
  maxX: number;
  minY: number;
  maxY: number;
  minZ: number;
  maxZ: number;
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
 * Extracts axis-aligned wall boxes from a loaded Three.js mesh or scene.
 * In collision.glb, the "walls" mesh consists of boxes of 8 vertices / 36 indices each.
 */
export function extractWallBoxes(rootOrMesh: any): WallBox[] {
  let mesh: any = rootOrMesh;
  if (rootOrMesh?.traverse) {
    rootOrMesh.traverse((o: any) => {
      if (o.name === "walls" && o.geometry?.attributes?.position) {
        mesh = o;
      }
    });
  }
  const pos = mesh?.geometry?.attributes?.position;
  if (!pos) return [];

  const boxes: WallBox[] = [];
  const numBoxes = Math.floor(pos.count / 8);
  for (let b = 0; b < numBoxes; b++) {
    let minX = Infinity;
    let maxX = -Infinity;
    let minY = Infinity;
    let maxY = -Infinity;
    let minZ = Infinity;
    let maxZ = -Infinity;
    for (let v = 0; v < 8; v++) {
      const i = b * 8 + v;
      const x = pos.getX(i);
      const y = pos.getY(i);
      const z = pos.getZ(i);
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

/**
 * Normalizes input box format if passed as `{ min: [x,y,z], max: [x,y,z] }`.
 */
function normalizeBox(b: any): WallBox {
  if (typeof b.minX === "number") {
    return {
      minX: b.minX,
      maxX: b.maxX,
      minY: b.minY,
      maxY: b.maxY,
      minZ: b.minZ,
      maxZ: b.maxZ,
    };
  }
  if (b.min && b.max) {
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
 * Pure function: for each closed door in rooms except the front door,
 * replace the box that spans it with up to 3 boxes:
 * - left of the opening
 * - right of the opening
 * - above the opening (from y = 2.03 up to box's top)
 *
 * Returns the new boxes and the list of opened doors: { id, a, b, floorY, width }.
 */
export function splitClosedDoors(
  wallBoxes: readonly (WallBox | any)[],
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
  } else if (frontDoor && typeof frontDoor === "object") {
    fx = (frontDoor as any).x;
    fz = (frontDoor as any).z;
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
  for (const r of rooms) {
    for (const o of r.openings ?? []) {
      if (o.type !== "door") continue;
      const mid: Point2 = [(o.a[0] + o.b[0]) / 2, (o.a[1] + o.b[1]) / 2];
      // Deduplicate if already present (e.g. reported from both connecting rooms)
      if (candidateDoors.some((d) => Math.hypot(d.mid[0] - mid[0], d.mid[1] - mid[1]) < 0.25)) {
        continue;
      }
      const width = Math.hypot(o.a[0] - o.b[0], o.a[1] - o.b[1]);
      candidateDoors.push({
        id: `${r.id}->${o.to_room ?? "outside"}`,
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

  const boxSplits = new Map<number, BoxSplit>();
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

    // Find the box spanning across this doorway
    normalizedBoxes.forEach((b, bIdx) => {
      // Must span below y = 2.0
      if (b.minY > 1.0 || b.maxY < 1.5) return;

      if (isX) {
        // Wall box running along X (thin in Z)
        const inThin = doorThin >= b.minZ - 0.08 && doorThin <= b.maxZ + 0.08;
        const spans = b.minX <= minDoor + 0.08 && b.maxX >= maxDoor - 0.08;
        if (inThin && spans) {
          boxSplits.set(bIdx, { door: d, isX, minDoor, maxDoor });
          openedDoors.push({
            id: d.id,
            a: d.a,
            b: d.b,
            floorY: d.floorY,
            width: d.width,
          });
        }
      } else {
        // Wall box running along Z (thin in X)
        const inThin = doorThin >= b.minX - 0.08 && doorThin <= b.maxX + 0.08;
        const spans = b.minZ <= minDoor + 0.08 && b.maxZ >= maxDoor - 0.08;
        if (inThin && spans) {
          boxSplits.set(bIdx, { door: d, isX, minDoor, maxDoor });
          openedDoors.push({
            id: d.id,
            a: d.a,
            b: d.b,
            floorY: d.floorY,
            width: d.width,
          });
        }
      }
    });
  });

  // 5. Construct new boxes: untouched boxes stay untouched, spanning boxes split into up to 3
  const newBoxes: WallBox[] = [];

  normalizedBoxes.forEach((b, bIdx) => {
    const split = boxSplits.get(bIdx);
    if (!split) {
      newBoxes.push(b);
      return;
    }

    const { isX, minDoor, maxDoor } = split;
    if (isX) {
      // Left of the opening
      if (minDoor - b.minX > 1e-4) {
        newBoxes.push({
          minX: b.minX,
          maxX: minDoor,
          minY: b.minY,
          maxY: b.maxY,
          minZ: b.minZ,
          maxZ: b.maxZ,
        });
      }
      // Right of the opening
      if (b.maxX - maxDoor > 1e-4) {
        newBoxes.push({
          minX: maxDoor,
          maxX: b.maxX,
          minY: b.minY,
          maxY: b.maxY,
          minZ: b.minZ,
          maxZ: b.maxZ,
        });
      }
      // Above the opening (from y = 2.03 up to box top)
      if (b.maxY > 2.03 + 1e-4) {
        newBoxes.push({
          minX: minDoor,
          maxX: maxDoor,
          minY: Math.max(b.minY, 2.03),
          maxY: b.maxY,
          minZ: b.minZ,
          maxZ: b.maxZ,
        });
      }
    } else {
      // Left of opening (smaller Z)
      if (minDoor - b.minZ > 1e-4) {
        newBoxes.push({
          minX: b.minX,
          maxX: b.maxX,
          minY: b.minY,
          maxY: b.maxY,
          minZ: b.minZ,
          maxZ: minDoor,
        });
      }
      // Right of opening (larger Z)
      if (b.maxZ - maxDoor > 1e-4) {
        newBoxes.push({
          minX: b.minX,
          maxX: b.maxX,
          minY: b.minY,
          maxY: b.maxY,
          minZ: maxDoor,
          maxZ: b.maxZ,
        });
      }
      // Above the opening (from y = 2.03 up to box top)
      if (b.maxY > 2.03 + 1e-4) {
        newBoxes.push({
          minX: b.minX,
          maxX: b.maxX,
          minY: Math.max(b.minY, 2.03),
          maxY: b.maxY,
          minZ: minDoor,
          maxZ: maxDoor,
        });
      }
    }
  });

  return { boxes: newBoxes, doors: openedDoors };
}
