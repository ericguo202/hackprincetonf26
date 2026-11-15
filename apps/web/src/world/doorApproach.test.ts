import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import type { Listing, Point2, Room } from "../contracts.gen";
import { viewDir } from "../player/armMath";
import { pointInPolygon } from "./geometry";
import { bodyCentreAt } from "./landing";
import { houseToBlock, makeDoor, parseListings } from "./pack";
import { doorApproach } from "./doorApproach";

const PACKS = join(dirname(fileURLToPath(import.meta.url)), "../../public/packs");
const V3 = join(PACKS, "packs/hou_navidad_01/v5");
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

const HOUSES = [H7014, H6915] as const;

describe.each(HOUSES)("doorApproach for %s", (id) => {
  const listing = listings.find((l) => l.id === id)!;
  const houseRooms = rooms(id);
  const door = makeDoor(listing, houseRooms)!;
  const approach = doorApproach(door, houseRooms);

  it("point is 1.5 m from the door in XZ", () => {
    const dist = Math.hypot(approach.pos[0] - door.block[0], approach.pos[2] - door.block[2]);
    expect(dist).toBeCloseTo(1.5, 4);
  });

  it("y matches bodyCentreAt(door.block[1])", () => {
    expect(approach.pos[1]).toBeCloseTo(bodyCentreAt(door.block[1]), 6);
  });

  it("point is outside every room polygon converted to the block frame", () => {
    for (const room of houseRooms) {
      const polyInBlock: Point2[] = room.polygon.map(([rx, rz]) => {
        const bp = houseToBlock(door.world, [rx, 0, rz]);
        return [bp[0], bp[2]];
      });
      const inside = pointInPolygon([approach.pos[0], approach.pos[2]], polyInBlock);
      expect(inside).toBe(false);
    }
  });

  it("yaw points at the door to within 1°", () => {
    const dx = door.block[0] - approach.pos[0];
    const dz = door.block[2] - approach.pos[2];
    const dist = Math.hypot(dx, dz);
    expect(dist).toBeGreaterThan(0);

    const dirX = dx / dist;
    const dirZ = dz / dist;

    // View direction vector at elevation 0
    const [vx, , vz] = viewDir(approach.yaw, 0);
    const dot = vx * dirX + vz * dirZ;
    const rad1Deg = (1 * Math.PI) / 180;
    const minDot = Math.cos(rad1Deg);

    expect(dot).toBeGreaterThanOrEqual(minDot);
  });
});
