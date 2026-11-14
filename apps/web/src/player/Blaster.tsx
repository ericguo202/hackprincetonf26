// Lane C: the local foam blaster (AGENTS.md section 11). Single player for
// now; every effect is local and immediate. The shot pipeline is in
// blasterMath.ts; this file does input, Rapier raycasts and effects.
//
// Left mouse (held = repeat) while the pointer is locked. Capped at
// BLASTER.maxShotsPerSecond, under the server's 5/s.
//
// SEAMS FOR LANE D
//   getHitboxes: () => Hitbox[]   remote players in MY zone, as capsules in the
//       zone frame (NetPlayer.pos is the body centre; radius 0.3, halfHeight
//       0.35 match MOVEMENT). Only alive players. Default: none.
//   onFire: (p: FirePayload) => void   send it as the `fire` message. origin is
//       the head (always within the server's 1.5 m of me.pos), dir is unit,
//       target_id is set only when a hitbox was hit. Default: no-op.
// Lane C never opens a socket; both are props on <Player>.

import { useEffect, useRef, type RefObject } from "react";
import { useFrame, useThree } from "@react-three/fiber";
import { useRapier } from "@react-three/rapier";
import type { EcctrlHandle } from "ecctrl";
import { Mesh, Object3D, PointLight, Quaternion, Vector3 } from "three";

import { SHOT_QUERY } from "../world/collisionGroups";
import { SHOT_PRIORITY } from "../world/frameOrder";
import { ARM, type V3 } from "./armMath";
import {
  BLASTER,
  aimPoint,
  aimRay,
  firePayload,
  headFrom,
  muzzleFrom,
  nearestHitbox,
  pointAt,
  resolveHit,
  shotRay,
  type FirePayload,
  type Hitbox,
  type Ray,
} from "./blasterMath";
import { useBlasterHud } from "./blasterHud";
import { WEAPON_KEYS, canAttack, noodlePayload, noodleSwing } from "./weapons";
import { useWeaponState } from "./weaponState";

type Props = {
  ecctrl: RefObject<EcctrlHandle | null>;
  /** Interpolated body mesh, same as the camera arm uses for the head. */
  body: RefObject<Object3D | null>;
  getHitboxes?: () => Hitbox[];
  onFire?: (p: FirePayload) => void;
  /** Called on every shot fired (clock seconds), for the `shoot` animation state. */
  onShot?: (now: number) => void;
};

const TRACERS = 12;
const STREAK_M = 0.7;
const FLASH_S = 0.05;
const PUFF_S = 0.18;
const FOAM = "#ff8a1f";
const UP = new Vector3(0, 1, 0);

type Tracer = { active: boolean; from: V3; dir: V3; total: number; travelled: number };

const noHitboxes = () => [];
const noFire = () => {};

