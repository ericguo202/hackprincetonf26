// INTERIOR_DOOR_LEAVES (doorSplit.ts): leaves off means the pack's walls as-is
// and no openable doors, so no leaf, no collider, no E toggle, no hint.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { beforeAll, describe, expect, it } from "vitest";
import { GLTFLoader, type GLTF } from "three/examples/jsm/loaders/GLTFLoader.js";

import type { Listing, PackManifest, Room } from "../contracts.gen";
import { doorTargetAt } from "./doorInteract";
import { extractWallBoxes, houseWalls, INTERIOR_DOOR_LEAVES, splitClosedDoors, type WallBox } from "./doorSplit";
import { blockDoors, parseListings, pressFrontDoor, type Vec3 } from "./pack";

const PACKS = join(dirname(fileURLToPath(import.meta.url)), "../../public/packs");
const V3 = join(PACKS, "packs/hou_navidad_01/v3");
const json = (p: string) => JSON.parse(readFileSync(p, "utf8"));
const H7014 = "hou_236595179";
const H6915 = "hou_237901269";
const listings = (parseListings(json(join(PACKS, "listings/index.json"))) as { value: Listing[] }).value;
const rooms = (id: string): Room[] => json(join(V3, `houses/${id}/rooms.json`));
const manifest = json(join(V3, "manifest.json")) as PackManifest;
const doors = blockDoors(manifest, listings, { [H7014]: rooms(H7014), [H6915]: rooms(H6915) });
const door7014 = doors.find((d) => d.listing_id === H7014)!;

describe("interior door leaves switched off", () => {
  let boxes: WallBox[];
  beforeAll(async () => {
    const buf = readFileSync(join(V3, `houses/${H7014}/collision.glb`));
    const ab = new ArrayBuffer(buf.length);
    new Uint8Array(ab).set(buf);
    const gltf = await new Promise<GLTF>((res, rej) => new GLTFLoader().parse(ab, "", res, rej));
    boxes = extractWallBoxes(gltf.scene);
  });

  it("is off", () => expect(INTERIOR_DOOR_LEAVES).toBe(false));

  it("off: the walls are the pack's boxes unchanged and no doors register", () => {
    const r = houseWalls(boxes, rooms(H7014), door7014.local);
    expect(r.doors).toEqual([]);
    expect(r.boxes).toEqual(boxes);
  });

  it("on (one line back): the same split as before", () => {
    const r = houseWalls(boxes, rooms(H7014), door7014.local, true);
    expect(r).toEqual(splitClosedDoors(boxes, rooms(H7014), door7014.local));
    expect(r.doors.length).toBeGreaterThan(0);
  });

  it("with no interior doors, E at the front door still targets and leaves through it", () => {
    const { doors: none } = houseWalls(boxes, rooms(H7014), door7014.local);
    const at = (t: number): Vec3 => [door7014.local[0] + t, door7014.local[1] + 1, door7014.local[2]];
    for (const t of [-1.5, 0, 1.5]) {
      expect(doorTargetAt(H7014, at(t), none, doors)?.kind).toBe("front");
      expect(pressFrontDoor({ zone: H7014, armed: true }, at(t), doors).change).toEqual({
        zone: "block",
        spawn: door7014.leaveTo,
        yaw: door7014.leaveYaw,
      });
    }
    // Anywhere else indoors, E has nothing to act on (no "E  Open door" hint).
    expect(doorTargetAt(H7014, [0, 1, -8], none, doors)).toBeNull();
  });
});
