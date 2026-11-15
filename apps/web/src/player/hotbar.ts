// Lane C: the weapon bar's logic (WeaponBar.tsx). Pure: tested in
// hotbar.test.ts. Slots come from weapons.ts WEAPON_KEYS, so the bar
// cannot drift from the keys Blaster listens to.

import type { Weapon } from "../../../../contracts/game-state";
import { WEAPON_KEYS, WEAPON_RULES } from "./weapons";

/** Short names under each slot. A Record over Weapon: a new weapon fails typecheck until named. */
export const WEAPON_NAMES: Record<Weapon, string> = {
  blaster: "Foam blaster",
  noodle: "Pool noodle",
};

export type Slot = { code: string; key: string; weapon: Weapon; name: string };

/** One slot per WEAPON_KEYS entry, in key order: Digit1 -> "1", Digit2 -> "2". */
export function weaponSlots(keys: Record<string, Weapon> = WEAPON_KEYS): Slot[] {
  return Object.entries(keys)
    .map(([code, weapon]) => ({ code, key: code.replace(/^Digit/, ""), weapon, name: WEAPON_NAMES[weapon] }))
    .sort((a, b) => a.key.localeCompare(b.key, undefined, { numeric: true }));
}

/**
 * Cooldown still to run, as a fraction: 1 right after the attack, 0.5 halfway,
 * 0 once the weapon can attack again (and before any attack). Clamped to [0, 1].
 */
export function cooldownFraction(sinceS: number | null, cooldownS: number): number {
  if (sinceS === null || cooldownS <= 0) return 0;
  return Math.min(1, Math.max(0, 1 - sinceS / cooldownS));
}

/** The weapon's cooldown, the same interval Blaster's canAttack uses. */
export const cooldownS = (weapon: Weapon): number => WEAPON_RULES[weapon].minIntervalS;

export type BarView = "hidden" | "shown" | "dimmed";

/** Explore or typing in chat: hidden. Foam and eliminated: dimmed. Otherwise shown. */
export function barView(foam: boolean, alive: boolean, typing: boolean): BarView {
  if (!foam || typing) return "hidden";
  return alive ? "shown" : "dimmed";
}

const NOT_TEXT = new Set(["button", "checkbox", "radio", "range", "submit", "reset", "file", "color", "image"]);

/** A focused element that takes typing: textarea, text-like input, contenteditable. */
export function isTextEntry(el: EventTarget | Element | null): boolean {
  if (typeof HTMLTextAreaElement !== "undefined" && el instanceof HTMLTextAreaElement) return true;
  if (typeof HTMLInputElement !== "undefined" && el instanceof HTMLInputElement) return !NOT_TEXT.has(el.type);
  return typeof HTMLElement !== "undefined" && el instanceof HTMLElement && el.isContentEditable === true;
}
