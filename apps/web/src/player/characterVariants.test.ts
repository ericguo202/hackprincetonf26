// Every player look (CHARACTER_VARIANTS) loaded from public/characters/ in Node,
// like characterRig.test.ts, and run through the real rig code.
import { readFileSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { beforeAll, describe, expect, it } from "vitest";
import { Box3, Mesh, MeshStandardMaterial, Quaternion, Vector3, type AnimationClip, type Object3D } from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";

import { CHARACTER, CHARACTER_VARIANTS, hashId, variantFor } from "./characterAsset";
import { buildRig, setWeapon, updateNoodleJiggle } from "./characterRig";
import { PACK_CLIPS } from "./clips";

const PUBLIC = join(dirname(fileURLToPath(import.meta.url)), "../../public");
const fileOf = (url: string) => join(PUBLIC, url);

type Loaded = { scene: Object3D; clips: AnimationClip[] };
const loaded = new Map<string, Loaded>();

beforeAll(async () => {
  for (const v of CHARACTER_VARIANTS) {
    const gltf = await new GLTFLoader().parseAsync(readFileSync(fileOf(v.url), "utf8"), "");
    loaded.set(v.id, { scene: gltf.scene, clips: gltf.animations });
  }
}, 60_000);

const bounds = (o: Object3D) => {
  o.updateMatrixWorld(true);
  const box = new Box3();
  const shown = (x: Object3D | null): boolean => !x || (x.visible && shown(x.parent));
  o.traverse((m) => {
    if (m instanceof Mesh && shown(m)) box.union(new Box3().setFromObject(m, true));
  });
  return box;
};

describe("variantFor", () => {
  it("Soldier first, and with no id", () => {
    expect(CHARACTER_VARIANTS[0].url).toBe(CHARACTER.url);
    expect(variantFor(null).id).toBe("soldier");
    expect(variantFor(undefined).id).toBe("soldier");
    expect(variantFor("").id).toBe("soldier");
  });

  it("is stable: the same id always gets the same look (known hashes pinned)", () => {
    expect(hashId("")).toBe(0x811c9dc5);
    expect(hashId("a")).toBe(0xe40c292c); // FNV-1a reference value
    for (const id of ["abc", "dev-remote-5", "XyZ123_9"]) expect(variantFor(id)).toBe(variantFor(id));
    expect(variantFor("dev-remote-5").id).toBe("hazmat");
    expect(variantFor("dev-remote-2").id).toBe("red");
  });

  it("spreads Colyseus-like session ids across every variant", () => {
    const counts = new Map<string, number>();
    const chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-";
    let seed = 7;
    const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
    const N = 3000;
    for (let i = 0; i < N; i++) {
      let id = "";
      for (let k = 0; k < 9; k++) id += chars[Math.floor(rnd() * chars.length)];
      const v = variantFor(id).id;
      counts.set(v, (counts.get(v) ?? 0) + 1);
    }
    expect(counts.size).toBe(CHARACTER_VARIANTS.length);
    for (const c of counts.values()) expect(c / N).toBeGreaterThan(1 / CHARACTER_VARIANTS.length - 0.05);
  });
});

describe.each(CHARACTER_VARIANTS.map((v) => [v.name, v] as const))("variant %s", (_, v) => {
  const src = () => loaded.get(v.id)!;

  it("is a self-contained .gltf under 3 MB", () => {
    expect(statSync(fileOf(v.url)).size).toBeLessThan(3 * 1024 * 1024);
    const json = JSON.parse(readFileSync(fileOf(v.url), "utf8")) as { buffers?: { uri?: string }[]; images?: { uri?: string }[] };
    for (const r of [...(json.buffers ?? []), ...(json.images ?? [])]) expect(r.uri?.startsWith("data:") ?? true).toBe(true);
  });

  it("has all 17 pack clips by name", () => {
    const names = src().clips.map((c) => c.name).sort();
    expect(names).toEqual([...PACK_CLIPS].sort());
  });

  it("has the RocketLauncher on the right hand bone, and all 14 weapon nodes there", () => {
    const rl = src().scene.getObjectByName(CHARACTER.keepWeapon)!;
    expect(rl).toBeTruthy();
    // The file's "Index1.R"; three.js drops the "." when it sanitizes node names.
    expect(rl.parent!.name).toBe("Index1R");
    for (const w of CHARACTER.weapons) expect(src().scene.getObjectByName(w)?.parent?.name).toBe("Index1R");
  });

  it("builds a rig 1.3 m tall with its feet at 0, from its own scale", () => {
    const rig = buildRig(src().scene, src().clips);
    const b = bounds(rig.root);
    expect(b.max.y - b.min.y).toBeCloseTo(CHARACTER.heightM, 2);
    expect(Math.abs(b.min.y)).toBeLessThan(0.005);
  });

  it("keeps only the foam-tinted blaster, and the noodle points world-up from the hand", () => {
    const rig = buildRig(src().scene, src().clips);
    expect(CHARACTER.weapons.filter((w) => rig.root.getObjectByName(w))).toEqual([CHARACTER.keepWeapon]);
    rig.root.getObjectByName(CHARACTER.keepWeapon)!.traverse((o) => {
      if (o instanceof Mesh && o.material instanceof MeshStandardMaterial) {
        expect(`#${o.material.color.getHexString()}`).toBe(CHARACTER.foamColor);
      }
    });
    setWeapon(rig, "noodle");
    rig.root.updateMatrixWorld(true);
    const tube = rig.props.noodle!.children[0] as Mesh;
    const axis = new Vector3(0, 1, 0).applyQuaternion(tube.getWorldQuaternion(new Quaternion()));
    expect(axis.y).toBeGreaterThan(0.999); // straight up at the rest pose
    // Length in metres, whatever this file's scale.
    tube.geometry.computeBoundingBox();
    const half = tube.geometry.boundingBox!.max.y;
    expect(tube.localToWorld(new Vector3(0, half, 0)).distanceTo(tube.localToWorld(new Vector3(0, -half, 0)))).toBeCloseTo(
      CHARACTER.noodle.lengthM,
      2,
    );
    // The jiggle runs and stays finite on this rig.
    updateNoodleJiggle(rig, 1 / 60);
    for (let i = 0; i < 30; i++) {
      rig.root.position.x += 0.05;
      rig.root.updateMatrixWorld(true);
      updateNoodleJiggle(rig, 1 / 60);
    }
    expect((tube.geometry.attributes.position.array as Float32Array).every(Number.isFinite)).toBe(true);
  });

  it("the Soldier's computed noodle up matches the value measured before (no regression)", () => {
    if (v.id !== "soldier") return;
    const rig = buildRig(src().scene, src().clips);
    const tube = rig.props.noodle!.children[0] as Mesh;
    const up = new Vector3(0, 1, 0).applyQuaternion(tube.quaternion);
    expect(up.x).toBeCloseTo(-0.4735 / 0.9993, 2);
    expect(up.y).toBeCloseTo(0.636 / 0.9993, 2);
    expect(up.z).toBeCloseTo(0.6094 / 0.9993, 2);
  });
});
