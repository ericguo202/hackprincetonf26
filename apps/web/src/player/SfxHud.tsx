// Lane C: sound wiring outside the canvas (sfx.ts). Mounted once in World.tsx
// next to the crosshair.
//   - The first pointer or key press unlocks (creates or resumes) the audio context.
//   - M toggles mute; a small "Sound off (M)" tag shows only while muted.
//   - Interior door opened or closed (doorState): a click, and a creak-lite on open.
//   - I get hit (store.hitsOnMe goes up; read only): a muffled thud.
// Shots, swings and pops are played where they happen (Blaster, RemoteShots).

import { useEffect, type CSSProperties } from "react";

import { useStore } from "../store";
import { useDoorState } from "../world/doorState";
import { play, unlockAudio, useSfxMute } from "./sfx";

export const MUTE_KEY_CODE = "KeyM";

export function SfxHud() {
  const muted = useSfxMute((s) => s.muted);

  useEffect(() => {
    const unlock = () => unlockAudio();
    const onKey = (e: KeyboardEvent) => {
      unlockAudio();
      const el = document.activeElement;
      if (e.code !== MUTE_KEY_CODE || e.repeat) return;
      if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement || (el as HTMLElement)?.isContentEditable) return;
      useSfxMute.getState().toggle();
    };
    window.addEventListener("pointerdown", unlock);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("pointerdown", unlock);
      window.removeEventListener("keydown", onKey);
    };
  }, []);

  // Doors: a click on every open/close flip. Doors forgotten on leaving a house make no sound.
  useEffect(
    () =>
      useDoorState.subscribe((s, prev) => {
        if (s.openDoors === prev.openDoors) return;
        for (const [id, open] of Object.entries(s.openDoors)) {
          if (open === Boolean(prev.openDoors[id])) continue;
          play(open ? "doorOpen" : "doorClose");
        }
      }),
    [],
  );

  // Hits on me (Lane D's counter, read only).
  useEffect(
    () =>
      useStore.subscribe((s, prev) => {
        if (s.hitsOnMe > prev.hitsOnMe) play("hurt");
      }),
    [],
  );

  return muted ? <div style={mutedStyle}>Sound off (M)</div> : null;
}

const mutedStyle: CSSProperties = {
  position: "absolute",
  right: 16,
  bottom: 16,
  padding: "3px 8px",
  borderRadius: 6,
  background: "rgba(16,20,26,0.6)",
  color: "rgba(232,234,237,0.8)",
  fontSize: 11,
  pointerEvents: "none",
};
