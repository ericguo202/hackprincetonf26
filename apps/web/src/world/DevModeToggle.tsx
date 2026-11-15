// Lane C, dev only (DEV_MODE_TOGGLE_ON: a dev build with VITE_SOLO=1, where
// no server ever changes store.mode). F flips a local mode override between
// explore and foam (player/foamMode.ts). Never in a production build.

import { useEffect } from "react";

import { useStore } from "../store";
import { effectiveMode, useModeOverride } from "../player/foamMode";

export const DEV_MODE_TOGGLE_KEY = "KeyF";

export function DevModeToggle() {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = document.activeElement;
      if (e.code !== DEV_MODE_TOGGLE_KEY || e.repeat || el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) return;
      const { override, set } = useModeOverride.getState();
      const now = effectiveMode(useStore.getState().mode, override);
      const next = now === "foam" ? "explore" : "foam";
      set(next);
      console.info(`[dev mode toggle] ${next}`);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  return null;
}
