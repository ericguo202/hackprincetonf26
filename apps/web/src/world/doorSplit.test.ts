import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { beforeAll, describe, expect, it } from "vitest";
import { GLTFLoader, type GLTF } from "three/examples/jsm/loaders/GLTFLoader.js";

import type { Listing, Room } from "../contracts.gen";
import { extractWallBoxes, splitClosedDoors, type SplitResult, type WallBox } from "./doorSplit";

const PACKS = join(dirname(fileURLToPath(import.meta.url)), "../../public/packs");
const V2_HOUSES = join(PACKS, "packs/hou_navidad_01/v2/houses");
const LISTINGS = join(PACKS, "listings/index.json");

describe("doorSplit with real 7014 collision.glb and rooms.json", () => {
  let originalBoxes: WallBox[];
  let result: SplitResult;
  let roomsJson: Room[];
  let frontDoorPos: [number, number, number];

  beforeAll(async () => {
    roomsJson = JSON.parse(
      readFileSync(join(V2_HOUSES, "hou_236595179/rooms.json"), "utf8"),
    ) as Room[];
    const listingsJson = JSON.parse(readFileSync(LISTINGS, "utf8")) as Listing[];
    const listing7014 = listingsJson.find((l) => l.id === "hou_236595179")!;
    frontDoorPos = listing7014.world!.door as [number, number, number];

    const glbBuf = readFileSync(join(V2_HOUSES, "hou_236595179/collision.glb"));
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
    const frontDoorOpening = result.doors.find((d) => d.id.includes("front"));
    expect(frontDoorOpening).toBeUndefined();

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

    const unchangedOriginalBoxes = originalBoxes.filter((orig) =>
      result.boxes.some((curr) => isBoxEqual(orig, curr)),
    );
    expect(unchangedOriginalBoxes).toHaveLength(57);
    expect(result.boxes).toHaveLength(78);

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

describe("synthetic box with two closed doors", () => {
  it("splits a single wall box into solid pieces outside/between doors and lintels above", () => {
    // Wall box along X: [0, 10], height 0 to 2.5, thickness in Z: [0, 0.15]
    const box: WallBox = {
      minX: 0,
      maxX: 10,
      minY: 0,
      maxY: 2.5,
      minZ: 0,
      maxZ: 0.15,
    };

    // Synthetic room with two closed doors along this box:
    // Door 1: x from 2 to 3, z = 0.075
    // Door 2: x from 6 to 7, z = 0.075
    const syntheticRooms: Room[] = [
      {
        id: "room_synthetic",
        label: "living_room",
        label_confidence: 1,
        floor: 1,
        floor_y: 0,
        height_m: 2.5,
        polygon: [
          [0, 0],
          [10, 0],
          [10, 5],
          [0, 5],
        ],
        center: [5, 2.5],
        area_m2: 50,
        openings: [
          {
            type: "door",
            a: [2, 0.075],
            b: [3, 0.075],
            to_room: "room_b",
          },
          {
            type: "door",
            a: [6, 0.075],
            b: [7, 0.075],
            to_room: "room_c",
          },
        ],
      },
    ];

    const res = splitClosedDoors([box], syntheticRooms, null);

    // Both doors opened
    expect(res.doors).toHaveLength(2);

    // Solid pieces: [0, 2], [3, 6], [7, 10] (height 0 to 2.5)
    const solidPieces = res.boxes.filter((b) => b.minY === 0 && b.maxY === 2.5);
    expect(solidPieces).toHaveLength(3);
    expect(solidPieces.some((b) => b.minX === 0 && b.maxX === 2)).toBe(true);
    expect(solidPieces.some((b) => b.minX === 3 && b.maxX === 6)).toBe(true);
    expect(solidPieces.some((b) => b.minX === 7 && b.maxX === 10)).toBe(true);

    // Lintel pieces: above [2, 3] and above [6, 7] from y=2.03 to 2.5
    const lintelPieces = res.boxes.filter((b) => b.minY >= 2.03 && b.maxY === 2.5);
    expect(lintelPieces).toHaveLength(2);
    expect(lintelPieces.some((b) => b.minX === 2 && b.maxX === 3)).toBe(true);
    expect(lintelPieces.some((b) => b.minX === 6 && b.maxX === 7)).toBe(true);

    // Clear opening below y=2.0: neither doorway is overlapped
    for (const d of res.doors) {
      for (let t = 0.1; t <= 0.9; t += 0.1) {
        const px = d.a[0] + (d.b[0] - d.a[0]) * t;
        const pz = d.a[1] + (d.b[1] - d.a[1]) * t;
        const hit = res.boxes.filter(
          (b) =>
            px > b.minX + 0.001 &&
            px < b.maxX - 0.001 &&
            pz > b.minZ + 0.001 &&
            pz < b.maxZ - 0.001 &&
            b.minY < 2.0 &&
            b.maxY > 0.0,
        );
        expect(hit).toHaveLength(0);
      }
    }
  });
});

describe("doorSplit with real 6915 (hou_237901269)", () => {
  let originalBoxes: WallBox[];
  let result: SplitResult;
  let frontDoorPos: [number, number, number];

  beforeAll(async () => {
    const roomsJson = JSON.parse(
      readFileSync(join(V2_HOUSES, "hou_237901269/rooms.json"), "utf8"),
    ) as Room[];
    const listingsJson = JSON.parse(readFileSync(LISTINGS, "utf8")) as Listing[];
    const listing6915 = listingsJson.find((l) => l.id === "hou_237901269")!;
    frontDoorPos = listing6915.world!.door as [number, number, number];

    const glbBuf = readFileSync(join(V2_HOUSES, "hou_237901269/collision.glb"));
    const ab = new ArrayBuffer(glbBuf.length);
    new Uint8Array(ab).set(glbBuf);

    const gltf = await new Promise<GLTF>((resolve, reject) => {
      new GLTFLoader().parse(ab, "", resolve, reject);
    });

    originalBoxes = extractWallBoxes(gltf.scene);
    result = splitClosedDoors(originalBoxes, roomsJson, frontDoorPos);
  });

  it("extracts wall boxes and opens closed doors including multi-door spanning wall", () => {
    expect(originalBoxes).toHaveLength(55);
    expect(result.doors).toHaveLength(6);
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
});
