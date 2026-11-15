import { describe, expect, it } from "vitest";

import type { HouseFurniture } from "../store";
import { applyLocalEdit } from "./furnitureEdits";

const H = "hou_1";
const pose = { x: 1, y: 0, z: 2, rot_deg: 90 };
const agentHouse: Record<string, HouseFurniture> = {
  [H]: {
    plan_id: "p_agent",
    objects: [{ instance_id: `${H}.r1/o1`, asset_id: "tv", x: 0, y: 0, z: 0, rot_deg: 0, reason: "faces couch", by_player: "agent" }],
  },
};

describe("applyLocalEdit", () => {
  it("places into a house with no furniture yet", () => {
    const out = applyLocalEdit({}, { type: "furniture_place", payload: { listing_id: H, room_id: `${H}.r2`, asset_id: "sofa", ...pose } }, "me")!;
    expect(out[H].plan_id).toBe("manual");
    expect(out[H].objects).toHaveLength(1);
    expect(out[H].objects[0]).toMatchObject({ asset_id: "sofa", reason: "", by_player: "me", ...pose });
    expect(out[H].objects[0].instance_id).toMatch(new RegExp(`^${H}\\.r2/m\\d+$`));
  });

  it("moves in place within a room, clearing the agent's reason", () => {
    const out = applyLocalEdit(agentHouse, { type: "furniture_move", payload: { listing_id: H, room_id: `${H}.r1`, instance_id: `${H}.r1/o1`, ...pose } }, "me")!;
    expect(out[H].objects[0]).toMatchObject({ instance_id: `${H}.r1/o1`, reason: "", by_player: "me", ...pose });
    expect(out[H].plan_id).toBe("p_agent");
  });

  it("re-files a move into another room, and removes", () => {
    const moved = applyLocalEdit(agentHouse, { type: "furniture_move", payload: { listing_id: H, room_id: `${H}.r2`, instance_id: `${H}.r1/o1`, ...pose } }, "me")!;
    const id = moved[H].objects[0].instance_id;
    expect(id.startsWith(`${H}.r2/m`)).toBe(true);
    const gone = applyLocalEdit(moved, { type: "furniture_remove", payload: { listing_id: H, instance_id: id } }, "me")!;
    expect(gone[H].objects).toEqual([]);
  });

  it("does nothing for an unknown piece", () => {
    expect(applyLocalEdit(agentHouse, { type: "furniture_remove", payload: { listing_id: H, instance_id: "nope" } }, "me")).toBeNull();
  });
});
