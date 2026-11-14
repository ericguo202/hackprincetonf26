import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import type { Asset, PlacementPlan, Point2, Room } from "../contracts.gen";
import { DEV_PLAN_B, assetCategory, devCatalog } from "./devFurniture";
import { footprints, fits, houseSpawns, retargetPlan } from "./furniturePlan";
import { minDistToPolygon, pointInPolygon, pointInRect, pushOut, rectsOverlap, SPAWN_CLEARANCE_M } from "./geometry";
import { polygonCentroid } from "./pack";

const CONTRACTS = join(dirname(fileURLToPath(import.meta.url)), "../../../../contracts/fixtures");
const json = <T,>(f: string) => JSON.parse(readFileSync(join(CONTRACTS, f), "utf8")) as T;
const fixtureRoom = json<Room>("room.json");
const fixturePlan = json<PlacementPlan>("placement_plan.json");
const fixtureAsset = json<Asset>("asset.json");
const catalog = devCatalog(fixtureAsset, [...fixturePlan.objects, ...DEV_PLAN_B.objects].map((o) => o.asset_id));

// The hero living room, hou_237901269.r4, as shipped in rooms.json.
const r4: Room = {
  id: "hou_237901269.r4",
  label: "living_room",
  label_confidence: 0.9,
  floor: 0,
  floor_y: 0.005,
  height_m: 2.43,
  area_m2: 32.3,
  polygon: [
    [-0.497, 0.512], [-0.496, 0.826], [0.56, 0.823], [1.278, 0.821], [3.701, 0.814],
    [5.2, -0.693], [5.191, -3.85], [-0.809, -3.85], [-0.806, -2.975], [-3.192, -0.575], [-3.189, 0.519],
  ],
  openings: [
    { type: "door", a: [-3.192, -0.525], b: [-3.19, 0.325], to_room: null },
    { type: "door", a: [0.454, 0.824], b: [-0.335, 0.826], to_room: "hou_237901269.r8" },
    { type: "door", a: [5.197, -1.847], b: [5.195, -2.688], to_room: null },
  ],
};

describe("dev catalog", () => {
  it("covers the plan's assets and the MVP living-room pieces", () => {
    for (const o of [...fixturePlan.objects, ...DEV_PLAN_B.objects]) expect(catalog[o.asset_id]).toBeDefined();
    const categories = new Set(Object.values(catalog).map((a) => a.category));
    for (const c of ["sofa", "sectional", "armchair", "coffee_table", "tv_stand", "rug", "bookshelf", "floor_lamp", "plant"]) {
      expect(categories.has(c), c).toBe(true);
    }
  });

  it("sets cover from height, per the asset schema", () => {
    expect(catalog.bookshelf_dev_01.cover).toBe("full");
    expect(catalog.sectional_dev_01.cover).toBe("half");
    expect(catalog.rug_dev_01.cover).toBe("none");
  });

  it("keeps the real fixture asset, and stands in for the fixture plan's ABO TV stand by category", () => {
    expect(catalog[fixtureAsset.id]).toEqual(fixtureAsset);
    const tv = catalog["tv_stand_abo_B07GFFPKBG"];
    expect(tv.category).toBe("tv_stand");
    expect(tv.file).toBe("furniture/v1/tv_stand_abo_B07GFFPKBG.glb");
    expect(assetCategory("sofa_abo_B072C8Q9RF")).toBe("sofa");
    expect(assetCategory("floor_lamp_dev_01")).toBe("floor_lamp");
  });
});

