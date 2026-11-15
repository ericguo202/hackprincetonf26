// doorCutout.ts: the pure math on synthetic quads, then a real run on 7014's
// interior.glb (meshopt geometry decoded in Node; the webp textures are not
// decoded, so triangles are keyed by their glTF texture index instead).
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { beforeAll, describe, expect, it, vi } from "vitest";
import { Mesh, type Object3D } from "three";
import { GLTFLoader, type GLTF } from "three/examples/jsm/loaders/GLTFLoader.js";
import { MeshoptDecoder } from "three/examples/jsm/libs/meshopt_decoder.module.js";

import type { Listing, Room } from "../contracts.gen";
import {
  area2,
  barycentric,
  clipToTriangle,
  DOORWAY_H_M,
  doorUvPolygons,
  groupByTexture,
  planCutouts,
  type Tri,
  type V2,
  type V3,
} from "./doorCutout";
import { extractWallBoxes, splitClosedDoors, type OpenedDoor } from "./doorSplit";
import { parseListings, type Vec3 } from "./pack";

/**
 * A wall quad in the plane z = zWall, x 0..4, y 0..3, mapped to UV
 * u = x / 4 * su + ou, v = 1 - y / 3 (glTF: v down the image), split along
 * one diagonal or the other.
 */
function quad(zWall: number, diag: "ac" | "bd", tex = "T", su = 1, ou = 0): Tri<string>[] {
  const P = (x: number, y: number): V3 => [x, y, zWall];
  const U = (x: number, y: number): V2 => [(x / 4) * su + ou, 1 - y / 3];
  const a = { p: P(0, 0), uv: U(0, 0) };
  const b = { p: P(4, 0), uv: U(4, 0) };
  const c = { p: P(4, 3), uv: U(4, 3) };
  const d = { p: P(0, 3), uv: U(0, 3) };
  const tri = (x: typeof a, y: typeof a, z: typeof a): Tri<string> => ({ p: [x.p, y.p, z.p], uv: [x.uv, y.uv, z.uv], tex });
  return diag === "ac" ? [tri(a, b, c), tri(a, c, d)] : [tri(a, b, d), tri(b, c, d)];
}

const door = (id: string, a: [number, number], b: [number, number], floorY = 0): OpenedDoor => ({
  id,
  a,
  b,
  floorY,
  width: Math.hypot(b[0] - a[0], b[1] - a[1]),
});
const big = (): V2 => [1e9, 1e9]; // huge texture: the 2 px inset is ~0
const bounds = (polys: V2[][]) => {
  const all = polys.flat();
  return {
    u0: Math.min(...all.map((p) => p[0])),
    u1: Math.max(...all.map((p) => p[0])),
    v0: Math.min(...all.map((p) => p[1])),
    v1: Math.max(...all.map((p) => p[1])),
    area: polys.reduce((s, p) => s + Math.abs(area2(p)), 0),
  };
};

describe("clipToTriangle and barycentric", () => {
  it("clips a rectangle to a triangle, either winding", () => {
    const rect: V2[] = [
      [0, 0],
      [2, 0],
      [2, 2],
      [0, 2],
    ];
    const ccw: [V2, V2, V2] = [
      [0, 0],
      [2, 0],
      [0, 2],
    ];
    const cw: [V2, V2, V2] = [ccw[0], ccw[2], ccw[1]];
    expect(Math.abs(area2(clipToTriangle(rect, ccw)))).toBeCloseTo(2, 9);
    expect(Math.abs(area2(clipToTriangle(rect, cw)))).toBeCloseTo(2, 9);
    // Partly outside: a 1x1 square at (1.5, 1.5) only overlaps the triangle's hypotenuse corner... not at all.
    expect(clipToTriangle([[1.5, 1.5], [2.5, 1.5], [2.5, 2.5], [1.5, 2.5]], ccw)).toHaveLength(0);
    // A square straddling the hypotenuse keeps the half inside.
    const half = clipToTriangle([[0.5, 0.5], [1.5, 0.5], [1.5, 1.5], [0.5, 1.5]], ccw);
    expect(Math.abs(area2(half))).toBeCloseTo(0.5, 9);
  });

  it("barycentric reproduces the vertices and the centroid", () => {
    const t: [V2, V2, V2] = [
      [0, 0],
      [3, 0],
      [0, 2],
    ];
    expect(barycentric([0, 0], t)).toEqual([1, 0, 0]);
    const c = barycentric([1, 2 / 3], t)!;
    c.forEach((x) => expect(x).toBeCloseTo(1 / 3, 9));
    expect(barycentric([0, 0], [[0, 0], [1, 1], [2, 2]])).toBeNull();
  });
});

