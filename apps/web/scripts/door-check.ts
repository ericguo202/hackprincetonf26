// Lane C dev tool: can the player capsule walk through every door of a house?
//
//   node --experimental-strip-types apps/web/scripts/door-check.ts <house dir>
//   e.g. apps/web/public/packs/packs/hou_navidad_01/v1/houses/hou_236595179
//
// For each door in rooms.json: measures the hole in collision.glb (short rays
// through the thin wall plane, 1 cm steps, 1 m up), then drives a dynamic
// capsule the size of MOVEMENT (movement.ts) from 0.9 m inside the larger room
// to 0.45 m past the door line. Then a breadth-first search from the living
// room over the doors that passed. Exits non-zero if a bathroom is unreachable
// (Lane A, HANDOFF 2026-11-14 16:35: bathrooms must be reachable).
//
// Gotcha baked in: a Rapier world must step once before ray queries see anything.

import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { GLTFLoader, type GLTF } from "three/examples/jsm/loaders/GLTFLoader.js";
import type { Point2, Room } from "../src/contracts.gen.ts";
import { extractTrimesh } from "../src/world/collision.ts";
import { pointInPolygon } from "../src/world/geometry.ts";
import { MOVEMENT } from "../src/player/movement.ts";

const HOUSE = process.argv[2];
if (!HOUSE) {
  console.error("usage: node --experimental-strip-types apps/web/scripts/door-check.ts <house dir with rooms.json and collision.glb>");
  process.exit(2);
}
// Rapier is a dependency of @react-three/rapier, not of the app: resolve it through that package.
const req = createRequire(createRequire(import.meta.url).resolve("@react-three/rapier"));
const RAPIER = (await import(pathToFileURL(req.resolve("@dimforge/rapier3d-compat")).href)).default;

const rooms = JSON.parse(readFileSync(join(HOUSE, "rooms.json"), "utf8")) as Room[];
const byId: Record<string, Room> = Object.fromEntries(rooms.map((r) => [r.id, r]));
const buf = readFileSync(join(HOUSE, "collision.glb"));
const gltf = await new Promise<GLTF>((ok, no) => new GLTFLoader().parse(buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength), "", ok, no));
const tm = extractTrimesh(gltf.scene);
await RAPIER.init({});
const R = MOVEMENT.capsuleRadius, HH = MOVEMENT.capsuleHalfHeight;

// Unique doors.
type DoorLine = { from: string; to: string | null; a: Point2; b: Point2; mid: Point2; w: number };
const doors: DoorLine[] = [];
for (const r of rooms) for (const o of r.openings) {
  if (o.type !== "door") continue;
  const mid = [(o.a[0] + o.b[0]) / 2, (o.a[1] + o.b[1]) / 2];
  if (doors.some((d) => Math.hypot(d.mid[0] - mid[0], d.mid[1] - mid[1]) < 0.3)) continue;
  doors.push({ from: r.id, to: o.to_room, a: o.a, b: o.b, mid, w: Math.hypot(o.a[0] - o.b[0], o.a[1] - o.b[1]) });
}

function world() {
  const w = new RAPIER.World({ x: 0, y: -9.81, z: 0 });
  w.createCollider(RAPIER.ColliderDesc.trimesh(tm.vertices, tm.indices), w.createRigidBody(RAPIER.RigidBodyDesc.fixed()));
  // Yard slab like HouseScene's HouseGround, so outside doors have ground beyond them.
  w.createCollider(RAPIER.ColliderDesc.cuboid(20, 0.05, 20).setTranslation(0.5, -0.07, 0.2), w.createRigidBody(RAPIER.RigidBodyDesc.fixed()));
  return w;
}
const label = (id: string | null) => (id ? id.split(".")[1] + " " + byId[id].label : "outside");
const graph: Record<string, string[]> = {};
const link = (x: string, y: string) => { (graph[x] ??= []).push(y); (graph[y] ??= []).push(x); };

