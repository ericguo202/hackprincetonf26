// Lane C: dart tracers and impact puffs, shared by the local Blaster and other
// players' shots (world/RemoteShots.tsx). Pooled meshes, mounted once and moved
// imperatively; call `step(dt)` once per frame from the owner's useFrame.

import { useMemo, useRef, useState } from "react";
import { Mesh, Quaternion, Vector3, type MeshBasicMaterial } from "three";

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

// ---------------------------------------------------------------------------
// Foam splat bursts: small orange and white foam bits flying out of a hit, in
// the hemisphere facing the shooter, falling under gravity, shrinking away
// over BURST_S. The pool logic is pure (tested in shotFx.test.ts); the hook
// below only mirrors it onto pooled meshes. Allocation-free per frame.
// ---------------------------------------------------------------------------

/** Lifetime of one foam bit, seconds. */
export const BURST_S = 0.4;
/** Bits in a hit splat (10 to 14). */
export const HIT_BITS = 12;
/** Downward acceleration on the bits, m/s^2. */
export const BURST_GRAVITY = 9.8;
/** Launch speed range, m/s. */
export const BIT_SPEED_MIN = 1.6;
export const BIT_SPEED_MAX = 3.4;
/** Edge of a foam bit, metres, at launch. */
export const BIT_M = 0.04;
const FOAM_WHITE = "#fff6ea";
const FOAM_YELLOW = "#fff1a6";

/** life counts down from maxLife; size scales BIT_M. */
export type Bit = {
  life: number;
  maxLife: number;
  size: number;
  x: number;
  y: number;
  z: number;
  vx: number;
  vy: number;
  vz: number;
};
export type BurstState = { bits: Bit[]; next: number };

export function createBurstState(size: number): BurstState {
  return {
    bits: Array.from({ length: size }, () => ({ life: 0, maxLife: BURST_S, size: 1, x: 0, y: 0, z: 0, vx: 0, vy: 0, vz: 0 })),
    next: 0,
  };
}

/** Unit vector from `at` toward `shooter`; straight up if they coincide. */
export function facing(at: V3, shooter: V3): V3 {
  const d: V3 = [shooter[0] - at[0], shooter[1] - at[1], shooter[2] - at[2]];
  const l = Math.hypot(d[0], d[1], d[2]);
  return l > 1e-6 ? [d[0] / l, d[1] / l, d[2] / l] : [0, 1, 0];
}

/**
 * A direction in the hemisphere around unit `n`, uniform over its area, from
 * two uniforms in [0, 1). Written into `out` (no allocation).
 */
export function hemisphereDir(n: V3, u1: number, u2: number, out: V3): V3 {
  // Any vector not parallel to n, then an orthonormal basis (t, b, n).
  const ax = Math.abs(n[0]) < 0.9 ? 1 : 0;
  const ay = ax ? 0 : 1;
  // t = normalize(a x n)
  let tx = ay * n[2];
  let ty = -ax * n[2];
  let tz = ax * n[1] - ay * n[0];
  const tl = Math.hypot(tx, ty, tz);
  tx /= tl;
  ty /= tl;
  tz /= tl;
  // b = n x t
  const bx = n[1] * tz - n[2] * ty;
  const by = n[2] * tx - n[0] * tz;
  const bz = n[0] * ty - n[1] * tx;
  const z = u1; // cos(theta), uniform in [0, 1): uniform over the hemisphere
  const r = Math.sqrt(Math.max(0, 1 - z * z));
  const phi = 2 * Math.PI * u2;
  const c = r * Math.cos(phi);
  const s = r * Math.sin(phi);
  out[0] = tx * c + bx * s + n[0] * z;
  out[1] = ty * c + by * s + n[1] * z;
  out[2] = tz * c + bz * s + n[2] * z;
  return out;
}

const tmpLaunch: V3 = [0, 0, 0];

