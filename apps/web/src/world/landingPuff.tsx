// Lane C: a puff of pale dust when a furniture piece finishes its drop-in
// (FurnitureLayer): a flat ring on the floor expanding around the footprint,
// plus a few small bits kicked outward, all fading over LANDING_S. Scaled to
// the piece's footprint (size_m). Pooled: rings and bits are mounted once and
// reused oldest-first; allocation-free per frame. Purely visual: it touches
// no collider, push-out or drop-in timing. Pure parts tested in
// landingPuff.test.ts.

import { useMemo, useRef, useState } from "react";
import type { Mesh, MeshBasicMaterial } from "three";

import { BIT_M, bitFade, createBurstState, stepBurst, type BurstState } from "../player/shotFx";

/** How long the puff lasts, seconds. */
export const LANDING_S = 0.35;
/** Bits per landing (6 to 10, more for bigger pieces). */
export const LANDING_BITS_MIN = 6;
export const LANDING_BITS_MAX = 10;
/** Rings and bits pooled (a whole room can land within a second or two). */
export const LANDING_RINGS = 6;
export const LANDING_BIT_POOL = 60;
const DUST = "#efe6d6";
const RING_OPACITY = 0.6;

/** Half the footprint's diagonal-ish size: the ring starts at the piece's edge. */
export function footprintRadius(w: number, d: number): number {
  return Math.max(0.15, Math.hypot(w, d) / 2);
}

/** Bits for a footprint: LANDING_BITS_MIN for small pieces, up to LANDING_BITS_MAX for a 2.5 m one. */
export function landingBitCount(radius: number): number {
  const n = Math.round(LANDING_BITS_MIN + (LANDING_BITS_MAX - LANDING_BITS_MIN) * Math.min(1, radius / 1.25));
  return Math.max(LANDING_BITS_MIN, Math.min(LANDING_BITS_MAX, n));
}

export type Ring = { age: number; x: number; y: number; z: number; r0: number };
export type PuffState = { rings: Ring[]; nextRing: number; bits: BurstState };

export function createPuffState(rings = LANDING_RINGS, bits = LANDING_BIT_POOL): PuffState {
  return {
    rings: Array.from({ length: rings }, () => ({ age: Infinity, x: 0, y: 0, z: 0, r0: 0 })),
    nextRing: 0,
    bits: createBurstState(bits),
  };
}

/** The ring `age` s into a puff: radius (from r0 to 1.6 r0, easing out) and opacity; null when done. */
export function ringAt(age: number, r0: number): { radius: number; opacity: number } | null {
  if (!(age >= 0) || age >= LANDING_S) return null;
  const f = age / LANDING_S;
  const ease = 1 - (1 - f) * (1 - f);
  return { radius: r0 * (1 + 0.6 * ease), opacity: RING_OPACITY * (1 - f) };
}

/**
 * Start a puff at floor point `at` (house frame) around a footprint of
 * radius `r`: the oldest ring, and landingBitCount(r) bits from the oldest
 * slots, launched outward from the footprint edge, low and quick.
 */
export function spawnLanding(s: PuffState, at: [number, number, number], r: number, rand: () => number = Math.random): void {
  const ring = s.rings[s.nextRing];
  s.nextRing = (s.nextRing + 1) % s.rings.length;
  ring.age = 0;
  ring.x = at[0];
  ring.y = at[1];
  ring.z = at[2];
  ring.r0 = r;

  const b = s.bits;
  const count = Math.min(landingBitCount(r), b.bits.length);
  const speed = 0.9 + 0.5 * Math.min(1, r);
  for (let k = 0; k < count; k++) {
    const bit = b.bits[b.next];
    b.next = (b.next + 1) % b.bits.length;
    const a = ((k + rand() * 0.6) / count) * 2 * Math.PI;
    const cx = Math.cos(a);
    const cz = Math.sin(a);
    bit.life = LANDING_S;
    bit.maxLife = LANDING_S;
    bit.size = 0.6 + 0.5 * rand();
    bit.x = at[0] + cx * r * 0.9;
    bit.y = at[1] + 0.03;
    bit.z = at[2] + cz * r * 0.9;
    const v = speed * (0.7 + 0.6 * rand());
    bit.vx = cx * v;
    bit.vy = 0.6 + 0.8 * rand();
    bit.vz = cz * v;
  }
}

/** Advance rings and bits by dt. */
export function stepPuffs(s: PuffState, dt: number): void {
  if (!(dt > 0)) return;
  for (const r of s.rings) if (r.age < LANDING_S) r.age += dt;
  stepBurst(s.bits, dt);
}

/** The pool's meshes and its stable spawn/step. Mount `elements` in the house frame. */
export function useLandingPuffs() {
  const [state] = useState(createPuffState);
  const ringMeshes = useRef<(Mesh | null)[]>([]);
  const ringMats = useRef<(MeshBasicMaterial | null)[]>([]);
  const bitMeshes = useRef<(Mesh | null)[]>([]);

  const api = useMemo(
    () => ({
      spawn(at: [number, number, number], radius: number) {
        spawnLanding(state, at, radius);
      },
      step(dt: number) {
        stepPuffs(state, dt);
        state.rings.forEach((r, i) => {
          const m = ringMeshes.current[i];
          if (!m) return;
          const now = ringAt(r.age, r.r0);
          m.visible = now !== null;
          if (!now) return;
          m.position.set(r.x, r.y + 0.02, r.z);
          m.scale.setScalar(now.radius);
          const mat = ringMats.current[i];
          if (mat) mat.opacity = now.opacity;
        });
        const bits = state.bits.bits;
        for (let i = 0; i < bits.length; i++) {
          const m = bitMeshes.current[i];
          if (!m) continue;
          const b = bits[i];
          const f = bitFade(b);
          m.visible = f > 0;
          if (f <= 0) continue;
          m.position.set(b.x, b.y, b.z);
          m.scale.setScalar(BIT_M * b.size * f);
        }
      },
    }),
    [state],
  );

  const elements = (
    <>
      {state.rings.map((_, i) => (
        <mesh
          key={`land-ring-${i}`}
          visible={false}
          rotation-x={-Math.PI / 2}
          renderOrder={1}
          ref={(m) => {
            ringMeshes.current[i] = m;
          }}
        >
          {/* Unit outer radius; scaled to the ring radius. */}
          <ringGeometry args={[0.7, 1, 40]} />
          <meshBasicMaterial
            ref={(m) => {
              ringMats.current[i] = m;
            }}
            color={DUST}
            transparent
            opacity={RING_OPACITY}
            depthWrite={false}
            toneMapped={false}
          />
        </mesh>
      ))}
      {state.bits.bits.map((_, i) => (
        <mesh
          key={`land-bit-${i}`}
          visible={false}
          ref={(m) => {
            bitMeshes.current[i] = m;
          }}
        >
          <boxGeometry args={[1, 1, 1]} />
          <meshBasicMaterial color={DUST} toneMapped={false} />
        </mesh>
      ))}
    </>
  );

  return { spawn: api.spawn, step: api.step, elements };
}
