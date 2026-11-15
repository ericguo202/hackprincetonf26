import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { beforeAll, describe, expect, it } from "vitest";
import { Group, Mesh, Vector3, type Object3D } from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";

import type { Weapon } from "../../../../contracts/game-state";
import { CHARACTER, CHARACTER_VARIANTS } from "./characterAsset";
import { setWeapon } from "./characterRig";
import { buildViewModel, type ViewModel } from "./fpViewModel";
import {
  bob,
  bobPhase,
  bobStrength,
  FP_BOB_M,
  FP_CAMERA_FOV_DEG,
  FP_KICK_BACK_M,
  FP_KICK_S,
  FP_NOODLE_LENGTH,
  FP_OFFSET,
  FP_SCALE,
  FP_SWAY_M,
  FP_SWIPE_DOWN_M,
  FP_SWIPE_LEFT_M,
  FP_SWIPE_S,
  FP_TILT,
  fpPose,
  fpVisible,
  kickCurve,
  swipeCurve,
  toNdc,
  type FpPose,
} from "./fpWeaponMath";

const newPose = (): FpPose => ({ x: 0, y: 0, z: 0, pitch: 0, yaw: 0, roll: 0 });

describe("fpVisible", () => {
  it("shows only in first person, holding a weapon, alive", () => {
    expect(fpVisible({ bodyHidden: true, weapon: "blaster", alive: true })).toBe(true);
    expect(fpVisible({ bodyHidden: true, weapon: "noodle", alive: true })).toBe(true);
    expect(fpVisible({ bodyHidden: false, weapon: "blaster", alive: true })).toBe(false); // third person: hand prop shows
    expect(fpVisible({ bodyHidden: true, weapon: null, alive: true })).toBe(false); // explore mode
    expect(fpVisible({ bodyHidden: true, weapon: "blaster", alive: false })).toBe(false); // eliminated
  });
});

describe("curves", () => {
  it("kick: shorter and smaller, full at the shot, 0 by FP_KICK_S", () => {
    expect(FP_KICK_S).toBeLessThan(0.12);
    expect(FP_KICK_BACK_M).toBeLessThan(0.06);
    expect(kickCurve(0)).toBe(1);
    expect(kickCurve(FP_KICK_S / 2)).toBeCloseTo(0.25, 9);
    expect(kickCurve(FP_KICK_S)).toBe(0);
    expect(kickCurve(-0.01)).toBe(0);
    expect(kickCurve(Number.NaN)).toBe(0);
  });

  it("noodle swipe: 0 -> 1 -> 0 over 0.2 s, 0 outside", () => {
    expect(FP_SWIPE_S).toBeCloseTo(0.2, 9);
    expect(swipeCurve(0)).toBe(0);
    expect(swipeCurve(FP_SWIPE_S / 2)).toBeCloseTo(1, 9);
    expect(swipeCurve(FP_SWIPE_S)).toBe(0);
    expect(swipeCurve(-1)).toBe(0);
  });

  it("walk bob: zero when still, grows with speed, capped at run speed", () => {
    expect(bobStrength(0)).toBe(0);
    expect(bobStrength(0.01)).toBe(0);
    expect(bobStrength(2.4)).toBeCloseTo(0.5, 9);
    expect(bobStrength(20)).toBe(1);
    const out = { x: 1, y: 1 };
    expect(bob(1.2, 0, out)).toEqual({ x: 0, y: -0 });
    bob(Math.PI / 2, 1, out);
    expect(out.x).toBeCloseTo(FP_BOB_M.x, 9);
    expect(out.y).toBeCloseTo(-FP_BOB_M.y, 9);
    expect(bobPhase(0, 0)).toBe(0);
    expect(bobPhase(0, 1)).toBeGreaterThan(0);
    expect(bobPhase(6, 100)).toBeLessThan(2 * Math.PI);
  });

  it("idle sway is half of before (4 mm / 3 mm then)", () => {
    expect(FP_SWAY_M.x).toBeCloseTo(0.002, 9);
    expect(FP_SWAY_M.y).toBeCloseTo(0.0015, 9);
  });
});

