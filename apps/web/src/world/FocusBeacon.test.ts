import { describe, expect, it } from "vitest";

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import type { PackManifest, Room } from "../contracts.gen";
import { BEACON_HEIGHT_M, BEACON_RADIUS_M, beaconPoint, effectiveFocus } from "./focusBeaconLogic";
import { DEV_FOCUS_HOUSE, useDevFocusOverride } from "./devFocus";
import { DEV_FOCUS_TOGGLE_ON } from "./devFlags";
import { blockDoors, DOOR_MARKER_M, parseListings, type Door, type LoadedPack } from "./pack";

const makeTestDoor = (listing_id: string, marker: [number, number, number]): Door => ({
  listing_id,
  world: { position: [0, 0, 0], heading_deg: 0, door: [0, 0, 0] },
  block: [0, 0, 0],
  local: [0, 0, 0],
  marker,
  exitTo: [0, 0, 0],
  leaveTo: [0, 0, 0],
  leaveYaw: 0,
  enterTo: [0, 0, 0],
});

describe("FocusBeacon constants", () => {
  it("radius is 0.8 m and height is 30 m", () => {
    expect(BEACON_RADIUS_M).toBe(0.8);
    expect(BEACON_HEIGHT_M).toBe(30);
  });
});

describe("effectiveFocus", () => {
  it("returns null if me.id is null and dev override is unset", () => {
    expect(effectiveFocus(null, { p1: "hou_1" }, null)).toBeNull();
  });

  it("returns store.focus[me.id] when me.id is present in focus map", () => {
    expect(effectiveFocus("p1", { p1: "hou_1" }, null)).toBe("hou_1");
  });

  it("returns null if me.id is not in focus map", () => {
    expect(effectiveFocus("p2", { p1: "hou_1" }, null)).toBeNull();
  });

  it("dev override takes priority when DEV_FOCUS_TOGGLE_ON is true", () => {
    if (DEV_FOCUS_TOGGLE_ON) {
      expect(effectiveFocus("p1", { p1: "hou_1" }, "hou_override")).toBe("hou_override");
      expect(effectiveFocus(null, {}, "hou_override")).toBe("hou_override");
    }
  });
});

describe("useDevFocusOverride", () => {
  it("toggles between null and DEV_FOCUS_HOUSE", () => {
    useDevFocusOverride.getState().set(null);
    expect(useDevFocusOverride.getState().override).toBeNull();

    useDevFocusOverride.getState().toggle();
    expect(useDevFocusOverride.getState().override).toBe(DEV_FOCUS_HOUSE);

    useDevFocusOverride.getState().toggle();
    expect(useDevFocusOverride.getState().override).toBeNull();
  });
});

describe("FocusBeacon door lookup", () => {
  const pack: Partial<LoadedPack> = {
    doors: [
      makeTestDoor("hou_236595179", [10, 0.35, 20]),
      makeTestDoor("hou_237901269", [-15, 0.1, -30]),
    ],
  };

  it("finds matching door for focused listing", () => {
    const door = pack.doors!.find((d) => d.listing_id === "hou_236595179");
    expect(door).toBeDefined();
    expect(beaconPoint(door!)).toEqual([10, 0.35, 20]);
  });

  it("returns undefined for unknown listing", () => {
    const door = pack.doors!.find((d) => d.listing_id === "hou_unknown");
    expect(door).toBeUndefined();
  });
});

describe("beacon position on real v3 data", () => {
  const PACKS = join(dirname(fileURLToPath(import.meta.url)), "../../public/packs");
  const V3 = join(PACKS, "packs/hou_navidad_01/v5");
  const json = (p: string) => JSON.parse(readFileSync(p, "utf8"));
  const parsed = parseListings(json(join(PACKS, "listings/index.json")));
  if (!parsed.ok) throw new Error("v3 listings index did not parse");
  const rooms = (id: string): Room[] => {
    const r = json(join(V3, `houses/${id}/rooms.json`));
    return Array.isArray(r) ? r : r.rooms;
  };
  const ids = ["hou_236595179", "hou_237901269"];
  const doors = blockDoors(json(join(V3, "manifest.json")) as PackManifest, parsed.value, Object.fromEntries(ids.map((id) => [id, rooms(id)])));

  it.each(ids)("%s: at the block door marker, 4.2 m out from the door point, not at exitTo", (id) => {
    const d = doors.find((x) => x.listing_id === id)!;
    const p = beaconPoint(d);
    expect(p).toEqual(d.marker);
    expect(Math.hypot(p[0] - d.block[0], p[2] - d.block[2])).toBeCloseTo(DOOR_MARKER_M, 6);
    expect(p).not.toEqual(d.exitTo);
  });
});
