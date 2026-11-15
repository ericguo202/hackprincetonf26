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
  CylinderGeometry,
  Group,
  Mesh,
  MeshStandardMaterial,
  Quaternion,
  SkinnedMesh,
  Vector3,
  type AnimationAction,
  type AnimationClip,
  type Material,
  type Object3D,
} from "three";
import { clone as cloneSkinned } from "three/examples/jsm/utils/SkeletonUtils.js";

import type { Weapon } from "../../../../contracts/game-state";
import { CHARACTER } from "./characterAsset";
import { CLIPS, CLIP_TRIM, MOVING_SHOOT, ONE_SHOT, type PackClip } from "./clips";

export type NoodleJiggle = {
  mesh: Mesh;
  basePositions: Float32Array;
  height: number;
  prevTipWorld: Vector3;
  tipOffset: Vector3;
  tipVel: Vector3;
  initialized: boolean;
};

export type Rig = {
  root: Object3D;
  mixer: AnimationMixer;
  /**
   * Clips by name. Deliberately no cached AnimationAction objects: after
   * mixer.uncacheRoot() (unmount, including React StrictMode's dev
   * unmount/remount) an old action has no bindings and play() throws
   * "Cannot set properties of undefined (setting '_cacheIndex')". Always go
   * through actionFor(), which asks the mixer and gets a live action.
   */
  clips: Map<string, AnimationClip>;
  /** Metres to raise the model while a clip plays, so its lowest foot meets the floor. */
  lift: Map<string, number>;
  scale: number;
  /** The kit's blaster prop and the procedural pool noodle; setWeapon shows one. */
  props: Record<Weapon, Object3D | null>;
  noodleJiggle?: NoodleJiggle | null;
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

/** CLIP_TRIM applied (Duck -> its crouch hold). Cached per source clip list. */
const trimmedCache = new WeakMap<AnimationClip[], AnimationClip[]>();
export function playableClips(clips: AnimationClip[]): AnimationClip[] {
  let out = trimmedCache.get(clips);
  if (!out) {
    out = clips.map((c) => {
      const t = CLIP_TRIM[c.name as PackClip];
      if (!t) return c;
      // trim() cuts the tracks to the clip's duration, so set that first.
      const cut = c.clone();
      cut.duration = Math.min(t.endS, c.duration);
      return cut.trim();
    });
    trimmedCache.set(clips, out);
  }
  return out;
}

export function buildRig(source: Object3D, rawClips: AnimationClip[]): Rig {
  const clips = playableClips(rawClips);
  const { scale, baseY, lift } = measure(source, clips);
  const root = cloneSkinned(source);
  prepare(root);
  root.scale.setScalar(scale);
  root.position.y = baseY;

  const mixer = new AnimationMixer(root);
  const noodleRes = attachNoodle(root);
  const rig: Rig = {
    root,
    mixer,
    clips: new Map(clips.map((c) => [c.name, c])),
    lift,
    scale,
    props: { blaster: root.getObjectByName(CHARACTER.keepWeapon) ?? null, noodle: noodleRes?.holder ?? null },
    noodleJiggle: noodleRes?.jiggle ?? null,
  };
  setWeapon(rig, "blaster");
  return rig;
}

/** Show the held weapon's prop, hide the other. */
export function setWeapon(rig: Rig, weapon: Weapon): void {
  for (const [w, prop] of Object.entries(rig.props)) if (prop) prop.visible = w === weapon;
}

/**
 * The pool noodle: a foam cylinder on the blaster's hand bone, with the
 * blaster's local placement, running along the blaster's long axis (its
 * local +X: the barrel reaches 3.78 forward of the grip, 2.52 behind). Sized
 * in world metres after the rig is scaled. Hidden until selected.
 */
function attachNoodle(root: Object3D): { holder: Object3D; jiggle: NoodleJiggle } | null {
  const blaster = root.getObjectByName(CHARACTER.keepWeapon);
  const hand = blaster?.parent;
  if (!blaster || !hand) return null;
  const n = CHARACTER.noodle;
  const holder = new Group();
  holder.name = n.name;
  holder.position.copy(blaster.position);
  holder.quaternion.copy(blaster.quaternion);
  holder.scale.copy(blaster.scale);
  hand.add(holder);
  root.updateMatrixWorld(true);
  // Local units per metre at this point in the hierarchy.
  const k = 1 / holder.getWorldScale(new Vector3()).x;
  const height = n.lengthM * k;
  const radius = n.radiusM * k;
  const geom = new CylinderGeometry(radius, radius, height, 10, 8);
  const basePositions = new Float32Array(geom.attributes.position.array);

  const mesh = new Mesh(
    geom,
    new MeshStandardMaterial({ color: n.color, roughness: 0.9 }),
  );
  // Held straight up vertically in the hand.
  // In holder (blaster) space, the world UP vector is [-0.4735, 0.6360, 0.6094].
  const upDir = new Vector3(-0.4735, 0.636, 0.6094).normalize();
  mesh.quaternion.setFromUnitVectors(new Vector3(0, 1, 0), upDir);
  mesh.position.copy(upDir.clone().multiplyScalar((n.lengthM / 2 - n.gripBackM) * k));
  mesh.castShadow = true;
  holder.add(mesh);
  holder.visible = false;

  const jiggle: NoodleJiggle = {
    mesh,
    basePositions,
    height,
    prevTipWorld: new Vector3(),
    tipOffset: new Vector3(),
    tipVel: new Vector3(),
    initialized: false,
  };

  return { holder, jiggle };
}

const tmpTipWorld = new Vector3();
const tmpQuat = new Quaternion();
const tmpDelta = new Vector3();

/**
 * Procedural floppy foam jiggle physics for the pool noodle (Option A).
 * Runs each frame on the visible noodle mesh without Rapier rigidbodies.
 */
export function updateNoodleJiggle(rig: Rig, dt: number): void {
  const j = rig.noodleJiggle;
  if (!j || !rig.props.noodle?.visible) {
    if (j) j.initialized = false;
    return;
  }
  const mesh = j.mesh;
  // Local tip is at (0, height / 2, 0) in mesh space
  mesh.localToWorld(tmpTipWorld.set(0, j.height / 2, 0));

  if (!j.initialized) {
    j.prevTipWorld.copy(tmpTipWorld);
    j.tipOffset.set(0, 0, 0);
    j.tipVel.set(0, 0, 0);
    j.initialized = true;
    return;
  }

  // World displacement of the tip this frame
  tmpDelta.subVectors(tmpTipWorld, j.prevTipWorld);
  j.prevTipWorld.copy(tmpTipWorld);

  // Convert world delta into mesh local coordinate space
  mesh.getWorldQuaternion(tmpQuat).invert();
  tmpDelta.applyQuaternion(tmpQuat);

  const step = Math.min(dt, 0.05);
  // Damped spring oscillator (foam noodle)
  const k = 150; // spring restoring stiffness
  const c = 10; // damping
  const accelLimit = 40;
  const lagX = Math.max(-accelLimit, Math.min(accelLimit, -(tmpDelta.x / step) * 1.6));
  const lagZ = Math.max(-accelLimit, Math.min(accelLimit, -(tmpDelta.z / step) * 1.6));

  const ax = -k * j.tipOffset.x - c * j.tipVel.x + lagX;
  const az = -k * j.tipOffset.z - c * j.tipVel.z + lagZ;

  j.tipVel.x += ax * step;
  j.tipVel.z += az * step;
  j.tipOffset.x += j.tipVel.x * step;
  j.tipOffset.z += j.tipVel.z * step;

  // Max bend: up to 35% of noodle length
  const maxBend = j.height * 0.35;
  const bendDist = Math.hypot(j.tipOffset.x, j.tipOffset.z);
  if (bendDist > maxBend && bendDist > 1e-4) {
    const scale = maxBend / bendDist;
    j.tipOffset.x *= scale;
    j.tipOffset.z *= scale;
  }

  // Deform mesh vertices along the bend curve
  const pos = mesh.geometry.attributes.position;
  const count = pos.count;
  const halfH = j.height / 2;
  const ox = j.tipOffset.x;
  const oz = j.tipOffset.z;

  for (let i = 0; i < count; i++) {
    const origY = j.basePositions[i * 3 + 1];
    // t: 0 at base (-halfH), 1 at tip (+halfH)
    const t = Math.max(0, Math.min(1, (origY + halfH) / j.height));
    const bendFactor = t * t; // quadratic curve
    pos.setX(i, j.basePositions[i * 3] + ox * bendFactor);
    pos.setZ(i, j.basePositions[i * 3 + 2] + oz * bendFactor);
  }
  pos.needsUpdate = true;
}

/** A live action for a clip: the mixer's cached one, or a fresh one after an uncache. */
export function actionFor(rig: Rig, name: string): AnimationAction | null {
  const clip = rig.clips.get(name);
  return clip ? rig.mixer.clipAction(clip) : null;
}

/** Lift for the current blend: each playing action's lift, by its weight. */
export function blendedLift(rig: Rig): number {
  let sum = 0;
  let weight = 0;
  for (const [name, clip] of rig.clips) {
    // existingAction: never creates one, so measuring the blend allocates nothing.
    const action = rig.mixer.existingAction(clip);
    if (!action || (!action.isRunning() && !action.isScheduled())) continue;
    const w = action.getEffectiveWeight();
    if (w <= 0) continue;
    sum += (rig.lift.get(name) ?? 0) * w;
    weight += w;
  }
  return weight > 0 ? sum / weight : 0;
}
