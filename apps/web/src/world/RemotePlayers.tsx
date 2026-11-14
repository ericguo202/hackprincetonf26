// Lane C: other players in my zone, each as a <Character>, interpolated
// INTERP_DELAY_S (100 ms) behind the newest patch (AGENTS.md section 8).
//
// Reads store.players (Lane D's net code, or the VITE_DEV_REMOTES fakes) and
// never writes it. Every patch is stamped on arrival into a per-player buffer;
// each frame every avatar is placed at its interpolated sample, and the same
// samples become the hitboxes the blaster tests (remoteTargets.ts), so what
// you shoot at is exactly what is drawn.

import { useEffect, useRef, useState, type MutableRefObject } from "react";
import { useFrame } from "@react-three/fiber";
import type { Group } from "three";

import type { AnimState } from "../../../../contracts/game-state";
import { useStore } from "../store";
import { Character } from "../player/Character";
import { FOOT_BELOW_CENTRE_M } from "../player/characterAsset";
import { MOVEMENT } from "../player/movement";
import { REMOTE_PRIORITY } from "./frameOrder";
import {
  MOVING_SPEED,
  footBelowCentre,
  hitboxFromBody,
  pushSnapshot,
  remotesInZone,
  sampleDelayed,
  type Snapshot,
} from "./remoteInterp";
import { setRemoteHitboxes } from "./remoteTargets";

type Buffers = MutableRefObject<Map<string, Snapshot[]>>;

const nowS = () => performance.now() / 1000;

export function RemotePlayers({ zone }: { zone: string }) {
  // Re-render only when the set of players in my zone changes, not on every patch.
  const idsKey = useStore((s) =>
    remotesInZone(s.players, zone, s.me.id)
      .map((p) => p.id)
      .join("|"),
  );
  const ids = idsKey ? idsKey.split("|") : [];
  const buffers = useRef(new Map<string, Snapshot[]>());

  // Stamp every patch on arrival.
  useEffect(() => {
    const feed = (players: ReturnType<typeof useStore.getState>["players"]) => {
      const t = nowS();
      const map = buffers.current;
      for (const p of Object.values(players)) {
        map.set(
          p.id,
          pushSnapshot(map.get(p.id) ?? [], { t, pos: p.pos, yaw: p.yaw, anim: p.anim, zone: p.zone, alive: p.alive }),
        );
      }
      for (const id of [...map.keys()]) if (!(id in players)) map.delete(id);
    };
    feed(useStore.getState().players);
    const unsub = useStore.subscribe((s, prev) => {
      if (s.players !== prev.players) feed(s.players);
    });
    return () => {
      unsub();
      setRemoteHitboxes([]);
    };
  }, []);

  // Hitboxes from the same delayed samples the avatars are drawn at.
  useFrame(() => {
    const t = nowS();
    const boxes = [];
    for (const id of ids) {
      const s = sampleDelayed(buffers.current.get(id) ?? [], t);
      if (s && s.alive && s.zone === zone) boxes.push(hitboxFromBody(id, s.pos, s.anim));
    }
    setRemoteHitboxes(boxes);
  }, REMOTE_PRIORITY);

  return (
    <>
      {ids.map((id) => (
        <RemoteAvatar key={id} id={id} zone={zone} buffers={buffers} />
      ))}
    </>
  );
}

function RemoteAvatar({ id, zone, buffers }: { id: string; zone: string; buffers: Buffers }) {
  const group = useRef<Group>(null);
  const [look, setLook] = useState<{ anim: AnimState; moving: boolean }>({ anim: "idle", moving: false });
  const lookRef = useRef(look);
  const footBelow = useRef(footBelowCentre("idle"));

  useFrame((_, dt) => {
    const s = sampleDelayed(buffers.current.get(id) ?? [], nowS());
    if (!s || !group.current) return;
    // A patch can say "other zone" before the id leaves my list; never draw it here.
    group.current.visible = s.zone === zone;
    // The anim flips to crouch at once while the body glides down (~0.2 s), so
    // ease the feet offset instead of popping the model 0.3 m into the floor.
    const target = footBelowCentre(s.anim);
    footBelow.current += (target - footBelow.current) * Math.min(1, dt * 12);
    group.current.position.set(s.pos[0], s.pos[1] - footBelow.current, s.pos[2]);
    group.current.rotation.y = s.yaw;
    const next = { anim: s.anim, moving: s.speed > MOVING_SPEED };
    if (next.anim !== lookRef.current.anim || next.moving !== lookRef.current.moving) {
      lookRef.current = next;
      setLook(next);
    }
  }, REMOTE_PRIORITY);

  return (
    <group ref={group} visible={false}>
      <Character animState={look.anim} moving={look.moving} fallback={<RemoteCapsule />} />
    </group>
  );
}

/** Stand-in if the model is missing: the capsule, back at the body centre above the feet. */
function RemoteCapsule() {
  return (
    <mesh position-y={FOOT_BELOW_CENTRE_M} castShadow>
      <capsuleGeometry args={[MOVEMENT.capsuleRadius, MOVEMENT.capsuleHalfHeight * 2]} />
      <meshStandardMaterial color="#e8743b" />
    </mesh>
  );
}
