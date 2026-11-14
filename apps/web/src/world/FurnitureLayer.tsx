// Lane C: renders the furniture the agent placed, for the house the local
// player is standing in. Reads the store; never writes network state.
//
// Inputs from the pack, not the store: the furniture catalog and the house's
// rooms (props). The plan itself comes from store.houses[zone] (Lane D).
//
// Per piece (AGENTS.md section 11):
//   - the mesh, or a labelled box sized from catalog size_m when the .glb is
//     missing (same LoadBoundary fallback as the block; also the shipping
//     fallback if real furniture meshes never land)
//   - a fixed cuboid collider from size_m, in the FURNITURE group: it stops
//     the player and darts, and matches the footprint every other rule uses,
//     whether or not the mesh loaded
//   - a 0.45 s drop-in; the collider is at its final pose from the first frame
//
// On every plan change: the old pieces unmount (no ghost colliders), the
// local player is pushed out of anything that landed on them (the physics
// body itself, via playerControl), and indoor spawns are recomputed.

import { Suspense, useLayoutEffect, useMemo, useRef } from "react";
import { useFrame } from "@react-three/fiber";
import { CuboidCollider, RigidBody } from "@react-three/rapier";
import type { Group } from "three";

import { useStore, type PlacedObject } from "../store";
import type { Asset, Room } from "../contracts.gen";
import { localPlayer } from "../player/playerControl";
import { pushOut, rotationY } from "./geometry";
import { usePackGLTF } from "./gltf";
import { FURNITURE_COLLIDER } from "./collisionGroups";
import { LoadBoundary } from "./Glb";
import { useMeshState } from "./meshProbe";
import { WorldLabel } from "./WorldLabel";
import { footprints, houseSpawns } from "./furniturePlan";
import { useIndoorSpawns } from "./indoorSpawns";
import { roomIdAt, warnOnce } from "./pack";

type Props = {
  /** asset_id -> Asset, from the pack's furniture catalog. */
  catalog: Record<string, Asset>;
  /** Rooms of the house the player is in, from houses/<id>/rooms.json. */
  rooms: Room[];
  /**
   * Published root (PACK_BASE_URL's root); asset `file` paths (furniture/v<N>/...,
   * contracts v2.3.0) resolve against it. "" is a valid root (the site root, as
   * for /packs/<block>/v<N> served locally); null means there is no pack at all.
   */
  packBaseUrl: string | null;
  showLabels?: boolean;
};

const DROP_S = 0.45;
const DROP_FROM_M = 1.2;
const STAGGER_S = 0.12;
/** Thinner than this (a rug) gets no collider: nothing to stop. */
const MIN_COLLIDER_H = 0.05;

const BOX_COLORS: Record<string, string> = {
  sofa: "#8e9bb0",
  couch: "#8e9bb0",
  sectional: "#7f8ea6",
  armchair: "#a7927c",
  coffee_table: "#9b7b5b",
  tv_stand: "#3d4148",
  rug: "#b9a27e",
  bookshelf: "#8a6a4c",
  floor_lamp: "#e6d8b8",
  plant: "#6f9a5e",
  beanbag: "#d97b4a",
};

function FallbackBox({ asset }: { asset: Asset }) {
  const { w, d, h } = asset.size_m;
  return (
    <group>
      <mesh position={[0, h / 2, 0]} castShadow receiveShadow>
        <boxGeometry args={[w, h, d]} />
        <meshStandardMaterial color={BOX_COLORS[asset.category] ?? "#a0a0a0"} />
      </mesh>
      {/* Front face is +Z (asset convention), so the label sits there. */}
      <WorldLabel position={[0, Math.max(h * 0.5, 0.1), d / 2 + 0.01]} style={boxLabelStyle}>
        {asset.name}
      </WorldLabel>
    </group>
  );
}

function MeshModel({ url }: { url: string }) {
  const { scene } = usePackGLTF(url);
  // Clone per instance: two couches from one glb must not share a transform.
  const model = useMemo(() => scene.clone(true), [scene]);
  return <primitive object={model} />;
}