describe("doorUvPolygons on a synthetic textured wall", () => {
  // Door from x 1 to x 2 on the wall z = 0: UV u 0.25..0.5, v from 1 (floor) to 1 - 2.03/3.
  const d = door("d1", [1, 0], [2, 0]);

  it.each(["ac", "bd"] as const)("maps the doorway rectangle into UV (diagonal %s)", (diag) => {
    const r = doorUvPolygons(d, quad(0, diag), big);
    if (!("cuts" in r)) throw new Error(r.skip);
    const b = bounds(r.cuts.map((c) => c.uv));
    expect(b.u0).toBeCloseTo(0.25, 6);
    expect(b.u1).toBeCloseTo(0.5, 6);
    expect(b.v1).toBeCloseTo(1, 6);
    expect(b.v0).toBeCloseTo(1 - DOORWAY_H_M / 3, 6);
    // The pieces tile the doorway exactly: area 0.25 * (2.03 / 3) in UV.
    expect(b.area).toBeCloseTo(0.25 * (DOORWAY_H_M / 3), 6);
  });

  it("insets the cut 2 px at the sides and top, not at the floor", () => {
    // 400 x 300 px texture over 4 m x 3 m: 100 px per metre, so 2 px = 0.02 m.
    const r = doorUvPolygons(d, quad(0, "ac"), () => [400, 300]);
    if (!("cuts" in r)) throw new Error(r.skip);
    const b = bounds(r.cuts.map((c) => c.uv));
    expect(b.u0).toBeCloseTo(0.25 + 2 / 400, 6);
    expect(b.u1).toBeCloseTo(0.5 - 2 / 400, 6);
    expect(b.v0).toBeCloseTo(1 - (DOORWAY_H_M - 0.02) / 3, 6);
    expect(b.v1).toBeCloseTo(1, 6);
  });

  it("cuts both faces of the wall, each in its own texture", () => {
    const tris = [...quad(-0.06, "ac", "roomA"), ...quad(0.06, "bd", "roomB")];
    const r = doorUvPolygons(d, tris, big);
    if (!("cuts" in r)) throw new Error(r.skip);
    expect(new Set(r.cuts.map((c) => c.tex))).toEqual(new Set(["roomA", "roomB"]));
    expect(new Set(r.cuts.map((c) => c.side))).toEqual(new Set([-1, 1]));
  });

  it("ignores walls too far from the door line, floors, and walls across it", () => {
    const far = quad(0.3, "ac");
    const floor: Tri<string> = { p: [[0, 0, -1], [4, 0, -1], [4, 0, 1]], uv: [[0, 0], [1, 0], [1, 1]], tex: "F" };
    const across: Tri<string> = { p: [[1.5, 0, -1], [1.5, 0, 1], [1.5, 3, 1]], uv: [[0, 0], [1, 0], [1, 1]], tex: "X" };
    const r = doorUvPolygons(d, [...far, floor, across], big);
    expect(r).toEqual({ skip: "no wall triangle in the doorway plane" });
  });

  it("skips a door whose UV would fall outside [0, 1]", () => {
    const r = doorUvPolygons(d, quad(0, "ac", "T", 1, 0.9), big);
    expect(r).toEqual({ skip: "UV outside [0, 1]" });
  });
});

