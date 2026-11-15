// Lane C: door prompt and E key handler, a DOM overlay next to the canvas
// (like Crosshair).
// - Interior doors (indoors, within 1.5 m of a door midpoint): open / close.
// - Front doors (within 2.0 m of the door point): "Enter <address>" on the
//   block, "Leave house" inside. E runs the same zone switch as walking in
//   (world/frontDoorState press -> pack.ts pressFrontDoor -> crossDoor).
// When both are in range, the nearer wins and the interior door wins a tie.

import { useEffect } from "react";

import { useStore } from "../store";
import { canCloseDoor, doorPromptText, doorTargetAt, frontDoorPromptText } from "../world/doorInteract";
import { doorsFor, useDoorState } from "../world/doorState";
import { useFrontDoors } from "../world/frontDoorState";
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
  const activeDoors = useDoorState((s) => doorsFor(s, zone));
  const openDoors = useDoorState((s) => s.openDoors);
  const frontDoors = useFrontDoors((s) => s.doors);
  const addresses = useFrontDoors((s) => s.addresses);

  const target = doorTargetAt(zone, pos, activeDoors, frontDoors);

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.code !== "KeyE" && e.key !== "e" && e.key !== "E") return;
      if (e.repeat) return;
      if (isTextEntry(document.activeElement)) return;

      const { zone: currentZone, pos: currentPos } = useStore.getState().me;
      const front = useFrontDoors.getState();
      const t = doorTargetAt(
        currentZone,
        currentPos,
        doorsFor(useDoorState.getState(), currentZone),
        front.doors,
      );
      if (!t) return;

      if (t.kind === "front") {
        if (front.press?.()) e.preventDefault();
        return;
      }

      const { isOpen, toggle } = useDoorState.getState();
      // Don't close if the player is within 0.4 m of the door line.
      if (isOpen(t.door.id) && !canCloseDoor([currentPos[0], currentPos[2]], t.door)) return;
      e.preventDefault();
      toggle(t.door.id);
    };

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  if (!target) return null;

  const text =
    target.kind === "interior"
      ? doorPromptText(Boolean(openDoors[target.door.id]))
      : frontDoorPromptText(zone, target.door.listing_id, addresses[target.door.listing_id]);

  return (
    <div style={promptWrap} aria-live="polite">
      {text}
    </div>
  );
}
