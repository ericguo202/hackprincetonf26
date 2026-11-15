import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import type { Room } from "../contracts.gen";
import { roomIdAt } from "../world/pack";
import { furnishedRoom, furnitureCentre, showcaseUrl, tourFurnitureUrl, tourRoot, viewpointSweep } from "./tourPlan";

// The hero house (7014 Navidad, Matterport y1cVPR1HsnL) as published.
const PACKS = join(dirname(fileURLToPath(import.meta.url)), "../../public/packs");
const rooms = JSON.parse(
  readFileSync(join(PACKS, "packs/hou_navidad_01/v3/houses/hou_236595179/rooms.json"), "utf8"),
) as Room[];
const LIVING = "hou_236595179.r3";

// Real Showcase SDK sweep positions of y1cVPR1HsnL (Sweep.data, 2026-11-15).
const SWEEPS = [
  { id: "9axk0p3dwfiww0g9ftk2iekxd", position: { x: 2.504, y: 1.486, z: -4.642 } },
  { id: "381kagw1ccru10149hrz458kd", position: { x: 4.834, y: 1.498, z: -4.512 } },
  { id: "f2mk71s5939fa9reuxiaea1zd", position: { x: 3.804, y: 1.489, z: -3.701 } },
  { id: "mh89kx9drfi1rgucpp6nck8ma", position: { x: 1.813, y: 1.489, z: -6.731 } },
  { id: "0ii3cn7zr1uhydgf15c41ew4d", position: { x: 20.202, y: 0.949, z: -9.687 } },
];

const couch = { asset_id: "sofa", x: 3.17, y: 0, z: -5.08 };
const table = { asset_id: "table", x: 3.4, y: 0, z: -4.6 };

describe("tour urls", () => {
  it("puts the bundle and furniture under <base>matterport/", () => {
    expect(tourRoot("/")).toBe("/matterport/");
    expect(tourRoot("/app")).toBe("/app/matterport/");
    expect(tourFurnitureUrl("/matterport/", "furniture/v3/sofa_abo_X.glb")).toBe("/matterport/furniture/v3/sofa_abo_X.glb");
    const url = showcaseUrl("/matterport/", "y1cVPR1HsnL", "k");
    expect(url.startsWith("/matterport/bundle/showcase.html?")).toBe(true);
    expect(new URL(url, "http://x").searchParams.get("m")).toBe("y1cVPR1HsnL");
    expect(new URL(url, "http://x").searchParams.get("applicationKey")).toBe("k");
  });
});

describe("furnishedRoom", () => {
  it("picks the room holding the furniture", () => {
    expect(furnishedRoom([couch, table], rooms)).toBe(LIVING);
  });
  it("is null with no furniture or none inside a room", () => {
    expect(furnishedRoom([], rooms)).toBeNull();
    expect(furnishedRoom([{ asset_id: "x", x: 500, y: 0, z: 500 }], rooms)).toBeNull();
  });
});

describe("viewpointSweep", () => {
  it("views from a sweep in the furnished room, the farthest from the furniture", () => {
    const centre = furnitureCentre([couch, table])!;
    const id = viewpointSweep(SWEEPS, rooms, LIVING, centre)!;
    const chosen = SWEEPS.find((s) => s.id === id)!;
    const p = chosen.position;
    expect(roomIdAt([p.x, p.y - 1.4, p.z], rooms)).toBe(LIVING);
    const dist = (q: { x: number; z: number }) => Math.hypot(q.x - centre.x, q.z - centre.z);
    for (const s of SWEEPS) {
      const q = s.position;
      if (roomIdAt([q.x, q.y - 1.4, q.z], rooms) === LIVING) expect(dist(q)).toBeLessThanOrEqual(dist(p));
    }
  });
  it("is null when no sweep is in the room", () => {
    expect(viewpointSweep(SWEEPS.slice(4), rooms, LIVING, { x: 3, y: 0.5, z: -5 })).toBeNull();
  });
});

describe("furnitureCentre", () => {
  it("averages the pieces and lifts to look height", () => {
    expect(furnitureCentre([couch, table], 0.5)).toEqual({ x: (3.17 + 3.4) / 2, y: 0.5, z: (-5.08 - 4.6) / 2 });
    expect(furnitureCentre([])).toBeNull();
  });
});
