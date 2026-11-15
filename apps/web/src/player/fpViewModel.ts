// Lane C: the first-person view model's props (FirstPersonWeapon.tsx): a
// dedicated rig of the player's look with its blaster and noodle taken off the
// hand, posed from the Idle_Shoot aim and turned so the barrel points along
// the camera's -Z, drawn last with depth test off.

import { Mesh, Quaternion, Vector3, type Material, type Object3D } from "three";

import { actionFor, buildRig, setWeapon, type Rig } from "./characterRig";
import { CLIPS } from "./clips";

/** Drawn after the world. */
const FP_RENDER_ORDER = 999;

export type ViewModel = { rig: Rig; props: Object3D[]; materials: Material[] };

/** A rig of this look with its blaster and noodle taken off the hand and posed for the camera. */
export function buildViewModel(scene: Object3D, clips: Parameters<typeof buildRig>[1]): ViewModel {
  const rig = buildRig(scene, clips);
  // Sample the aim pose, so the props sit as they do when shooting.
  const aim = actionFor(rig, CLIPS.shoot);
  aim?.reset().play();
  rig.mixer.update(0);
  rig.root.updateMatrixWorld(true);

  // Turn about Y so the blaster's barrel (its local +X) points along the camera's -Z.
  const q = new Quaternion();
  const barrel = new Vector3(1, 0, 0);
  const blaster = rig.props.blaster;
  let turn = new Quaternion();
  if (blaster) {
    blaster.getWorldQuaternion(q);
    barrel.applyQuaternion(q);
    const yawAim = Math.atan2(-barrel.x, -barrel.z); // 0 when aiming down -Z
    turn = new Quaternion().setFromAxisAngle(new Vector3(0, 1, 0), -yawAim);
  }

  const props: Object3D[] = [];
  const materials: Material[] = [];
  const s = new Vector3();
  for (const p of [rig.props.blaster, rig.props.noodle]) {
    if (!p) continue;
    p.getWorldScale(s);
    p.getWorldQuaternion(q);
    p.removeFromParent();
    p.position.set(0, 0, 0);
    p.scale.copy(s);
    p.quaternion.copy(q).premultiply(turn);
    p.traverse((o) => {
      if (!(o instanceof Mesh)) return;
      o.castShadow = false;
      o.receiveShadow = false;
      o.frustumCulled = false;
      o.renderOrder = FP_RENDER_ORDER;
      for (const m of Array.isArray(o.material) ? o.material : [o.material]) {
        // This rig's prop materials are its own (tinted clones, the noodle's new one).
        m.depthTest = false;
        m.depthWrite = false;
        materials.push(m);
      }
    });
    props.push(p);
  }
  rig.mixer.stopAllAction();
  rig.mixer.uncacheRoot(rig.root);
  setWeapon(rig, null);
  return { rig, props, materials };
}

