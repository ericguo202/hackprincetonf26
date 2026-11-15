// Lane C: collision-aware over-the-shoulder camera. Runs every frame (not at
// the 20 Hz store rate). The math is in armMath.ts; this file does input,
// the raycast and moving the three.js camera.
//
// Click the canvas to lock the pointer for mouse look (Esc releases it).
// In build mode (manual furnishing) the pointer stays free and dragging on
// the canvas looks around instead.
// Scroll changes how far back the arm reaches. V pins the arm to zero for
// explicit first person; scrolling out or pressing V again lets it back out.
// Each zone starts with its own default (armDefaults): first person indoors,
// the full arm on the block.
// There is no separate first-person mode: when the arm gets short, because
// of a wall or because of V, the local body is simply hidden.
//
// The ray only hits zone collision (collision.glb or its placeholder, via
// CAMERA_QUERY). Render meshes have no colliders, furniture is in another
// group, and the local body is excluded, so none of them pull the camera in.

import { useEffect, useRef, type MutableRefObject, type RefObject } from "react";
import { useFrame, useThree } from "@react-three/fiber";
import { useRapier } from "@react-three/rapier";
import type { EcctrlHandle } from "ecctrl";
import { PerspectiveCamera, Vector3, type Object3D } from "three";

import { CAMERA_QUERY } from "../world/collisionGroups";
import { DRAG_PX, buildActiveNow, buildHoldingNow } from "../world/buildState";
import { CAMERA_PRIORITY } from "../world/frameOrder";
import { CENTRE_ABOVE_FLOOR_M } from "./movement";
import {
  ARM,
  applyLook,
  armOffset,
  armRay,
  clampArm,
  armDefaults,
  hideBody,
  horizontalFov,
  scrollArm,
  smoothArm,
  toggleFirstPerson,
  type ArmSetting,
} from "./armMath";

/** Toggles the arm pinned at zero (explicit first person). */
export const FIRST_PERSON_KEY = "KeyV";

type Props = {
  ecctrl: RefObject<EcctrlHandle | null>;
  /**
   * The local body's mesh group. The camera follows ITS world position, which
   * @react-three/rapier has interpolated between physics steps, not ecctrl's
   * currPos, which is the raw body translation and only moves when the world
   * steps (60 Hz). Following the raw value makes the drawn capsule swing up
   * to one step (about 8 cm at run speed) against the camera every frame.
   */
  body: RefObject<Object3D | null>;
  /** Called when the arm crosses hideThreshold(): hide the local body (first person) or show it. */
  onBodyHidden: (hidden: boolean) => void;
  /** "block" or a listing_id; picks the arm's default on every zone change. */
  zone: string;
  /**
   * Live crouch state (ref so it is read per-frame without a React re-render).
   * When crouched, the capsule stays standing-height but the camera eye drops
   * to the crouched body centre: the difference between standing and crouched
   * centres (CENTRE_ABOVE_FLOOR_M).
   */
  crouchedRef?: MutableRefObject<boolean>;
  /** Yaw (radians) the camera starts at when mounted; default 0. */
  initialYaw?: number;
};

/** Rapier's Ray, typed through @react-three/rapier (rapier3d-compat is not a direct dependency). */
type Ray = InstanceType<ReturnType<typeof useRapier>["rapier"]["Ray"]>;

function typing(): boolean {
  const el = document.activeElement;
  return el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement;
}

