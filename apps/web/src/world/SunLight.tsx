// Lane C: the directional sun, aimed from pack v2's baked sun direction
// (sun.ts, Lane A HANDOFF 22:20) so live shadows match the baked ones. The
// shadow box is a SUN_SHADOW_HALF_M square that follows the local player
// every frame (light at target + dir * 60 m), so the play area around them
// always has shadows without one huge, blurry shadow map over the block.
// Runs at SUN_PRIORITY (-5): after physics and the camera, never positive.

import { useFrame } from "@react-three/fiber";
import { useEffect, useMemo, useRef } from "react";
import { Object3D, type DirectionalLight } from "three";

import { localPlayer } from "../player/playerControl";
import { useStore } from "../store";
import { SUN_PRIORITY } from "./frameOrder";
import { SUN_DIR_BLOCK, SUN_SHADOW_HALF_M, SUN_SHADOW_MAP, sunDirInHouse, sunPosition } from "./sun";
import type { Vec3 } from "./pack";

/** `headingDeg`: the current zone's heading (0 on the block, the house's heading indoors). */
export function SunLight({ headingDeg = 0, intensity = 1.2 }: { headingDeg?: number; intensity?: number }) {
  const light = useRef<DirectionalLight>(null);
  const target = useMemo(() => new Object3D(), []);
  const dir = useMemo<Vec3>(() => (headingDeg ? sunDirInHouse(headingDeg) : SUN_DIR_BLOCK), [headingDeg]);

  useEffect(() => {
    if (light.current) light.current.target = target;
  }, [target]);

  useFrame(() => {
    const l = light.current;
    if (!l) return;
    const at = (localPlayer()?.getPos() ?? useStore.getState().me.pos) as Vec3;
    target.position.set(at[0], at[1], at[2]);
    target.updateMatrixWorld();
    const p = sunPosition(at, dir);
    l.position.set(p[0], p[1], p[2]);
  }, SUN_PRIORITY);

  const h = SUN_SHADOW_HALF_M;
  return (
    <>
      <primitive object={target} />
      <directionalLight
        ref={light}
        intensity={intensity}
        castShadow
        shadow-mapSize={[SUN_SHADOW_MAP, SUN_SHADOW_MAP]}
        shadow-camera-left={-h}
        shadow-camera-right={h}
        shadow-camera-top={h}
        shadow-camera-bottom={-h}
        shadow-camera-near={1}
        shadow-camera-far={140}
        shadow-bias={-0.0005}
        shadow-normalBias={0.02}
      />
    </>
  );
}
