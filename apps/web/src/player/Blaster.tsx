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

import { useEffect, useRef, type MutableRefObject, type RefObject } from "react";
import { useFrame, useThree } from "@react-three/fiber";
import { useRapier } from "@react-three/rapier";
import type { EcctrlHandle } from "ecctrl";
import { Mesh, Object3D, PointLight, Vector3 } from "three";

import { SHOT_QUERY } from "../world/collisionGroups";
import { SHOT_PRIORITY } from "../world/frameOrder";
import { CENTRE_ABOVE_FLOOR_M } from "./movement";
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
import { HIT_BITS, popBitCount, useFoamBurstPool, usePuffPool, useShockRing, useTracerPool } from "./shotFx";
import { canAttack, noodlePayload, noodleSwing, swingImpactPoint } from "./weapons";
import { clearHit, eliminationPops, markHit, markPop } from "../world/hitFlash";
import { FOOT_BELOW_CENTRE_M } from "./characterAsset";
import { useStore } from "../store";
import { canAttackNow, foamModeNow, meAliveNow, weaponForKey } from "./foamMode";
import { useWeaponState } from "./weaponState";
import { markAttack } from "./attackClock";
import { buildActiveNow } from "../world/buildState";

type Props = {
  ecctrl: RefObject<EcctrlHandle | null>;
  /** Interpolated body mesh, same as the camera arm uses for the head. */
  body: RefObject<Object3D | null>;
  getHitboxes?: () => Hitbox[];
  onFire?: (p: FirePayload) => void;
  /** Called on every shot fired (clock seconds), for the `shoot` animation state. */
  onShot?: (now: number) => void;
  crouchedRef?: MutableRefObject<boolean>;
};

const TRACERS = 12;
/** Foam bits pooled for hit splats (about four at once). */
const BURST_BITS = 48;
/** Foam bits pooled for elimination pops (two of mine at once). */
const POP_POOL_BITS = 96;
const FLASH_S = 0.05;

const noHitboxes = () => [];
const noFire = () => {};

export function Blaster({ ecctrl, body, getHitboxes = noHitboxes, onFire = noFire, onShot, crouchedRef }: Props) {
  const canvas = useThree((s) => s.gl.domElement);
  const { world, rapier } = useRapier();
  const showMarker = useBlasterHud((s) => s.showMarker);

  const triggerHeld = useRef(false);
  const setWeapon = useWeaponState((s) => s.setWeapon);
  const lastShotAt = useRef<number | null>(null);

  // Scratch objects, reused every frame.
  const tmpBody = useRef(new Vector3());
  const tmpDir = useRef(new Vector3());
  const rapierRay = useRef<InstanceType<typeof rapier.Ray> | null>(null);

  // Effect pools: meshes are mounted once and moved imperatively.
  const tracers = useTracerPool(TRACERS);
  const puffs = usePuffPool(1);
  const bursts = useFoamBurstPool(BURST_BITS);
  const pops = useFoamBurstPool(POP_POOL_BITS, true);
  const ring = useShockRing();
  const spawnPop = pops.pop; // stable
  const startRing = ring.start; // stable
  const flashMesh = useRef<Mesh | null>(null);
  const flashLight = useRef<PointLight | null>(null);
  const flashLeft = useRef(0);

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
      // Explore mode: keys 1/2 do nothing (foamMode.weaponForKey).
      const w = weaponForKey(e.code, foamModeNow());
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

  // Elimination foam pop: any remote in my zone going from alive to
  // eliminated (eliminationPops). Mine (I hit them within KILL_CREDIT_S) get
  // the full bit count, others' a share. Bits from the body centre, a ring at
  // the feet, and the model's scale pop (Character reads popScale).
  useEffect(
    () =>
      useStore.subscribe((s, prev) => {
        if (s.players === prev.players) return;
        for (const { id, mine } of eliminationPops(s.players, prev.players, s.me.id, s.me.zone)) {
          if (mine) clearHit(id);
          const c = s.players[id].pos as V3;
          markPop(id);
          spawnPop(c, popBitCount(mine));
          startRing([c[0], c[1] - FOOT_BELOW_CENTRE_M, c[2]]);
        }
      }),
    [spawnPop, startRing],
  );

  const headOffset = () =>
    ARM.headOffsetY -
    (crouchedRef?.current ? CENTRE_ABOVE_FLOOR_M.standing - CENTRE_ABOVE_FLOOR_M.crouched : 0);

  /**
   * Pool noodle swing: an arc query from the head along the aim, players only
   * (no walls or furniture), then the `fire` message with weapon "noodle".
   */
  function swing(camDir: V3, bodyCentre: V3, now: number) {
    const head = headFrom(bodyCentre, headOffset());
    const boxes = getHitboxes();
    const hit = noodleSwing(head, camDir, boxes);
    onFire(noodlePayload(head, camDir, hit));
    onShot?.(now); // the swing uses the `shoot` anim state
    if (hit) {
      showMarker("player");
      markHit(hit.id);
      const box = boxes.find((b) => b.id === hit.id);
      if (box) bursts.spawn(swingImpactPoint(head, box), head, HIT_BITS);
    }
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
    const head = headFrom(bodyCentre, headOffset());
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
    tracers.spawn(muzzle, end);
    flashLeft.current = FLASH_S;
    if (flashMesh.current) flashMesh.current.position.set(...muzzle);
    if (flashLight.current) flashLight.current.position.set(...muzzle);
    if (hit.kind !== "miss") {
      showMarker(hit.kind);
      puffs.spawn(end);
    }
    if (hit.kind === "player") {
      markHit(hit.targetId);
      bursts.spawn(end, head, HIT_BITS);
    }
  }

  useFrame((state, dt) => {
    const step = Math.min(dt, 0.1);
    const handle = ecctrl.current;

    // Explore mode or eliminated: nothing fires and nothing is sent; read
    // live every frame, so a mode switch or a death lands within a frame.
    // Darts already in flight finish. Holding the trigger through a respawn
    // does not auto-fire: it must be pressed again.
    // Build mode (manual furnishing): the left click places furniture instead.
    if (!canAttackNow(foamModeNow(), meAliveNow()) || buildActiveNow()) triggerHeld.current = false;
    if (handle && triggerHeld.current) {
      const now = state.clock.elapsedTime;
      const weapon = useWeaponState.getState().weapon;
      if (canAttack(weapon, lastShotAt.current, now)) {
        lastShotAt.current = now;
        markAttack(weapon); // for the weapon bar's cooldown sweep only
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
    tracers.step(step);
    puffs.step(step);
    bursts.step(step);
    pops.step(step);
    ring.step(step);

    flashLeft.current = Math.max(0, flashLeft.current - step);
    if (flashMesh.current) flashMesh.current.visible = flashLeft.current > 0;
    if (flashLight.current) flashLight.current.intensity = flashLeft.current > 0 ? 3 : 0;
  }, SHOT_PRIORITY);

  return (
    <>
      {tracers.elements}
      <mesh ref={flashMesh} visible={false}>
        <sphereGeometry args={[0.06, 10, 8]} />
        <meshBasicMaterial color="#ffe0a0" toneMapped={false} />
      </mesh>
      <pointLight ref={flashLight} color="#ffb347" intensity={0} distance={3} />
      {puffs.elements}
      {bursts.elements}
      {pops.elements}
      {ring.element}
    </>
  );
}