describe("retargetPlan", () => {
  it("leaves the fixture plan fitting the fixture room", () => {
    for (const r of footprints(fixturePlan.objects, catalog)) expect(fits(r, fixtureRoom)).toBe(true);
  });

  it("moves the fixture plan into the real living room with every piece inside", () => {
    const { plan, misfits } = retargetPlan(fixturePlan, fixtureRoom, r4, "hou_237901269", catalog);
    expect(misfits).toEqual([]);
    expect(plan.listing_id).toBe("hou_237901269");
    expect(plan.room_id).toBe(r4.id);
    for (const r of footprints(plan.objects, catalog)) expect(fits(r, r4)).toBe(true);
    // Layout shape and rotations kept: couch and TV still face each other across the room.
    const [couch, tv] = plan.objects;
    expect(tv.z - couch.z).toBeCloseTo(fixturePlan.objects[1].z - fixturePlan.objects[0].z, 9);
    expect([couch.rot_deg, tv.rot_deg]).toEqual([0, 180]);
    expect(couch.y).toBeCloseTo(r4.floor_y, 9);
  });

  it("moves plan B in too, with its sectional on the room's centroid", () => {
    const { plan, misfits } = retargetPlan(DEV_PLAN_B, fixtureRoom, r4, "hou_237901269", catalog);
    expect(misfits).toEqual([]);
    const sectional = plan.objects.find((o) => o.asset_id === "sectional_dev_01")!;
    const c = polygonCentroid(r4.polygon);
    expect(sectional.x).toBeCloseTo(c[0], 6);
    expect(sectional.z).toBeCloseTo(c[1], 6);
  });

  it("reports pieces that would not fit", () => {
    const tiny: Room = { ...fixtureRoom, id: "x.r1", polygon: [[0, 0], [2, 0], [2, 2], [0, 2]] };
    const { misfits } = retargetPlan(fixturePlan, fixtureRoom, tiny, "x", catalog);
    expect(misfits).toEqual(["o1", "o2"]);
  });
});

describe("re-furnish: push-out and spawns in the real living room", () => {
  const a = retargetPlan(fixturePlan, fixtureRoom, r4, "hou_237901269", catalog).plan;
  const b = retargetPlan(DEV_PLAN_B, fixtureRoom, r4, "hou_237901269", catalog).plan;
  const start = polygonCentroid(r4.polygon) as Point2; // where VITE_START_ROOM puts you

  it("plan A leaves the start point clear; plan B lands a sectional on it", () => {
    expect(pushOut(start, r4, footprints(a.objects, catalog))).toEqual(start);
    const moved = pushOut(start, r4, footprints(b.objects, catalog))!;
    expect(moved).not.toEqual(start);
    for (const r of footprints(b.objects, catalog)) expect(pointInRect(moved, r, SPAWN_CLEARANCE_M)).toBe(false);
    expect(pointInPolygon(moved, r4.polygon)).toBe(true);
  });

  it("spawns sit on free floor, clear of walls and furniture, 3 m apart", () => {
    for (const plan of [a, b]) {
      const rects = footprints(plan.objects, catalog);
      const spawns = houseSpawns([r4], rects);
      expect(spawns.length).toBeGreaterThan(0);
      for (const s of spawns) {
        expect(pointInPolygon(s.point, r4.polygon)).toBe(true);
        expect(minDistToPolygon(s.point, r4.polygon)).toBeGreaterThanOrEqual(SPAWN_CLEARANCE_M);
        for (const r of rects) expect(pointInRect(s.point, r, SPAWN_CLEARANCE_M)).toBe(false);
      }
      for (let i = 0; i < spawns.length; i++)
        for (let j = i + 1; j < spawns.length; j++)
          expect(Math.hypot(spawns[i].point[0] - spawns[j].point[0], spawns[i].point[1] - spawns[j].point[1])).toBeGreaterThanOrEqual(3);
    }
  });

  it("recomputing after the swap moves spawns off the new furniture", () => {
    const before = houseSpawns([r4], footprints(a.objects, catalog));
    const rectsB = footprints(b.objects, catalog);
    // At least one plan-A spawn is under plan-B furniture: stale spawns would be wrong.
    expect(before.some((s) => rectsB.some((r) => pointInRect(s.point, r, SPAWN_CLEARANCE_M)))).toBe(true);
    const after = houseSpawns([r4], rectsB);
    expect(after.some((s) => rectsB.some((r) => pointInRect(s.point, r, SPAWN_CLEARANCE_M)))).toBe(false);
  });

  it("plans have no overlapping pieces", () => {
    for (const plan of [a, b]) {
      const rects = footprints(plan.objects, catalog);
      for (let i = 0; i < rects.length; i++)
        for (let j = i + 1; j < rects.length; j++) expect(rectsOverlap(rects[i], rects[j])).toBe(false);
    }
  });
});