describe("fpPose", () => {
  it("rests lower right and forward, at the fixed up-left hold angle", () => {
    const p = fpPose("blaster", 0, null, null, newPose());
    expect(p.x).toBeCloseTo(FP_OFFSET.right, 2);
    expect(p.y).toBeCloseTo(-FP_OFFSET.down, 2);
    expect(p.z).toBeCloseTo(-FP_OFFSET.forward, 9);
    expect(p.yaw).toBeCloseTo((20 * Math.PI) / 180, 9); // inward (left)
    expect(p.pitch).toBeCloseTo((15 * Math.PI) / 180, 9); // up
    expect(p.roll).toBe(FP_TILT.roll);
  });

  it("kick only for the blaster, the swipe only for the noodle (down-left)", () => {
    const rest = { ...fpPose("blaster", 0, null, null, newPose()) };
    const k = fpPose("blaster", 0, "blaster", 0, newPose());
    expect(k.z - rest.z).toBeCloseTo(FP_KICK_BACK_M, 9);
    expect(k.pitch).toBeGreaterThan(rest.pitch);
    expect(fpPose("blaster", 0, "noodle", 0, newPose())).toEqual(rest);
    const s = fpPose("noodle", 0, "noodle", FP_SWIPE_S / 2, newPose());
    expect(rest.x - s.x).toBeCloseTo(FP_SWIPE_LEFT_M, 9);
    expect(rest.y - s.y).toBeCloseTo(FP_SWIPE_DOWN_M, 9);
    expect(fpPose("noodle", 0, "noodle", FP_SWIPE_S + 0.01, newPose())).toEqual(fpPose("noodle", 0, null, null, newPose()));
  });

  it("walk bob moves it only while walking", () => {
    const still = fpPose("blaster", 0, null, null, newPose(), { phase: Math.PI / 2, strength: 0 });
    const walking = fpPose("blaster", 0, null, null, newPose(), { phase: Math.PI / 2, strength: 1 });
    expect(walking.y).toBeLessThan(still.y);
    expect(walking.x).toBeGreaterThan(still.x);
  });

  it("allocates nothing: it writes into the given pose", () => {
    const out = newPose();
    expect(fpPose("noodle", 1.234, null, null, out)).toBe(out);
  });
});

