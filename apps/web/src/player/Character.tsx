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
import { LoopRepeat, type AnimationAction, type Group } from "three";

import type { AnimState, Weapon } from "../../../../contracts/game-state";
import { LoadBoundary } from "../world/Glb";
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
  weapon?: Weapon;
  url?: string;
  /** Rendered while the model is checked or loading, and if it is missing or broken. */
  fallback?: ReactNode;
};

export function Character({ animState, moving = false, weapon = "blaster", url = CHARACTER.url, fallback = null }: Props) {
  const mesh = useMeshState(url, url);
  if (mesh !== "ok") return <>{fallback}</>;
  return (
    <LoadBoundary url={url} label={url} fallback={fallback}>
      <Suspense fallback={fallback}>
        <CharacterModel url={url} animState={animState} moving={moving} weapon={weapon} />
      </Suspense>
    </LoadBoundary>
  );
}

function CharacterModel({
  url,
  animState,
  moving,
  weapon,
}: {
  url: string;
  animState: AnimState;
  moving: boolean;
  weapon: Weapon;
}) {
  const gltf = usePackGLTF(url);
  const rig = useMemo(() => buildRig(gltf.scene, gltf.animations), [gltf]);
  useEffect(() => setWeapon(rig, weapon), [rig, weapon]);
  const liftGroup = useRef<Group>(null);
  const current = useRef<AnimationAction | null>(null);

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
    if (liftGroup.current) liftGroup.current.position.y = blendedLift(rig);
    updateNoodleJiggle(rig, step);
  });

  return (
    <group ref={liftGroup}>
      <primitive object={rig.root} />
    </group>
  );
}
