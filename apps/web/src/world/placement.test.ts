import { describe, expect, it } from "vitest";

import type { Asset, Room } from "../contracts.gen";
import { rectCorners } from "./geometry";
import {
  facingDeg,
  feetInches,
  floorBelow,
  nearestWall,
  pickSolid,
  planGhost,
  rayHitsSolid,
  rayPlaneT,
  solidsOf,
  WALL_GAP_M,
  type Solid,
} from "./placement";

function room(id: string, polygon: [number, number][], floor_y = 0): Room {
  return { id, label: "living_room", label_confidence: 1, floor: floor_y > 1 ? 1 : 0, floor_y, height_m: 2.6, area_m2: 0, polygon, openings: [] } as unknown as Room;
}

function asset(id: string, over: Partial<Asset> = {}): Asset {
  return {
    id,
    name: id[0].toUpperCase() + id.slice(1),
    category: id,
    style: null,
    color: null,
    material: null,
    tags: [],
    file: `furniture/v3/${id}.glb`,
    size_m: { w: 2, d: 1, h: 0.8 },
    placement: "free",
    cover: "half",
    source: { dataset: "ABO", license: "CC BY 4.0", credit: "Amazon Berkeley Objects, Amazon.com" },
    ...over,
  } as Asset;
}

// A 6 x 4 m room from (0,0) to (6,4), and a 3 x 4 m room beside it to the east.
const LIVING = room("h.r1", [[0, 0], [6, 0], [6, 4], [0, 4]]);
const DEN = room("h.r2", [[6, 0], [9, 0], [9, 4], [6, 4]]);
const UPSTAIRS = room("h.r3", [[0, 0], [6, 0], [6, 4], [0, 4]], 3);
const ROOMS = [LIVING, DEN, UPSTAIRS];

const base = { floorY: 0, rotDeg: 0, rooms: ROOMS, solids: [] as Solid[], snap: true };

describe("planGhost", () => {
  it("places a free piece where aimed, in the room holding its centre", () => {
    const g = planGhost({ ...base, point: [3, 2], asset: asset("table"), rotDeg: 30 });
    expect(g).toMatchObject({ x: 3, z: 2, y: 0, rot_deg: 30, room_id: "h.r1", valid: true, snapped: null });
  });

  it("refuses a centre outside every room on this floor", () => {
    const g = planGhost({ ...base, point: [-2, 2], asset: asset("table") });
    expect(g.valid).toBe(false);
    expect(g.room_id).toBeNull();
  });

  it("refuses a footprint through an outside wall but allows one across two rooms", () => {
    expect(planGhost({ ...base, point: [0.5, 2], asset: asset("table"), snap: false }).why).toBe("Goes through a wall");
    // Straddling the r1/r2 boundary at x = 6: every corner is in some room.
    expect(planGhost({ ...base, point: [6, 2], asset: asset("table") }).valid).toBe(true);
  });

  it("snaps a wall piece flush to the nearest wall, facing into the room", () => {
    const sofa = asset("sofa", { placement: "wall" });
    // Near the north wall z = 4: its back goes to the wall, its front faces -Z.
    const g = planGhost({ ...base, point: [3, 3.4], asset: sofa, rotDeg: 10 });
    expect(g.snapped).toBe("wall");
    expect(g.rot_deg).toBeCloseTo(180);
    expect(g.z).toBeCloseTo(4 - 0.5 - WALL_GAP_M);
    expect(g.x).toBeCloseTo(3);
    expect(g.valid).toBe(true);
    // The snapped footprint's back edge is just inside the wall.
    const maxZ = Math.max(...rectCorners({ x: g.x, z: g.z, w: 2, d: 1, rotDeg: g.rot_deg }).map((c) => c[1]));
    expect(maxZ).toBeCloseTo(4 - WALL_GAP_M);
  });

  it("does not snap with snap off, or when far from every wall", () => {
    const sofa = asset("sofa", { placement: "wall" });
    expect(planGhost({ ...base, point: [3, 3.4], asset: sofa, snap: false }).snapped).toBeNull();
    expect(planGhost({ ...base, point: [3, 2], asset: sofa }).snapped).toBeNull();
  });

  it("refuses a solid overlap and names the piece in the way", () => {
    const solids = solidsOf([{ instance_id: "a", asset_id: "couch", x: 3, y: 0, z: 2, rot_deg: 0 }], { couch: asset("couch") });
    const g = planGhost({ ...base, point: [3.5, 2.2], asset: asset("table"), solids });
    expect(g).toMatchObject({ valid: false, why: "Hits the couch" });
  });

  it("lets flat pieces overlap anything, and lets a wall TV hang above a stand", () => {
    const catalog = { stand: asset("stand", { size_m: { w: 1.5, d: 0.4, h: 0.6 } }), rug: asset("rug", { size_m: { w: 2, d: 1.5, h: 0.01 } }) };
    const solids = solidsOf(
      [
        { instance_id: "s", asset_id: "stand", x: 3, y: 0, z: 3.7, rot_deg: 180 },
        { instance_id: "r", asset_id: "rug", x: 3, y: 0, z: 2, rot_deg: 0 },
      ],
      catalog,
    );
    expect(planGhost({ ...base, point: [3, 2], asset: asset("table"), solids }).valid).toBe(true);
    expect(planGhost({ ...base, point: [3, 3.1], asset: catalog.rug, solids }).valid).toBe(true);
    const tv = asset("tv", { placement: "wall", mount_y_m: 0.75, size_m: { w: 1.2, d: 0.08, h: 0.7 } });
    const g = planGhost({ ...base, point: [3, 3.8], asset: tv, solids });
    expect(g).toMatchObject({ valid: true, snapped: "wall", y: 0.75 });
  });

  it("puts an on_surface piece on top of the piece under it", () => {
    const solids = solidsOf([{ instance_id: "s", asset_id: "stand", x: 3, y: 0, z: 2, rot_deg: 180 }], {
      stand: asset("stand", { size_m: { w: 1.5, d: 0.5, h: 0.6 } }),
    });
    const tv = asset("tv", { placement: "on_surface", size_m: { w: 1.2, d: 0.25, h: 0.7 } });
    expect(planGhost({ ...base, point: [3, 2], asset: tv, solids, rotDeg: 30 })).toMatchObject({ y: 0.6, rot_deg: 180, snapped: "surface", valid: true });
    expect(planGhost({ ...base, point: [3, 2], asset: tv, solids, rotDeg: 30, snap: false }).rot_deg).toBe(30);
    expect(planGhost({ ...base, point: [1, 1.5], asset: tv, solids })).toMatchObject({ y: 0, snapped: null });
  });

  it("keeps doorways clear, on both sides, except for flat pieces", () => {
    // A door in the south wall (z = 0) from x = 2 to x = 3.
    const withDoor = { ...LIVING, openings: [{ type: "door", a: [2, 0], b: [3, 0], to_room: null }] } as unknown as Room;
    const rooms = [withDoor, DEN];
    const sofa = asset("sofa", { placement: "wall" });
    expect(planGhost({ ...base, rooms, point: [2.5, 0.4], asset: sofa })).toMatchObject({ valid: false, why: "Blocks a doorway" });
    expect(planGhost({ ...base, rooms, point: [4.8, 0.4], asset: sofa }).valid).toBe(true);
    // 0.7 m in front of the door: the 1 m deep table still reaches into the clear zone.
    expect(planGhost({ ...base, rooms, point: [2.5, 1.0], asset: asset("table") }).why).toBe("Blocks a doorway");
    expect(planGhost({ ...base, rooms, point: [2.5, 1.2], asset: asset("table") }).valid).toBe(true);
    const rug = asset("rug", { size_m: { w: 2, d: 1.5, h: 0.01 } });
    expect(planGhost({ ...base, rooms, point: [2.5, 1.0], asset: rug }).valid).toBe(true);
  });

  it("furnishes the floor the player is on", () => {
    const g = planGhost({ ...base, floorY: 3, point: [3, 2], asset: asset("table") });
    expect(g).toMatchObject({ room_id: "h.r3", y: 3, valid: true });
    expect(floorBelow(3.1, ROOMS)).toBe(3);
    expect(floorBelow(1.2, ROOMS)).toBe(0);
  });
});

