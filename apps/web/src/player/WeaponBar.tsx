// Lane C: weapon hotbar. A DOM overlay next to the canvas, like Crosshair:
// two slots bottom centre (1 foam blaster, 2 pool noodle, from weapons.ts
// WEAPON_KEYS). The selected slot is bright and a little larger; the other is
// dimmed. After an attack the selected slot shows a bottom-up cooldown sweep.
//
// Shown only in foam mode, whether or not the pointer is locked (players see
// it before clicking in). Greyed to 40% while eliminated. Hidden while a text
// input or textarea has focus, so it never covers the chat panel.
// pointer-events: none throughout: it never eats a click.

import { useEffect, useState } from "react";

import { nowS, useAttackClock } from "./attackClock";
import { useFoamMode, useMeAlive } from "./foamMode";
import { barView, cooldownFraction, cooldownS, isTextEntry as isTextFocus, weaponSlots } from "./hotbar";
import { BlasterIcon, NoodleIcon, type IconProps } from "./weaponIcons";
import type { Weapon } from "../../../../contracts/game-state";
import { useWeaponState } from "./weaponState";

const SLOTS = weaponSlots();

const WEAPON_ICONS: Record<Weapon, (p: IconProps) => React.JSX.Element> = { blaster: BlasterIcon, noodle: NoodleIcon };

/** True while a text field has focus (the chat panel open). */
function useTyping(): boolean {
  const [typing, setTyping] = useState(() => isTextFocus(document.activeElement));
  useEffect(() => {
    const onIn = (e: FocusEvent) => setTyping(isTextFocus(e.target));
    // relatedTarget is where focus goes next (null: nowhere).
    const onOut = (e: FocusEvent) => setTyping(isTextFocus(e.relatedTarget));
    document.addEventListener("focusin", onIn);
    document.addEventListener("focusout", onOut);
    return () => {
      document.removeEventListener("focusin", onIn);
      document.removeEventListener("focusout", onOut);
    };
  }, []);
  return typing;
}

/** The selected weapon's remaining cooldown fraction, animated with rAF only while it runs. */
function useCooldown(selected: Weapon): number {
  const seq = useAttackClock((s) => s.seq);
  const atS = useAttackClock((s) => s.atS);
  const [frac, setFrac] = useState(0);
  useEffect(() => {
    // Same rule as Blaster's canAttack: the selected weapon's interval since the last attack.
    const tick = () => cooldownFraction(atS === null ? null : nowS() - atS, cooldownS(selected));
    let raf = 0;
    const loop = () => {
      const f = tick();
      setFrac(f);
      if (f > 0) raf = requestAnimationFrame(loop);
    };
    loop();
    return () => cancelAnimationFrame(raf);
  }, [seq, atS, selected]);
  return frac;
}

export function WeaponBar() {
  const foam = useFoamMode();
  const alive = useMeAlive();
  const typing = useTyping();
  const selected = useWeaponState((s) => s.weapon);
  const cooldown = useCooldown(selected);
  const view = barView(foam, alive, typing);
  if (view === "hidden") return null;

  return (
    <div className="lc-wbar" data-view={view} aria-hidden>
      <style>{CSS}</style>
      {SLOTS.map((slot) => {
        const on = slot.weapon === selected;
        const Icon = WEAPON_ICONS[slot.weapon];
        return (
          <div key={slot.code} className="lc-wslot" data-weapon={slot.weapon} data-selected={on}>
            <div className="lc-wbox">
              <span className="lc-wkey">{slot.key}</span>
              <Icon />
              {on && cooldown > 0 ? <div className="lc-wcool" style={{ height: `${cooldown * 100}%` }} /> : null}
            </div>
            <span className="lc-wname">{slot.name}</span>
          </div>
        );
      })}
    </div>
  );
}

const CSS = `
.lc-wbar { position: absolute; left: 50%; bottom: 18px; transform: translateX(-50%); transform-origin: bottom center;
  display: flex; gap: 10px; pointer-events: none; user-select: none; transition: opacity 120ms ease, filter 120ms ease;
  font-family: system-ui, sans-serif; }
.lc-wbar * { pointer-events: none; }
.lc-wbar[data-view="dimmed"] { opacity: 0.4; filter: grayscale(1); }
.lc-wslot { display: flex; flex-direction: column; align-items: center; gap: 4px; opacity: 0.55;
  transform: scale(1); transform-origin: bottom center; transition: transform 120ms ease, opacity 120ms ease; }
.lc-wslot[data-selected="true"] { opacity: 1; transform: scale(1.12); }
.lc-wbox { position: relative; width: 64px; height: 64px; border-radius: 14px; overflow: hidden;
  display: flex; align-items: center; justify-content: center; box-sizing: border-box;
  background: rgba(20, 24, 36, 0.62); border: 2px solid rgba(255, 255, 255, 0.25);
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.35); transition: border-color 120ms ease, box-shadow 120ms ease; }
.lc-wslot[data-selected="true"] .lc-wbox { border: 3px solid #ffe14d; box-shadow: 0 0 0 2px rgba(0,0,0,0.35), 0 0 12px rgba(255, 225, 77, 0.55); }
.lc-wkey { position: absolute; left: 6px; top: 3px; font-size: 12px; font-weight: 700; color: #fff;
  text-shadow: 0 1px 2px rgba(0, 0, 0, 0.8); z-index: 2; }
.lc-wcool { position: absolute; left: 0; right: 0; top: 0; background: rgba(0, 0, 0, 0.55); z-index: 1; }
.lc-wname { font-size: 11px; font-weight: 600; color: #fff; text-shadow: 0 1px 2px rgba(0, 0, 0, 0.9); white-space: nowrap; }
@media (max-width: 699px) { .lc-wbar { transform: translateX(-50%) scale(0.75); bottom: 10px; } }
`;
