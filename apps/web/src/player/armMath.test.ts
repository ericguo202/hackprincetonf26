import { describe, expect, it } from "vitest";

import {
  ARM,
  applyLook,
  armOffset,
  armRay,
  clampArm,
  armDefaults,
  hideBody,
  hideThreshold,
  horizontalFov,
  nextArmBack,
  rightDir,
  scrollArm,
  smoothArm,
  toggleFirstPerson,
  viewDir,
  type V3,
} from "./armMath";
import { MOVEMENT } from "./movement";
import { FOOT_BELOW_CENTRE_M } from "./characterAsset";

const close = (a: V3, b: V3) => a.forEach((v, i) => expect(v).toBeCloseTo(b[i], 9));
const dot = (a: V3, b: V3) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];

describe("directions", () => {
  it("yaw 0 looks down -Z like a three.js camera, right is +X", () => {
    close(viewDir(0, 0), [0, 0, -1]);
    close(rightDir(0), [1, 0, 0]);
  });

  it("matches three.js rotation-y: +90 deg turns left, to -X", () => {
    close(viewDir(Math.PI / 2, 0), [-1, 0, 0]);
    close(rightDir(Math.PI / 2), [0, 0, -1]);
  });

  it("right stays perpendicular to the view at any yaw and pitch", () => {
    for (const yaw of [0, 0.7, 2, -2.5]) for (const pitch of [0, 0.4, -1]) {
      expect(dot(viewDir(yaw, pitch), rightDir(yaw))).toBeCloseTo(0, 9);
    }
  });
});

describe("armOffset", () => {
  it("is ARM.back behind and 0.4 m right of the head at eye height, looking level", () => {
    close(armOffset(0, 0, ARM.back, ARM.side), [0.4, 0, ARM.back]);
  });

  it("frames the whole 1.3 m capsule at the default length (50 deg vertical FOV)", () => {
    const tall = 2 * MOVEMENT.capsuleHalfHeight + 2 * MOVEMENT.capsuleRadius;
    const d = Math.hypot(ARM.back, ARM.side);
    expect(2 * d * Math.tan((25 * Math.PI) / 180)).toBeGreaterThan(tall * 2);
    expect(ARM.back).toBe(3.0);
    expect([ARM.minBack, ARM.maxBack]).toEqual([0.6, 3.5]);
  });

  it("rotates with yaw", () => {
    // Facing -X: behind is +X, right is -Z.
    close(armOffset(Math.PI / 2, 0, 1.3, 0.4), [1.3, 0, -0.4]);
  });

  it("swings below the head when looking up, above when looking down", () => {
    expect(armOffset(0, 0.5, 1.3, 0.4)[1]).toBeLessThan(0);
    expect(armOffset(0, -0.5, 1.3, 0.4)[1]).toBeGreaterThan(0);
  });
});

describe("armRay", () => {
  it("splits the offset into a unit direction and a length", () => {
    const { dir, len } = armRay([0.4, 0, 1.3]);
    expect(len).toBeCloseTo(Math.hypot(0.4, 1.3), 9);
    expect(Math.hypot(...dir)).toBeCloseTo(1, 9);
  });

  it("a zero arm (first person) has length 0", () => {
    expect(armRay([0, 0, 0])).toEqual({ dir: [0, 0, 0], len: 0 });
  });
});

describe("clampArm", () => {
  it("keeps the full arm when nothing is hit", () => {
    expect(clampArm(1.36, null)).toBe(1.36);
  });

  it("stops 0.15 m short of a wall", () => {
    expect(clampArm(1.36, 0.8)).toBeCloseTo(0.65, 9);
  });

  it("never goes negative or past the desired length", () => {
    expect(clampArm(1.36, 0.05)).toBe(0);
    expect(clampArm(1.0, 5)).toBe(1.0);
  });
});

describe("smoothArm", () => {
  it("snaps in at once so the camera is never inside a wall", () => {
    expect(smoothArm(1.36, 0.2, 1 / 60)).toBe(0.2);
  });

  it("eases back out at the extend rate", () => {
    expect(smoothArm(0.2, 1.36, 0.1)).toBeCloseTo(0.2 + ARM.extendRate * 0.1, 9);
    expect(smoothArm(1.3, 1.36, 1)).toBe(1.36);
  });
});

