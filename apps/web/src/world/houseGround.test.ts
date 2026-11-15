import { describe, expect, it } from "vitest";
import { Group, Mesh } from "three";

import { showPlaceholderGround } from "./houseGround";

const scene = (...names: string[]) => {
  const g = new Group();
  for (const n of names) {
    const m = new Mesh();
    m.name = n;
    g.add(m);
  }
  return g;
};

describe("showPlaceholderGround", () => {
  it("hides the slab when collision.glb has a ground node (v6 scanned lot)", () => {
    expect(showPlaceholderGround(scene("walls", "floor", "fixtures", "ground", "outdoor", "bounds"))).toBe(false);
  });

  it("finds a nested ground node", () => {
    const root = new Group();
    root.add(scene("walls", "ground"));
    expect(showPlaceholderGround(root)).toBe(false);
  });

  it("keeps the slab when there is no ground node (v3 houses)", () => {
    expect(showPlaceholderGround(scene("walls", "floor", "fixtures"))).toBe(true);
  });

  it("keeps the slab while collision.glb is not loaded or failed", () => {
    expect(showPlaceholderGround(null)).toBe(true);
  });
});
