// Lane C: crosshair and hit marker. A DOM overlay, rendered next to the
// canvas, shown only while pointer lock is active (that is when the blaster
// can fire). The marker: a white tick on a surface, a red X on a player.

import { useEffect, useState } from "react";

import { BLASTER } from "./blasterMath";
import { useBlasterHud } from "./blasterHud";
import { canAttackNow, useFoamMode, useMeAlive } from "./foamMode";

function usePointerLocked(): boolean {
  const [locked, setLocked] = useState(() => document.pointerLockElement instanceof HTMLCanvasElement);
  useEffect(() => {
    const onChange = () => setLocked(document.pointerLockElement instanceof HTMLCanvasElement);
    document.addEventListener("pointerlockchange", onChange);
    return () => document.removeEventListener("pointerlockchange", onChange);
  }, []);
  return locked;
}

const MARKER_MS = BLASTER.hitMarkerS * 1000;

export function Crosshair() {
  const locked = usePointerLocked();
  const marker = useBlasterHud((s) => s.marker);
  const foam = useFoamMode();
  const alive = useMeAlive();
  // Explore mode or eliminated: no crosshair, no hit marker (foamMode.ts).
  if (!locked || !canAttackNow(foam, alive)) return null;

  return (
    <div style={wrap} aria-hidden>
      <style>{`@keyframes lc-hit { from { opacity: 1; transform: scale(1.25); } to { opacity: 0; transform: scale(1); } }`}</style>
      <div style={{ ...dot }} />
      {marker ? (
        <div
          key={marker.seq}
          style={{
            ...markerBox,
            animation: `lc-hit ${MARKER_MS}ms ease-out forwards`,
          }}
        >
          {marker.kind === "player" ? <X color="#ff4d4d" size={26} /> : <X color="#ffffff" size={16} />}
        </div>
      ) : null}
    </div>
  );
}

function X({ color, size }: { color: string; size: number }) {
  const arm: React.CSSProperties = {
    position: "absolute",
    left: "50%",
    top: "50%",
    width: size,
    height: 2,
    background: color,
    boxShadow: "0 0 2px rgba(0,0,0,0.8)",
  };
  return (
    <>
      <div style={{ ...arm, transform: "translate(-50%, -50%) rotate(45deg)" }} />
      <div style={{ ...arm, transform: "translate(-50%, -50%) rotate(-45deg)" }} />
    </>
  );
}

const wrap: React.CSSProperties = {
  position: "absolute",
  inset: 0,
  pointerEvents: "none",
};

const dot: React.CSSProperties = {
  position: "absolute",
  left: "50%",
  top: "50%",
  width: 6,
  height: 6,
  marginLeft: -3,
  marginTop: -3,
  borderRadius: "50%",
  background: "#ffffff",
  boxShadow: "0 0 0 1.5px rgba(0,0,0,0.6)",
};

const markerBox: React.CSSProperties = {
  position: "absolute",
  left: "50%",
  top: "50%",
  width: 0,
  height: 0,
};
