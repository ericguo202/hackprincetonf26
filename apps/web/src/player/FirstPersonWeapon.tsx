// Lane C: first-person view model. In first person the camera arm hides the
// whole body (armMath ARM.hideBodyBelow), and the held blaster or noodle with
// it. This draws the held weapon on the camera instead: lower right, in a
// group that copies the camera every frame (after CAMERA_PRIORITY), drawn last
// with depth test off so it never clips into walls.
//
// The props come from a dedicated rig of the player's own look (buildRig on
// the same cached glTF): the foam-tinted RocketLauncher and the pool noodle
// with its jiggle. They are posed from the Idle_Shoot aim, turned so the
// barrel points along the camera. Pure parts: fpWeaponMath.ts.

import { useEffect, useMemo, useRef, type MutableRefObject } from "react";
import { useFrame } from "@react-three/fiber";
import { Vector3, type Group } from "three";

import type { Weapon } from "../../../../contracts/game-state";
import { FP_WEAPON_PRIORITY } from "../world/frameOrder";
import { usePackGLTF } from "../world/gltf";
import { nowS, useAttackClock } from "./attackClock";
import { setWeapon, updateNoodleJiggle } from "./characterRig";
import { buildViewModel } from "./fpViewModel";
import { meAliveNow } from "./foamMode";
import { buildActiveNow } from "../world/buildState";
import { bobPhase, bobStrength, FP_SCALE, fpPose, fpVisible, type FpPose } from "./fpWeaponMath";

type Props = {
  /** The player's look (characterAsset variantFor), so the props match the hand prop in third person. */
  url: string;
  /** The held prop: null in explore mode. */
  weapon: Weapon | null;
  /** Set by CameraArm's onBodyHidden. */
  bodyHidden: MutableRefObject<boolean>;
};

export function FirstPersonWeapon({ url, weapon, bodyHidden }: Props) {
  const gltf = usePackGLTF(url);
  const vm = useMemo(() => buildViewModel(gltf.scene, gltf.animations), [gltf]);
  useEffect(
    () => () => {
      vm.materials.forEach((m) => m.dispose());
      const noodle = vm.rig.noodleJiggle?.mesh;
      noodle?.geometry.dispose();
    },
    [vm],
  );

  const follow = useRef<Group>(null);
  const offset = useRef<Group>(null);
  const shown = useRef<Weapon | null>(null);
  const pose = useRef<FpPose>({ x: 0, y: 0, z: 0, pitch: 0, yaw: 0, roll: 0 });
  // Walk bob: horizontal camera speed (smoothed) and a step phase advanced by distance.
  const walk = useRef({ phase: 0, strength: 0 });
  const lastCam = useRef(new Vector3());
  const hasLastCam = useRef(false);

  useFrame((state, dt) => {
    const f = follow.current;
    const o = offset.current;
    if (!f || !o) return;
    // Build mode (manual furnishing): nothing fires, so the weapon would only hide the room.
    const visible = fpVisible({ bodyHidden: bodyHidden.current, weapon, alive: meAliveNow() }) && !buildActiveNow();
    const want = visible ? weapon : null;
    if (want !== shown.current) {
      shown.current = want;
      setWeapon(vm.rig, want); // shows one prop; also resets the noodle jiggle when hidden
    }
    f.visible = visible;
    const cam = state.camera;
    if (!visible || !want) {
      hasLastCam.current = false;
      return;
    }

    // Horizontal speed of the camera (it rides the body in first person).
    const step = Math.min(dt, 0.1);
    let dist = 0;
    if (hasLastCam.current && step > 0) {
      dist = Math.hypot(cam.position.x - lastCam.current.x, cam.position.z - lastCam.current.z);
      if (dist > 1) dist = 0; // a teleport or zone change, not a step
    }
    lastCam.current.copy(cam.position);
    hasLastCam.current = true;
    const w = walk.current;
    const target = step > 0 ? bobStrength(dist / step) : 0;
    w.strength += (target - w.strength) * Math.min(1, step * 10);
    w.phase = bobPhase(w.phase, dist);

    f.position.copy(cam.position);
    f.quaternion.copy(cam.quaternion);
    const clock = useAttackClock.getState();
    const now = nowS();
    const p = fpPose(want, now, clock.weapon, clock.atS === null ? null : now - clock.atS, pose.current, w);
    o.position.set(p.x, p.y, p.z);
    o.rotation.set(p.pitch, p.yaw, p.roll, "YXZ");
    f.updateMatrixWorld(true);
    // The noodle flops with the camera's motion, like the hand prop.
    updateNoodleJiggle(vm.rig, Math.min(dt, 0.1));
  }, FP_WEAPON_PRIORITY);

  return (
    <group ref={follow} visible={false}>
      <group ref={offset} scale={FP_SCALE}>
        {vm.props.map((p) => (
          <primitive key={p.uuid} object={p} />
        ))}
      </group>
    </group>
  );
}
