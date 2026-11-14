import { describe, expect, it } from "vitest";
import { useStore } from "./store";

describe("store", () => {
  it("merges player updates", () => {
    useStore.getState().setPlayer({ roomId: "house_a.r3" });
    expect(useStore.getState().player).toEqual({ pos: [0, 0, 0], listingId: null, roomId: "house_a.r3" });
  });
});
