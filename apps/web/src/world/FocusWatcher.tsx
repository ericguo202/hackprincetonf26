// Lane C: focus teleport (HANDOFF lane-b 16:45, lane-d 21:58 item 2, lane-c 01:10).
// When the agent's focus_listing CHANGES my state.focus entry, put me on the
// block at doorApproach(): 1.5 m outside that house's front door, facing it.
// Renders nothing. When to act: focusWatch.ts (pure, tested).
//
// Indoors: the same goTo("block", ...) a door exit uses, so net/game.ts sees
// me.zone change and sends enter_zone before the first move in the block frame.
// On the block: localPlayer().teleport() and setYaw(), no remount. Either way
// it happens in this store callback, so the next 20 Hz move falls inside the
// server's 1.5 s teleport window after a focus change (contracts v2.4.1).
//
// Dev (DEV_FOCUS_TOGGLE_ON, VITE_SOLO): the B toggle's override counts as my
// focus too, so this can be tried without a server.

import { useEffect, useRef } from "react";

import { localPlayer } from "../player/playerControl";
import { useStore } from "../store";
import { DEV_FOCUS_TOGGLE_ON } from "./devFlags";
import { useDevFocusOverride } from "./devFocus";
import { doorApproach } from "./doorApproach";
import { effectiveFocus } from "./focusBeaconLogic";
import { DEV_SOLO_SESSION, FOCUS_WATCH_START, type FocusInput, stepFocusWatch } from "./focusWatch";
import type { LoadedPack, Vec3 } from "./pack";

type Props = {
  pack: LoadedPack;
  /** Zones' zone switch (setMe, setZone, setSpawn, remount); `at` is a floor point. */
  goTo: (zone: string, at: Vec3, yaw?: number) => void;
  /** Turn the door trigger off until the player walks clear: we land next to a door. */
  disarmDoors: () => void;
};

function readInput(): FocusInput {
  const s = useStore.getState();
  const override = DEV_FOCUS_TOGGLE_ON ? useDevFocusOverride.getState().override : null;
  const session = s.connected && s.me.id ? s.me.id : DEV_FOCUS_TOGGLE_ON ? DEV_SOLO_SESSION : null;
  return { session, focus: effectiveFocus(s.me.id, s.focus, override) };
}

export function FocusWatcher({ pack, goTo, disarmDoors }: Props) {
  const watch = useRef(FOCUS_WATCH_START);

  useEffect(() => {
    const known = (id: string) => pack.doors.some((d) => d.listing_id === id);
    const check = () => {
      const { state, goTo: listing } = stepFocusWatch(watch.current, readInput(), known);
      watch.current = state;
      if (!listing) return;
      const s = useStore.getState();
      if (s.me.id && s.players[s.me.id]?.alive === false) return; // eliminated: the respawn owns the body
      const door = pack.doors.find((d) => d.listing_id === listing);
      if (!door) return;
      const { pos, yaw } = doorApproach(door, pack.roomsByHouse[listing] ?? []);
      disarmDoors();
      const player = localPlayer();
      if (s.me.zone === "block" && player) {
        player.teleport(pos);
        player.setYaw?.(yaw);
      } else {
        // goTo takes a floor point and lifts it to the body centre itself.
        goTo("block", [pos[0], door.block[1], pos[2]], yaw);
      }
    };
    check(); // baseline on mount
    const unStore = useStore.subscribe(check);
    const unDev = DEV_FOCUS_TOGGLE_ON ? useDevFocusOverride.subscribe(check) : () => {};
    return () => {
      unStore();
      unDev();
    };
  }, [pack, goTo, disarmDoors]);

  return null;
}
