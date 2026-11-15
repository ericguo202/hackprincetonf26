// Crouch as cover, against the dev plan B sectional (2.8 x 1.9 x 0.85 m),
// with hitboxes from remoteInterp and the blaster's ray-vs-capsule. The
// headless Rapier run against the real 6915 living room gives the same
// numbers (docs/lane-c-status.md section 13): crouching behind the sectional
// blocks about 61% of the shots that would hit you standing; the crouched
// head (Duck hold, 0.97 m) stays 12 cm above the 0.85 m back.
import { describe, expect, it } from "vitest";

import { rayCapsule, type Ray } from "../player/blasterMath";
import { ARM } from "../player/armMath";
import { CENTRE_ABOVE_FLOOR_M, CROUCH } from "../player/movement";
import { hitboxFromBody, type Vec3 } from "./remoteInterp";

/** Ray vs axis-aligned box (slab method): entry distance, or null. */
function rayBox(r: Ray, lo: Vec3, hi: Vec3): number | null {
  let t0 = 0;
  let t1 = Infinity;
  for (let i = 0; i < 3; i++) {
    if (Math.abs(r.dir[i]) < 1e-12) {
      if (r.origin[i] < lo[i] || r.origin[i] > hi[i]) return null;
      continue;
    }
    const a = (lo[i] - r.origin[i]) / r.dir[i];
    const b = (hi[i] - r.origin[i]) / r.dir[i];
    t0 = Math.max(t0, Math.min(a, b));
    t1 = Math.min(t1, Math.max(a, b));
  }
  return t0 <= t1 ? t0 : null;
}

// Sectional at the room centre (plan B in 6915's living room), shooters to its north.
const SECTIONAL = { lo: [0.04, 0, -2.4] as Vec3, hi: [2.84, 0.85, -0.5] as Vec3 };
const TARGET: [number, number] = [1.44, -2.85]; // 0.45 m behind its south face
const standing = hitboxFromBody("t", [TARGET[0], CENTRE_ABOVE_FLOOR_M.standing, TARGET[1]], "idle");
// Crouch keeps the standing capsule: the wire body centre is at standing height; only the model/hitbox is lower.
const crouched = hitboxFromBody("t", [TARGET[0], CENTRE_ABOVE_FLOOR_M.standing, TARGET[1]], "crouch");

type Shot = { aimY: number; standingHit: boolean; crouchedHit: boolean; sofaFirst: boolean };

function shots(): Shot[] {
  const out: Shot[] = [];
  for (const sx of [0, 0.7, 1.44, 2.2, 2.9]) {
    const eye: Vec3 = [sx, CENTRE_ABOVE_FLOOR_M.standing + ARM.headOffsetY, 0.25];
    for (let aimY = 0.05; aimY <= 1.2501; aimY += 0.05) {
      for (let lx = -0.2; lx <= 0.2001; lx += 0.1) {
        const aim: Vec3 = [TARGET[0] + lx, aimY, TARGET[1]];
        const v = aim.map((q, i) => q - eye[i]) as Vec3;
        const L = Math.hypot(...v);
        const ray: Ray = { origin: eye, dir: v.map((q) => q / L) as Vec3 };
        const sofa = rayBox(ray, SECTIONAL.lo, SECTIONAL.hi) ?? Infinity;
        const ts = rayCapsule(ray, standing);
        const tc = rayCapsule(ray, crouched);
        out.push({
          aimY,
          standingHit: ts !== null && ts < sofa,
          crouchedHit: tc !== null && tc < sofa,
          sofaFirst: sofa < Infinity,
        });
      }
    }
  }
  return out;
}

describe("crouch as cover behind the 0.85 m sectional", () => {
  const all = shots();

  it("never makes you easier to hit: every shot that hits you crouched would hit you standing", () => {
    for (const s of all) if (s.crouchedHit) expect(s.standingHit).toBe(true);
  });

  it("blocks everything aimed above the crouched head", () => {
    for (const s of all) if (s.aimY > CROUCH.topM) expect(s.crouchedHit).toBe(false);
    expect(all.some((s) => s.aimY > CROUCH.topM && s.standingHit)).toBe(true);
  });

  it("the sectional stops low shots in both postures", () => {
    for (const s of all) if (s.aimY < 0.5) expect(s.standingHit || s.crouchedHit).toBe(false);
  });

  it("is partial cover: the head above the 0.85 m back stays exposed (pinned so a change is noticed)", () => {
    const standingHits = all.filter((s) => s.standingHit);
    const stillHit = standingHits.filter((s) => s.crouchedHit);
    const blocked = 1 - stillHit.length / standingHits.length;
    expect(blocked).toBeGreaterThan(0.55);
    expect(blocked).toBeLessThan(0.7);
    for (const s of stillHit) {
      expect(s.aimY).toBeGreaterThan(SECTIONAL.hi[1] - 0.15);
      expect(s.aimY).toBeLessThanOrEqual(CROUCH.topM);
    }
  });
});
