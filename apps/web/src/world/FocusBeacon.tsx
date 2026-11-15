// Lane C: focus beacon on the block (HANDOFF lane-b 16:45, lane-d 21:58).
// When my state.focus entry names a house on this block, draws a translucent
// column of light at that house's block door marker (beaconPoint: 4.2 m out,
// in front of the real facade) with a "Your match" label.
// Hidden when focus is unset or inside a house. No colliders.

import { useRef, type CSSProperties } from "react";
import { useFrame } from "@react-three/fiber";
import { AdditiveBlending, DoubleSide, type MeshBasicMaterial } from "three";

import { useStore } from "../store";
import { DEV_FOCUS_TOGGLE_ON } from "./devFlags";
import { useDevFocusOverride } from "./devFocus";
import { BEACON_HEIGHT_M, BEACON_LABEL_Y_M, BEACON_RADIUS_M, beaconPoint, effectiveFocus } from "./focusBeaconLogic";
import type { LoadedPack } from "./pack";
import { WorldLabel } from "./WorldLabel";

type Props = {
  pack: LoadedPack;
};

export function FocusBeacon({ pack }: Props) {
  const zone = useStore((s) => s.me.zone);
  const meId = useStore((s) => s.me.id);
  const focus = useStore((s) => s.focus);
  const devOverride = useDevFocusOverride((s) => (DEV_FOCUS_TOGGLE_ON ? s.override : null));

  const matRef = useRef<MeshBasicMaterial>(null);

  // Gently pulse opacity over time (priority 0, never positive).
  useFrame((state) => {
    if (!matRef.current) return;
    const pulse = Math.sin(state.clock.elapsedTime * 2.5);
    matRef.current.opacity = 0.35 + 0.15 * pulse;
  }, 0);

  // Draw nothing when inside a house.
  if (zone !== "block") return null;

  const targetHouse = effectiveFocus(meId, focus, devOverride);
  if (!targetHouse) return null;

  const door = pack.doors.find((d) => d.listing_id === targetHouse);
  if (!door) return null;

  const [x, y, z] = beaconPoint(door);

  return (
    <group position={[x, y, z]}>
      {/* 30 m column centered vertically at +15 m so base sits at the marker */}
      <mesh position={[0, BEACON_HEIGHT_M / 2, 0]}>
        <cylinderGeometry args={[BEACON_RADIUS_M, BEACON_RADIUS_M, BEACON_HEIGHT_M, 32]} />
        <meshBasicMaterial
          ref={matRef}
          color="#ffd54f"
          transparent
          opacity={0.35}
          blending={AdditiveBlending}
          depthWrite={false}
          side={DoubleSide}
        />
      </mesh>
      <WorldLabel position={[0, BEACON_LABEL_Y_M, 0]} style={matchLabelStyle} range={{ fadeFrom: 40, fadeTo: 60 }}>
        Your match
      </WorldLabel>
    </group>
  );
}

const matchLabelStyle: CSSProperties = {
  background: "rgba(16, 20, 26, 0.85)",
  color: "#ffd54f",
  border: "1px solid rgba(255, 213, 79, 0.4)",
  padding: "4px 10px",
  borderRadius: 6,
  fontFamily: "system-ui, -apple-system, sans-serif",
  fontSize: 13,
  fontWeight: 600,
  lineHeight: 1.3,
  pointerEvents: "none",
  textAlign: "center",
  boxShadow: "0 2px 8px rgba(0, 0, 0, 0.4)",
};
