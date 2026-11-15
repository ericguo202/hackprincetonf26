// Pack v2 door heights, with the real listings index and rooms.json from
// apps/web/public/packs (Lane A, HANDOFF 22:20): Listing.world.position.y is
// the block ground at each front door, 7014 0.348 m and 6915 0.103 m.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import type { Listing, Room } from "../contracts.gen";
import { FOOT_BELOW_CENTRE_M } from "../player/characterAsset";
import { SPAWN_DROP_M, bodyCentreAt } from "./landing";
import { blockToHouse, deriveSpawn, makeDoor, parseListings, stepZone, DOOR_RADIUS_M, type Vec3 } from "./pack";

const PACKS = join(dirname(fileURLToPath(import.meta.url)), "../../public/packs");
const V2 = join(PACKS, "packs/hou_navidad_01/v2");
const json = (p: string) => JSON.parse(readFileSync(p, "utf8"));
const parsed = parseListings(json(join(PACKS, "listings/index.json")));
if (!parsed.ok) throw new Error("v2 listings index did not parse");
const listings: Listing[] = parsed.value;
const rooms = (id: string): Room[] => {
  const r = json(join(V2, `houses/${id}/rooms.json`));
  return Array.isArray(r) ? r : r.rooms;
};

const H7014 = "hou_236595179";
const H6915 = "hou_237901269";
const OFFSET = FOOT_BELOW_CENTRE_M + SPAWN_DROP_M;

// [id, ground at the door (position.y), the door's own local y, the room floor_y]
const CASES = [
  [H7014, 0.348, -0.003, -0.003],
  [H6915, 0.103, 0.004, 0.004],
] as const;

describe.each(CASES)("pack v2 door heights: %s", (id, ground, doorY, floorY) => {
  const listing = listings.find((l) => l.id === id)!;
  const door = makeDoor(listing, rooms(id))!;

  it("the index carries Lane A's ground height and door point", () => {
    expect(listing.world!.position[1]).toBeCloseTo(ground, 6);
    expect(listing.world!.door[1]).toBeCloseTo(doorY, 6);
  });

  it("the door and its exit landing sit on the block ground, not at 0", () => {
    expect(door.block[1]).toBeCloseTo(ground + doorY, 6);
    expect(door.exitTo[1]).toBeCloseTo(ground + doorY, 6);
    expect(door.exitTo[1]).toBeGreaterThan(0.09);
  });

  it("leaving the house puts the body centre at ground + foot offset + drop", () => {
    const inside = stepZone({ zone: id, armed: true }, door.local, [door]);
    expect(inside.change?.zone).toBe("block");
    const body = bodyCentreAt(inside.change!.spawn);
    expect(body[1]).toBeCloseTo(ground + doorY + OFFSET, 6);
    expect(Math.abs(body[1] - OFFSET)).toBeGreaterThan(0.09); // not the v1 landing at y 0
  });

  it("entering lands on the room floor in the house frame (block ground removed)", () => {
    expect(door.enterTo[1]).toBeCloseTo(floorY, 6);
    // exitTo back into the house frame is at the door's local floor again.
    expect(blockToHouse(door.world, door.exitTo)[1]).toBeCloseTo(doorY, 6);
  });

  it("the block door trigger fires for a player standing on the raised ground", () => {
    // me.pos is the body centre: ground + offset above the door point.
    const standing: Vec3 = [door.block[0] + 0.5, door.block[1] + FOOT_BELOW_CENTRE_M, door.block[2]];
    const r = stepZone({ zone: "block", armed: true }, standing, [door]);
    expect(0.5).toBeLessThan(DOOR_RADIUS_M);
    expect(r.change).toEqual({ zone: id, spawn: door.enterTo });
  });

  it("a derived spawn near this door is at its ground height", () => {
    expect(deriveSpawn([door], { [id]: rooms(id) })![1]).toBeCloseTo(ground + doorY, 6);
  });
});

describe("bodyCentreAt", () => {
  it("adds the standing foot offset plus a small drop, nothing else", () => {
    expect(bodyCentreAt([1, 0.288, 2])).toEqual([1, 0.288 + OFFSET, 2]);
    expect(SPAWN_DROP_M).toBeGreaterThan(0);
    expect(SPAWN_DROP_M).toBeLessThan(0.2);
  });
});
