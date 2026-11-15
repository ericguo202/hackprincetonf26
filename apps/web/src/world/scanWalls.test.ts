import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { Group, Mesh, MeshBasicMaterial, PlaneGeometry, type Object3D } from "three";

import { collectScanSets, nearestScanSet, prepareScanWalls, scanSetOf, showScanSet } from "./scanWalls";
import type { Vec3 } from "./pack";

const wall = (name: string, sweep?: Vec3) => {
  const m = new Mesh(new PlaneGeometry(), new MeshBasicMaterial());
  m.name = name;
  if (sweep) m.userData.sweep = sweep;
  return m;
};
const scene = (...kids: Object3D[]) => {
  const g = new Group();
  kids.forEach((k) => g.add(k));
  return g;
};

describe("nearestScanSet", () => {
  const sets = [
    { kk: 0, sweep: [0, 1.5, 0] as Vec3 },
    { kk: 1, sweep: [10, 1.5, 0] as Vec3 },
    { kk: 2, sweep: [0, 1.5, -10] as Vec3 },
  ];
  it("picks the set whose sweep is nearest the eye, by 3D distance", () => {
    expect(nearestScanSet(sets, [1, 1.6, 0])).toBe(0);
    expect(nearestScanSet(sets, [6, 1.6, 0])).toBe(1);
    expect(nearestScanSet(sets, [0, 1.6, -7])).toBe(2);
    // Height counts too (plain 3D distance).
    expect(nearestScanSet([{ kk: 3, sweep: [0, 0, 0] }, { kk: 4, sweep: [0.5, 5, 0] }], [0.4, 4.5, 0])).toBe(4);
  });
  it("null with no sets (houses without scan walls)", () => {
    expect(nearestScanSet([], [0, 0, 0])).toBeNull();
  });
});

describe("collect / prepare / show", () => {
  it("groups backdrop_s<kk>_<page> by kk, leaves backdrop_<n> alone, drops sets without a sweep", () => {
    expect(scanSetOf("backdrop_s07_0")).toBe(7);
    expect(scanSetOf("backdrop_0")).toBeNull();
    expect(scanSetOf("backdrop_far_0")).toBeNull();
    const root = scene(
      wall("backdrop_0"),
      wall("backdrop_s00_0", [1, 1, 1]),
      wall("backdrop_s00_1"),
      wall("backdrop_s01_0", [5, 1, 1]),
      wall("backdrop_s02_0"), // no sweep
    );
    const sets = collectScanSets(root);
    expect(sets.map((s) => [s.kk, s.nodes.map((n) => n.name)])).toEqual([
      [0, ["backdrop_s00_0", "backdrop_s00_1"]],
      [1, ["backdrop_s01_0"]],
    ]);
  });

  it("prepare: unlit cut-out, no tone mapping, no shadows, no raycast, all hidden; show one set only", () => {
    const bd = wall("backdrop_0");
    const root = scene(bd, wall("backdrop_s00_0", [0, 1, 0]), wall("backdrop_s01_0", [9, 1, 0]));
    const sets = prepareScanWalls(root);
    for (const s of sets)
      for (const n of s.nodes) {
        const m = n as Mesh;
        const mat = m.material as MeshBasicMaterial;
        expect(n.visible).toBe(false);
        expect(mat.toneMapped).toBe(false);
        expect(mat.alphaTest).toBe(0.5);
        expect(mat.transparent).toBe(false);
        expect(m.castShadow || m.receiveShadow).toBe(false);
        const hits: unknown[] = [];
        m.raycast({} as never, hits as never);
        expect(hits).toHaveLength(0);
      }
    expect(bd.visible).toBe(true);
    showScanSet(sets, nearestScanSet(sets, [8, 1.6, 0]));
    expect(sets.map((s) => s.nodes[0].visible)).toEqual([false, true]);
    expect(bd.visible).toBe(true);
  });

  it("a house without backdrop_s nodes has no sets and nothing changes", () => {
    const bd = wall("backdrop_0");
    expect(prepareScanWalls(scene(bd, wall("walls_0")))).toEqual([]);
    expect(bd.visible).toBe(true);
  });
});

describe("real pack v7 7014 interior.glb", () => {
  const PACKS = join(dirname(fileURLToPath(import.meta.url)), "../../public/packs");
  const path = join(PACKS, "packs/hou_navidad_01/v7/houses/hou_236595179/interior.glb");
  let buf: Buffer | null = null;
  try {
    buf = readFileSync(path);
  } catch {
    buf = null; // v7 is local-only (not in git): skip where it is not unpacked
  }
  it.skipIf(!buf)("15 sets s00..s14 with sweeps; one visible after a pick", async () => {
    // Parse the JSON chunk only: the mesh data is meshopt-compressed.
    const n = buf!.readUInt32LE(12);
    const json = JSON.parse(buf!.subarray(20, 20 + n).toString("utf8")) as {
      nodes: { name?: string; extras?: { sweep?: Vec3 } }[];
    };
    const root = new Group();
    for (const nd of json.nodes) {
      const o = new Group();
      o.name = nd.name ?? "";
      if (nd.extras?.sweep) o.userData.sweep = nd.extras.sweep;
      root.add(o);
    }
    const sets = collectScanSets(root);
    expect(sets.map((s) => s.kk)).toEqual([...Array(15).keys()]);
    showScanSet(sets, nearestScanSet(sets, [-9, 1.6, 1]));
    expect(sets.filter((s) => s.nodes[0].visible).map((s) => s.kk)).toEqual([0]);
  });
});
