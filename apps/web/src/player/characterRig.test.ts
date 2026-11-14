import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { beforeAll, describe, expect, it } from "vitest";
import { Box3, LoopRepeat, Mesh, MeshStandardMaterial, type AnimationClip, type Object3D } from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";

import { CHARACTER, FOOT_BELOW_CENTRE_M } from "./characterAsset";
import { actionFor, blendedLift, buildRig, usedClips } from "./characterRig";
import { MOVEMENT } from "./movement";

const MODEL = join(dirname(fileURLToPath(import.meta.url)), "../../public/characters/Character_Soldier.gltf");
let scene: Object3D;
let clips: AnimationClip[];

beforeAll(async () => {
  const text = readFileSync(MODEL, "utf8");
  const gltf = await new GLTFLoader().parseAsync(text, "");
  scene = gltf.scene;
  clips = gltf.animations;
}, 30_000);

const bounds = (o: Object3D) => {
  o.updateMatrixWorld(true);
  return new Box3().setFromObject(o, true);
};

describe("character rig (real Toon Shooter soldier)", () => {
  it("is 1.3 m tall, the capsule's height, with feet at the origin", () => {
    expect(CHARACTER.heightM).toBeCloseTo(2 * (MOVEMENT.capsuleHalfHeight + MOVEMENT.capsuleRadius), 9);
    const rig = buildRig(scene, clips);
    const b = bounds(rig.root);
    expect(b.max.y - b.min.y).toBeCloseTo(1.3, 2);
    expect(Math.abs(b.min.y)).toBeLessThan(0.005); // neither sinking nor hovering at rest
  });

  it("carries one weapon, tinted foam, and leaves the source model untouched", () => {
    const rig = buildRig(scene, clips);
    const kept = CHARACTER.weapons.filter((w) => rig.root.getObjectByName(w));
    expect(kept).toEqual([CHARACTER.keepWeapon]);
    rig.root.getObjectByName(CHARACTER.keepWeapon)!.traverse((o) => {
      if (o instanceof Mesh && o.material instanceof MeshStandardMaterial) {
        expect(`#${o.material.color.getHexString()}`).toBe(CHARACTER.foamColor);
      }
    });
    // The source still has all 14: each instance prepares its own clone.
    expect(CHARACTER.weapons.every((w) => scene.getObjectByName(w))).toBe(true);
  });

  it("gives every instance its own skeleton", () => {
    const a = buildRig(scene, clips);
    const b = buildRig(scene, clips);
    expect(a.root).not.toBe(b.root);
    expect(a.root.getObjectByName("Hips")).not.toBe(b.root.getObjectByName("Hips"));
  });

  it("lifts the run so feet meet the floor instead of sinking, and leaves idle alone", () => {
    const rig = buildRig(scene, clips);
    expect(rig.lift.get("Idle")).toBeCloseTo(0, 2);
    const runLift = rig.lift.get("Run_Gun")!;
    expect(runLift).toBeGreaterThan(0.02);
    expect(runLift).toBeLessThan(0.1);

    // Play the run with its lift applied: the lowest point over the cycle sits on the floor.
    const run = actionFor(rig, "Run_Gun")!;
    run.reset().setLoop(LoopRepeat, Infinity).play();
    let lowest = Infinity;
    for (let i = 0; i <= 24; i++) {
      rig.mixer.setTime((run.getClip().duration * i) / 24);
      const b = bounds(rig.root);
      lowest = Math.min(lowest, b.min.y + blendedLift(rig));
    }
    expect(Math.abs(lowest)).toBeLessThan(0.01);
  });

  it("survives unmount + remount with the same rig (React StrictMode), the browser crash", () => {
    const rig = buildRig(scene, clips);
    // Mount: play, as Character's crossfade effect does.
    actionFor(rig, "Idle")!.reset().play();
    rig.mixer.update(0.016);
    // StrictMode's dev cleanup (and any real unmount): Character's cleanup effect.
    rig.mixer.stopAllAction();
    rig.mixer.uncacheRoot(rig.root);
    // Remount with the same memoized rig: playing again must not throw.
    expect(() => {
      actionFor(rig, "Idle")!.reset().play();
      const run = actionFor(rig, "Run_Gun")!;
      run.reset().play();
      run.crossFadeFrom(actionFor(rig, "Idle")!, 0.2, false);
      rig.mixer.update(0.1);
    }).not.toThrow();
    expect(blendedLift(rig)).toBeGreaterThan(0);
  });

  it("measures every clip it plays", () => {
    const rig = buildRig(scene, clips);
    for (const c of usedClips()) expect(rig.lift.has(c), c).toBe(true);
  });

  it("puts the feet where ecctrl's floating capsule leaves the floor", () => {
    expect(FOOT_BELOW_CENTRE_M).toBeCloseTo(
      MOVEMENT.capsuleHalfHeight + MOVEMENT.capsuleRadius + MOVEMENT.floatHeight,
      9,
    );
  });
});
