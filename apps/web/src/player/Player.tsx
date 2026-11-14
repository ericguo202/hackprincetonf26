// Lane C: the local player. Owns input, the character body, and writing
// position/yaw/anim into the store. Lane D reads the store and sends `move`
// at 20 Hz; this file never opens a socket (AGENTS.md section 6).
//
// ecctrl v2 has no built-in keyboard handling — movement goes in through
// handle.setMovement(). Joystick and VirtualButton from "ecctrl/input" are
// the touch path; we drive the same input shape from the keyboard.
//
// The character model is a placeholder capsule until the Quaternius pack is
// chosen and player/clips.ts is filled in. Nothing below depends on that
// choice, so D is unblocked before the pack lands.

import { useEffect, useRef } from "react";
import { useFrame } from "@react-three/fiber";
import { Ecctrl, EcctrlAnimationStateController, type EcctrlHandle } from "ecctrl";
import { EcctrlCameraControls, type EcctrlCameraControlsHandle } from "ecctrl/camera";
import { Euler, Quaternion } from "three";

import { useStore } from "../store";
import { toAnimState, type LocomotionState, type PlayerAction } from "./animState";
import type { AnimState } from "../../../../contracts/game-state";
import { CAMERA, MOVEMENT } from "./movement";

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
  /** Third-person camera that follows the body. Mouse drag orbits, wheel zooms. */
  followCamera?: boolean;
  children?: React.ReactNode;
};

export function Player({ spawn = [0, 1, 0], followCamera = true, children }: Props) {
  const ecctrl = useRef<EcctrlHandle | null>(null);
  const cameraControls = useRef<EcctrlCameraControlsHandle | null>(null);
  const keys = useRef({ ...KEYS });
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
      const k = KEY_MAP[e.code];
      if (k) keys.current[k] = false;
    };
    // Releasing a key while the tab is hidden otherwise leaves it stuck down.
    const onBlur = () => {
      keys.current = { ...KEYS };
    };
    window.addEventListener("keydown", onDown);
    window.addEventListener("keyup", onUp);
    window.addEventListener("blur", onBlur);
    return () => {
      window.removeEventListener("keydown", onDown);
      window.removeEventListener("keyup", onUp);
      window.removeEventListener("blur", onBlur);
    };
  }, []);

  // Place the camera behind the spawn once; after that, moveTo keeps the
  // offset the player has orbited to.
  useEffect(() => {
    const cc = cameraControls.current;
    if (!cc) return;
    const [x, y, z] = spawn;
    const [ox, oy, oz] = CAMERA.offset;
    void cc.setLookAt(x + ox, y + oy, z + oz, x, y + CAMERA.lookHeight, z, false);
    // Spawn changes are teleports handled elsewhere; this is initial framing only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [followCamera]);

  useFrame((state, dt) => {
    const handle = ecctrl.current;
    if (!handle) return;

    handle.setMovement({ ...keys.current });

    // Every frame, not at the 20 Hz store rate, or the camera stutters.
    const cc = cameraControls.current;
    if (cc) {
      const p = handle.currPos;
      void cc.moveTo(p.x, p.y + CAMERA.lookHeight, p.z, true);
    }

    // Expire a one-shot action (shoot, hit). `eliminated` holds until respawn.
    if (action.current && state.clock.elapsedTime >= actionUntil.current) {
      action.current = null;
    }

    sinceWrite.current += dt;
    if (sinceWrite.current < WRITE_INTERVAL_S) return;
    sinceWrite.current = 0;

    const p = handle.currPos;
    const yaw = new Euler().setFromQuaternion(
      handle.currQuat as unknown as Quaternion,
      "YXZ",
    ).y;
    const anim = toAnimState(locomotion.current, action.current);

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
      {followCamera ? (
        <EcctrlCameraControls
          ref={cameraControls}
          makeDefault
          smoothTime={CAMERA.smoothTime}
          minDistance={CAMERA.minDistance}
          maxDistance={CAMERA.maxDistance}
        />
      ) : null}
      <Ecctrl
        ref={ecctrl}
        position={spawn}
        capsuleHalfHeight={MOVEMENT.capsuleHalfHeight}
        capsuleRadius={MOVEMENT.capsuleRadius}
        maxWalkVel={MOVEMENT.maxWalkVel}
        maxRunVel={MOVEMENT.maxRunVel}
        jumpVel={MOVEMENT.jumpVel}
        floatHeight={MOVEMENT.floatHeight}
      >
        {children ?? <PlaceholderCharacter />}
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
function PlaceholderCharacter() {
  return (
    <mesh castShadow>
      <capsuleGeometry args={[MOVEMENT.capsuleRadius, MOVEMENT.capsuleHalfHeight * 2]} />
      <meshStandardMaterial color="#4f7cff" />
    </mesh>
  );
}
