import { afterEach, describe, expect, it } from "vitest";

import { useStore } from "../store";
import { DEV_MODE_TOGGLE_ON } from "../world/devFlags";
import {
  effectiveMode,
  foamModeNow,
  heldWeaponProp,
  useModeOverride,
  weaponForKey,
  weaponsEnabled,
} from "./foamMode";

afterEach(() => {
  useStore.getState().set({ mode: "explore" });
  useModeOverride.getState().set(null);
});

describe("weapons only in foam mode", () => {
  it("explore: weapons off; foam: weapons on", () => {
    expect(weaponsEnabled("explore")).toBe(false);
    expect(weaponsEnabled("foam")).toBe(true);
  });

  it("follows the server's store.mode live, within the same tick", () => {
    expect(useStore.getState().mode).toBe("explore"); // the default
    expect(foamModeNow()).toBe(false);
    useStore.getState().set({ mode: "foam" });
    expect(foamModeNow()).toBe(true);
    useStore.getState().set({ mode: "explore" });
    expect(foamModeNow()).toBe(false);
  });

  it("a dev override beats the server mode; no override defers to it", () => {
    expect(effectiveMode("explore", "foam")).toBe("foam");
    expect(effectiveMode("foam", "explore")).toBe("explore");
    expect(effectiveMode("foam", null)).toBe("foam");
  });

  it("the override is ignored unless the dev toggle is on (dev build + VITE_SOLO)", () => {
    expect(DEV_MODE_TOGGLE_ON).toBe(false); // tests run without VITE_SOLO
    useModeOverride.getState().set("foam");
    expect(foamModeNow()).toBe(false);
  });
});

describe("keys 1/2", () => {
  it("switch weapons in foam", () => {
    expect(weaponForKey("Digit1", true)).toBe("blaster");
    expect(weaponForKey("Digit2", true)).toBe("noodle");
  });

  it("are ignored in explore", () => {
    expect(weaponForKey("Digit1", false)).toBeNull();
    expect(weaponForKey("Digit2", false)).toBeNull();
  });

  it("other keys never pick a weapon", () => {
    expect(weaponForKey("KeyF", true)).toBeNull();
    expect(weaponForKey("Digit3", true)).toBeNull();
  });
});

describe("held weapon prop per mode", () => {
  it("explore holds nothing, whatever is selected", () => {
    expect(heldWeaponProp(false, "blaster")).toBeNull();
    expect(heldWeaponProp(false, "noodle")).toBeNull();
  });

  it("foam holds the selected weapon", () => {
    expect(heldWeaponProp(true, "blaster")).toBe("blaster");
    expect(heldWeaponProp(true, "noodle")).toBe("noodle");
  });
});
