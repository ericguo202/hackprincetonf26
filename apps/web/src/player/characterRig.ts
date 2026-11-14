// Lane C: build a playable character instance from the loaded glTF. Plain
// three.js, no React, so it is tested against the real model
// (characterRig.test.ts). Character.tsx owns one rig per rendered character.
//
// Per instance: a skeleton-aware clone (two players must not share bones),
// only the kept weapon, scaled to CHARACTER.heightM with the feet at the
// origin, and an AnimationMixer with one action per clip.
//
// Per source model (cached): the scale and each clip's foot lift. Some clips
// put the lowest foot below the bind-pose floor (Run_Gun dips about 5 cm at
// foot strike); lift raises the model by that much while the clip plays, so
// feet touch the floor instead of sinking, and Idle (lift 0) does not hover.

import {
  AnimationMixer,
  Box3,
  Mesh,
  MeshStandardMaterial,
  SkinnedMesh,
  type AnimationAction,
  type AnimationClip,
  type Material,
  type Object3D,
} from "three";
import { clone as cloneSkinned } from "three/examples/jsm/utils/SkeletonUtils.js";

import { CHARACTER } from "./characterAsset";
import { CLIPS, MOVING_SHOOT, ONE_SHOT, type PackClip } from "./clips";

export type Rig = {
  root: Object3D;
  mixer: AnimationMixer;
  actions: Map<string, AnimationAction>;
  /** Metres to raise the model while a clip plays, so its lowest foot meets the floor. */
  lift: Map<string, number>;
  scale: number;
};

type Measured = { scale: number; baseY: number; lift: Map<string, number> };
const measured = new WeakMap<Object3D, Measured>();

/** Clips a character actually plays: the mapped six, their variants and follow-ups. */
export function usedClips(): PackClip[] {
  const set = new Set<PackClip>([...Object.values(CLIPS), MOVING_SHOOT]);
  for (const v of Object.values(ONE_SHOT)) if (v?.then) set.add(v.then);
  return [...set];
}

function prepare(root: Object3D): void {
  for (const name of CHARACTER.weapons) {
    if (name === CHARACTER.keepWeapon) continue;
    root.getObjectByName(name)?.removeFromParent();
  }
  const weapon = root.getObjectByName(CHARACTER.keepWeapon);
  weapon?.traverse((o) => {
    if (!(o instanceof Mesh)) return;
    // Clone, so tinting one character's blaster never touches the shared source material.
    const tint = (m: Material) => {
      const c = m.clone();
      if (c instanceof MeshStandardMaterial) c.color.set(CHARACTER.foamColor);
      return c;
    };
    o.material = Array.isArray(o.material) ? o.material.map(tint) : tint(o.material);
  });
  root.traverse((o) => {
    if (o instanceof Mesh) {
      o.castShadow = true;
      o.receiveShadow = true;
    }
    // Skinned bounds follow the bind pose, not the animation: never cull them.
    if (o instanceof SkinnedMesh) o.frustumCulled = false;
  });
}

function lowestY(root: Object3D): number {
  root.updateMatrixWorld(true);
  return new Box3().setFromObject(root, true).min.y;
}

/** Scale, base offset and per-clip lift for a source model, on a throwaway clone. */
function measure(source: Object3D, clips: AnimationClip[]): Measured {
  const hit = measured.get(source);
  if (hit) return hit;

  const probe = cloneSkinned(source);
  prepare(probe);
  probe.updateMatrixWorld(true);
  const rest = new Box3().setFromObject(probe, true);
  const scale = CHARACTER.heightM / (rest.max.y - rest.min.y);
  const baseY = -rest.min.y * scale;
  probe.scale.setScalar(scale);
  probe.position.y = baseY;

  const mixer = new AnimationMixer(probe);
  const lift = new Map<string, number>();
  const SAMPLES = 24;
  for (const name of usedClips()) {
    const clip = clips.find((c) => c.name === name);
    if (!clip) continue;
    mixer.stopAllAction();
    mixer.clipAction(clip).reset().play();
    let lo = Infinity;
    for (let i = 0; i <= SAMPLES; i++) {
      mixer.setTime((clip.duration * i) / SAMPLES);
      lo = Math.min(lo, lowestY(probe));
    }
    lift.set(name, Math.max(0, -lo));
  }
  mixer.stopAllAction();
  mixer.uncacheRoot(probe);

  const m = { scale, baseY, lift };
  measured.set(source, m);
  return m;
}

export function buildRig(source: Object3D, clips: AnimationClip[]): Rig {
  const { scale, baseY, lift } = measure(source, clips);
  const root = cloneSkinned(source);
  prepare(root);
  root.scale.setScalar(scale);
  root.position.y = baseY;

  const mixer = new AnimationMixer(root);
  const actions = new Map<string, AnimationAction>();
  for (const clip of clips) actions.set(clip.name, mixer.clipAction(clip));
  return { root, mixer, actions, lift, scale };
}

/** Lift for the current blend: each playing action's lift, by its weight. */
export function blendedLift(rig: Rig): number {
  let sum = 0;
  let weight = 0;
  for (const [name, action] of rig.actions) {
    if (!action.isRunning() && !action.isScheduled()) continue;
    const w = action.getEffectiveWeight();
    if (w <= 0) continue;
    sum += (rig.lift.get(name) ?? 0) * w;
    weight += w;
  }
  return weight > 0 ? sum / weight : 0;
}