describe("walls and facing", () => {
  it("facingDeg turns +Z (rot 0) toward the given direction", () => {
    expect(facingDeg(0, 1)).toBeCloseTo(0);
    expect(facingDeg(1, 0)).toBeCloseTo(90);
    expect(facingDeg(0, -1)).toBeCloseTo(180);
    expect(facingDeg(-1, 0)).toBeCloseTo(270);
  });

  it("nearestWall gives an inward normal whatever the polygon winding", () => {
    const cw = room("cw", [[0, 0], [0, 4], [6, 4], [6, 0]]);
    for (const r of [LIVING, cw]) {
      const w = nearestWall([3, 0.3], r)!;
      expect(w.dist).toBeCloseTo(0.3);
      expect(w.normal[0]).toBeCloseTo(0);
      expect(w.normal[1]).toBeCloseTo(1);
    }
  });
});

describe("picking", () => {
  const solids = solidsOf([{ instance_id: "a", asset_id: "couch", x: 3, y: 0, z: 2, rot_deg: 90 }], { couch: asset("couch") });

  it("hits a rotated box from the camera and misses beside it", () => {
    // Rotated 90 degrees: 1 m along X, 2 m along Z.
    expect(rayHitsSolid([3, 1.6, -1], [0, -0.3, 1], solids[0])).not.toBeNull();
    expect(rayHitsSolid([4, 1.6, -1], [0, 0, 1], solids[0])).toBeNull();
    expect(rayHitsSolid([3.8, 0.5, -1], [0, 0, 1], solids[0])).toBeNull(); // past the 1 m width
  });

  it("does not pick through a closer wall", () => {
    expect(pickSolid([3, 0.5, -1], [0, 0, 1], solids, 10)?.id).toBe("a");
    expect(pickSolid([3, 0.5, -1], [0, 0, 1], solids, 0.5)).toBeNull();
  });

  it("rayPlaneT finds the floor ahead and nothing behind", () => {
    expect(rayPlaneT([0, 1.6, 0], [0, -1, 0], 0)).toBeCloseTo(1.6);
    expect(rayPlaneT([0, 1.6, 0], [0, 1, 0], 0)).toBeNull();
  });
});

describe("feetInches", () => {
  it("shows feet and inches", () => {
    expect(feetInches(2.312)).toBe("7′7″");
    expect(feetInches(0.3)).toBe("1′0″");
    expect(feetInches(0.1)).toBe("4″");
  });
});
