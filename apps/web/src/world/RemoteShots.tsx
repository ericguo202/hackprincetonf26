// Lane C: draw other players' shots from store.fx (AGENTS.md section 11), so
// you can see who is shooting at you. Lane D's net code writes the latest 8
// `fx` broadcasts; this reads them, plays each id once, and never writes.
//
// - On mount every id already in the list is marked seen: a reload or a
//   remount never replays old shots.
// - An event is drawn only if its origin is near a remote player drawn in my
//   zone this frame (remoteFx.fxInMyZone); otherwise it is dropped for good.
// - blaster: the same tracer and impact puff as the local Blaster (shotFx.tsx),
//   cast from the origin along dir against zone + furniture, 40 m. No flash.
// - noodle: a fading 60-degree arc at head height, centred on the swing.
// fx only arrives in foam mode, so there is no extra gating here.

import { useRef } from "react";
import { useFrame } from "@react-three/fiber";
import { useRapier } from "@react-three/rapier";
import { DoubleSide, type Group, type MeshBasicMaterial } from "three";

import { useStore } from "../store";
import { BLASTER, pointAt } from "../player/blasterMath";
import { FOAM, usePuffPool, useTracerPool } from "../player/shotFx";
import { SHOT_QUERY } from "./collisionGroups";
import { REMOTE_FX_PRIORITY } from "./frameOrder";
import { fxInMyZone, seenIds, swooshYaw, takeUnseen, unitDir } from "./remoteFx";
import { remoteBodies } from "./remoteTargets";
import type { Vec3 } from "./remoteInterp";

const TRACERS = 8;
const PUFFS = 4;
const SWOOSH_S = 0.25;
const SWOOSH_INNER_M = 1.1;
const SWOOSH_OUTER_M = 1.5;
const SWOOSH_ARC = Math.PI / 3; // 60 degrees

export function RemoteShots() {
  const { world, rapier } = useRapier();
  const seen = useRef<Set<number> | null>(null);
  seen.current ??= seenIds(useStore.getState().fx);
  const lastList = useRef(useStore.getState().fx);
  const rapierRay = useRef<InstanceType<typeof rapier.Ray> | null>(null);

  const tracers = useTracerPool(TRACERS);
  const puffs = usePuffPool(PUFFS);
  const swoosh = useRef<Group>(null);
  const swooshMat = useRef<MeshBasicMaterial>(null);
  const swooshLeft = useRef(0);

  function castWorld(origin: Vec3, dir: Vec3): number | null {
    rapierRay.current ??= new rapier.Ray({ x: 0, y: 0, z: 0 }, { x: 0, y: 0, z: 1 });
    const r = rapierRay.current;
    r.origin = { x: origin[0], y: origin[1], z: origin[2] };
    r.dir = { x: dir[0], y: dir[1], z: dir[2] };
    const hit = world.castRay(r, BLASTER.rangeM, true, undefined, SHOT_QUERY);
    return hit ? hit.timeOfImpact : null;
  }

  function playSwoosh(origin: Vec3, dir: Vec3) {
    const g = swoosh.current;
    const yaw = swooshYaw(dir);
    if (!g || yaw === null) return;
    g.position.set(...origin);
    g.rotation.set(0, yaw, 0);
    swooshLeft.current = SWOOSH_S;
  }

  useFrame((_, dt) => {
    const step = Math.min(dt, 0.1);
    const list = useStore.getState().fx;
    if (list !== lastList.current && seen.current) {
      lastList.current = list;
      const bodies = remoteBodies();
      for (const e of takeUnseen(list, seen.current)) {
        const dir = unitDir(e.dir);
        if (!dir || !fxInMyZone(e, bodies)) continue;
        if (e.kind === "noodle") {
          playSwoosh(e.origin, dir);
        } else {
          const t = castWorld(e.origin, dir);
          const end = pointAt(e.origin, dir, t ?? BLASTER.rangeM);
          tracers.spawn(e.origin, end);
          if (t !== null) puffs.spawn(end);
        }
      }
    }

    tracers.step(step);
    puffs.step(step);

    swooshLeft.current = Math.max(0, swooshLeft.current - step);
    const k = swooshLeft.current / SWOOSH_S;
    if (swoosh.current) {
      swoosh.current.visible = k > 0;
      swoosh.current.scale.setScalar(0.85 + (1 - k) * 0.15);
    }
    if (swooshMat.current) swooshMat.current.opacity = 0.6 * k;
  }, REMOTE_FX_PRIORITY);

  return (
    <>
      {tracers.elements}
      {puffs.elements}
      <group ref={swoosh} visible={false}>
        {/* Ring in XY centred on +X; laid flat so the arc is horizontal, centred on local +X. */}
        <mesh rotation-x={-Math.PI / 2}>
          <ringGeometry args={[SWOOSH_INNER_M, SWOOSH_OUTER_M, 16, 1, -SWOOSH_ARC / 2, SWOOSH_ARC]} />
          <meshBasicMaterial
            ref={swooshMat}
            color={FOAM}
            transparent
            opacity={0}
            side={DoubleSide}
            depthWrite={false}
            toneMapped={false}
          />
        </mesh>
      </group>
    </>
  );
}
