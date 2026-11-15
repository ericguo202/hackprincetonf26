import { beforeEach, describe, expect, it } from "vitest";

import { hiddenLocally, normDeg, useBuild } from "./buildState";

describe("build mode state", () => {
  beforeEach(() => useBuild.getState().setActive(false));

  it("opens on the catalog, folds it on a pick, brings it back only on a put-back", () => {
    const s = useBuild.getState;
    s().setActive(true);
    expect(s().catalogOpen).toBe(true);
    s().pick("sofa_1");
    expect(s()).toMatchObject({ held: { kind: "new", asset_id: "sofa_1" }, catalogOpen: false });
    s().drop(); // placed a moved piece: keep the room in view
    expect(s().catalogOpen).toBe(false);
    s().pickUp({ instance_id: "r1/o1", asset_id: "tv", rot_deg: -90 });
    expect(s().rotDeg).toBe(270);
    s().drop(true);
    expect(s()).toMatchObject({ held: null, catalogOpen: true });
  });

  it("turning build mode off lets go of everything", () => {
    useBuild.getState().pick("sofa_1");
    useBuild.getState().setActive(false);
    expect(useBuild.getState()).toMatchObject({ active: false, held: null, hover: null });
  });

  it("hides the carried piece, and a just-moved piece until its new pose arrives", () => {
    const carrying = { active: true, held: { kind: "move" as const, instance_id: "a", asset_id: "x" }, pending: null };
    expect(hiddenLocally(carrying, "a", "k")).toBe(true);
    expect(hiddenLocally(carrying, "b", "k")).toBe(false);
    const moved = { active: true, held: null, pending: { instance_id: "a", key: "old" } };
    expect(hiddenLocally(moved, "a", "old")).toBe(true);
    expect(hiddenLocally(moved, "a", "new")).toBe(false);
  });

  it("normDeg wraps into [0, 360)", () => {
    expect(normDeg(-45)).toBe(315);
    expect(normDeg(720)).toBe(0);
    expect(normDeg(359.99)).toBe(0);
  });
});
