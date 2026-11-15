// WeaponBar rendered with react-dom in jsdom, driven through the same stores
// the game writes: store.mode / players (Lane D), weaponState (keys 1/2),
// attackClock (Blaster).
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { type NetPlayer, useStore } from "../store";
import { markAttack, nowS, useAttackClock } from "./attackClock";
import { useWeaponState } from "./weaponState";
import { WeaponBar } from "./WeaponBar";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const me = (alive: boolean): NetPlayer => ({
  id: "me", name: "me", zone: "block", pos: [0, 0, 0], yaw: 0,
  anim: alive ? "idle" : "eliminated", hp: alive ? 3 : 0, score: 0, alive,
});

let root: Root;
let el: HTMLDivElement;
const bar = () => el.querySelector<HTMLElement>(".lc-wbar");
const selected = () => [...el.querySelectorAll<HTMLElement>('.lc-wslot[data-selected="true"]')].map((s) => s.dataset.weapon);

beforeEach(() => {
  useStore.getState().setMe({ id: "me" });
  useStore.getState().set({ mode: "foam", players: { me: me(true) } });
  useWeaponState.getState().setWeapon("blaster");
  useAttackClock.setState({ seq: 0, weapon: null, atS: null });
  el = document.createElement("div");
  document.body.appendChild(el);
  root = createRoot(el);
  act(() => root.render(<WeaponBar />));
});

afterEach(() => {
  act(() => root.unmount());
  el.remove();
  useStore.getState().set({ mode: "explore", players: {} });
  useStore.getState().setMe({ id: null });
});

describe("WeaponBar", () => {
  it("highlights exactly the selected weapon's slot", () => {
    expect(selected()).toEqual(["blaster"]);
    act(() => useWeaponState.getState().setWeapon("noodle"));
    expect(selected()).toEqual(["noodle"]);
    act(() => useWeaponState.getState().setWeapon("blaster"));
    expect(selected()).toEqual(["blaster"]);
  });

  it("shows slot keys and names in order", () => {
    const slots = [...el.querySelectorAll<HTMLElement>(".lc-wslot")];
    expect(slots.map((s) => s.querySelector(".lc-wkey")?.textContent)).toEqual(["1", "2"]);
    expect(slots.map((s) => s.querySelector(".lc-wname")?.textContent)).toEqual(["Foam blaster", "Pool noodle"]);
  });

  it("is hidden in explore, shown in foam, dimmed while eliminated", () => {
    expect(bar()?.dataset.view).toBe("shown");
    act(() => useStore.getState().set({ players: { me: me(false) } }));
    expect(bar()?.dataset.view).toBe("dimmed");
    act(() => useStore.getState().set({ players: { me: me(true) } }));
    expect(bar()?.dataset.view).toBe("shown");
    act(() => useStore.getState().set({ mode: "explore" }));
    expect(bar()).toBeNull();
  });

  it("hides while a text field has focus and comes back on blur", () => {
    const chat = document.createElement("textarea");
    document.body.appendChild(chat);
    act(() => chat.focus());
    expect(bar()).toBeNull();
    act(() => chat.blur());
    expect(bar()?.dataset.view).toBe("shown");
    chat.remove();
  });

  it("an attack puts a cooldown sweep on the selected slot only", () => {
    expect(el.querySelector(".lc-wcool")).toBeNull();
    act(() => markAttack("blaster", nowS()));
    const sweep = el.querySelector<HTMLElement>('.lc-wslot[data-weapon="blaster"] .lc-wcool');
    expect(sweep).not.toBeNull();
    expect(parseFloat(sweep!.style.height)).toBeGreaterThan(90);
    expect(el.querySelector('.lc-wslot[data-weapon="noodle"] .lc-wcool')).toBeNull();
  });

  it("a finished cooldown shows no sweep", () => {
    act(() => markAttack("noodle", nowS() - 5));
    expect(el.querySelector(".lc-wcool")).toBeNull();
  });
});
