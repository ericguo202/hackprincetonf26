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
import type { Group } from "three";

import type { Weapon } from "../../../../contracts/game-state";
import { FP_WEAPON_PRIORITY } from "../world/frameOrder";
import { usePackGLTF } from "../world/gltf";
import { nowS, useAttackClock } from "./attackClock";
import { setWeapon, updateNoodleJiggle } from "./characterRig";
import { buildViewModel } from "./fpViewModel";
import { meAliveNow } from "./foamMode";
import { fpPose, fpVisible, type FpPose } from "./fpWeaponMath";

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
  const pose = useRef<FpPose>({ x: 0, y: 0, z: 0, pitch: 0, yaw: 0 });
  const tmp = useRef({ x: 0, y: 0 });

  useFrame((state, dt) => {
    const f = follow.current;
    const o = offset.current;
    if (!f || !o) return;
    const visible = fpVisible({ bodyHidden: bodyHidden.current, weapon, alive: meAliveNow() });
    const want = visible ? weapon : null;
    if (want !== shown.current) {
      shown.current = want;
      setWeapon(vm.rig, want); // shows one prop; also resets the noodle jiggle when hidden
    }
    f.visible = visible;
    if (!visible || !want) return;

    const cam = state.camera;
    f.position.copy(cam.position);
    f.quaternion.copy(cam.quaternion);
    const clock = useAttackClock.getState();
    const now = nowS();
    const p = fpPose(want, now, clock.weapon, clock.atS === null ? null : now - clock.atS, pose.current, tmp.current);
    o.position.set(p.x, p.y, p.z);
    o.rotation.set(p.pitch, p.yaw, 0);
    f.updateMatrixWorld(true);
    // The noodle flops with the camera's motion, like the hand prop.
    updateNoodleJiggle(vm.rig, Math.min(dt, 0.1));
  }, FP_WEAPON_PRIORITY);

  return (
    <group ref={follow} visible={false}>
      <group ref={offset}>
        {vm.props.map((p) => (
          <primitive key={p.uuid} object={p} />
        ))}
      </group>
    </group>
  );
}
