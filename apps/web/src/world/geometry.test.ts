// node --experimental-strip-types apps/web/src/world/geometry.test.ts
// (or point vitest at it — node:assert works there unchanged)
//
// The room below is contracts/fixtures/room.json verbatim, so if Lane A
// changes the fixture and these fail, the contract moved and this file is
// the early warning.

import assert from "node:assert/strict";
import type { Asset, Point2, Room } from "../contracts.gen";
import {
  distToOpening,
  freeFloorAreaM2,
  openingMidpoint,
  pointInPolygon,
  pointInRect,
  polygonArea,
  pushOut,
  rectCorners,
  rectFromPlacement,
  rectsOverlap,
  roomAt,
  rotationY,
  spawnPoints,
  type Rect,
} from "./geometry.ts";

let passed = 0;
function test(name: string, fn: () => void) {
  fn();
  passed++;
  console.log("  ok  " + name);
}

const livingRoom = {
  id: "hou_000123.r1",
  label: "living_room",
  label_confidence: 0.95,
  floor: 0,
  floor_y: 0.0,
  height_m: 2.7,
  area_m2: 25.2,
  polygon: [
    [0, 0],
    [6.0, 0],
    [6.0, 4.2],
    [0, 4.2],
  ],
  openings: [
    { type: "door", a: [0, 1.0], b: [0, 1.9], to_room: null },
    { type: "door", a: [6.0, 2.8], b: [6.0, 3.7], to_room: "hou_000123.r3" },
    { type: "window", a: [0, 2.6], b: [0, 3.8], to_room: null },
  ],
} as unknown as Room;

const couchAsset = {
  id: "couch_01",
  size_m: { w: 2.1, d: 0.9, h: 0.85 },
} as unknown as Asset;

const couch: Rect = { x: 3.0, z: 0.45, w: 2.1, d: 0.9, rotDeg: 0 };
const tvStand: Rect = { x: 3.0, z: 3.95, w: 1.4, d: 0.35, rotDeg: 180 };

console.log("contract alignment");

test("placement + asset becomes a footprint", () => {
  const obj = {
    instance_id: "o1",
    asset_id: "couch_01",
    x: 3.0,
    y: 0.0,
    z: 0.45,
    rot_deg: 0,
    reason: "Against the south wall, facing the TV",
  };
  assert.deepEqual(rectFromPlacement(obj, couchAsset), couch);
});

test("computed area matches the fixture's area_m2", () => {
  assert.ok(Math.abs(polygonArea(livingRoom.polygon) - livingRoom.area_m2) < 1e-9);
});

test("rot_deg maps to three.js rotation-y without negation", () => {
  assert.ok(Math.abs(rotationY(180) - Math.PI) < 1e-12);
});

console.log("primitives");

test("inside and outside the room", () => {
  assert.ok(pointInPolygon([3, 2], livingRoom.polygon));
  assert.ok(!pointInPolygon([-0.1, 2], livingRoom.polygon));
  assert.ok(!pointInPolygon([3, 4.5], livingRoom.polygon));
});

test("rotating 90 deg swaps the footprint extents", () => {
  const c = rectCorners({ x: 0, z: 0, w: 2, d: 1, rotDeg: 90 });
  const xs = c.map((p) => p[0]);
  const zs = c.map((p) => p[1]);
  assert.ok(Math.abs(Math.max(...xs) - Math.min(...xs) - 1) < 1e-9);
  assert.ok(Math.abs(Math.max(...zs) - Math.min(...zs) - 2) < 1e-9);
});

test("rotating 180 deg leaves the footprint where it was", () => {
  const a = rectCorners({ x: 1, z: 2, w: 2, d: 1, rotDeg: 0 });
  const b = rectCorners({ x: 1, z: 2, w: 2, d: 1, rotDeg: 180 });
  // Normalise -0 and float dust before comparing, or sin(pi) bites.
  const n = (v: number) => (Math.abs(v) < 1e-9 ? 0 : v).toFixed(6);
  const key = (ps: Point2[]) =>
    ps.map(([x, z]) => `${n(x)},${n(z)}`).sort().join("|");
  assert.equal(key(a), key(b));
});

test("couch and tv stand leave a 2.875 m walkway between faces", () => {
  // Couch spans z 0.0-0.9; the stand, rotated 180, spans z 3.775-4.125.
  assert.ok(!rectsOverlap(couch, tvStand));
  assert.ok(!rectsOverlap(couch, tvStand, 2.8));
  assert.ok(rectsOverlap(couch, tvStand, 2.95));
});

