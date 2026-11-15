import { describe, expect, it } from "vitest";

import type { OpenedDoor } from "./doorSplit";
import {
  canCloseDoor,
  distanceToSegment,
  doorPromptText,
  nearestDoorInRange,
} from "./doorInteract";

describe("doorInteract", () => {
  const doorA: OpenedDoor = {
    id: "door_a",
    a: [0, 0],
    b: [1, 0],
    floorY: 0,
    width: 1,
  };

  const doorB: OpenedDoor = {
    id: "door_b",
    a: [5, 5],
    b: [5, 6],
    floorY: 0,
    width: 1,
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
