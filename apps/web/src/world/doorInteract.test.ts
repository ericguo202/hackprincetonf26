import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { beforeAll, describe, expect, it } from "vitest";
import { Group, Vector3 } from "three";
import { GLTFLoader, type GLTF } from "three/examples/jsm/loaders/GLTFLoader.js";

import type { Room } from "../contracts.gen";
import { extractWallBoxes, splitClosedDoors, type OpenedDoor } from "./doorSplit";
import {
  canCloseDoor,
  distanceToSegment,
  doorPromptText,
  doorSwingSign,
  nearestDoorInRange,
  outsideDirection,
} from "./doorInteract";
import { pointInPolygon } from "./geometry";
import { parseListings, type Vec3 } from "./pack";

describe("doorInteract", () => {
  const doorA: OpenedDoor = {
    id: "door_a",
    a: [0, 0],
    b: [1, 0],
    floorY: 0,
    width: 1,
    toOutside: false,
  };

  const doorB: OpenedDoor = {
    id: "door_b",
    a: [5, 5],
    b: [5, 6],
    floorY: 0,
    width: 1,
    toOutside: false,
  };

  describe("distanceToSegment", () => {
    it("returns 0 for points on the segment", () => {
      expect(distanceToSegment([0.5, 0], [0, 0], [1, 0])).toBeCloseTo(0, 5);
      expect(distanceToSegment([0, 0], [0, 0], [1, 0])).toBeCloseTo(0, 5);
      expect(distanceToSegment([1, 0], [0, 0], [1, 0])).toBeCloseTo(0, 5);
    });

    it("measures perpendicular distance when projecting onto the segment", () => {
      expect(distanceToSegment([0.5, 0.3], [0, 0], [1, 0])).toBeCloseTo(0.3, 5);
      expect(distanceToSegment([0.5, -0.5], [0, 0], [1, 0])).toBeCloseTo(0.5, 5);
    });

    it("clamps to closest endpoint beyond segment bounds", () => {
      expect(distanceToSegment([-1, 0], [0, 0], [1, 0])).toBeCloseTo(1, 5);
      expect(distanceToSegment([2, 0], [0, 0], [1, 0])).toBeCloseTo(1, 5);
      expect(distanceToSegment([2, 1], [0, 0], [1, 0])).toBeCloseTo(Math.hypot(1, 1), 5);
    });
  });

  describe("nearestDoorInRange", () => {
    it("returns null when no doors are in range", () => {
      expect(nearestDoorInRange([10, 10], [doorA, doorB])).toBeNull();
      expect(nearestDoorInRange([0, 0], [])).toBeNull();
    });

    it("finds door within 1.5 m of midpoint", () => {
      // doorA midpoint is [0.5, 0]
      const near = nearestDoorInRange([0.5, 1.2], [doorA, doorB]);
      expect(near).not.toBeNull();
      expect(near!.door.id).toBe("door_a");
      expect(near!.distance).toBeCloseTo(1.2, 5);
    });

    it("picks the closer door when multiple are in range", () => {
      const doorC: OpenedDoor = {
        id: "door_c",
        a: [0.5, 1],
        b: [0.5, 2],
        floorY: 0,
        width: 1,
        toOutside: false,
      };
      // doorA mid is [0.5, 0], doorC mid is [0.5, 1.5]
      // Player at [0.5, 0.4] is 0.4 m from doorA, 1.1 m from doorC
      const near = nearestDoorInRange([0.5, 0.4], [doorA, doorC]);
      expect(near!.door.id).toBe("door_a");
    });
  });

  describe("canCloseDoor", () => {
    it("prevents closing when player is within 0.4 m of the door line", () => {
      expect(canCloseDoor([0.5, 0], doorA)).toBe(false);
      expect(canCloseDoor([0.5, 0.2], doorA)).toBe(false);
      expect(canCloseDoor([0.5, 0.39], doorA)).toBe(false);
    });

    it("allows closing when player is >= 0.4 m from the door line", () => {
      expect(canCloseDoor([0.5, 0.4], doorA)).toBe(true);
      expect(canCloseDoor([0.5, 1.0], doorA)).toBe(true);
      expect(canCloseDoor([-0.5, 0], doorA)).toBe(true);
    });
  });

  describe("doorPromptText", () => {
    it("returns 'E  Open door' when closed and 'E  Close door' when open", () => {
      expect(doorPromptText(false)).toBe("E  Open door");
      expect(doorPromptText(true)).toBe("E  Close door");
    });
  });
});

// ---------------------------------------------------------------------------
// Swing direction: outside doors open outward.
// ---------------------------------------------------------------------------

/**
 * Where the open leaf's free edge ends up (house frame XZ), built with the
 * same transforms as HouseDoors.SingleDoor: a group at `a` turned by
 * yaw = atan2(-dz, dx), a hinge turned by sign * 90 deg, the leaf from x 0 to width.
 */