describe("planCutouts", () => {
  it("dedupes a texture shared by two doors, and skips a door with no match once", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const tris = [...quad(0, "ac", "shared"), ...quad(5, "bd", "other")];
    const doors = [door("d1", [0.5, 0], [1.3, 0]), door("d2", [2.2, 0], [3.0, 0]), door("nowhere", [0, 20], [1, 20])];
    const plan = planCutouts("hou_test", doors, tris, big);
    expect(plan.cut).toEqual(["d1", "d2"]);
    expect(plan.skipped).toEqual([{ id: "nowhere", why: "no wall triangle in the doorway plane" }]);
    expect([...plan.byTexture.keys()]).toEqual(["shared"]);
    expect(plan.byTexture.get("shared")!.length).toBeGreaterThanOrEqual(2);
    planCutouts("hou_test", doors, tris, big);
    expect(warn.mock.calls.filter((c) => String(c[0]).includes("nowhere"))).toHaveLength(1);
    warn.mockRestore();
  });

  it("groupByTexture keeps every polygon", () => {
    const g = groupByTexture([
      { doorId: "a", tex: 1, uv: [[0, 0]], side: 1 },
      { doorId: "b", tex: 1, uv: [[1, 1]], side: 1 },
      { doorId: "b", tex: 2, uv: [[2, 2]], side: -1 },
    ]);
    expect(g.get(1)).toHaveLength(2);
    expect(g.get(2)).toHaveLength(1);
  });
});

// ---------------------------------------------------------------------------
// Real data: 7014's interior.glb and its split doors.
// ---------------------------------------------------------------------------

const PACKS = join(dirname(fileURLToPath(import.meta.url)), "../../public/packs");
const V3DIR = join(PACKS, "packs/hou_navidad_01/v3");
const H7014 = "hou_236595179";

/** Pixel size of a webp image (VP8, VP8L or VP8X). */
function webpSize(b: Uint8Array): V2 {
  const tag = String.fromCharCode(b[12], b[13], b[14], b[15]);
  if (tag === "VP8X") return [1 + (b[24] | (b[25] << 8) | (b[26] << 16)), 1 + (b[27] | (b[28] << 8) | (b[29] << 16))];
  if (tag === "VP8L") {
    const bits = b[21] | (b[22] << 8) | (b[23] << 16) | (b[24] << 24);
    return [1 + (bits & 0x3fff), 1 + ((bits >> 14) & 0x3fff)];
  }
  return [(b[26] | (b[27] << 8)) & 0x3fff, (b[28] | (b[29] << 8)) & 0x3fff];
}