describe("toNdc", () => {
  it("projects the view centre to 0 and the frustum edge to 1", () => {
    expect(toNdc([0, 0, -1], 50, 16 / 9)).toEqual([0, 0]);
    const edge = Math.tan((25 * Math.PI) / 180);
    expect(toNdc([0, edge, -1], 50, 1)![1]).toBeCloseTo(1, 9);
    expect(toNdc([0, 0, 1], 50, 1)).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Screen coverage with the real props, at 16:9 and 16:10.
// ---------------------------------------------------------------------------

const PUBLIC = join(dirname(fileURLToPath(import.meta.url)), "../../public");
const vms = new Map<string, ViewModel>();
beforeAll(async () => {
  for (const v of CHARACTER_VARIANTS) {
    const gltf = await new GLTFLoader().parseAsync(readFileSync(join(PUBLIC, v.url), "utf8"), "");
    vms.set(v.id, buildViewModel(gltf.scene, gltf.animations));
  }
}, 60_000);

/** Camera-space vertices of the shown prop, posed like FirstPersonWeapon does. */
function cameraSpacePoints(vm: ViewModel, weapon: Weapon, pose: FpPose): [number, number, number][] {
  const offset = new Group();
  for (const p of vm.props) offset.add(p);
  setWeapon(vm.rig, weapon);
  offset.position.set(pose.x, pose.y, pose.z);
  offset.rotation.set(pose.pitch, pose.yaw, pose.roll, "YXZ");
  offset.scale.setScalar(FP_SCALE);
  offset.updateMatrixWorld(true);
  const pts: [number, number, number][] = [];
  const v = new Vector3();
  const shown = (o: Object3D | null): boolean => !o || o === offset || (o.visible && shown(o.parent));
  offset.traverse((o) => {
    if (!(o instanceof Mesh) || !shown(o)) return;
    const pos = o.geometry.getAttribute("position");
    for (let i = 0; i < pos.count; i++) {
      v.fromBufferAttribute(pos, i).applyMatrix4(o.matrixWorld);
      pts.push([v.x, v.y, v.z]);
    }
  });
  for (const p of [...vm.props]) offset.remove(p);
  return pts;
}

const POSES: [string, (w: Weapon) => FpPose][] = [
  ["rest", (w) => fpPose(w, 0, null, null, newPose())],
  ["kick/swipe peak", (w) => fpPose(w, 0, w, w === "blaster" ? 0 : FP_SWIPE_S / 2, newPose())],
  ["walking", (w) => fpPose(w, 0, null, null, newPose(), { phase: Math.PI / 2, strength: 1 })],
];

describe.each(CHARACTER_VARIANTS.map((v) => [v.name, v.id] as const))("view model on screen, %s", (_, id) => {
  it.each(["blaster", "noodle"] as const)("%s stays in the lower right and out of the centre third (16:9 and 16:10)", (weapon) => {
    const vm = vms.get(id)!;
    for (const aspect of [16 / 9, 16 / 10]) {
      for (const [name, poseOf] of POSES) {
        const ndc = cameraSpacePoints(vm, weapon, poseOf(weapon)).map((p) => toNdc(p, FP_CAMERA_FOV_DEG, aspect));
        expect(ndc.every((q) => q !== null), `${name}: behind the camera`).toBe(true);
        const pts = ndc as [number, number][];
        const inCentre = pts.filter(([x, y]) => Math.abs(x) < 1 / 3 && Math.abs(y) < 1 / 3).length;
        expect(inCentre, `${weapon} ${name} @${aspect.toFixed(2)}: in the centre third`).toBe(0);
        const lowerRight = pts.filter(([x, y]) => x > 0 && y < 0).length / pts.length;
        expect(lowerRight, `${weapon} ${name} @${aspect.toFixed(2)}: share in the lower-right quarter`).toBeGreaterThan(0.9);
        // Visible: like a held item, its back end runs off the lower-right edge
        // (blaster about 2/3 of its vertices on screen, noodle's grip end below the frame).
        const onScreen = pts.filter(([x, y]) => Math.abs(x) <= 1 && Math.abs(y) <= 1).length / pts.length;
        expect(onScreen, `${weapon} ${name} @${aspect.toFixed(2)}: share on screen`).toBeGreaterThan(weapon === "blaster" ? 0.6 : 0.35);
        const minX = Math.min(...pts.map((p) => p[0]));
        const maxY = Math.max(...pts.map((p) => p[1]));
        if (aspect === 16 / 9) console.info(`[fp ${id} ${weapon} ${name}] ndc minX ${minX.toFixed(2)} maxY ${maxY.toFixed(2)}`);
      }
    }
  });

  it("the noodle points up-left toward the centre: its tip is the item's nearest point to it", () => {
    const vm = vms.get(id)!;
    cameraSpacePoints(vm, "noodle", fpPose("noodle", 0, null, null, newPose())); // poses it
    const tube = vm.rig.noodleJiggle!.mesh;
    const offset = new Group();
    for (const p of vm.props) offset.add(p);
    const pose = fpPose("noodle", 0, null, null, newPose());
    offset.position.set(pose.x, pose.y, pose.z);
    offset.rotation.set(pose.pitch, pose.yaw, pose.roll, "YXZ");
    offset.scale.setScalar(FP_SCALE);
    offset.updateMatrixWorld(true);
    const h = (tube.geometry as unknown as { parameters: { height: number } }).parameters.height / 2;
    const tip = tube.localToWorld(new Vector3(0, h, 0));
    const grip = tube.localToWorld(new Vector3(0, -h, 0));
    for (const p of [...vm.props]) offset.remove(p);
    const t = toNdc([tip.x, tip.y, tip.z], FP_CAMERA_FOV_DEG, 16 / 9)!;
    const g = toNdc([grip.x, grip.y, grip.z], FP_CAMERA_FOV_DEG, 16 / 9)!;
    expect(t[0]).toBeLessThan(g[0]); // tip left of the grip
    expect(t[1]).toBeGreaterThan(g[1]); // and above it
    expect(t[0]).toBeGreaterThan(1 / 3); // but short of the centre third
    expect(Math.abs(t[0])).toBeLessThan(1); // on screen
  });

  it("the noodle is shorter in first person (0.6 of its length), and the blaster aims ahead before the tilt", () => {
    const vm = vms.get(id)!;
    const tube = vm.rig.noodleJiggle!.mesh;
    tube.updateMatrixWorld(true);
    tube.geometry.computeBoundingBox();
    const h = tube.geometry.boundingBox!.max.y;
    const len = tube.localToWorld(new Vector3(0, h, 0)).distanceTo(tube.localToWorld(new Vector3(0, -h, 0)));
    expect(len).toBeCloseTo(CHARACTER.noodle.lengthM * FP_NOODLE_LENGTH, 2);
    const blaster = vm.props.find((p) => p.name === CHARACTER.keepWeapon)!;
    const barrel = new Vector3(1, 0, 0).applyQuaternion(blaster.quaternion);
    expect(barrel.z).toBeLessThan(-0.7);
    for (const m of vm.materials) expect(m.depthTest).toBe(false);
  });
});