/** Launch `count` bits from `at` into the hemisphere facing `shooter`, reusing the oldest slots. */
export function spawnBurst(
  state: BurstState,
  at: V3,
  shooter: V3,
  count: number,
  speedScale = 1,
  rand: () => number = Math.random,
): void {
  const n = facing(at, shooter);
  const size = state.bits.length;
  for (let k = 0; k < Math.min(count, size); k++) {
    const b = state.bits[state.next];
    state.next = (state.next + 1) % size;
    hemisphereDir(n, rand(), rand(), tmpLaunch);
    const speed = (BIT_SPEED_MIN + (BIT_SPEED_MAX - BIT_SPEED_MIN) * rand()) * speedScale;
    b.life = BURST_S;
    b.maxLife = BURST_S;
    b.size = 1;
    b.x = at[0];
    b.y = at[1];
    b.z = at[2];
    b.vx = tmpLaunch[0] * speed;
    b.vy = tmpLaunch[1] * speed;
    b.vz = tmpLaunch[2] * speed;
  }
}

/** Advance every live bit by dt: gravity, motion, life. */
export function stepBurst(state: BurstState, dt: number): void {
  if (!(dt > 0)) return;
  for (const b of state.bits) {
    if (b.life <= 0) continue;
    b.life = Math.max(0, b.life - dt);
    b.vy -= BURST_GRAVITY * dt;
    b.x += b.vx * dt;
    b.y += b.vy * dt;
    b.z += b.vz * dt;
  }
}

/** Size factor of a bit: 1 at launch, 0 when it is gone (the fade). */
export function bitFade(b: Bit): number {
  return b.maxLife > 0 ? Math.max(0, b.life / b.maxLife) : 0;
}

// ---------------------------------------------------------------------------
// Elimination "foam pop": a big burst of bits mostly up and out from the body
// centre, plus a foam ring expanding on the floor at the feet.
// ---------------------------------------------------------------------------

/** Bits in a pop I scored; one I only saw gets POP_OTHER_SHARE of that. */
export const POP_BITS = 45;
export const POP_OTHER_SHARE = 0.6;
/** Pop bits: lifetime (s), launch speed (m/s), size relative to a splat bit. */
export const POP_S = 0.8;
export const POP_SPEED_MIN = 3;
export const POP_SPEED_MAX = 6;
export const POP_BIT_SIZE = 1.5;
/** Lowest cos(angle from straight up) a pop bit launches at: mostly upward, some outward. */
export const POP_MIN_UP = 0.35;
/** Shockwave ring on the floor: radius from/to (m) over RING_S, fading out. */
export const RING_FROM_M = 0.3;
export const RING_TO_M = 2.0;
export const RING_S = 0.35;
export const RING_OPACITY = 0.85;

const UP_V3: V3 = [0, 1, 0];

/** Bits in a pop: all of POP_BITS for mine, POP_OTHER_SHARE of it for one I only saw. */
export function popBitCount(mine: boolean): number {
  return mine ? POP_BITS : Math.round(POP_BITS * POP_OTHER_SHARE);
}

/** Launch a foam pop of `count` bits from `centre`: up and outward, POP_S long, POP_BIT_SIZE big. */
export function spawnPop(state: BurstState, centre: V3, count: number, rand: () => number = Math.random): void {
  const size = state.bits.length;
  for (let k = 0; k < Math.min(count, size); k++) {
    const b = state.bits[state.next];
    state.next = (state.next + 1) % size;
    // cos(theta) in [POP_MIN_UP, 1): a cap around straight up.
    hemisphereDir(UP_V3, POP_MIN_UP + (1 - POP_MIN_UP) * rand(), rand(), tmpLaunch);
    const speed = POP_SPEED_MIN + (POP_SPEED_MAX - POP_SPEED_MIN) * rand();
    b.life = POP_S;
    b.maxLife = POP_S;
    b.size = POP_BIT_SIZE;
    b.x = centre[0];
    b.y = centre[1];
    b.z = centre[2];
    b.vx = tmpLaunch[0] * speed;
    b.vy = tmpLaunch[1] * speed;
    b.vz = tmpLaunch[2] * speed;
  }
}

