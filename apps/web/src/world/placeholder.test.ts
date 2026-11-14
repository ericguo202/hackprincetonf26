import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { BoxGeometry, Group, Mesh } from "three";

import type { Room } from "../contracts.gen";
import { extractTrimesh } from "./collision";
import { ceilingTrimesh, houseBoxLocal, segmentBox, wallSegments } from "./placeholder";

const here = dirname(fileURLToPath(import.meta.url));
const room = JSON.parse(
  readFileSync(join(here, "../../../../contracts/fixtures/room.json"), "utf8"),
) as Room;

const length = (s: { a: number[]; b: number[] }) => Math.hypot(s.b[0] - s.a[0], s.b[1] - s.a[1]);

describe("wallSegments", () => {
  it("cuts a gap for each door opening, not for windows", () => {
    const segs = wallSegments(room);
    // Perimeter 20.4 m, two 0.9 m doors; the window on x = 0 cuts nothing.
    expect(segs.reduce((s, w) => s + length(w), 0)).toBeCloseTo(20.4 - 1.8, 6);
    expect(segs).toHaveLength(6);
  });

  it("cuts a gap for the listing's front door when it sits on an edge", () => {
    const segs = wallSegments(room, [[1.2, 0]], 1.0);
    expect(segs.reduce((s, w) => s + length(w), 0)).toBeCloseTo(20.4 - 1.8 - 1.0, 6);
    expect(segs).toHaveLength(7);
    // Nothing is built across the doorway.
    for (const s of segs) {
      if (s.a[1] !== 0 || s.b[1] !== 0) continue;
      const lo = Math.min(s.a[0], s.b[0]);
      const hi = Math.max(s.a[0], s.b[0]);
      expect(hi <= 0.7 + 1e-9 || lo >= 1.7 - 1e-9).toBe(true);
    }
  });

  it("ignores a front door point that is nowhere near a wall", () => {
    expect(wallSegments(room, [[3, 2]])).toHaveLength(6);
  });
});

describe("segmentBox", () => {
  it("turns a box's +X onto the segment", () => {
    const x = segmentBox({ a: [0, 0], b: [4, 0] });
    expect(x).toMatchObject({ cx: 2, len: 4 });
    expect(x.cz).toBeCloseTo(0, 9);
    expect(x.yaw).toBeCloseTo(0, 9); // atan2(-0, 4) is -0; same rotation
    const z = segmentBox({ a: [1, 0], b: [1, 2] });
    // rotation-y maps +X to (cos, -sin); that must equal +Z here.
    expect(Math.cos(z.yaw)).toBeCloseTo(0, 9);
    expect(-Math.sin(z.yaw)).toBeCloseTo(1, 9);
  });
});

describe("houseBoxLocal", () => {
  it("wraps the rooms", () => {
    expect(houseBoxLocal([1.2, 0, 0], [room], 3)).toEqual({ center: [3, 1.5, 2.1], size: [6, 3, 4.2] });
  });

  it("without rooms, puts the door on the front face", () => {
    const b = houseBoxLocal([0, 0, 0], [], 3);
    expect(b.center[2] + b.size[2] / 2).toBeCloseTo(0, 9);
  });
});

describe("extractTrimesh", () => {
  it("bakes nested transforms into one buffer", () => {
    const root = new Group();
    const child = new Group();
    child.position.set(10, 0, 0);
    child.scale.set(2, 2, 2);
    const box = new Mesh(new BoxGeometry(1, 1, 1));
    box.position.set(0, 1, 0);
    child.add(box);
    root.add(child);
    root.add(new Mesh(new BoxGeometry(1, 1, 1)));

    const t = extractTrimesh(root);
    expect(t.vertices.length).toBe(2 * 24 * 3);
    expect(t.indices.length).toBe(2 * 36);
    // The second mesh's indices are offset past the first mesh's vertices.
    expect(Math.max(...t.indices)).toBe(47);

    const xs = Array.from(t.vertices).filter((_, i) => i % 3 === 0);
    const ys = Array.from(t.vertices).filter((_, i) => i % 3 === 1);
    expect(Math.max(...xs)).toBeCloseTo(11, 6); // 10 + 2 * 0.5
    expect(Math.max(...ys)).toBeCloseTo(3, 6); // 2 * (1 + 0.5)
  });

  it("ignores the root's own transform", () => {
    const root = new Group();
    root.position.set(100, 0, 0);
    root.add(new Mesh(new BoxGeometry(1, 1, 1)));
    const xs = Array.from(extractTrimesh(root).vertices).filter((_, i) => i % 3 === 0);
    expect(Math.max(...xs)).toBeCloseTo(0.5, 6);
  });
});

describe("ceilingTrimesh", () => {
  const triArea = (v: Float32Array, i: Uint32Array) => {
    let a = 0;
    for (let k = 0; k < i.length; k += 3) {
      const [p, q, r] = [i[k], i[k + 1], i[k + 2]].map((n) => [v[n * 3], v[n * 3 + 2]]);
      a += Math.abs((q[0] - p[0]) * (r[1] - p[1]) - (r[0] - p[0]) * (q[1] - p[1])) / 2;
    }
    return a;
  };

  it("covers the room exactly, at floor_y + height_m", () => {
    const t = ceilingTrimesh(room);
    expect(triArea(t.vertices, t.indices)).toBeCloseTo(25.2, 4); // 6 x 4.2 (Float32 vertices)
    for (let k = 1; k < t.vertices.length; k += 3) expect(t.vertices[k]).toBeCloseTo(room.floor_y + room.height_m, 5);
  });

  it("triangulates a non-convex room (the hero living room's 11-point outline)", () => {
    const r4: Room = {
      ...room,
      floor_y: 0.005,
      height_m: 2.43,
      polygon: [
        [-0.497, 0.512], [-0.496, 0.826], [0.56, 0.823], [1.278, 0.821], [3.701, 0.814],
        [5.2, -0.693], [5.191, -3.85], [-0.809, -3.85], [-0.806, -2.975], [-3.192, -0.575], [-3.189, 0.519],
      ],
    };
    const t = ceilingTrimesh(r4);
    expect(t.indices.length).toBe((r4.polygon.length - 2) * 3);
    expect(triArea(t.vertices, t.indices)).toBeCloseTo(32.27, 1); // the polygon's own area
  });
});
