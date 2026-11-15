// Lane C: dart tracers and impact puffs, shared by the local Blaster and other
// players' shots (world/RemoteShots.tsx). Pooled meshes, mounted once and moved
// imperatively; call `step(dt)` once per frame from the owner's useFrame.

import { useRef } from "react";
import { Mesh, Quaternion, Vector3 } from "three";

import type { V3 } from "./armMath";
import { BLASTER, pointAt } from "./blasterMath";

export const FOAM = "#ff8a1f";
export const PUFF_S = 0.18;
const STREAK_M = 0.7;
const UP = new Vector3(0, 1, 0);

type Tracer = { active: boolean; from: V3; dir: V3; total: number; travelled: number };

/** A pool of `size` tracers: a short streak flying from `from` to `to`. */
export function useTracerPool(size: number) {
  const tracers = useRef<Tracer[]>(
    Array.from({ length: size }, () => ({ active: false, from: [0, 0, 0], dir: [0, 0, 1], total: 0, travelled: 0 })),
  );
  const next = useRef(0);
  const meshes = useRef<(Mesh | null)[]>([]);
  const tmpDir = useRef(new Vector3());
  const tmpQuat = useRef(new Quaternion());

  function spawn(from: V3, to: V3) {
    const v: V3 = [to[0] - from[0], to[1] - from[1], to[2] - from[2]];
    const total = Math.hypot(...v);
    if (total < 1e-3) return;
    const t = tracers.current[next.current];
    next.current = (next.current + 1) % size;
    t.active = true;
    t.from = from;
    t.dir = [v[0] / total, v[1] / total, v[2] / total];
    t.total = total;
    t.travelled = 0;
  }

  function step(dt: number) {
    tracers.current.forEach((t, i) => {
      const mesh = meshes.current[i];
      if (!mesh) return;
      if (!t.active) {
        mesh.visible = false;
        return;
      }
      t.travelled += BLASTER.tracerSpeed * dt;
      const head = Math.min(t.travelled, t.total);
      const tail = Math.max(0, t.travelled - STREAK_M);
      if (tail >= t.total) {
        t.active = false;
        mesh.visible = false;
        return;
      }
      const segLen = Math.max(0.01, head - tail);
      const mid = pointAt(t.from, t.dir, (head + tail) / 2);
      mesh.visible = true;
      mesh.position.set(...mid);
      tmpDir.current.set(...t.dir);
      mesh.quaternion.copy(tmpQuat.current.setFromUnitVectors(UP, tmpDir.current));
      mesh.scale.set(1, segLen, 1);
    });
  }

  const elements = Array.from({ length: size }, (_, i) => (
    <mesh
      key={`tracer-${i}`}
      visible={false}
      ref={(m) => {
        meshes.current[i] = m;
      }}
    >
      {/* Unit-length along +Y; scaled to the streak length each frame. */}
      <cylinderGeometry args={[0.012, 0.012, 1, 6]} />
      <meshBasicMaterial color={FOAM} toneMapped={false} />
    </mesh>
  ));

  return { spawn, step, elements };
}

/** A pool of `size` impact puffs: a foam ball that grows and vanishes over PUFF_S. */
export function usePuffPool(size: number) {
  const left = useRef<number[]>(Array.from({ length: size }, () => 0));
  const next = useRef(0);
  const meshes = useRef<(Mesh | null)[]>([]);

  function spawn(at: V3) {
    const i = next.current;
    next.current = (next.current + 1) % size;
    left.current[i] = PUFF_S;
    meshes.current[i]?.position.set(...at);
  }

  function step(dt: number) {
    left.current.forEach((l, i) => {
      const k = Math.max(0, l - dt);
      left.current[i] = k;
      const mesh = meshes.current[i];
      if (!mesh) return;
      const f = k / PUFF_S;
      mesh.visible = f > 0;
      mesh.scale.setScalar(0.05 + (1 - f) * 0.12);
    });
  }

  const elements = Array.from({ length: size }, (_, i) => (
    <mesh
      key={`puff-${i}`}
      visible={false}
      ref={(m) => {
        meshes.current[i] = m;
      }}
    >
      <sphereGeometry args={[1, 10, 8]} />
      <meshBasicMaterial color={FOAM} transparent opacity={0.7} toneMapped={false} />
    </mesh>
  ));

  return { spawn, step, elements };
}