/** The ring `age` seconds after a pop: radius and opacity, or null once it is done (or before it starts). */
export function ringAt(age: number): { radius: number; opacity: number } | null {
  if (!(age >= 0) || age >= RING_S) return null;
  const f = age / RING_S;
  const ease = 1 - (1 - f) * (1 - f); // fast out, slowing
  return { radius: RING_FROM_M + (RING_TO_M - RING_FROM_M) * ease, opacity: RING_OPACITY * (1 - f) };
}

/** Bit colours by pool slot: hit splats are orange and white; pops add a few pale yellow. */
function bitColour(i: number, withYellow: boolean): string {
  if (withYellow && i % 7 === 3) return FOAM_YELLOW;
  return i % 3 === 0 ? FOAM_WHITE : FOAM;
}

/**
 * A pool of `size` foam bits. spawn(at, shooter, count) starts a hit splat,
 * pop(centre, count) a foam pop; both share the slots. spawn, pop and step are stable.
 */
export function useFoamBurstPool(size: number, withYellow = false) {
  const [state] = useState(() => createBurstState(size));
  const meshes = useRef<(Mesh | null)[]>([]);

  const api = useMemo(
    () => ({
      spawn(at: V3, shooter: V3, count: number, speedScale = 1) {
        spawnBurst(state, at, shooter, count, speedScale);
      },
      pop(centre: V3, count: number) {
        spawnPop(state, centre, count);
      },
      step(dt: number) {
        stepBurst(state, dt);
        for (let i = 0; i < state.bits.length; i++) {
          const mesh = meshes.current[i];
          if (!mesh) continue;
          const b = state.bits[i];
          const f = bitFade(b);
          mesh.visible = f > 0;
          if (f <= 0) continue;
          mesh.position.set(b.x, b.y, b.z);
          mesh.scale.setScalar(BIT_M * b.size * f);
          mesh.rotation.x += dt * 9;
          mesh.rotation.y += dt * 7;
        }
      },
    }),
    [state],
  );

  const elements = Array.from({ length: size }, (_, i) => (
    <mesh
      key={`bit-${i}`}
      visible={false}
      ref={(m) => {
        meshes.current[i] = m;
      }}
    >
      <boxGeometry args={[1, 1, 1]} />
      <meshBasicMaterial color={bitColour(i, withYellow)} toneMapped={false} />
    </mesh>
  ));

  return { spawn: api.spawn, pop: api.pop, step: api.step, elements };
}

/** The pop's floor ring: one pooled mesh. start(feet) restarts it; step(dt) each frame. Stable. */
export function useShockRing() {
  const mesh = useRef<Mesh | null>(null);
  const mat = useRef<MeshBasicMaterial | null>(null);
  const age = useRef(Infinity);
  const api = useMemo(
    () => ({
      start(feet: V3) {
        age.current = 0;
        mesh.current?.position.set(feet[0], feet[1] + 0.03, feet[2]);
      },
      step(dt: number) {
        if (dt > 0) age.current += dt;
        const r = ringAt(age.current);
        const m = mesh.current;
        if (!m) return;
        m.visible = r !== null;
        if (!r) return;
        m.scale.setScalar(r.radius);
        if (mat.current) mat.current.opacity = r.opacity;
      },
    }),
    [],
  );
  const element = (
    <mesh ref={mesh} visible={false} rotation-x={-Math.PI / 2} renderOrder={2}>
      {/* Unit outer radius; scaled to the ring radius. */}
      <ringGeometry args={[0.82, 1, 48]} />
      <meshBasicMaterial ref={mat} color={FOAM} transparent opacity={RING_OPACITY} depthWrite={false} toneMapped={false} />
    </mesh>
  );
  return { start: api.start, step: api.step, element };
}
