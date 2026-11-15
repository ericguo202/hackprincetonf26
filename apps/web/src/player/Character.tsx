// Lane C: one animated character. Reusable: it takes the wire AnimState as a
// prop and reads nothing else, so Lane D renders remote players with it too.
//
//   <Character animState={p.anim} moving={speed > 0.5} />
//
// Origin is the FEET. Place it at the ground point: for a NetPlayer, that is
// pos - [0, FOOT_BELOW_CENTRE_M, 0] (characterAsset.ts), rotated by yaw.
//
// Clips come from clips.ts (clipFor), crossfaded over CROSSFADE_S (0.2 s) by
// an AnimationMixer. One-shots (Jump, HitReact, Death) play once and hold, or
// hand over (Jump -> Jump_Idle). The model is probed before loading, like
// every other mesh (meshProbe.ts): missing or broken, `fallback` renders.

import { Suspense, useEffect, useMemo, useRef, type ReactNode } from "react";
import { useFrame } from "@react-three/fiber";
import { Color, LoopRepeat, Mesh, MeshStandardMaterial, type AnimationAction, type Group, type Material, type Object3D } from "three";

import type { AnimState, Weapon } from "../../../../contracts/game-state";
import { LoadBoundary } from "../world/Glb";
import { flashLevel, popScale } from "../world/hitFlash";
import { usePackGLTF } from "../world/gltf";
import { useMeshState } from "../world/meshProbe";
import { CHARACTER } from "./characterAsset";
import { actionFor, blendedLift, buildRig, crossfadeTo, setWeapon, updateNoodleJiggle } from "./characterRig";
import { CROSSFADE_S, clipFor } from "./clips";

type Props = {
  animState: AnimState;
  /** Optional: true while the body is moving, so shoot uses the running variant. */
  moving?: boolean;
  /** Which prop the character holds. Remote players: always the blaster for now (no wire field). */
  weapon?: Weapon | null;
  url?: string;
  /** Rendered while the model is checked or loading, and if it is missing or broken. */
  fallback?: ReactNode;
  /** Remote players: their id, so a hit of mine flashes this model (world/hitFlash.ts). */
  flashId?: string;
};

/** Tint of a hit flash (emissive), at full strength. */
const HIT_FLASH_COLOR = new Color("#ffd8a8");
const HIT_FLASH_INTENSITY = 1.4;

type Flashable = { mat: MeshStandardMaterial; emissive: Color; intensity: number };

/** Tint the rig's own materials toward the flash colour by `level` (0 restores them). */
function applyFlash(f: { list: Flashable[]; level: number }, level: number): void {
  f.level = level;
  for (const m of f.list) {
    m.mat.emissive.copy(m.emissive).lerp(HIT_FLASH_COLOR, level);
    m.mat.emissiveIntensity = m.intensity + (HIT_FLASH_INTENSITY - m.intensity) * level;
  }
}

/**
 * Give this rig its own copies of its standard materials (the skinned clone
 * shares them with every other character), so only this player flashes.
 * Returns the copies with their original emissive, and a dispose.
 */
function ownMaterials(root: Object3D): { list: Flashable[]; dispose: () => void } {
  const list: Flashable[] = [];
  const copies = new Map<Material, MeshStandardMaterial>();
  const own = (m: Material): Material => {
    if (!(m instanceof MeshStandardMaterial)) return m;
    let c = copies.get(m);
    if (!c) {
      c = m.clone();
      copies.set(m, c);
      list.push({ mat: c, emissive: c.emissive.clone(), intensity: c.emissiveIntensity });
    }
    return c;
  };
  root.traverse((o) => {
    const mesh = o as Mesh;
    if (!mesh.isMesh) return;
    mesh.material = Array.isArray(mesh.material) ? mesh.material.map(own) : own(mesh.material);
  });
  return { list, dispose: () => list.forEach((f) => f.mat.dispose()) };
}

export function Character({
  animState,
  moving = false,
  weapon = "blaster",
  url = CHARACTER.url,
  fallback = null,
  flashId,
}: Props) {
  const mesh = useMeshState(url, url);
  if (mesh !== "ok") return <>{fallback}</>;
  return (
    <LoadBoundary url={url} label={url} fallback={fallback}>
      <Suspense fallback={fallback}>
        <CharacterModel url={url} animState={animState} moving={moving} weapon={weapon} flashId={flashId} />
      </Suspense>
    </LoadBoundary>
  );
}

function CharacterModel({
  url,
  animState,
  moving,
  weapon,
  flashId,
}: {
  url: string;
  animState: AnimState;
  moving: boolean;
  weapon: Weapon | null;
  flashId?: string;
}) {
  const gltf = usePackGLTF(url);
  const rig = useMemo(() => buildRig(gltf.scene, gltf.animations), [gltf]);
  useEffect(() => setWeapon(rig, weapon), [rig, weapon]);
  const liftGroup = useRef<Group>(null);
  const current = useRef<AnimationAction | null>(null);
  // Hit flash (remote players only): this rig's own materials, and the level last applied.
  const flash = useRef<{ list: Flashable[]; level: number } | null>(null);
  useEffect(() => {
    if (!flashId) return;
    const own = ownMaterials(rig.root);
    flash.current = { list: own.list, level: 0 };
    return () => {
      flash.current = null;
      own.dispose();
    };
  }, [rig, flashId]);

  // One-shot hand-over (Jump -> Jump_Idle) when a clip finishes.
  useEffect(() => {
    const onFinished = (e: { action: AnimationAction }) => {
      if (e.action !== current.current) return;
      const name = e.action.getClip().name;
      const plan = clipFor(animState, moving);
      if (plan.clip !== name || !plan.then) return;
      const next = actionFor(rig, plan.then);
      if (!next) return;
      next.reset().setLoop(LoopRepeat, Infinity).play();
      next.crossFadeFrom(e.action, CROSSFADE_S, false);
      current.current = next;
    };
    rig.mixer.addEventListener("finished", onFinished);
    return () => rig.mixer.removeEventListener("finished", onFinished);
  }, [rig, animState, moving]);

  // New state: crossfade to its clip.
  useEffect(() => {
    // crossfadeTo: a looping clip already playing carries on; a one-shot replays
    // (`if (prev === next && !plan.once) return`).
    current.current = crossfadeTo(rig, current.current, clipFor(animState, moving));
  }, [rig, animState, moving]);

  useEffect(
    () => () => {
      rig.mixer.stopAllAction();
      rig.mixer.uncacheRoot(rig.root);
      // The action is gone with the cache; StrictMode's remount (same rig) must start clean.
      current.current = null;
    },
    [rig],
  );

  useFrame((_, dt) => {
    const step = Math.min(dt, 0.1);
    rig.mixer.update(step);
    if (liftGroup.current) {
      liftGroup.current.position.y = blendedLift(rig);
      // Elimination pop (remote players): scale up and back, about the feet.
      if (flashId) liftGroup.current.scale.setScalar(popScale(flashId));
    }
    updateNoodleJiggle(rig, step);
    const f = flash.current;
    if (f && flashId) {
      const level = flashLevel(flashId);
      if (level !== f.level) applyFlash(f, level);
    }
  });

  return (
    <group ref={liftGroup}>
      <primitive object={rig.root} />
    </group>
  );
}