test("rotated footprints separate correctly", () => {
  const a: Rect = { x: 0, z: 0, w: 2, d: 0.5, rotDeg: 0 };
  assert.ok(rectsOverlap(a, { x: 0, z: 0.6, w: 2, d: 0.5, rotDeg: 45 }));
  assert.ok(!rectsOverlap(a, { x: 0, z: 3, w: 2, d: 0.5, rotDeg: 45 }));
});

test("pointInRect respects rotation", () => {
  const r: Rect = { x: 0, z: 0, w: 2, d: 0.4, rotDeg: 90 };
  assert.ok(pointInRect([0, 0.9], r));
  assert.ok(!pointInRect([0.9, 0], r));
});

console.log("openings are segments, not points");

test("distance to a door is measured along the wall segment", () => {
  const door = livingRoom.openings[0];
  // Beside the middle of the doorway, 0.5 m into the room.
  assert.ok(Math.abs(distToOpening([0.5, 1.45], door) - 0.5) < 1e-9);
  // Level with the door but far along the wall.
  assert.ok(distToOpening([0.5, 3.9], door) > 2.0);
});

test("opening midpoint", () => {
  assert.deepEqual(openingMidpoint(livingRoom.openings[0]), [0, 1.45]);
});

console.log("free floor, spawns, push-out");

test("furniture reduces free floor area", () => {
  const empty = freeFloorAreaM2(livingRoom, []);
  const furnished = freeFloorAreaM2(livingRoom, [couch, tvStand]);
  assert.ok(furnished < empty, `${furnished} should be < ${empty}`);
  assert.ok(empty > 10 && empty < livingRoom.area_m2);
});

test("two spawns are separated and on free floor", () => {
  const pts = spawnPoints(livingRoom, [couch, tvStand], 2);
  assert.equal(pts.length, 2);
  assert.ok(Math.hypot(pts[0][0] - pts[1][0], pts[0][1] - pts[1][1]) >= 1.5);
  for (const p of pts) {
    assert.ok(pointInPolygon(p, livingRoom.polygon));
    assert.ok(!pointInRect(p, couch, 0.35));
    assert.ok(!pointInRect(p, tvStand, 0.35));
  }
});

test("spawns are deterministic, so clients agree without the server", () => {
  assert.deepEqual(
    spawnPoints(livingRoom, [couch, tvStand], 4),
    spawnPoints(livingRoom, [couch, tvStand], 4),
  );
});

test("nobody spawns in either doorway", () => {
  const doors = livingRoom.openings.filter((o) => o.type === "door");
  for (const p of spawnPoints(livingRoom, [], 6)) {
    for (const d of doors) assert.ok(distToOpening(p, d) >= 0.9);
  }
});

test("a packed room yields no spawns rather than bad ones", () => {
  const wall: Rect = { x: 3, z: 2.1, w: 8, d: 6, rotDeg: 0 };
  assert.deepEqual(spawnPoints(livingRoom, [wall], 2), []);
});

test("a clear player is not moved", () => {
  const p: Point2 = [1.5, 2.0];
  assert.deepEqual(pushOut(p, livingRoom, [couch, tvStand]), p);
});

test("a player buried by a re-furnish comes out nearby and legal", () => {
  const inside: Point2 = [3.0, 0.45];
  const out = pushOut(inside, livingRoom, [couch, tvStand]);
  assert.ok(out !== null);
  assert.ok(!pointInRect(out!, couch, 0.35));
  assert.ok(pointInPolygon(out!, livingRoom.polygon));
  assert.ok(Math.hypot(out![0] - inside[0], out![1] - inside[1]) < 2.0);
});

test("push-out is deterministic", () => {
  const inside: Point2 = [3.0, 0.45];
  assert.deepEqual(
    pushOut(inside, livingRoom, [couch, tvStand]),
    pushOut(inside, livingRoom, [couch, tvStand]),
  );
});

test("push-out returns null when there is nowhere to stand", () => {
  const wall: Rect = { x: 3, z: 2.1, w: 8, d: 6, rotDeg: 0 };
  assert.equal(pushOut([3, 2.1], livingRoom, [wall]), null);
});

test("roomAt finds the room the player is standing in", () => {
  assert.equal(roomAt([3, 2], [livingRoom])?.id, "hou_000123.r1");
  assert.equal(roomAt([99, 99], [livingRoom]), null);
});

console.log(`\n${passed} passed`);
