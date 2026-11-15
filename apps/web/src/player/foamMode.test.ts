import { afterEach, describe, expect, it } from "vitest";

import { useStore } from "../store";
import { DEV_MODE_TOGGLE_ON } from "../world/devFlags";
import type { NetPlayer } from "../store";
import {
  canAttackNow,
  effectiveMode,
  isAlive,
  meAliveNow,
  foamModeNow,
  heldWeaponProp,
  useModeOverride,
  weaponForKey,
  weaponsEnabled,
} from "./foamMode";

afterEach(() => {
  useStore.getState().set({ mode: "explore", players: {} });
  useStore.getState().setMe({ id: null });
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

const netPlayer = (id: string, alive: boolean): NetPlayer => ({
  id,
  name: id,
  zone: "block",
  pos: [0, 0, 0],
  yaw: 0,
  anim: alive ? "idle" : "eliminated",
  hp: alive ? 3 : 0,
  score: 0,
  alive,
});

describe("alive (no firing while eliminated)", () => {
  it("no id yet (SOLO, before join) counts as alive", () => {
    expect(isAlive(null, {})).toBe(true);
    useStore.getState().setMe({ id: null });
    expect(meAliveNow()).toBe(true);
  });

  it("my entry not in state yet counts as alive", () => {
    expect(isAlive("me", { other: netPlayer("other", false) })).toBe(true);
    useStore.getState().setMe({ id: "me" });
    useStore.getState().set({ players: { other: netPlayer("other", false) } });
    expect(meAliveNow()).toBe(true);
  });

  it("alive entry is alive", () => {
    useStore.getState().setMe({ id: "me" });
    useStore.getState().set({ players: { me: netPlayer("me", true) } });
    expect(meAliveNow()).toBe(true);
  });

  it("dead entry is dead, and respawn reads alive again within the same tick", () => {
    useStore.getState().setMe({ id: "me" });
    useStore.getState().set({ players: { me: netPlayer("me", false) } });
    expect(meAliveNow()).toBe(false);
    useStore.getState().set({ players: { me: netPlayer("me", true) } });
    expect(meAliveNow()).toBe(true);
  });

  it("another player's death does not affect me", () => {
    useStore.getState().setMe({ id: "me" });
    useStore.getState().set({ players: { me: netPlayer("me", true), other: netPlayer("other", false) } });
    expect(meAliveNow()).toBe(true);
  });
});

describe("trigger gate: foam and alive", () => {
  it.each([
    [false, false, false],
    [false, true, false],
    [true, false, false],
    [true, true, true],
  ])("foam %s, alive %s -> fires %s", (foam, alive, fires) => {
    expect(canAttackNow(foam, alive)).toBe(fires);
  });

  it("keys 1/2 still switch weapons while dead (they read only the mode)", () => {
    useStore.getState().setMe({ id: "me" });
    useStore.getState().set({ mode: "foam", players: { me: netPlayer("me", false) } });
    expect(meAliveNow()).toBe(false);
    expect(weaponForKey("Digit2", foamModeNow())).toBe("noodle");
  });
});
