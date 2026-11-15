import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { beforeAll, describe, expect, it } from "vitest";
import { Box3, LoopRepeat, Mesh, MeshStandardMaterial, Vector3, type AnimationClip, type Object3D } from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";

import { CHARACTER, FOOT_BELOW_CENTRE_M } from "./characterAsset";
import {
  actionFor,
  blendedLift,
  buildRig,
  crossfadeTo,
  NOODLE_DROOP,
  NOODLE_MAX_BEND,
  NOODLE_SEGMENTS,
  setWeapon,
  updateNoodleJiggle,
  usedClips,
  type Rig,
} from "./characterRig";
import { CLIPS, clipFor } from "./clips";
import { heldWeaponProp } from "./foamMode";
import { CROUCH } from "./movement";
import { LoopOnce } from "three";
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

/** World bounds of the VISIBLE meshes only (Box3.setFromObject also counts hidden ones, like the noodle). */
const bounds = (o: Object3D) => {
  o.updateMatrixWorld(true);
  const box = new Box3();
  const shown = (x: Object3D | null): boolean => !x || (x.visible && shown(x.parent));
  o.traverse((m) => {
    if (m instanceof Mesh && shown(m)) box.union(new Box3().setFromObject(m, true));
  });
  return box;
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

describe("crouch pose (the kit's Duck, trimmed to its hold)", () => {
  it("drops to and holds CROUCH.topM (0.97 m), feet on the floor", () => {
    const rig = buildRig(scene, clips);
    const duck = actionFor(rig, "Duck")!;
    expect(duck.getClip().duration).toBeCloseTo(0.5, 6);
    duck.reset().setLoop(LoopOnce, 1);
    duck.clampWhenFinished = true;
    duck.play();
    rig.mixer.update(0.6); // past the end: clamped on the last (held) frame
    rig.mixer.update(1.0);
    const b = bounds(rig.root);
    expect(b.max.y).toBeCloseTo(CROUCH.topM, 1);
    expect(Math.abs(b.max.y - CROUCH.topM)).toBeLessThan(0.03);
    expect(Math.abs(b.min.y)).toBeLessThan(0.01);
  });

  it("the crouched capsule's top is the crouched model's top", () => {
    expect(MOVEMENT.floatHeight + 2 * (CROUCH.halfHeight + MOVEMENT.capsuleRadius)).toBeCloseTo(CROUCH.topM, 9);
    expect(CROUCH.halfHeight).toBeGreaterThan(0);
  });
});

describe("pool noodle prop", () => {
  it("is on the blaster's hand bone, about 1.2 m long, hidden until selected", () => {
    const rig = buildRig(scene, clips);
    const noodle = rig.props.noodle!;
    expect(noodle).toBeTruthy();
    expect(noodle.parent).toBe(rig.root.getObjectByName(CHARACTER.keepWeapon)!.parent);
    expect(noodle.visible).toBe(false);
    expect(rig.props.blaster!.visible).toBe(true);
    // Length in the world: the cylinder's two end points, wherever the hand points it.
    rig.root.updateMatrixWorld(true);
    const tube = noodle.children[0] as Mesh;
    tube.geometry.computeBoundingBox();
    const half = tube.geometry.boundingBox!.max.y;
    const a = tube.localToWorld(new Vector3(0, half, 0));
    const b = tube.localToWorld(new Vector3(0, -half, 0));
    expect(a.distanceTo(b)).toBeCloseTo(CHARACTER.noodle.lengthM, 2);
  });

  it("explore mode (weapon null) hides both props, and foam brings the selected one back", () => {
    const rig = buildRig(scene, clips);
    setWeapon(rig, heldWeaponProp(false, "noodle"));
    expect([rig.props.blaster!.visible, rig.props.noodle!.visible]).toEqual([false, false]);
    setWeapon(rig, heldWeaponProp(true, "noodle"));
    expect([rig.props.blaster!.visible, rig.props.noodle!.visible]).toEqual([false, true]);
    setWeapon(rig, heldWeaponProp(false, "blaster"));
    expect([rig.props.blaster!.visible, rig.props.noodle!.visible]).toEqual([false, false]);
    setWeapon(rig, heldWeaponProp(true, "blaster"));
    expect([rig.props.blaster!.visible, rig.props.noodle!.visible]).toEqual([true, false]);
  });

  it("setWeapon shows exactly one prop", () => {
    const rig = buildRig(scene, clips);
    setWeapon(rig, "noodle");
    expect([rig.props.blaster!.visible, rig.props.noodle!.visible]).toEqual([false, true]);
    setWeapon(rig, "blaster");
    expect([rig.props.blaster!.visible, rig.props.noodle!.visible]).toEqual([true, false]);
  });
});

describe("crossfadeTo: repeated states", () => {
  it("a repeated one-shot state replays from the start", () => {
    const rig = buildRig(scene, clips);
    const cur = crossfadeTo(rig, null, clipFor("hit"));
    rig.mixer.update(0.3); // most of the way through HitReact (0.43 s)
    expect(cur!.time).toBeGreaterThan(0.2);
    const again = crossfadeTo(rig, cur, clipFor("hit"));
    expect(again).toBe(cur); // same action...
    expect(again!.time).toBe(0); // ...restarted
    expect(again!.isRunning()).toBe(true);
  });

  it("a repeated looping state carries on without restarting", () => {
    const rig = buildRig(scene, clips);
    const cur = crossfadeTo(rig, null, clipFor("idle"));
    rig.mixer.update(0.5);
    const t = cur!.time;
    expect(crossfadeTo(rig, cur, clipFor("idle"))).toBe(cur);
    expect(cur!.time).toBeCloseTo(t, 9);
  });

  it("shoot is a loop (Idle_Shoot), so rapid fire keeps one continuous shoot clip", () => {
    expect(clipFor("shoot").once).toBe(false);
  });
});

describe("floppy pool noodle", () => {
  const DT = 1 / 60;
  /** A rig holding the noodle, initialised (first update only records the tip). */
  function noodleRig(): Rig {
    const rig = buildRig(scene, clips);
    setWeapon(rig, "noodle");
    rig.root.updateMatrixWorld(true);
    updateNoodleJiggle(rig, DT);
    return rig;
  }
  /** Move the body sideways along a sine for `seconds` (amplitude m, frequency Hz), running the jiggle. */
  function shake(rig: Rig, seconds: number, amp = 0.4, hz = 3, dt = DT, onStep?: () => void) {
    for (let t = 0; t < seconds; t += dt) {
      rig.root.position.x = amp * Math.sin(2 * Math.PI * hz * t);
      rig.root.position.z = amp * Math.cos(2 * Math.PI * hz * t * 0.7);
      rig.root.updateMatrixWorld(true);
      updateNoodleJiggle(rig, dt);
      onStep?.();
    }
  }
  /** Walk sideways at `speed` m/s for `seconds`, running the jiggle. */
  function walk(rig: Rig, seconds: number, speed: number, dt = DT) {
    for (let t = 0; t < seconds; t += dt) {
      rig.root.position.x += speed * dt;
      rig.root.updateMatrixWorld(true);
      updateNoodleJiggle(rig, dt);
    }
  }
  /** Start the swing clip (rising edge for the whip). */
  function swing(rig: Rig) {
    rig.mixer.stopAllAction();
    actionFor(rig, CLIPS.shoot)!.reset().play();
    rig.mixer.update(DT);
  }
  const j = (rig: Rig) => rig.noodleJiggle!;
  const offFromRest = (rig: Rig) => Math.hypot(j(rig).tipOffset.x - j(rig).rest.x, j(rig).tipOffset.z - j(rig).rest.z);

  it("has 16 segments along its length", () => {
    const rig = buildRig(scene, clips);
    const g = j(rig).mesh.geometry as unknown as { parameters: { heightSegments: number } };
    expect(g.parameters.heightSegments).toBe(NOODLE_SEGMENTS);
    expect(NOODLE_SEGMENTS).toBe(16);
  });

  it("wobbles visibly after a stop, then settles back to its droop rest within 1.5 s", () => {
    const rig = noodleRig();
    walk(rig, 0.6, 3);
    const h = j(rig).height;
    // Still wobbling a bit right after the stop...
    let maxEarly = 0;
    let t = 0;
    for (; t < 0.3; t += DT) {
      updateNoodleJiggle(rig, DT);
      maxEarly = Math.max(maxEarly, offFromRest(rig));
    }
    expect(maxEarly).toBeGreaterThan(0.05 * h);
    for (; t < 1.5; t += DT) updateNoodleJiggle(rig, DT);
    expect(offFromRest(rig)).toBeLessThan(0.05 * h);
    expect(Math.hypot(j(rig).tipVel.x, j(rig).tipVel.z)).toBeLessThan(0.2 * h);
  });

  it("droops toward world down when still, by at most the droop fraction of the length", () => {
    const rig = noodleRig();
    // Tip the character over 60 degrees so the noodle leans and droops sideways.
    rig.root.rotation.z = Math.PI / 3;
    rig.root.updateMatrixWorld(true);
    updateNoodleJiggle(rig, DT); // re-reads the tip; settle from there
    for (let t = 0; t < 3; t += DT) updateNoodleJiggle(rig, DT);
    const h = j(rig).height;
    const rest = Math.hypot(j(rig).rest.x, j(rig).rest.z);
    expect(rest).toBeGreaterThan(0.05 * h);
    expect(rest).toBeLessThanOrEqual(NOODLE_DROOP * h + 1e-9);
    expect(offFromRest(rig)).toBeLessThan(0.02 * h);
    // The tip moved toward world down.
    const m = j(rig).mesh;
    const base = m.localToWorld(new Vector3(0, h / 2, 0));
    const arr = m.geometry.attributes.position;
    let top = 0;
    for (let i = 1; i < arr.count; i++) if (arr.getY(i) > arr.getY(top)) top = i;
    const tip = m.localToWorld(new Vector3(arr.getX(top), arr.getY(top), arr.getZ(top)));
    expect(tip.y).toBeLessThan(base.y);
  });

  it("never bends past the cap, even when whipped around hard", () => {
    const rig = noodleRig();
    const cap = NOODLE_MAX_BEND * j(rig).height + 1e-6;
    let max = 0;
    const track = () => (max = Math.max(max, Math.hypot(j(rig).tipOffset.x, j(rig).tipOffset.z)));
    for (let k = 0; k < 6; k++) {
      swing(rig);
      shake(rig, 0.25, 1.5, 1, DT, track);
      rig.mixer.stopAllAction();
      updateNoodleJiggle(rig, DT);
      walk(rig, 0.2, k % 2 ? 4 : -4);
      track();
    }
    expect(max).toBeLessThanOrEqual(cap);
    expect(max).toBeGreaterThan(0.3 * j(rig).height); // it does flop, not just twitch
  });

  it("stays finite at dt 0 and dt 0.1", () => {
    for (const dt of [0, 0.1]) {
      const rig = noodleRig();
      shake(rig, 1, 0.8, 4, dt === 0 ? DT : dt, () => updateNoodleJiggle(rig, dt));
      const arr = j(rig).mesh.geometry.attributes.position.array as Float32Array;
      expect(arr.every(Number.isFinite)).toBe(true);
      expect([j(rig).tipOffset, j(rig).tipVel].every((v) => Number.isFinite(v.x + v.y + v.z))).toBe(true);
    }
  });

  it("whips the tip once when a swing starts", () => {
    const rig = noodleRig();
    for (let t = 0; t < 2; t += DT) updateNoodleJiggle(rig, DT);
    const before = Math.hypot(j(rig).tipVel.x, j(rig).tipVel.z);
    swing(rig);
    updateNoodleJiggle(rig, DT);
    expect(Math.hypot(j(rig).tipVel.x, j(rig).tipVel.z)).toBeGreaterThan(before + j(rig).height);
  });
});