console.log("door".padEnd(44), "traced", "gap@1m", "walled", "drive");
for (const d of doors) {
  const t = [(d.b[0] - d.a[0]) / d.w, (d.b[1] - d.a[1]) / d.w];
  let n = [-t[1], t[0]];
  if (!pointInPolygon([d.mid[0] + n[0] * 0.4, d.mid[1] + n[1] * 0.4], byId[d.from].polygon)) n = [-n[0], -n[1]];
  const w = world();
  w.step(); // queries see nothing until the world has stepped once
  // Clear gap: walk along the door line in 1 cm steps, casting a short ray through the wall
  // plane at each step (1 m up); the hole is where those rays pass. Walls are thin surfaces.
  const blocked = (u: number) => !!w.castRay(new RAPIER.Ray(
    { x: d.mid[0] + t[0] * u + n[0] * 0.3, y: 1.0, z: d.mid[1] + t[1] * u + n[1] * 0.3 },
    { x: -n[0], y: 0, z: -n[1] }), 0.6, true);
  let left = 0, right = 0;
  while (right < 1.5 && !blocked(right + 0.01)) right += 0.01;
  while (left < 1.5 && !blocked(-(left + 0.01))) left += 0.01;
  const gap = left + right;
  // Walled: a ray straight through the doorway from 0.6 m each side.
  const thru = w.castRay(new RAPIER.Ray({ x: d.mid[0] + n[0] * 0.6, y: 1.0, z: d.mid[1] + n[1] * 0.6 }, { x: -n[0], y: 0, z: -n[1] }), 1.2, true);
  // Drive from the larger room's side, 0.9 m back, to 0.45 m into the smaller one (closets are ~0.73 m deep).
  const area = (id: string | null) => (id ? byId[id].area_m2 : 1e9);
  const fromBig = area(d.from) >= area(d.to);
  const side = fromBig ? 1 : -1; // +n points into d.from
  const start = [d.mid[0] + n[0] * 0.9 * side, d.mid[1] + n[1] * 0.9 * side], goal = [d.mid[0] - n[0] * 0.45 * side, d.mid[1] - n[1] * 0.45 * side];
  const body = w.createRigidBody(RAPIER.RigidBodyDesc.dynamic().setTranslation(start[0], 0.005 + HH + R + 0.02, start[1]).lockRotations());
  w.createCollider(RAPIER.ColliderDesc.capsule(HH, R).setFriction(0), body);
  for (let i = 0; i < 240; i++) {
    const p = body.translation(), v = body.linvel();
    const dx = goal[0] - p.x, dz = goal[1] - p.z, L = Math.hypot(dx, dz) || 1;
    body.setLinvel({ x: (dx / L) * 2.6, y: v.y, z: (dz / L) * 2.6 }, true);
    w.step();
  }
  const p = body.translation();
  const past = -side * ((p.x - d.mid[0]) * n[0] + (p.z - d.mid[1]) * n[1]); // metres beyond the door line
  const ok = past > 0.3;
  if (ok) link(d.from, d.to ?? "outside");
  console.log(`${(label(d.from) + " -> " + label(d.to)).padEnd(44)} ${d.w.toFixed(2)}   ${gap >= 2.99 ? " none" : gap.toFixed(2)}   ${thru ? "yes" : "no "}    ${ok ? "PASS" : "blocked"} (${past.toFixed(2)} m past)`);
}
const start = (rooms.find((r) => r.label === "living_room") ?? rooms[0]).id;
const seen = new Set([start]); const q = [start];
while (q.length) for (const x of graph[q.shift()!] ?? []) if (!seen.has(x)) { seen.add(x); q.push(x); }
console.log("\nreachable from the living room:", [...seen].map((x) => x === "outside" ? x : label(x)).join(", "));
const unreachable = rooms.filter((r) => !seen.has(r.id));
console.log("unreachable rooms:", unreachable.map((r) => label(r.id)).join(", ") || "none");
console.log(`capsule radius ${R} m (diameter ${(2 * R).toFixed(2)})`);
if (unreachable.some((r) => r.label === "bathroom")) process.exit(1);
