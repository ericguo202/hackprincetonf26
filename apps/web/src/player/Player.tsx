// Lane C: the local player. Owns input, the character body, and writing
// position/yaw/anim into the store. Lane D reads the store and sends `move`
// at 20 Hz; this file never opens a socket (AGENTS.md section 6).
//
// ecctrl v2 has no built-in keyboard handling — movement goes in through
// handle.setMovement(). Joystick and VirtualButton from "ecctrl/input" are
// the touch path; we drive the same input shape from the keyboard.
//
// Camera: CameraArm.tsx, a collision-aware over-the-shoulder arm. With
// followCamera on, ecctrl's lockForward keeps the body facing where the
// camera looks, and WASD is relative to the camera (ecctrl reads the scene
// camera's direction every frame).
//
// Model: <Character> (Quaternius Toon Shooter soldier), driven by
// ecctrl locomotion -> toAnimState() -> clips.ts CLIPS. Its feet are put on
// whatever the body stands on (ecctrl's standPoint) every frame, falling back
// to FOOT_BELOW_CENTRE_M in the air. If the model is missing, the blue
// capsule renders instead (same HEAD probe as every other mesh). First
// person hides bodyMesh, which holds the model and the capsule alike.

import { useEffect, useRef, useState } from "react";
import { useFrame } from "@react-three/fiber";
import { Ecctrl, EcctrlAnimationStateController, type EcctrlHandle } from "ecctrl";
import { Euler, type Group, Quaternion, Vector3 } from "three";

import { useStore } from "../store";
import { ACTION_HOLD_S, toAnimState, type LocomotionState, type PlayerAction } from "./animState";
import { Blaster } from "./Blaster";
import type { FirePayload, Hitbox } from "./blasterMath";
import type { AnimState } from "../../../../contracts/game-state";
import { CameraArm } from "./CameraArm";
import { CROUCH, MOVEMENT } from "./movement";

/** Hold to crouch (contracts v2.4.0). C rather than Ctrl, which the browser owns (Ctrl+W). */
export const CROUCH_KEY = "KeyC";
import { Character } from "./Character";
import { CHARACTER, FOOT_BELOW_CENTRE_M } from "./characterAsset";
import { useMeshState } from "../world/meshProbe";
import { registerPlayer } from "./playerControl";
import { useWeaponState } from "./weaponState";
import { heldWeaponProp, useFoamMode } from "./foamMode";

/** Lane D sends `move` at 20 Hz, so there is no point writing faster. */
const WRITE_HZ = 20;
const WRITE_INTERVAL_S = 1 / WRITE_HZ;
/** Don't wake subscribers for sub-centimetre jitter. */
const POS_EPSILON_M = 0.01;
const YAW_EPSILON_RAD = 0.017; // ~1 degree

const KEY_MAP: Record<string, keyof typeof KEYS> = {
  KeyW: "forward",
  ArrowUp: "forward",
  KeyS: "backward",
  ArrowDown: "backward",
  KeyA: "leftward",
  ArrowLeft: "leftward",
  KeyD: "rightward",
  ArrowRight: "rightward",
  ShiftLeft: "run",
  ShiftRight: "run",
  Space: "jump",
};

const KEYS = {
  forward: false,
  backward: false,
  leftward: false,
  rightward: false,
  run: false,
  jump: false,
};

type Props = {
  /** Where to drop the player in the current zone's frame. */
  spawn?: [number, number, number];
  /** "block" or a listing_id. Indoors the camera starts in first person. */
  zone?: string;
  /**
   * Lane D seam: remote players in this zone as capsules (zone frame). The
   * blaster tests shots against them. Default: none (single player).
   */
  getHitboxes?: () => Hitbox[];
  /** Lane D seam: send as the `fire` message. Default: no-op. */
  onFire?: (p: FirePayload) => void;
  /**
   * Over-the-shoulder camera on a collision-aware arm (CameraArm.tsx): click
   * for mouse look, scroll for distance, V for first person. Off: the camera
   * is left alone and the body is always visible.
   */
  followCamera?: boolean;
  children?: React.ReactNode;
};