describe("first person", () => {
  const deg = (d: number) => (d * Math.PI) / 180;
  // The World camera: 50 deg vertical FOV, at a 16:9 window.
  const H_16_9 = horizontalFov(50, 16 / 9);

  it("computes horizontal FOV from vertical FOV and aspect", () => {
    expect(horizontalFov(50, 1)).toBeCloseTo(deg(50), 9);
    expect((H_16_9 * 180) / Math.PI).toBeCloseTo(79.3, 1);
  });

  it("hides the body whenever the arm is shorter than r / tan(hFOV / 4), at any FOV", () => {
    const r = MOVEMENT.capsuleRadius;
    for (let fovDeg = 30; fovDeg <= 120; fovDeg += 1) {
      const h = deg(fovDeg);
      const limit = r / Math.tan(h / 4);
      for (let arm = 0; arm < limit; arm += 0.01) {
        expect(hideBody(arm, h), `hFOV ${fovDeg} deg, arm ${arm.toFixed(2)} m`).toBe(true);
      }
    }
  });

  it("uses the 1.6 m floor on common screens, and shows the body at the full arm", () => {
    for (const aspect of [16 / 9, 4 / 3]) {
      const h = horizontalFov(50, aspect);
      expect(hideThreshold(h)).toBe(1.6);
      expect(hideBody(1.59, h)).toBe(true);
      expect(hideBody(1.6, h)).toBe(false);
      expect(hideBody(clampArm(Math.hypot(ARM.back, ARM.side), null), h)).toBe(false);
    }
  });

  it("raises the threshold above the floor on a very narrow view (40 deg horizontal)", () => {
    expect(hideThreshold(deg(40))).toBeCloseTo(0.3 / Math.tan(deg(40) / 4), 9);
    expect(hideThreshold(deg(40))).toBeGreaterThan(1.6);
  });

  it("a wall close behind you reads as first person", () => {
    expect(hideBody(clampArm(3.03, 1.5), H_16_9)).toBe(true);
  });
});

describe("eye height", () => {
  it("puts the camera at an adult 1.6 m eye height when standing", () => {
    expect(FOOT_BELOW_CENTRE_M + ARM.headOffsetY).toBeCloseTo(1.6, 9);
  });
});

describe("arm per zone", () => {
  it("starts in first person indoors and at the full arm on the block", () => {
    expect(armDefaults("hou_237901269")).toEqual({ back: ARM.back, pinned: true });
    expect(armDefaults("block")).toEqual({ back: ARM.back, pinned: false });
  });

  it("V toggles first person both ways and remembers the length", () => {
    const outside = { back: 2.2, pinned: false };
    expect(toggleFirstPerson(outside)).toEqual({ back: 2.2, pinned: true });
    expect(toggleFirstPerson(toggleFirstPerson(outside))).toEqual(outside);
  });

  it("indoors, scrolling out leaves first person at the shortest arm, then grows", () => {
    const fp = armDefaults("hou_237901269");
    const out = scrollArm(fp, 100);
    expect(out).toEqual({ back: ARM.minBack, pinned: false });
    expect(scrollArm(out, 100).back).toBeGreaterThan(ARM.minBack);
  });

  it("scrolling in from first person stays in first person", () => {
    const fp = armDefaults("hou_237901269");
    expect(scrollArm(fp, -100)).toEqual(fp);
  });

  it("on the block, scroll adjusts within 0.6-3.5 m", () => {
    expect(scrollArm(armDefaults("block"), 1e6).back).toBe(3.5);
    expect(scrollArm(armDefaults("block"), -1e6).back).toBe(0.6);
  });
});

describe("input", () => {
  it("mouse right turns right, mouse down looks down", () => {
    const r = applyLook(0, 0, 100, 0);
    expect(r.yaw).toBeLessThan(0);
    expect(dot(viewDir(r.yaw, 0), rightDir(0))).toBeGreaterThan(0);
    expect(applyLook(0, 0, 0, 100).pitch).toBeLessThan(0);
  });

  it("clamps pitch short of straight up and down", () => {
    expect(applyLook(0, 0, 0, -1e6).pitch).toBe(ARM.maxPitch);
    expect(applyLook(0, 0, 0, 1e6).pitch).toBe(-ARM.maxPitch);
  });

  it("scroll moves the arm length within its limits", () => {
    expect(nextArmBack(1.3, 100)).toBeGreaterThan(1.3);
    expect(nextArmBack(1.3, -100)).toBeLessThan(1.3);
    expect(nextArmBack(1.3, 1e6)).toBe(ARM.maxBack);
    expect(nextArmBack(1.3, -1e6)).toBe(ARM.minBack);
  });
});