export function CameraArm({ ecctrl, body, onBodyHidden, zone, crouchedRef, initialYaw = 0 }: Props) {
  const canvas = useThree((s) => s.gl.domElement);
  const { world, rapier } = useRapier();

  const look = useRef({ yaw: initialYaw, pitch: -0.12 });
  const arm = useRef<ArmSetting>(armDefaults(zone));
  const armLen = useRef(0);
  /** One Ray reused every frame. */
  const ray = useRef<Ray | null>(null);
  const bodyHidden = useRef<boolean | null>(null);
  const bodyPos = useRef(new Vector3());

  useEffect(() => {
    const locked = () => document.pointerLockElement === canvas;
    const onClick = () => {
      // Build mode keeps the cursor free: clicks place furniture, drags look (below).
      if (locked() || buildActiveNow()) return;
      // Promise in current browsers, undefined in older Safari; a rejection
      // (clicked again too soon after Esc) is harmless.
      const p = canvas.requestPointerLock() as Promise<void> | undefined;
      p?.catch(() => {});
    };
    const onMove = (e: MouseEvent) => {
      if (!locked()) return;
      look.current = applyLook(look.current.yaw, look.current.pitch, e.movementX, e.movementY);
    };
    // Build mode: drag on the scene (either button) to look around, no pointer lock.
    // Under DRAG_PX it is a click, which BuildController handles.
    let drag: { x: number; y: number; dragging: boolean } | null = null;
    const onDragStart = (e: PointerEvent) => {
      if (locked() || !buildActiveNow() || (e.button !== 0 && e.button !== 2)) return;
      drag = { x: e.clientX, y: e.clientY, dragging: false };
    };
    const onDragMove = (e: PointerEvent) => {
      if (!drag) return;
      if (!drag.dragging && Math.hypot(e.clientX - drag.x, e.clientY - drag.y) < DRAG_PX) return;
      drag.dragging = true;
      look.current = applyLook(look.current.yaw, look.current.pitch, e.movementX, e.movementY);
    };
    const onDragEnd = () => {
      drag = null;
    };
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      // Build mode: the wheel turns the held piece (BuildController), not the arm.
      if (buildHoldingNow()) return;
      arm.current = scrollArm(arm.current, e.deltaY);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.code !== FIRST_PERSON_KEY || e.repeat || typing()) return;
      arm.current = toggleFirstPerson(arm.current);
    };
    canvas.addEventListener("click", onClick);
    canvas.addEventListener("pointerdown", onDragStart);
    window.addEventListener("pointermove", onDragMove);
    window.addEventListener("pointerup", onDragEnd);
    window.addEventListener("blur", onDragEnd);
    canvas.addEventListener("wheel", onWheel, { passive: false });
    document.addEventListener("mousemove", onMove);
    window.addEventListener("keydown", onKey);
    return () => {
      canvas.removeEventListener("click", onClick);
      canvas.removeEventListener("pointerdown", onDragStart);
      window.removeEventListener("pointermove", onDragMove);
      window.removeEventListener("pointerup", onDragEnd);
      window.removeEventListener("blur", onDragEnd);
      canvas.removeEventListener("wheel", onWheel);
      document.removeEventListener("mousemove", onMove);
      window.removeEventListener("keydown", onKey);
      // Pointer lock is left alone: the Player remounts on every zone change
      // and the lock should survive walking through a door.
    };
  }, [canvas]);

  // New zone, new default. The Player usually remounts on a zone change too,
  // but this keeps the default right even if it does not.
  useEffect(() => {
    arm.current = armDefaults(zone);
  }, [zone]);

  useFrame((state, dt) => {
    const h = ecctrl.current;
    if (!h) return;
    // Interpolated (drawn) position; raw currPos only before the mesh exists.
    const p = body.current ? body.current.getWorldPosition(bodyPos.current) : h.currPos;
    const hx = p.x;
    // When crouched, the capsule stays standing-height but the eye drops.
    // The drop equals the difference between the standing and crouched body
    // centres — what used to happen via capsuleHalfHeight shrinking.
    const crouchDrop = crouchedRef?.current
      ? CENTRE_ABOVE_FLOOR_M.standing - CENTRE_ABOVE_FLOOR_M.crouched
      : 0;
    const hy = p.y + ARM.headOffsetY - crouchDrop;
    const hz = p.z;
    const { yaw, pitch } = look.current;

    const reach = arm.current.pinned ? 0 : arm.current.back;
    const side = arm.current.pinned ? 0 : ARM.side;
    const { dir, len } = armRay(armOffset(yaw, pitch, reach, side));

    let target = 0;
    if (len > 0) {
      ray.current ??= new rapier.Ray({ x: 0, y: 0, z: 0 }, { x: 0, y: 0, z: -1 });
      ray.current.origin = { x: hx, y: hy, z: hz };
      ray.current.dir = { x: dir[0], y: dir[1], z: dir[2] };
      const hit = world.castRay(ray.current, len, true, undefined, CAMERA_QUERY, undefined, h.body);
      target = clampArm(len, hit ? hit.timeOfImpact : null);
    }
    // Clamp dt so a stalled tab does not fling the arm out in one frame.
    armLen.current = smoothArm(armLen.current, target, Math.min(dt, 0.1));
    const d = armLen.current;

    const camera = state.camera;
    camera.position.set(hx + dir[0] * d, hy + dir[1] * d, hz + dir[2] * d);
    camera.rotation.order = "YXZ";
    camera.rotation.set(pitch, yaw, 0);

    // Horizontal FOV from the live camera: it changes with the window shape.
    const hFov =
      camera instanceof PerspectiveCamera ? horizontalFov(camera.fov, camera.aspect) : Math.PI / 2;
    const hidden = hideBody(d, hFov);
    if (hidden !== bodyHidden.current) {
      bodyHidden.current = hidden;
      onBodyHidden(hidden);
    }
  }, CAMERA_PRIORITY);

  return null;
}
