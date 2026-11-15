// Lane C: the directional sun, aimed from pack v2's baked sun direction
// (sun.ts, Lane A HANDOFF 22:20) so live shadows match the baked ones. The
// shadow box is a SUN_SHADOW_HALF_M square that follows the local player
// every frame (light at target + dir * 60 m), so the play area around them
// always has shadows without one huge, blurry shadow map over the block.
// The followed point is snapped to the shadow map's texel grid in light
// space (sun.ts snapToTexel), so the box moves in whole texels and shadow
// edges don't shimmer while walking. Indoors the same, with the house-frame
// sun direction.
// Runs at SUN_PRIORITY (-5): after physics and the camera, never positive.

import { useFrame } from "@react-three/fiber";
import { useEffect, useMemo, useRef } from "react";
import { Object3D, type DirectionalLight } from "three";

import { localPlayer } from "../player/playerControl";
import { useStore } from "../store";
import { SUN_PRIORITY } from "./frameOrder";
import {
  lightBasis,
  SUN_DIR_BLOCK,
  SUN_DISTANCE_M,
  SUN_SHADOW_HALF_M,
  SUN_SHADOW_MAP,
  SUN_TEXEL_M,
  snapToTexel,
  sunDirInHouse,
} from "./sun";
import type { Vec3 } from "./pack";

/** `headingDeg`: the current zone's heading (0 on the block, the house's heading indoors). */
export function SunLight({ headingDeg = 0, intensity = 1.2 }: { headingDeg?: number; intensity?: number }) {
  const light = useRef<DirectionalLight>(null);
  const target = useMemo(() => new Object3D(), []);
  const dir = useMemo<Vec3>(() => (headingDeg ? sunDirInHouse(headingDeg) : SUN_DIR_BLOCK), [headingDeg]);
  // The shadow camera's axes for this direction, built once; and a scratch point.
  const basis = useMemo(() => lightBasis(dir), [dir]);
  const snapped = useRef<Vec3>([0, 0, 0]);

  useEffect(() => {
    if (light.current) light.current.target = target;
  }, [target]);

  useFrame(() => {
    const l = light.current;
    if (!l) return;
    const at = (localPlayer()?.getPos() ?? useStore.getState().me.pos) as Vec3;
    const s = snapToTexel(at, basis, SUN_TEXEL_M, snapped.current);
    target.position.set(s[0], s[1], s[2]);
    target.updateMatrixWorld();
    // Light at target + dir * distance (sunPosition), written without allocating.
    l.position.set(s[0] + dir[0] * SUN_DISTANCE_M, s[1] + dir[1] * SUN_DISTANCE_M, s[2] + dir[2] * SUN_DISTANCE_M);
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
