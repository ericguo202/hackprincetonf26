// E at front doors (pack.ts pressFrontDoor / crossDoor, doorInteract.ts
// doorTargetAt), with the real pack v3 listings index, rooms.json and 7014's
// collision.glb (for its openable interior doors).
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { beforeAll, describe, expect, it } from "vitest";
import { GLTFLoader, type GLTF } from "three/examples/jsm/loaders/GLTFLoader.js";

import type { Listing, PackManifest, Room } from "../contracts.gen";
import { type Mesh, Triangle, Vector3 } from "three";
import { doorTargetAt, frontDoorPromptText, pickDoorAction, DOOR_INTERACT_RADIUS_M } from "./doorInteract";
import { extractWallBoxes, splitClosedDoors, type OpenedDoor } from "./doorSplit";
import {
  blockDoors,
  DOOR_EXIT_M,
  DOOR_RADIUS_M,
  FRONT_DOOR_KEY_M,
  parseListings,
  pressFrontDoor,
  roomIdAt,
  stepZone,
  type Door,
  type Vec3,
} from "./pack";

const PACKS = join(dirname(fileURLToPath(import.meta.url)), "../../public/packs");
const V3 = join(PACKS, "packs/hou_navidad_01/v5"); // v5 = v3's houses and block, furniture v4
const json = (p: string) => JSON.parse(readFileSync(p, "utf8"));
const parsed = parseListings(json(join(PACKS, "listings/index.json")));
if (!parsed.ok) throw new Error("v3 listings index did not parse");
const listings: Listing[] = parsed.value;
const rooms = (id: string): Room[] => {
  const r = json(join(V3, `houses/${id}/rooms.json`));
  return Array.isArray(r) ? r : r.rooms;
};

const H7014 = "hou_236595179";
const H6915 = "hou_237901269";
const listing7014 = listings.find((l) => l.id === H7014)!;
const rooms7014 = rooms(H7014);
const manifest = json(join(V3, "manifest.json")) as PackManifest;
// Through blockDoors, as the game builds them: leaveTo comes from manifest.spawns.
const blockDoorList = blockDoors(manifest, listings, { [H7014]: rooms7014, [H6915]: rooms(H6915) });
const door7014 = blockDoorList.find((d) => d.listing_id === H7014)!;
const door6915 = blockDoorList.find((d) => d.listing_id === H6915)!;
const doors: Door[] = [door7014, door6915];

/** A body centre `d` m from `p` along +X (XZ distance d), 1 m up. */
const off = (p: Vec3, d: number, dir: [number, number] = [1, 0]): Vec3 => [p[0] + dir[0] * d, p[1] + 1, p[2] + dir[1] * d];

describe("E at 7014's front door (real v3 data)", () => {
  it("on the block, E within 2 m enters at enterTo, in the living room r3", () => {
    const pos = off(door7014.marker, 1.5);
    // The walk-in trigger alone would not fire here (0.9 m).
    expect(stepZone({ zone: "block", armed: true }, pos, doors).change).toBeNull();
    const { state, change } = pressFrontDoor({ zone: "block", armed: true }, pos, doors);
    expect(change).toEqual({ zone: H7014, spawn: door7014.enterTo });
    expect(state).toEqual({ zone: H7014, armed: false });
    expect(roomIdAt(change!.spawn, rooms7014)).toBe(`${H7014}.r3`);
  });

  it("E gives exactly the walk-in trigger's result", () => {
    const walk = stepZone({ zone: "block", armed: true }, door7014.marker, doors);
    const key = pressFrontDoor({ zone: "block", armed: true }, door7014.marker, doors);
    expect(key).toEqual(walk);
    const walkOut = stepZone({ zone: H7014, armed: true }, door7014.local, doors);
    const keyOut = pressFrontDoor({ zone: H7014, armed: true }, door7014.local, doors);
    expect(keyOut).toEqual(walkOut);
  });

  it("inside, E near the door goes back out to leaveTo", () => {
    const pos = off(door7014.local, 1.9, [0, 1]);
    const { state, change } = pressFrontDoor({ zone: H7014, armed: true }, pos, doors);
    expect(change).toEqual({ zone: "block", spawn: door7014.leaveTo, yaw: door7014.leaveYaw });
    expect(state).toEqual({ zone: "block", armed: false });
  });

  it("at 2.1 m nothing happens, on the block or inside", () => {
    const outside = pressFrontDoor({ zone: "block", armed: true }, off(door7014.marker, 2.1), doors);
    expect(outside.change).toBeNull();
    expect(outside.state).toEqual({ zone: "block", armed: true });
    const inside = pressFrontDoor({ zone: H7014, armed: true }, off(door7014.local, 2.1), doors);
    expect(inside.change).toBeNull();
  });

  it("can't bounce: after E in, the walk-in trigger stays disarmed at the landing and re-arms only when clear", () => {
    const { state, change } = pressFrontDoor({ zone: "block", armed: true }, off(door7014.marker, 1.5), doors);
    let s = state;
    for (let i = 0; i < 40; i++) {
      const step = stepZone(s, change!.spawn, doors); // standing still at enterTo
      expect(step.change).toBeNull();
      s = step.state;
    }
    expect(s).toEqual({ zone: H7014, armed: false });
    // Pressing E again while disarmed still works: it is a deliberate key press.
    expect(pressFrontDoor(s, change!.spawn, doors).change?.zone).toBe("block");
  });

  it("inside a house, only that house's door counts", () => {
    // 7014's door point in its own frame, but we are in 6915.
    const t = pressFrontDoor({ zone: H6915, armed: true }, off(door6915.local, 3), doors);
    expect(t.change).toBeNull();
  });

  it("prompt text: the address on the block, the listing id without one, Leave house inside", () => {
    expect(listing7014.address).toBeTruthy();
    expect(frontDoorPromptText("block", H7014, listing7014.address)).toBe(`E  Enter ${listing7014.address}`);
    expect(frontDoorPromptText("block", H7014, null)).toBe(`E  Enter ${H7014}`);
    expect(frontDoorPromptText("block", H7014, "  ")).toBe(`E  Enter ${H7014}`);
    expect(frontDoorPromptText(H7014, H7014, listing7014.address)).toBe("E  Leave house");
  });

  it("the E range is wider than the walk-in trigger", () => {
    expect(FRONT_DOOR_KEY_M).toBe(2.0);
    expect(FRONT_DOOR_KEY_M).toBeGreaterThan(DOOR_RADIUS_M);
  });
});