function openFreeEdge(door: OpenedDoor, sign: 1 | -1): [number, number] {
  const dx = door.b[0] - door.a[0];
  const dz = door.b[1] - door.a[1];
  const outer = new Group();
  outer.position.set(door.a[0], door.floorY, door.a[1]);
  outer.rotation.y = Math.atan2(-dz, dx);
  const hinge = new Group();
  hinge.rotation.y = sign * (Math.PI / 2);
  outer.add(hinge);
  outer.updateMatrixWorld(true);
  const p = hinge.localToWorld(new Vector3(door.width, 0, 0));
  return [p.x, p.z];
}

describe("doorSwingSign", () => {
  // One 4 m x 3 m room: x 0..4 (east is +X), z 0..3.
  const room = (openings: Room["openings"]): Room => ({
    id: "h.r1",
    label: "living_room",
    label_confidence: 1,
    floor: 0,
    floor_y: 0,
    height_m: 2.6,
    area_m2: 12,
    polygon: [
      [0, 0],
      [4, 0],
      [4, 3],
      [0, 3],
    ],
    openings,
  });
  const outsideDoor = (a: [number, number], b: [number, number]): OpenedDoor => ({
    id: "h.r1->outside",
    a,
    b,
    floorY: 0,
    width: Math.hypot(b[0] - a[0], b[1] - a[1]),
    toOutside: true,
  });

  it("an outside door on the east wall opens with its free edge east of the wall, either a/b order", () => {
    for (const [a, b] of [
      [[4, 1], [4, 2]],
      [[4, 2], [4, 1]],
    ] as [[number, number], [number, number]][]) {
      const rooms = [room([{ type: "door", a, b, to_room: null }])];
      const d = outsideDoor(a, b);
      const sign = doorSwingSign(d, rooms);
      expect(openFreeEdge(d, sign)[0]).toBeGreaterThan(4.5); // outside, a leaf-width out
      expect(openFreeEdge(d, sign === 1 ? -1 : 1)[0]).toBeLessThan(3.5); // the other sign would open inward
      const out = outsideDirection(d, rooms)!;
      expect(out[0]).toBeCloseTo(1, 9);
      expect(out[1]).toBeCloseTo(0, 9);
    }
  });

  it("works on the west and north walls too", () => {
    const west = outsideDoor([0, 1], [0, 2]);
    const wRooms = [room([{ type: "door", a: west.a, b: west.b, to_room: null }])];
    expect(openFreeEdge(west, doorSwingSign(west, wRooms))[0]).toBeLessThan(-0.5);
    const north = outsideDoor([1, 0], [2, 0]);
    const nRooms = [room([{ type: "door", a: north.a, b: north.b, to_room: null }])];
    expect(openFreeEdge(north, doorSwingSign(north, nRooms))[1]).toBeLessThan(-0.5);
  });

  it("a room-to-room door keeps the old +90 deg swing", () => {
    const inner: OpenedDoor = { id: "h.r1->h.r2", a: [4, 1], b: [4, 2], floorY: 0, width: 1, toOutside: false };
    expect(doorSwingSign(inner, [room([{ type: "door", a: inner.a, b: inner.b, to_room: "h.r2" }])])).toBe(1);
    // Even with no rooms at all.
    expect(doorSwingSign(inner, [])).toBe(1);
  });

  describe("on 7014's real v3 doors", () => {
    const V3 = join(dirname(fileURLToPath(import.meta.url)), "../../public/packs/packs/hou_navidad_01/v3");
    const H7014 = "hou_236595179";
    const roomsJson = JSON.parse(readFileSync(join(V3, `houses/${H7014}/rooms.json`), "utf8"));
    const rooms: Room[] = Array.isArray(roomsJson) ? roomsJson : roomsJson.rooms;
    let doors: OpenedDoor[] = [];

    beforeAll(async () => {
      const listingsRaw = JSON.parse(readFileSync(join(V3, "../../../listings/index.json"), "utf8"));
      const parsed = parseListings(listingsRaw);
      if (!parsed.ok) throw new Error("listings");
      const listing = parsed.value.find((l) => l.id === H7014)!;
      const buf = readFileSync(join(V3, `houses/${H7014}/collision.glb`));
      const ab = new ArrayBuffer(buf.length);
      new Uint8Array(ab).set(buf);
      const gltf = await new Promise<GLTF>((res, rej) => new GLTFLoader().parse(ab, "", res, rej));
      doors = splitClosedDoors(extractWallBoxes(gltf.scene), rooms, listing.world!.door as Vec3).doors;
    });

    it("the 3 opened outside doors swing outward: the open leaf's free edge is outside every room", () => {
      const outside = doors.filter((d) => d.toOutside);
      expect(outside.map((d) => d.id).sort()).toEqual([`${H7014}.r3->outside`, `${H7014}.r7->outside`, `${H7014}.r8->outside`]);
      for (const d of outside) {
        const edge = openFreeEdge(d, doorSwingSign(d, rooms));
        const inside = rooms.filter((r) => pointInPolygon(edge, r.polygon)).map((r) => r.id);
        expect(inside, `${d.id} free edge ${edge.map((v) => v.toFixed(2))}`).toEqual([]);
      }
    });

    it("the room-to-room doors keep +1", () => {
      for (const d of doors.filter((x) => !x.toOutside)) expect(doorSwingSign(d, rooms)).toBe(1);
    });
  });
});