export function Blaster({ ecctrl, body, getHitboxes = noHitboxes, onFire = noFire, onShot }: Props) {
  const canvas = useThree((s) => s.gl.domElement);
  const { world, rapier } = useRapier();
  const showMarker = useBlasterHud((s) => s.showMarker);

  const triggerHeld = useRef(false);
  const setWeapon = useWeaponState((s) => s.setWeapon);
  const lastShotAt = useRef<number | null>(null);

  // Scratch objects, reused every frame.
  const tmpBody = useRef(new Vector3());
  const tmpDir = useRef(new Vector3());
  const tmpQuat = useRef(new Quaternion());
  const rapierRay = useRef<InstanceType<typeof rapier.Ray> | null>(null);

  // Effect pools: meshes are mounted once and moved imperatively.
  const tracers = useRef<Tracer[]>(
    Array.from({ length: TRACERS }, () => ({ active: false, from: [0, 0, 0], dir: [0, 0, 1], total: 0, travelled: 0 })),
  );
  const nextTracer = useRef(0);
  const tracerMeshes = useRef<(Mesh | null)[]>([]);
  const flashMesh = useRef<Mesh | null>(null);
  const flashLight = useRef<PointLight | null>(null);
  const flashLeft = useRef(0);
  const puffMesh = useRef<Mesh | null>(null);
  const puffLeft = useRef(0);

  useEffect(() => {
    const locked = () => document.pointerLockElement === canvas;
    const onDown = (e: MouseEvent) => {
      if (e.button === 0 && locked()) triggerHeld.current = true;
    };
    const onUp = (e: MouseEvent) => {
      if (e.button === 0) triggerHeld.current = false;
    };
    const release = () => {
      if (!locked()) triggerHeld.current = false;
    };
    // Keys 1 and 2 pick the weapon (weapons.ts WEAPON_KEYS).
    const onKey = (e: KeyboardEvent) => {
      const w = WEAPON_KEYS[e.code];
      const el = document.activeElement;
      if (!w || el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) return;
      setWeapon(w);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("mouseup", onUp);
    document.addEventListener("pointerlockchange", release);
    window.addEventListener("blur", release);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("mouseup", onUp);
      document.removeEventListener("pointerlockchange", release);
      window.removeEventListener("blur", release);
    };
  }, [canvas, setWeapon]);

  /**
   * Pool noodle swing: an arc query from the head along the aim, players only
   * (no walls or furniture), then the `fire` message with weapon "noodle".
   */
  function swing(camDir: V3, bodyCentre: V3, now: number) {
    const head = headFrom(bodyCentre, ARM.headOffsetY);
    const hit = noodleSwing(head, camDir, getHitboxes());
    onFire(noodlePayload(head, camDir, hit));
    onShot?.(now); // the swing uses the `shoot` anim state
    if (hit) showMarker("player");
  }

  /** First zone-or-furniture hit along a ray, between minT and maxT, ignoring my own body. */
  function castWorld(ray: Ray, minT: number, maxT: number, exclude: EcctrlHandle["body"]): number | null {
    if (maxT <= minT) return null;
    rapierRay.current ??= new rapier.Ray({ x: 0, y: 0, z: 0 }, { x: 0, y: 0, z: 1 });
    const r = rapierRay.current;
    const start = pointAt(ray.origin, ray.dir, minT);
    r.origin = { x: start[0], y: start[1], z: start[2] };
    r.dir = { x: ray.dir[0], y: ray.dir[1], z: ray.dir[2] };
    const hit = world.castRay(r, maxT - minT, true, undefined, SHOT_QUERY, undefined, exclude);
    return hit ? minT + hit.timeOfImpact : null;
  }

  function fire(camPos: V3, camDir: V3, bodyCentre: V3, handle: EcctrlHandle, now: number) {
    const head = headFrom(bodyCentre, ARM.headOffsetY);
    const boxes = getHitboxes();

    // 1. Aim: the camera ray through the crosshair, only past the head.
    const aim = aimRay(camPos, camDir, head);
    const aimMax = aim.minT + BLASTER.rangeM;
    const aimWorld = castWorld(aim, aim.minT, aimMax, handle.body);
    const aimBox = nearestHitbox(aim, boxes, aim.minT, aimWorld ?? aimMax);
    const target = aimPoint(aim, aimBox ? aimBox.t : aimWorld);

    // 2. Shot: head toward the aim point. This decides the hit.
    const shot = shotRay(head, target, aim.dir);
    const shotWorld = castWorld(shot, 0, BLASTER.rangeM, handle.body);
    const shotBox = nearestHitbox(shot, boxes, 0, shotWorld ?? BLASTER.rangeM);
    const hit = resolveHit(shotWorld, shotBox);

    // 3. Wire.
    onFire(firePayload(shot, hit));
    onShot?.(now);

    // 4. Effects, all local and immediate.
    const end = pointAt(shot.origin, shot.dir, hit.t);
    const yaw = Math.atan2(-camDir[0], -camDir[2]);
    const pitch = Math.asin(Math.max(-1, Math.min(1, camDir[1])));
    const muzzle = muzzleFrom(head, yaw, pitch);
    spawnTracer(muzzle, end);
    flashLeft.current = FLASH_S;
    if (flashMesh.current) flashMesh.current.position.set(...muzzle);
    if (flashLight.current) flashLight.current.position.set(...muzzle);
    if (hit.kind !== "miss") {
      showMarker(hit.kind);
      puffLeft.current = PUFF_S;
      if (puffMesh.current) puffMesh.current.position.set(...end);
    }
  }

  function spawnTracer(from: V3, to: V3) {
    const v: V3 = [to[0] - from[0], to[1] - from[1], to[2] - from[2]];
    const total = Math.hypot(...v);
    if (total < 1e-3) return;
    const t = tracers.current[nextTracer.current];
    nextTracer.current = (nextTracer.current + 1) % TRACERS;
    t.active = true;
    t.from = from;
    t.dir = [v[0] / total, v[1] / total, v[2] / total];
    t.total = total;
    t.travelled = 0;
  }

  useFrame((state, dt) => {
    const step = Math.min(dt, 0.1);
    const handle = ecctrl.current;

    if (handle && triggerHeld.current) {
      const now = state.clock.elapsedTime;
      const weapon = useWeaponState.getState().weapon;
      if (canAttack(weapon, lastShotAt.current, now)) {
        lastShotAt.current = now;
        const cam = state.camera;
        cam.getWorldDirection(tmpDir.current);
        const centre = body.current ? body.current.getWorldPosition(tmpBody.current) : handle.currPos;
        if (weapon === "noodle") {
          swing([tmpDir.current.x, tmpDir.current.y, tmpDir.current.z], [centre.x, centre.y, centre.z], now);
        } else fire(
          [cam.position.x, cam.position.y, cam.position.z],
          [tmpDir.current.x, tmpDir.current.y, tmpDir.current.z],
          [centre.x, centre.y, centre.z],
          handle,
          now,
        );
      }
    }

    // Tracers: a short streak flying from the muzzle to the hit point.
    tracers.current.forEach((t, i) => {
      const mesh = tracerMeshes.current[i];
      if (!mesh) return;
      if (!t.active) {
        mesh.visible = false;
        return;
      }
      t.travelled += BLASTER.tracerSpeed * step;
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

    flashLeft.current = Math.max(0, flashLeft.current - step);
    if (flashMesh.current) flashMesh.current.visible = flashLeft.current > 0;
    if (flashLight.current) flashLight.current.intensity = flashLeft.current > 0 ? 3 : 0;

    puffLeft.current = Math.max(0, puffLeft.current - step);
    if (puffMesh.current) {
      const k = puffLeft.current / PUFF_S;
      puffMesh.current.visible = k > 0;
      puffMesh.current.scale.setScalar(0.05 + (1 - k) * 0.12);
    }
  }, SHOT_PRIORITY);

  return (
    <>
      {Array.from({ length: TRACERS }, (_, i) => (
        <mesh
          key={i}
          visible={false}
          ref={(m) => {
            tracerMeshes.current[i] = m;
          }}
        >
          {/* Unit-length along +Y; scaled to the streak length each frame. */}
          <cylinderGeometry args={[0.012, 0.012, 1, 6]} />
          <meshBasicMaterial color={FOAM} toneMapped={false} />
        </mesh>
      ))}
      <mesh ref={flashMesh} visible={false}>
        <sphereGeometry args={[0.06, 10, 8]} />
        <meshBasicMaterial color="#ffe0a0" toneMapped={false} />
      </mesh>
      <pointLight ref={flashLight} color="#ffb347" intensity={0} distance={3} />
      <mesh ref={puffMesh} visible={false}>
        <sphereGeometry args={[1, 10, 8]} />
        <meshBasicMaterial color={FOAM} transparent opacity={0.7} toneMapped={false} />
      </mesh>
    </>
  );
}