describe("interior door vs front door precedence", () => {
  it("pickDoorAction: nearer wins, the interior door wins a tie", () => {
    expect(pickDoorAction(null, null)).toBeNull();
    expect(pickDoorAction(1.0, null)).toBe("interior");
    expect(pickDoorAction(null, 1.9)).toBe("front");
    expect(pickDoorAction(0.8, 1.2)).toBe("interior");
    expect(pickDoorAction(1.2, 0.8)).toBe("front");
    expect(pickDoorAction(1.0, 1.0)).toBe("interior");
  });

  describe("with 7014's real interior doors", () => {
    let interior: readonly OpenedDoor[];
    beforeAll(async () => {
      const buf = readFileSync(join(V3, `houses/${H7014}/collision.glb`));
      const ab = new ArrayBuffer(buf.length);
      new Uint8Array(ab).set(buf);
      const gltf = await new Promise<GLTF>((res, rej) => new GLTFLoader().parse(ab, "", res, rej));
      interior = splitClosedDoors(extractWallBoxes(gltf.scene), rooms7014, listing7014.world!.door as Vec3).doors;
    });

    const mid = (d: OpenedDoor): Vec3 => [(d.a[0] + d.b[0]) / 2, 1, (d.a[1] + d.b[1]) / 2];

    it("at an interior door far from the front door, E targets the interior door", () => {
      const far = interior.find(
        (d) => Math.hypot(mid(d)[0] - door7014.local[0], mid(d)[2] - door7014.local[2]) > FRONT_DOOR_KEY_M + 1,
      )!;
      expect(far).toBeDefined();
      const t = doorTargetAt(H7014, mid(far), interior, doors);
      expect(t?.kind).toBe("interior");
      expect(t?.kind === "interior" && t.door.id).toBe(far.id);
    });

    it("at 7014's front door no real interior door is in range (nearest 2.7 m), so E leaves", () => {
      const pos = off(door7014.local, 0.2);
      const nearestInterior = Math.min(
        ...interior.map((d) => Math.hypot(mid(d)[0] - pos[0], mid(d)[2] - pos[2])),
      );
      expect(nearestInterior).toBeGreaterThan(DOOR_INTERACT_RADIUS_M);
      const t = doorTargetAt(H7014, pos, interior, doors);
      expect(t?.kind).toBe("front");
      expect(t?.kind === "front" && t.door.listing_id).toBe(H7014);
    });

    it("an interior door and the front door both in range: the nearer wins, a tie goes to the interior door", () => {
      // A synthetic interior door placed beside 7014's real front door.
      const fd = door7014.local;
      const beside = (dx: number): OpenedDoor => ({
        id: "synthetic",
        a: [fd[0] + dx, fd[2] - 0.4],
        b: [fd[0] + dx, fd[2] + 0.4],
        floorY: 0,
        width: 0.8,
      });
      const pos = off(fd, 0.5); // 0.5 m from the front door
      expect(doorTargetAt(H7014, pos, [beside(0.7)], doors)?.kind).toBe("interior"); // 0.2 m away
      expect(doorTargetAt(H7014, pos, [beside(1.9)], doors)?.kind).toBe("front"); // 1.4 m away
      expect(doorTargetAt(H7014, pos, [beside(1.0)], doors)?.kind).toBe("interior"); // 0.5 m: a tie
    });

    it("on the block, interior doors never count", () => {
      const t = doorTargetAt("block", off(door7014.marker, 1), interior, doors);
      expect(t?.kind).toBe("front");
      expect(t?.kind === "front" && t.door.listing_id).toBe(H7014);
    });
  });
});