export function Player({
  spawn = [0, 1, 0],
  zone = "block",
  followCamera = true,
  getHitboxes,
  onFire,
  children,
}: Props) {
  const ecctrl = useRef<EcctrlHandle | null>(null);
  const bodyMesh = useRef<Group | null>(null);
  /** Inside bodyMesh: moved each frame so the model's feet meet the ground. */
  const feet = useRef<Group | null>(null);
  const bodyWorld = useRef(new Vector3());
  const model = useMeshState(children ? null : CHARACTER.url, CHARACTER.url);
  // What the model shows. Changes a few times a second at most, so a state.
  const [look, setLook] = useState<{ anim: AnimState; moving: boolean }>({ anim: "idle", moving: false });
  const lookRef = useRef(look);
  const keys = useRef({ ...KEYS });
  // Crouch changes the capsule, so it is React state (an Ecctrl prop); the ref
  // is for the frame loop.
  const [crouched, setCrouched] = useState(false);
  // Explore mode: the model holds nothing (foamMode.ts); foam: the selected weapon.
  const weapon = heldWeaponProp(useFoamMode(), useWeaponState((s) => s.weapon));
  const crouchedRef = useRef(false);
  useEffect(() => {
    crouchedRef.current = crouched;
  }, [crouched]);
  const locomotion = useRef<LocomotionState>("IDLE");
  /** Set by the foam-fight code when the player shoots, is hit, or is out. */
  const action = useRef<PlayerAction>(null);
  const actionUntil = useRef(0);

  const setMe = useStore((s) => s.setMe);

  // Accumulators for the throttled store write.
  const sinceWrite = useRef(0);
  const lastSent = useRef<{ pos: [number, number, number]; yaw: number; anim: AnimState }>({
    pos: [NaN, NaN, NaN],
    yaw: NaN,
    anim: "idle",
  });

  useEffect(() => {
    const onDown = (e: KeyboardEvent) => {
      const k = KEY_MAP[e.code];
      if (!k) return;
      // Don't hijack typing in the chat panel.
      const el = document.activeElement;
      if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) return;
      if (e.code === "Space") e.preventDefault();
      keys.current[k] = true;
    };
    const onUp = (e: KeyboardEvent) => {
      if (e.code === CROUCH_KEY) setCrouched(false);
      const k = KEY_MAP[e.code];
      if (k) keys.current[k] = false;
    };
    const onCrouchDown = (e: KeyboardEvent) => {
      if (e.code !== CROUCH_KEY || e.repeat) return;
      const el = document.activeElement;
      if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) return;
      setCrouched(true);
    };
    // Releasing a key while the tab is hidden otherwise leaves it stuck down.
    const onBlur = () => {
      keys.current = { ...KEYS };
      setCrouched(false);
    };
    window.addEventListener("keydown", onCrouchDown);
    window.addEventListener("keydown", onDown);
    window.addEventListener("keyup", onUp);
    window.addEventListener("blur", onBlur);
    return () => {
      window.removeEventListener("keydown", onDown);
      window.removeEventListener("keyup", onUp);
      window.removeEventListener("blur", onBlur);
      window.removeEventListener("keydown", onCrouchDown);
    };
  }, []);

  // Let the world move the real body (push-out, respawn); see playerControl.ts.
  useEffect(
    () =>
      registerPlayer({
        getPos: () => {
          const p = ecctrl.current?.currPos;
          return p ? [p.x, p.y, p.z] : (lastSent.current.pos as [number, number, number]);
        },
        teleport: ([x, y, z]) => {
          const body = ecctrl.current?.body;
          if (!body) return;
          body.setTranslation({ x, y, z }, true);
          body.setLinvel({ x: 0, y: 0, z: 0 }, true);
          // Report the new position at once, not on the next 20 Hz write.
          lastSent.current = { ...lastSent.current, pos: [x, y, z] };
          setMe({ pos: [x, y, z] });
        },
      }),
    [setMe],
  );

  useFrame((state, dt) => {
    const handle = ecctrl.current;
    if (!handle) return;

    // Crouched: walk only, no run, no jump (CROUCH.maxVel caps the speed too).
    handle.setMovement(crouchedRef.current ? { ...keys.current, run: false, jump: false } : { ...keys.current });

    // Expire a one-shot action (shoot, hit). `eliminated` holds until respawn.
    if (action.current && state.clock.elapsedTime >= actionUntil.current) {
      action.current = null;
    }

    // Model: same state the wire carries, every frame (the store write below is 20 Hz).
    const nextLook = {
      anim: toAnimState(locomotion.current, action.current, crouchedRef.current),
      moving: locomotion.current === "WALK" || locomotion.current === "RUN",
    };
    if (nextLook.anim !== lookRef.current.anim || nextLook.moving !== lookRef.current.moving) {
      lookRef.current = nextLook;
      setLook(nextLook);
    }

    // Feet on the ground: the interpolated body's height above ecctrl's ground
    // contact. In the air, the nominal standing offset. Smoothed so landing and
    // the float spring settling do not pop.
    if (feet.current && bodyMesh.current) {
      const by = bodyMesh.current.getWorldPosition(bodyWorld.current).y;
      // The capsule keeps its standing size when crouched, so the nominal offset is always the standing one.
      const target = handle.isOnGround ? handle.standPoint.y - by : -FOOT_BELOW_CENTRE_M;
      const k = 1 - Math.exp(-20 * Math.min(dt, 0.1));
      feet.current.position.y += (target - feet.current.position.y) * k;
    }

    sinceWrite.current += dt;
    if (sinceWrite.current < WRITE_INTERVAL_S) return;
    sinceWrite.current = 0;

    const p = handle.currPos;
    const yaw = new Euler().setFromQuaternion(
      handle.currQuat as unknown as Quaternion,
      "YXZ",
    ).y;
    const anim = toAnimState(locomotion.current, action.current, crouchedRef.current);

    const prev = lastSent.current;
    const moved =
      Math.abs(p.x - prev.pos[0]) > POS_EPSILON_M ||
      Math.abs(p.y - prev.pos[1]) > POS_EPSILON_M ||
      Math.abs(p.z - prev.pos[2]) > POS_EPSILON_M;
    const turned = Math.abs(yaw - prev.yaw) > YAW_EPSILON_RAD;
    if (!moved && !turned && anim === prev.anim) return;

    const pos: [number, number, number] = [p.x, p.y, p.z];
    lastSent.current = { pos, yaw, anim };
    setMe({ pos, yaw, anim });
  });

  return (
    <>
      {/* Every frame, not at the 20 Hz store rate, or the camera stutters. */}
      {followCamera ? (
        <CameraArm
          ecctrl={ecctrl}
          body={bodyMesh}
          zone={zone}
          crouchedRef={crouchedRef}
          onBodyHidden={(hidden) => {
            if (bodyMesh.current) bodyMesh.current.visible = !hidden;
          }}
        />
      ) : null}
      {/* The blaster needs the pointer-locked camera, so it comes with followCamera. */}
      {followCamera ? (
        <Blaster
          ecctrl={ecctrl}
          body={bodyMesh}
          getHitboxes={getHitboxes}
          onFire={onFire}
          crouchedRef={crouchedRef}
          onShot={(now) => {
            // Don't cut short a hit or elimination with a shoot pose.
            if (action.current === "hit" || action.current === "eliminated") return;
            action.current = "shoot";
            actionUntil.current = now + ACTION_HOLD_S.shoot;
          }}
        />
      ) : null}
      <Ecctrl
        ref={ecctrl}
        position={spawn}
        // Crouch does NOT change capsuleHalfHeight: ecctrl rebuilds the collider
        // when args change, and the autoBalance spring cannot re-stabilise the
        // new shorter capsule quickly enough, so the body tips over and the game
        // crashes (GitHub issue: capsule rotation on collider rebuild). Instead
        // crouch only lowers the camera eye (CameraArm reads crouchedRef) and
        // caps movement speed; the model plays Duck to visually crouch. The
        // physics capsule stays standing-height, so a crouched player's hurtbox
        // is taller than the model — acceptable until ecctrl supports in-place
        // collider resize or hard rotation locks.
        capsuleHalfHeight={MOVEMENT.capsuleHalfHeight}
        capsuleRadius={MOVEMENT.capsuleRadius}
        maxWalkVel={crouched ? CROUCH.maxVel : MOVEMENT.maxWalkVel}
        maxRunVel={crouched ? CROUCH.maxVel : MOVEMENT.maxRunVel}
        jumpVel={crouched ? 0 : MOVEMENT.jumpVel}
        floatHeight={MOVEMENT.floatHeight}
        lockForward={followCamera}
      >
        <group ref={bodyMesh}>
          {children ??
            (model === "ok" ? (
              <group ref={feet} position-y={-FOOT_BELOW_CENTRE_M}>
                <Character
                  animState={look.anim}
                  moving={look.moving}
                  weapon={weapon}
                  // A file that passes the probe but fails to parse: the capsule, back at the body centre.
                  fallback={
                    <group position-y={FOOT_BELOW_CENTRE_M}>
                      <PlaceholderCharacter />
                    </group>
                  }
                />
              </group>
            ) : (
              <PlaceholderCharacter />
            ))}
        </group>
      </Ecctrl>
      <EcctrlAnimationStateController
        ecctrl={ecctrl}
        onChange={(next) => {
          locomotion.current = next;
        }}
      />
    </>
  );
}

/** Stand-in until the Quaternius pack is picked and clips.ts is filled. */
function PlaceholderCharacter({ halfHeight = MOVEMENT.capsuleHalfHeight }: { halfHeight?: number }) {
  return (
    <mesh castShadow>
      <capsuleGeometry args={[MOVEMENT.capsuleRadius, halfHeight * 2]} />
      <meshStandardMaterial color="#4f7cff" />
    </mesh>
  );
}
