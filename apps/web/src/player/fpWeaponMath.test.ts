import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { Mesh, Quaternion, Vector3 } from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";

import { CHARACTER, CHARACTER_VARIANTS } from "./characterAsset";
import { buildViewModel } from "./fpViewModel";
import {
  FP_KICK_BACK_M,
  FP_KICK_S,
  FP_OFFSET,
  FP_SWEEP_M,
  FP_SWEEP_S,
  fpPose,
  fpVisible,
  kickCurve,
  sweepCurve,
  type FpPose,
} from "./fpWeaponMath";

describe("fpVisible", () => {
  it("shows only in first person, holding a weapon, alive", () => {
    expect(fpVisible({ bodyHidden: true, weapon: "blaster", alive: true })).toBe(true);
    expect(fpVisible({ bodyHidden: true, weapon: "noodle", alive: true })).toBe(true);
    expect(fpVisible({ bodyHidden: false, weapon: "blaster", alive: true })).toBe(false); // third person: hand prop shows
    expect(fpVisible({ bodyHidden: true, weapon: null, alive: true })).toBe(false); // explore mode
    expect(fpVisible({ bodyHidden: true, weapon: "blaster", alive: false })).toBe(false); // eliminated
  });
});

describe("kick and sweep curves", () => {
  it("kick: full at the shot, falling to 0 by FP_KICK_S, 0 outside", () => {
    expect(kickCurve(0)).toBe(1);
    expect(kickCurve(FP_KICK_S / 2)).toBeCloseTo(0.25, 9);
    expect(kickCurve(FP_KICK_S)).toBe(0);
    expect(kickCurve(-0.01)).toBe(0);
    expect(kickCurve(Number.NaN)).toBe(0);
    let prev = 2;
    for (let t = 0; t < FP_KICK_S; t += FP_KICK_S / 20) {
      expect(kickCurve(t)).toBeLessThan(prev);
      prev = kickCurve(t);
    }
  });

  it("sweep: 0 -> 1 -> 0 over FP_SWEEP_S, 0 outside", () => {
    expect(sweepCurve(0)).toBe(0);
    expect(sweepCurve(FP_SWEEP_S / 2)).toBeCloseTo(1, 9);
    expect(sweepCurve(FP_SWEEP_S)).toBe(0);
    expect(sweepCurve(-1)).toBe(0);
    expect(sweepCurve(FP_SWEEP_S * 0.25)).toBeCloseTo(sweepCurve(FP_SWEEP_S * 0.75), 9);
  });

  it("fpPose: lower right and forward at rest; kick only for the blaster, sweep only for the noodle", () => {
    const out: FpPose = { x: 0, y: 0, z: 0, pitch: 0, yaw: 0 };
    fpPose("blaster", 0, null, null, out);
    expect(out.x).toBeCloseTo(FP_OFFSET.right, 2);
    expect(out.y).toBeCloseTo(-FP_OFFSET.down, 2);
    expect(out.z).toBeCloseTo(-FP_OFFSET.forward, 9);
    const rest = { ...out };

    fpPose("blaster", 0, "blaster", 0, out);
    expect(out.z - rest.z).toBeCloseTo(FP_KICK_BACK_M, 9);
    expect(out.pitch).toBeGreaterThan(0);
    // An old noodle swing does not kick the blaster.
    fpPose("blaster", 0, "noodle", 0, out);
    expect(out).toEqual(rest);

    fpPose("noodle", 0, "noodle", FP_SWEEP_S / 2, out);
    expect(rest.x - out.x).toBeCloseTo(FP_SWEEP_M, 9);
    expect(out.yaw).toBeGreaterThan(0);
    fpPose("noodle", 0, "noodle", FP_SWEEP_S + 0.01, out);
    expect(out.x).toBeCloseTo(rest.x, 9);
  });

  it("fpPose allocates nothing: it writes into the given objects", () => {
    const out: FpPose = { x: 0, y: 0, z: 0, pitch: 0, yaw: 0 };
    const tmp = { x: 0, y: 0 };
    expect(fpPose("noodle", 1.234, null, null, out, tmp)).toBe(out);
  });
});

describe.each(CHARACTER_VARIANTS.map((v) => [v.name, v] as const))("view model for %s", (_, v) => {
  it("aims the blaster down -Z, carries the noodle, and draws on top", async () => {
    const PUBLIC = join(dirname(fileURLToPath(import.meta.url)), "../../public");
    const gltf = await new GLTFLoader().parseAsync(readFileSync(join(PUBLIC, v.url), "utf8"), "");
    const vm = buildViewModel(gltf.scene, gltf.animations);
    expect(vm.props).toHaveLength(2);
    const blaster = vm.props.find((p) => p.name === CHARACTER.keepWeapon)!;
    const noodle = vm.props.find((p) => p.name === CHARACTER.noodle.name)!;
    expect(blaster.parent).toBeNull();
    const barrel = new Vector3(1, 0, 0).applyQuaternion(blaster.quaternion);
    expect(barrel.z).toBeLessThan(-0.7); // mostly forward
    expect(Math.abs(barrel.x)).toBeLessThan(0.05); // no sideways aim
    // Metre scale kept: the noodle is still 1.2 m long.
    noodle.updateMatrixWorld(true);
    const tube = noodle.children[0] as Mesh;
    tube.geometry.computeBoundingBox();
    const h = tube.geometry.boundingBox!.max.y;
    expect(tube.localToWorld(new Vector3(0, h, 0)).distanceTo(tube.localToWorld(new Vector3(0, -h, 0)))).toBeCloseTo(
      CHARACTER.noodle.lengthM,
      2,
    );
    for (const m of vm.materials) {
      expect(m.depthTest).toBe(false);
      expect(m.depthWrite).toBe(false);
    }
    expect(new Quaternion().copy(blaster.quaternion).length()).toBeCloseTo(1, 6);
    console.info(`[fp view model ${v.id}] barrel ${barrel.toArray().map((x) => x.toFixed(3)).join(", ")}`);
  }, 30_000);
});