describe("block door marker and leave landing clear of the real house (pack v3)", () => {
  const dist = (a: Vec3, b: Vec3) => Math.hypot(a[0] - b[0], a[2] - b[2]);
  let tris: Triangle[] = [];
  beforeAll(async () => {
    const buf = readFileSync(join(V3, "block/collision.glb"));
    const ab = new ArrayBuffer(buf.length);
    new Uint8Array(ab).set(buf);
    const gltf = await new Promise<GLTF>((res, rej) => new GLTFLoader().parse(ab, "", res, rej));
    gltf.scene.updateMatrixWorld(true);
    const out: Triangle[] = [];
    gltf.scene.traverse((o) => {
      const m = o as Mesh;
      if (!m.isMesh) return;
      const g = m.geometry;
      const pos = g.getAttribute("position");
      const idx = g.getIndex();
      const n = idx ? idx.count : pos.count;
      const v = (k: number) => new Vector3().fromBufferAttribute(pos, idx ? idx.getX(k) : k).applyMatrix4(m.matrixWorld);
      for (let k = 0; k < n; k += 3) out.push(new Triangle(v(k), v(k + 1), v(k + 2)));
    });
    tris = out;
  });
  /** Nearest block collision triangle to a point at body height (0.5 to 1.5 m above the landing floor point). */
  const clearance = (p: Vec3) => {
    let best = Infinity;
    const q = new Vector3();
    for (const h of [0.5, 1.0, 1.5]) {
      const at = new Vector3(p[0], p[1] + h, p[2]);
      for (const t of tris) best = Math.min(best, t.closestPointToPoint(at, q).distanceTo(at));
    }
    return best;
  };
  const leave = (d: Door) => pressFrontDoor({ zone: d.listing_id, armed: true }, d.local, doors);

  it("loaded the block collision", () => expect(tris.length).toBeGreaterThan(0));

  it("leaving 7014 (walk-in and E) lands on a manifest spawn, facing the house", () => {
    const out = leave(door7014);
    expect(stepZone({ zone: H7014, armed: true }, door7014.local, doors)).toEqual(out);
    expect(manifest.spawns.some((s) => dist(s as Vec3, out.change!.spawn) < 1e-9)).toBe(true);
    expect(out.change!.yaw).toBeCloseTo(door7014.leaveYaw, 9);
  });

  it("leaving 6915 lands DOOR_EXIT_M (7.0 m) out from its door point (no spawn in range)", () => {
    const out = leave(door6915);
    expect(dist(out.change!.spawn, door6915.block)).toBeCloseTo(DOOR_EXIT_M, 6);
  });

  it.each([
    ["7014", door7014],
    ["6915", door6915],
  ])("%s: landing is 0.3 m clear of block collision and does not re-trigger the door", (_, d) => {
    const out = leave(d);
    expect(clearance(out.change!.spawn)).toBeGreaterThan(0.3);
    let s = out.state;
    for (let i = 0; i < 20; i++) {
      const step = stepZone(s, out.change!.spawn, doors);
      expect(step.change).toBeNull();
      s = step.state;
    }
  });

  it.each([
    ["7014", door7014],
    ["6915", door6915],
  ])("%s: marker 4.2 m out; walk-in and E measured from it", (_, d) => {
    expect(dist(d.marker, d.block)).toBeCloseTo(4.2, 6);
    expect(stepZone({ zone: "block", armed: true }, d.block, doors).change).toBeNull();
    expect(stepZone({ zone: "block", armed: true }, off(d.marker, DOOR_RADIUS_M - 0.05), doors).change).toEqual({
      zone: d.listing_id,
      spawn: d.enterTo,
    });
    expect(pressFrontDoor({ zone: "block", armed: true }, off(d.marker, FRONT_DOOR_KEY_M - 0.05), doors).change?.zone).toBe(d.listing_id);
  });
});