describe("7014 interior.glb (real v3 data)", () => {
  const tris: Tri<number>[] = [];
  let doors: OpenedDoor[] = [];
  let sizes: V2[] = [];

  beforeAll(async () => {
    const raw = readFileSync(join(V3DIR, `houses/${H7014}/interior.glb`));
    const jsonLen = raw.readUInt32LE(12);
    const json = JSON.parse(raw.subarray(20, 20 + jsonLen).toString("utf8"));
    const binStart = 20 + jsonLen + 8;
    // Texture sizes from the webp headers, then strip textures (Node can't decode webp).
    sizes = json.images.map((img: { bufferView: number }) => {
      const bv = json.bufferViews[img.bufferView];
      return webpSize(raw.subarray(binStart + (bv.byteOffset ?? 0), binStart + (bv.byteOffset ?? 0) + bv.byteLength));
    });
    const texOfMaterial: number[] = json.materials.map(
      (m: { pbrMetallicRoughness?: { baseColorTexture?: { index: number } } }) => m.pbrMetallicRoughness?.baseColorTexture?.index ?? -1,
    );
    const textures = json.textures as { source?: number; extensions?: { EXT_texture_webp?: { source: number } } }[];
    const texSource = (t: number) => textures[t].extensions?.EXT_texture_webp?.source ?? textures[t].source!;
    for (const m of json.materials) delete m.pbrMetallicRoughness?.baseColorTexture;
    delete json.textures;
    delete json.images;
    delete json.samplers;
    json.extensionsUsed = json.extensionsUsed.filter((e: string) => e !== "EXT_texture_webp");
    json.extensionsRequired = json.extensionsRequired.filter((e: string) => e !== "EXT_texture_webp");
    let text = JSON.stringify(json);
    while (text.length % 4) text += " ";
    const jsonBytes = Buffer.from(text, "utf8");
    const bin = raw.subarray(20 + jsonLen);
    const out = Buffer.alloc(12 + 8 + jsonBytes.length + bin.length);
    out.write("glTF", 0);
    out.writeUInt32LE(2, 4);
    out.writeUInt32LE(out.length, 8);
    out.writeUInt32LE(jsonBytes.length, 12);
    out.write("JSON", 16);
    jsonBytes.copy(out, 20);
    bin.copy(out, 20 + jsonBytes.length);
    // Copy into this realm's ArrayBuffer (GLTFLoader checks instanceof ArrayBuffer).
    const ab = new ArrayBuffer(out.length);
    new Uint8Array(ab).set(out);

    await MeshoptDecoder.ready;
    const loader = new GLTFLoader().setMeshoptDecoder(MeshoptDecoder);
    const gltf = await new Promise<GLTF>((res, rej) => loader.parse(ab, "", res, rej));
    const root: Object3D = gltf.scene;
    root.updateMatrixWorld(true);
    root.traverse((o) => {
      if (!(o instanceof Mesh) || !/^walls_/.test(o.name)) return;
      const assoc = gltf.parser.associations.get(o.material);
      const tex = texSource(texOfMaterial[assoc!.materials!]);
      const pos = o.geometry.getAttribute("position");
      const uv = o.geometry.getAttribute("uv");
      const idx = o.geometry.getIndex()!;
      const p = (k: number): V3 => {
        const i = idx.getX(k);
        const w = { x: pos.getX(i), y: pos.getY(i), z: pos.getZ(i) };
        const e = o.matrixWorld.elements;
        return [
          e[0] * w.x + e[4] * w.y + e[8] * w.z + e[12],
          e[1] * w.x + e[5] * w.y + e[9] * w.z + e[13],
          e[2] * w.x + e[6] * w.y + e[10] * w.z + e[14],
        ];
      };
      const u = (k: number): V2 => [uv.getX(idx.getX(k)), uv.getY(idx.getX(k))];
      for (let k = 0; k + 2 < idx.count; k += 3) tris.push({ p: [p(k), p(k + 1), p(k + 2)], uv: [u(k), u(k + 1), u(k + 2)], tex });
    });

    // The opened doors, exactly as HouseScene gets them.
    const listings = parseListings(JSON.parse(readFileSync(join(PACKS, "listings/index.json"), "utf8")));
    if (!listings.ok) throw new Error("listings");
    const listing = listings.value.find((l: Listing) => l.id === H7014)!;
    const r = JSON.parse(readFileSync(join(V3DIR, `houses/${H7014}/rooms.json`), "utf8"));
    const rooms: Room[] = Array.isArray(r) ? r : r.rooms;
    const cbuf = readFileSync(join(V3DIR, `houses/${H7014}/collision.glb`));
    const cab = new ArrayBuffer(cbuf.length);
    new Uint8Array(cab).set(cbuf);
    const col = await new Promise<GLTF>((res, rej) => new GLTFLoader().parse(cab, "", res, rej));
    doors = splitClosedDoors(extractWallBoxes(col.scene), rooms, listing.world!.door as Vec3).doors;
  }, 60_000);

  it("decodes the walls and finds the opened doors", () => {
    expect(tris.length).toBeGreaterThan(100);
    expect(doors.length).toBeGreaterThan(0);
  });

  it("cuts every opened door (both faces between two rooms, the inside face to outside), inside [0, 1] UV", () => {
    const plan = planCutouts(H7014, doors, tris, (t) => sizes[t]);
    const report = doors.map((d) => {
      const r = doorUvPolygons(d, tris, (t) => sizes[t]);
      return "cuts" in r
        ? `${d.id}: ${r.cuts.length} polys, faces ${[...new Set(r.cuts.map((c) => c.side))].join(",")}, textures ${[...new Set(r.cuts.map((c) => c.tex))].join(",")}`
        : `${d.id}: SKIP ${r.skip}`;
    });
    console.info(`[door cutout 7014]\n  ${report.join("\n  ")}`);
    expect(plan.skipped).toEqual([]);
    for (const d of doors) {
      const r = doorUvPolygons(d, tris, (t) => sizes[t]);
      if (!("cuts" in r)) throw new Error(`${d.id}: ${r.skip}`);
      // Between two rooms: both faces of the wall. To outside: interior.glb only has the inside face.
      const faces = new Set(r.cuts.map((c) => c.side)).size;
      expect(faces).toBe(d.id.endsWith("->outside") ? 1 : 2);
      for (const c of r.cuts) for (const [u, v] of c.uv) expect(u >= 0 && u <= 1 && v >= 0 && v <= 1).toBe(true);
    }
  });
});
