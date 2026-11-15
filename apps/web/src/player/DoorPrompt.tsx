// Lane C: Door interaction prompt and E key handler.
// A DOM overlay next to the canvas (like Crosshair), shown when the player
// is indoors and within 1.5 m of a door midpoint.

import { useEffect } from "react";

import { useStore } from "../store";
import {
  canCloseDoor,
  doorPromptText,
  nearestDoorInRange,
} from "../world/doorInteract";
import { useDoorState } from "../world/doorState";
import { isTextEntry } from "./hotbar";

const promptWrap: React.CSSProperties = {
  position: "fixed",
  top: "56%",
  left: "50%",
  transform: "translate(-50%, -50%)",
  pointerEvents: "none",
  background: "rgba(16, 20, 26, 0.75)",
  color: "#ffffff",
  padding: "6px 14px",
  borderRadius: 6,
  fontFamily: "system-ui, -apple-system, sans-serif",
  fontSize: 13,
  fontWeight: 600,
  letterSpacing: "0.5px",
  boxShadow: "0 2px 8px rgba(0, 0, 0, 0.3)",
  zIndex: 40,
  userSelect: "none",
};

export function DoorPrompt() {
  const zone = useStore((s) => s.me.zone);
  const pos = useStore((s) => s.me.pos);
  const activeDoors = useDoorState((s) => s.activeDoors);
  const openDoors = useDoorState((s) => s.openDoors);

  const isIndoor = zone !== "block";
  const playerXZ: [number, number] = [pos[0], pos[2]];

  const nearest = isIndoor ? nearestDoorInRange(playerXZ, activeDoors) : null;
  const door = nearest?.door ?? null;
  const isOpen = door ? Boolean(openDoors[door.id]) : false;

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.code !== "KeyE" && e.key !== "e" && e.key !== "E") return;
      if (e.repeat) return;
      if (isTextEntry(document.activeElement)) return;

      const currentZone = useStore.getState().me.zone;
      if (currentZone === "block") return;

      const currentPos = useStore.getState().me.pos;
      const currentXZ: [number, number] = [currentPos[0], currentPos[2]];
      const doors = useDoorState.getState().activeDoors;
      const target = nearestDoorInRange(currentXZ, doors);
      if (!target) return;

      const { isOpen, toggle } = useDoorState.getState();
      const open = isOpen(target.door.id);
      if (open) {
        // Don't close if player is within 0.4 m of the door line
        if (!canCloseDoor(currentXZ, target.door)) return;
      }
      e.preventDefault();
      toggle(target.door.id);
    };

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  if (!isIndoor || !door) return null;

  return (
    <div style={promptWrap} aria-live="polite">
      {doorPromptText(isOpen)}
    </div>
  );
}
