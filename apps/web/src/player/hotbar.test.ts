import { describe, expect, it } from "vitest";

import { barView, cooldownFraction, cooldownS, isTextEntry, weaponSlots } from "./hotbar";
import { NOODLE, WEAPON_KEYS } from "./weapons";

describe("weapon slots", () => {
  it("come from WEAPON_KEYS in key order: 1 blaster, 2 noodle", () => {
    expect(weaponSlots().map((s) => [s.key, s.weapon, s.name])).toEqual([
      ["1", "blaster", "Foam blaster"],
      ["2", "noodle", "Pool noodle"],
    ]);
    expect(weaponSlots().map((s) => s.code)).toEqual(Object.keys(WEAPON_KEYS).sort());
  });

  it("follow the keys if they change (no drift)", () => {
    expect(weaponSlots({ Digit2: "blaster", Digit1: "noodle" }).map((s) => s.weapon)).toEqual(["noodle", "blaster"]);
  });
});

describe("cooldown fraction", () => {
  it.each([
    ["blaster", 0.25],
    ["noodle", NOODLE.cooldownS],
  ] as const)("%s: 1 at t = 0, 0.5 halfway, 0 at full (%s s)", (weapon, full) => {
    expect(cooldownS(weapon)).toBeCloseTo(full, 9);
    expect(cooldownFraction(0, cooldownS(weapon))).toBe(1);
    expect(cooldownFraction(full / 2, cooldownS(weapon))).toBeCloseTo(0.5, 9);
    expect(cooldownFraction(full, cooldownS(weapon))).toBeCloseTo(0, 9);
  });

  it("clamps outside the window, and is 0 before any attack", () => {
    expect(cooldownFraction(5, 0.8)).toBe(0);
    expect(cooldownFraction(-0.1, 0.8)).toBe(1);
    expect(cooldownFraction(null, 0.8)).toBe(0);
  });
});

describe("bar visibility", () => {
  it("explore: hidden, alive or dead", () => {
    expect(barView(false, true, false)).toBe("hidden");
    expect(barView(false, false, false)).toBe("hidden");
  });
  it("foam: shown; foam and eliminated: dimmed", () => {
    expect(barView(true, true, false)).toBe("shown");
    expect(barView(true, false, false)).toBe("dimmed");
  });
  it("typing in a text field hides it in foam too", () => {
    expect(barView(true, true, true)).toBe("hidden");
    expect(barView(true, false, true)).toBe("hidden");
  });
});

describe("text entry focus", () => {
  it("textarea, text input and contenteditable count; buttons and checkboxes do not", () => {
    const input = (type: string) => Object.assign(document.createElement("input"), { type });
    expect(isTextEntry(document.createElement("textarea"))).toBe(true);
    expect(isTextEntry(input("text"))).toBe(true);
    expect(isTextEntry(input("search"))).toBe(true);
    expect(isTextEntry(input("checkbox"))).toBe(false);
    expect(isTextEntry(document.createElement("button"))).toBe(false);
    expect(isTextEntry(document.body)).toBe(false);
    expect(isTextEntry(null)).toBe(false);
  });
});
