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
//
// Pieces are keyed one by one on their pose (furnitureKey.pieceKey), so a
// manual edit (messages.md v2.6.0) remounts only the piece that moved: it
// gets a fresh RigidBody at the new pose and the rest of the room stays put.
// Only pieces new to this render drop in, staggered among themselves; a
// hand-placed or hand-moved piece (empty reason) settles from a short height.
// While this player carries a piece in build mode (buildState), that piece
// is hidden here, collider included; everyone else still sees it.

import { Suspense, useCallback, useEffect, useMemo, useRef } from "react";
import { useFrame } from "@react-three/fiber";
import { CuboidCollider, RigidBody } from "@react-three/rapier";
import type { Group } from "three";

import { useStore, type PlacedObject } from "../store";
import type { Asset, Room } from "../contracts.gen";
import { rotationY } from "./geometry";
import { usePackGLTF } from "./gltf";
import { FURNITURE_COLLIDER } from "./collisionGroups";
import { LoadBoundary } from "./Glb";
import { useMeshState } from "./meshProbe";
import { WorldLabel } from "./WorldLabel";
import { footprints } from "./furniturePlan";
import { pieceKey } from "./furnitureKey";
import { hiddenLocally, useBuild } from "./buildState";
import { useFurnitureLayout, useStableFurniture } from "./useFurniturePlan";
import { useIndoorSpawns } from "./indoorSpawns";
import { warnOnce } from "./pack";
import { footprintRadius, useLandingPuffs } from "./landingPuff";
import { play } from "../player/sfx";
import { ScreenVideo } from "./TvScreen";
import { TV_VIDEO_URL, findScreen } from "./tvVideo";

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
/** A hand-placed piece settles from just above the ghost, not from the ceiling. */
const SHORT_DROP_M = 0.15;
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
  tv: "#1d1f24",
  crib: "#d8c3a0",
  rug: "#b9a27e",
  bookshelf: "#8a6a4c",
  floor_lamp: "#e6d8b8",
  plant: "#6f9a5e",
  beanbag: "#d97b4a",
};

/**
 * zone -> pose keys of the pieces drawn there last, written after each commit.
 * Module-level (one local player, one FurnitureLayer): a piece is "new", and
 * drops in, only if its key was not drawn before. Re-entering a house redraws
 * from scratch, so the drop-in plays again there.
 */
const drawnKeys = new Map<string, Set<string>>();

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
  // A TV's "tv_screen" mesh plays VITE_TV_VIDEO_URL (TvScreen.tsx); clone(true)
  // shares materials, but ScreenVideo swaps the material on this instance only.
  const screen = useMemo(() => (TV_VIDEO_URL ? findScreen(model) : undefined), [model]);
  return (
    <>
      <primitive object={model} />
      {screen && TV_VIDEO_URL ? (
        // Own boundary: the video loading must not swap the TV back to its box.
        <Suspense fallback={null}>
          <ScreenVideo mesh={screen} src={TV_VIDEO_URL} />
        </Suspense>
      ) : null}
    </>
  );
}

function FurniturePiece({
  obj,
  asset,
  url,
  index,
  showLabel,
  dropFrom,
  onLanded,
}: {
  obj: PlacedObject;
  asset: Asset;
  url: string | null;
  /** Position among the pieces that arrived in the same render (stagger), or -1: no drop at all. */
  index: number;
  showLabel: boolean;
  dropFrom: number;
  /** Called once, on the frame a drop-in reaches the floor (landing puff and thunk). */
  onLanded: (obj: PlacedObject, asset: Asset) => void;
}) {
  const group = useRef<Group>(null);
  const elapsed = useRef(index < 0 ? DROP_S + 1 : -index * STAGGER_S);
  const { w, d, h } = asset.size_m;
  const box = <FallbackBox asset={asset} />;
  // Probe before loading: a missing .glb never reaches the loader (meshProbe.ts).
  const mesh = useMeshState(url, asset.file);

  useFrame((_, dt) => {
    if (!group.current || elapsed.current > DROP_S) return;
    const before = elapsed.current;
    elapsed.current += Math.min(dt, 0.1);
    // Landed this frame: only pieces that actually drop in (index >= 0).
    if (index >= 0 && before < DROP_S && elapsed.current >= DROP_S) onLanded(obj, asset);
    const t = Math.min(1, Math.max(0, elapsed.current / DROP_S));
    const e = 1 - Math.pow(1 - t, 3); // easeOutCubic
    group.current.position.y = (1 - e) * dropFrom;
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
      <group ref={group} position-y={index < 0 ? 0 : dropFrom}>
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

  // Keyed on the furniture's content, not array identity: net/game.ts writes a
  // new objects array on every state patch (~20 Hz per player), even when
  // nothing changed (useFurniturePlan.ts, furnitureKey.ts).
  const { key: contentKey, objects } = useStableFurniture(house);
  const rects = useMemo(() => footprints(objects, catalog), [objects, catalog]);
  useFurnitureLayout({
    zone,
    rooms,
    rects,
    furnitureKey: contentKey,
    setSpawns,
    onPushedInto: (roomId) => setMe({ roomId }),
  });

  // Pose keys already drawn in this zone: only pieces new to a render drop in.
  const keys = useMemo(() => objects.map(pieceKey), [objects]);
  const drawn = drawnKeys.get(zone);
  useEffect(() => {
    drawnKeys.set(zone, new Set(keys));
  }, [zone, keys]);
  useEffect(() => () => void drawnKeys.delete(zone), [zone]);

  // Landing puffs (pooled, house frame) and the thunk, when a drop-in reaches the floor.
  const puffs = useLandingPuffs();
  const spawnPuff = puffs.spawn; // stable
  const onLanded = useCallback(
    (obj: PlacedObject, asset: Asset) => {
      const { w, d } = asset.size_m;
      spawnPuff([obj.x, obj.y, obj.z], footprintRadius(w, d));
      play("thunk", { size: Math.max(w, d) });
    },
    [spawnPuff],
  );
  useFrame((_, dt) => puffs.step(Math.min(dt, 0.1)));

  // Build mode: the piece I carry, or just moved, is hidden here until its new pose arrives.
  const held = useBuild((s) => s.held);
  const active = useBuild((s) => s.active);
  const pending = useBuild((s) => s.pending);

  if (zone === "block" || objects.length === 0) return null;
  if (packBaseUrl === null) {
    warnOnce("furniture-nomesh", "[furniture] no pack base URL: every piece renders as a labelled box");
  }

  let fresh = 0;
  return (
    <group>
      {objects.map((o, i) => {
        const asset = catalog[o.asset_id];
        if (!asset) {
          warnOnce(`noasset:${o.asset_id}`, `[furniture] no catalog entry for ${o.asset_id}; skipping it`);
          return null;
        }
        const key = keys[i];
        if (hiddenLocally({ active, held, pending }, o.instance_id, key)) return null;
        const isNew = !drawn?.has(key);
        return (
          // key = instance + pose: a moved piece is a new RigidBody, never a
          // collider mutated in place (stale colliders stop darts in mid-air).
          <FurniturePiece
            key={key}
            obj={o}
            asset={asset}
            url={packBaseUrl === null ? null : `${packBaseUrl}/${asset.file}`}
            index={isNew ? fresh++ : -1}
            dropFrom={o.reason ? DROP_FROM_M : SHORT_DROP_M}
            showLabel={showLabels}
            onLanded={onLanded}
          />
        );
      })}
      {puffs.elements}
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
