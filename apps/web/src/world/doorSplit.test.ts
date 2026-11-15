import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { beforeAll, describe, expect, it } from "vitest";
import { GLTFLoader, type GLTF } from "three/examples/jsm/loaders/GLTFLoader.js";

import type { Room } from "../contracts.gen";
import { extractWallBoxes, splitClosedDoors, type SplitResult, type WallBox } from "./doorSplit";

const PACKS = join(dirname(fileURLToPath(import.meta.url)), "../../public/packs");
const V2_HOUSE = join(PACKS, "packs/hou_navidad_01/v2/houses/hou_236595179");
const LISTINGS = join(PACKS, "listings/index.json");

describe("doorSplit with real 7014 collision.glb and rooms.json", () => {
  let originalBoxes: WallBox[];
  let result: SplitResult;
  let roomsJson: Room[];
  let frontDoorPos: [number, number, number];

  beforeAll(async () => {
    roomsJson = JSON.parse(readFileSync(join(V2_HOUSE, "rooms.json"), "utf8")) as Room[];
    const listingsJson = JSON.parse(readFileSync(LISTINGS, "utf8")) as any[];
    const listing7014 = listingsJson.find((l) => l.id === "hou_236595179")!;
    frontDoorPos = listing7014.world.door as [number, number, number];

    const glbBuf = readFileSync(join(V2_HOUSE, "collision.glb"));
    const ab = new ArrayBuffer(glbBuf.length);
    new Uint8Array(ab).set(glbBuf);

    const gltf = await new Promise<GLTF>((resolve, reject) => {
      new GLTFLoader().parse(ab, "", resolve, reject);
    });

    originalBoxes = extractWallBoxes(gltf.scene);
    result = splitClosedDoors(originalBoxes, roomsJson, frontDoorPos);
  });

  it("extracts 64 wall boxes from collision.glb and opens the 7 closed doors", () => {
    expect(originalBoxes).toHaveLength(64);
    expect(result.doors).toHaveLength(7);
    for (const d of result.doors) {
      expect(d.id).toBeDefined();
      expect(d.a).toHaveLength(2);
      expect(d.b).toHaveLength(2);
      expect(d.floorY).toBeCloseTo(-0.003, 3);
      expect(d.width).toBeGreaterThan(0.5);
    }
  });

  it("no box overlaps any opened doorway below y=2.0", () => {
    for (const door of result.doors) {
      for (let t = 0.1; t <= 0.9; t += 0.05) {
        const px = door.a[0] + (door.b[0] - door.a[0]) * t;
        const pz = door.a[1] + (door.b[1] - door.a[1]) * t;

        const overlapping = result.boxes.filter(
          (b) =>
            px > b.minX + 0.001 &&
            px < b.maxX - 0.001 &&
            pz > b.minZ + 0.001 &&
            pz < b.maxZ - 0.001 &&
            b.minY < 2.0 &&
            b.maxY > 0.0,
        );
        expect(overlapping).toHaveLength(0);
      }
    }
  });

  it("the front door is still covered", () => {
    // Front door is at [5.407, -0.003, -4.476]; it must not be in the opened doors list
    const frontDoorOpening = result.doors.find((d) => d.id.includes("front"));
    expect(frontDoorOpening).toBeUndefined();

    // The solid wall spanning the front door must remain intact below y=2.0
    const fdX = frontDoorPos[0];
    const fdZ = frontDoorPos[2];
    const coveringFrontDoor = result.boxes.filter(
      (b) =>
        fdX >= b.minX &&
        fdX <= b.maxX &&
        fdZ >= b.minZ &&
        fdZ <= b.maxZ &&
        b.minY < 2.0 &&
        b.maxY > 1.5,
    );
    expect(coveringFrontDoor.length).toBeGreaterThan(0);
  });

  it("the boxes away from doors are unchanged", () => {
    const isBoxEqual = (a: WallBox, b: WallBox) =>
      Math.abs(a.minX - b.minX) < 1e-4 &&
      Math.abs(a.maxX - b.maxX) < 1e-4 &&
      Math.abs(a.minY - b.minY) < 1e-4 &&
      Math.abs(a.maxY - b.maxY) < 1e-4 &&
      Math.abs(a.minZ - b.minZ) < 1e-4 &&
      Math.abs(a.maxZ - b.maxZ) < 1e-4;

    // 64 original boxes - 7 split boxes = 57 unchanged boxes (including the front door box)
    const unchangedOriginalBoxes = originalBoxes.filter((orig) =>
      result.boxes.some((curr) => isBoxEqual(orig, curr)),
    );
    expect(unchangedOriginalBoxes).toHaveLength(57);

    // Each of the 7 split boxes became 3 boxes: 57 + 7 * 3 = 78 total boxes
    expect(result.boxes).toHaveLength(78);

    // Each opened doorway has a lintel box above y=2.03
    for (const door of result.doors) {
      const midX = (door.a[0] + door.b[0]) / 2;
      const midZ = (door.a[1] + door.b[1]) / 2;
      const lintel = result.boxes.find(
        (b) =>
          midX >= b.minX - 0.01 &&
          midX <= b.maxX + 0.01 &&
          midZ >= b.minZ - 0.01 &&
          midZ <= b.maxZ + 0.01 &&
          b.minY >= 2.02,
      );
      expect(lintel).toBeDefined();
    }
  });
});