function FurniturePiece({
  obj,
  asset,
  url,
  index,
  showLabel,
}: {
  obj: PlacedObject;
  asset: Asset;
  url: string | null;
  index: number;
  showLabel: boolean;
}) {
  const group = useRef<Group>(null);
  const elapsed = useRef(-index * STAGGER_S);
  const { w, d, h } = asset.size_m;
  const box = <FallbackBox asset={asset} />;
  // Probe before loading: a missing .glb never reaches the loader (meshProbe.ts).
  const mesh = useMeshState(url, asset.file);

  useFrame((_, dt) => {
    if (!group.current || elapsed.current > DROP_S) return;
    elapsed.current += Math.min(dt, 0.1);
    const t = Math.min(1, Math.max(0, elapsed.current / DROP_S));
    const e = 1 - Math.pow(1 - t, 3); // easeOutCubic
    group.current.position.y = (1 - e) * DROP_FROM_M;
    group.current.scale.setScalar(0.92 + 0.08 * e);
  });

  return (
    <RigidBody
      type="fixed"
      colliders={false}
      position={[obj.x, obj.y, obj.z]}
      rotation={[0, rotationY(obj.rot_deg), 0]}
    >
      {h >= MIN_COLLIDER_H ? (
        <CuboidCollider
          args={[w / 2, h / 2, d / 2]}
          position={[0, h / 2, 0]}
          collisionGroups={FURNITURE_COLLIDER}
        />
      ) : null}
      {/* Starts up in the air; useFrame drops it in. The collider above does not move. */}
      <group ref={group} position-y={DROP_FROM_M}>
        {url && mesh === "ok" ? (
          <LoadBoundary url={url} label={asset.file} fallback={box}>
            <Suspense fallback={box}>
              <MeshModel url={url} />
            </Suspense>
          </LoadBoundary>
        ) : (
          box
        )}
      </group>
      {showLabel && obj.reason ? (
        <WorldLabel position={[0, h + 0.3, 0]} style={reasonStyle}>
          {obj.reason}
        </WorldLabel>
      ) : null}
    </RigidBody>
  );
}

export function FurnitureLayer({ catalog, rooms, packBaseUrl, showLabels = true }: Props) {
  const zone = useStore((s) => s.me.zone);
  const setMe = useStore((s) => s.setMe);
  const house = useStore((s) => (s.me.zone === "block" ? undefined : s.houses[s.me.zone]));
  const setSpawns = useIndoorSpawns((s) => s.set);

  // Memoized so `rects` below only rebuilds when the plan does, not every render.
  const objects = useMemo(() => house?.objects ?? [], [house?.objects]);
  const planId = house?.plan_id ?? "none";
  const rects = useMemo(() => footprints(objects, catalog), [objects, catalog]);

  // On every plan change, before the next physics step (layout effect, so the
  // new colliders never get a step to shove the player around):
  //   1. push the local player out of anything that landed on them, moving
  //      the physics body, not just the store
  //   2. recompute indoor spawns from the new free floor
  // Deterministic, so every client agrees without the server deciding.
  useLayoutEffect(() => {
    if (zone === "block") return;
    setSpawns(zone, houseSpawns(rooms, rects));

    const player = localPlayer();
    if (!player || rects.length === 0) return;
    const [x, y, z] = player.getPos();
    const roomId = roomIdAt([x, y, z], rooms);
    const room = rooms.find((r) => r.id === roomId);
    if (!room) return;

    const moved = pushOut([x, z], room, rects);
    if (moved === null) {
      // Nowhere free in this room: the nearest door is always walkable.
      const door = room.openings.find((o) => o.type === "door");
      if (door) player.teleport([(door.a[0] + door.b[0]) / 2, y, (door.a[1] + door.b[1]) / 2]);
      return;
    }
    if (moved[0] !== x || moved[1] !== z) {
      player.teleport([moved[0], y, moved[1]]);
      setMe({ roomId: room.id });
    }
    // Plan changes only, not every step the player takes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [planId, zone, rects]);

  if (zone === "block" || objects.length === 0) return null;
  if (packBaseUrl === null) {
    warnOnce("furniture-nomesh", "[furniture] no pack base URL: every piece renders as a labelled box");
  }

  // key={planId} tears down every RigidBody on a re-furnish. Mutating
  // positions in place leaves stale colliders, which shows up as darts
  // stopping in mid-air where the old couch used to be.
  return (
    <group key={planId}>
      {objects.map((o, i) => {
        const asset = catalog[o.asset_id];
        if (!asset) {
          warnOnce(`noasset:${o.asset_id}`, `[furniture] no catalog entry for ${o.asset_id}; skipping it`);
          return null;
        }
        return (
          <FurniturePiece
            key={`${planId}-${o.instance_id}`}
            obj={o}
            asset={asset}
            url={packBaseUrl === null ? null : `${packBaseUrl}/${asset.file}`}
            index={i}
            showLabel={showLabels}
          />
        );
      })}
    </group>
  );
}

const reasonStyle: React.CSSProperties = {
  background: "rgba(20,20,20,0.82)",
  color: "white",
  padding: "4px 8px",
  borderRadius: 6,
  fontSize: 12,
  whiteSpace: "nowrap",
  pointerEvents: "none",
};

const boxLabelStyle: React.CSSProperties = {
  color: "#10141a",
  background: "rgba(255,255,255,0.75)",
  padding: "1px 5px",
  borderRadius: 3,
  fontSize: 11,
  fontWeight: 600,
  whiteSpace: "nowrap",
  pointerEvents: "none",
};
