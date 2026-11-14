// Lane C: renders the furniture the agent placed, for the house the local
// player is standing in. Reads the store, never writes network state.
//
// Two inputs come from the pack, not the store: the furniture catalog
// (manifest.furniture_catalog) and the house's rooms.json. Whoever owns pack
// loading passes them in. If they end up in the store instead, swap the props
// for selectors and nothing else here changes.

import { useEffect, useMemo, useRef } from "react";
import { useFrame } from "@react-three/fiber";
import { Html, useGLTF } from "@react-three/drei";
import { RigidBody } from "@react-three/rapier";
import type { Group } from "three";

import { useStore, type PlacedObject } from "../store";
import type { Asset, Point2, Room } from "../contracts.gen";
import { pushOut, rectFromPlacement, rotationY, type Rect } from "./geometry";

type Props = {
  /** asset_id -> Asset, from the pack's furniture catalog. */
  catalog: Record<string, Asset>;
  /** Rooms of the house the player is in, from houses/<id>/rooms.json. */
  rooms: Room[];
  /** Base URL the pack's relative `file` paths resolve against. */
  packBaseUrl: string;
  showLabels?: boolean;
};

function FurniturePiece({
  obj,
  asset,
  url,
  index,
  showLabel,
}: {
  obj: PlacedObject;
  asset: Asset;
  url: string;
  index: number;
  showLabel: boolean;
}) {
  const { scene } = useGLTF(url);
  // Clone per instance: two couches from one glb must not share a transform.
  const model = useMemo(() => scene.clone(true), [scene]);
  const group = useRef<Group>(null);
  const elapsed = useRef(-index * 0.12);

  useFrame((_, dt) => {
    if (!group.current) return;
    elapsed.current += dt;
    const t = Math.min(1, Math.max(0, elapsed.current / 0.45));
    const e = 1 - Math.pow(1 - t, 3); // easeOutCubic
    group.current.position.y = (1 - e) * 1.2;
    group.current.scale.setScalar(0.92 + 0.08 * e);
  });

  return (
    <RigidBody
      type="fixed"
      colliders="cuboid"
      position={[obj.x, obj.y, obj.z]}
      rotation={[0, rotationY(obj.rot_deg), 0]}
    >
      <group ref={group}>
        <primitive object={model} />
      </group>
      {showLabel && obj.reason ? (
        <Html position={[0, asset.size_m.h + 0.25, 0]} center distanceFactor={8}>
          <div
            style={{
              background: "rgba(20,20,20,0.82)",
              color: "white",
              padding: "4px 8px",
              borderRadius: 6,
              fontSize: 12,
              whiteSpace: "nowrap",
              pointerEvents: "none",
            }}
          >
            {obj.reason}
          </div>
        </Html>
      ) : null}
    </RigidBody>
  );
}

export function FurnitureLayer({
  catalog,
  rooms,
  packBaseUrl,
  showLabels = true,
}: Props) {
  const zone = useStore((s) => s.me.zone);
  const roomId = useStore((s) => s.me.roomId);
  const pos = useStore((s) => s.me.pos);
  const setMe = useStore((s) => s.setMe);
  const house = useStore((s) => (s.me.zone === "block" ? undefined : s.houses[s.me.zone]));

  const objects = house?.objects ?? [];
  const planId = house?.plan_id ?? "none";

  const rects: Rect[] = useMemo(
    () =>
      objects
        .map((o) => {
          const asset = catalog[o.asset_id];
          return asset ? rectFromPlacement(o, asset) : null;
        })
        .filter((r): r is Rect => r !== null),
    [objects, catalog],
  );

  // After every plan swap, move the local player out of anything that landed
  // on them. Deterministic, so each client agrees without the server deciding.
  useEffect(() => {
    if (zone === "block" || !roomId) return;
    const room = rooms.find((r) => r.id === roomId);
    if (!room) return;

    const here: Point2 = [pos[0], pos[2]];
    const moved = pushOut(here, room, rects);

    if (moved === null) {
      const door = room.openings.find((o) => o.type === "door");
      if (door) {
        const mx = (door.a[0] + door.b[0]) / 2;
        const mz = (door.a[1] + door.b[1]) / 2;
        setMe({ pos: [mx, room.floor_y, mz] });
      }
      return;
    }
    if (moved[0] !== here[0] || moved[1] !== here[1]) {
      setMe({ pos: [moved[0], pos[1], moved[1]] });
    }
    // Fires on plan change, not on every step the player takes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [planId, roomId, zone]);

  if (zone === "block" || objects.length === 0) return null;

  // key={planId} tears down every RigidBody on a re-furnish. Mutating
  // positions in place leaves stale colliders, which shows up as darts
  // stopping in mid-air where the old couch used to be.
  return (
    <group key={planId}>
      {objects.map((o, i) => {
        const asset = catalog[o.asset_id];
        if (!asset) {
          console.warn(`[furniture] no catalog entry for ${o.asset_id}`);
          return null;
        }
        return (
          <FurniturePiece
            key={`${planId}-${o.instance_id}`}
            obj={o}
            asset={asset}
            url={`${packBaseUrl}/${asset.file}`}
            index={i}
            showLabel={showLabels}
          />
        );
      })}
    </group>
  );
}
