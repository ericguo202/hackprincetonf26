// Lane C, dev only (DEV_FOCUS_TOGGLE_ON: a dev build with VITE_SOLO=1).
// B flips a local focus override between null and "hou_236595179". Never in a production build.

import { useEffect } from "react";
import { useDevFocusOverride } from "./devFocus";

export const DEV_FOCUS_KEY = "KeyB";

export function DevFocusToggle() {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = document.activeElement;
      if (
        e.code !== DEV_FOCUS_KEY ||
        e.repeat ||
        el instanceof HTMLInputElement ||
        el instanceof HTMLTextAreaElement ||
        (el as HTMLElement)?.isContentEditable
      ) {
        return;
      }
      useDevFocusOverride.getState().toggle();
      console.info(`[focus toggle] ${useDevFocusOverride.getState().override ?? "off"}`);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  return null;
}
