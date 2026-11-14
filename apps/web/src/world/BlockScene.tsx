// Lane C: the block zone. block/render.glb is drawn, block/collision.glb is
// collided with; when either is missing, a placeholder stands in for it.
//
// The placeholder is meant to read as "the block, before the scan lands":
// a lawn sized to everything placed, the street the houses face (derived from
// their doors, pack.ts blockLayout), a massing box per listed house with a
// lit front door. Render and collision placeholders are separate
// components built from the same numbers, so the render path never collides.

import { useMemo } from "react";
import { CuboidCollider, RigidBody } from "@react-three/rapier";

import type { Listing } from "../contracts.gen";
import { GlbOrPlaceholder } from "./Glb";
import { ZONE_COLLIDER } from "./collisionGroups";
import { rotationY } from "./geometry";
import {
  DOOR_RADIUS_M,
  STREET_WIDTH_M,
  blockLayout,
  doorOutward,
  packUrl,
  type BlockLayout,
  type Door,
  type LoadedPack,
} from "./pack";
import { houseBoxLocal } from "./placeholder";
import { WorldLabel } from "./WorldLabel";

const PALETTE = {
  lawn: "#7d9168",
  lot: "#8ea377",
  street: "#3b3f45",
  sidewalk: "#b9b6ad",
  house: "#d9d2c5",
  roof: "#8a5a44",
  door: "#ffb347",
};

export function BlockScene({ pack }: { pack: LoadedPack }) {
  const { base, manifest } = pack;
  // Ground and street from what is actually placed, not a fixed size.
  const layout = useMemo(() => blockLayout(pack), [pack]);
  return (
    <>
      <GlbOrPlaceholder
        kind="render"
        url={base && packUrl(base, manifest.block.render)}
        label={manifest.block.render}
        placeholder={<PlaceholderBlockRender pack={pack} layout={layout} />}
      />
      <GlbOrPlaceholder
        kind="collision"
        url={base && packUrl(base, manifest.block.collision)}
        label={manifest.block.collision}
        placeholder={<PlaceholderBlockCollision pack={pack} layout={layout} />}
      />
      {pack.doors.map((d) => (
        <DoorMarker
          key={d.listing_id}
          door={d}
          listing={pack.listings.find((l) => l.id === d.listing_id)}
          outward={doorOutward(d.local, pack.roomsByHouse[d.listing_id] ?? [])}
        />
      ))}
    </>
  );
}

/**
 * House massing box in its own frame. With rooms: their bounds. Without:
 * 8 m wide and 10 m deep behind the door, oriented by the door's outward
 * normal (7014's door is on its east wall, so +Z is not its front).
 */
function houseBox(pack: LoadedPack, d: Door) {
  const rooms = pack.roomsByHouse[d.listing_id] ?? [];
  if (rooms.length) return houseBoxLocal(d.local, rooms);
  const [ox, oz] = doorOutward(d.local, rooms);
  const depth = 10;
  const width = 8;
  const h = 3.2;
  return {
    center: [d.local[0] - (ox * depth) / 2, h / 2, d.local[2] - (oz * depth) / 2] as [number, number, number],
    size: [ox !== 0 ? depth : width, h, ox !== 0 ? width : depth] as [number, number, number],
  };
}

/** One flat quad per street segment, centred on the segment, rotated to it. */
function StreetSegments({ street, width, color, y }: { street: [number, number][]; width: number; color: string; y: number }) {
  return (
    <>
      {street.slice(1).map((b, i) => {
        const a = street[i];
        const dx = b[0] - a[0];
        const dz = b[1] - a[1];
        // Overlap the joints a little so bends leave no gaps.
        const len = Math.hypot(dx, dz) + width * 0.5;
        return (
          <mesh
            key={i}
            rotation={[-Math.PI / 2, 0, Math.atan2(-dz, dx)]}
            position={[(a[0] + b[0]) / 2, y, (a[1] + b[1]) / 2]}
            receiveShadow
          >
            <planeGeometry args={[len, width]} />
            <meshStandardMaterial color={color} />
          </mesh>
        );
      })}
    </>
  );
}

function PlaceholderBlockRender({ pack, layout }: { pack: LoadedPack; layout: BlockLayout }) {
  const g = layout.ground;
  return (
    <group>
      <mesh rotation-x={-Math.PI / 2} position={[g.cx, 0, g.cz]} receiveShadow>
        <planeGeometry args={[g.w, g.d]} />
        <meshStandardMaterial color={PALETTE.lawn} />
      </mesh>
      {layout.street ? (
        <>
          {/* Sidewalk band under the road, then the road on top. */}
          <StreetSegments street={layout.street} width={STREET_WIDTH_M + 4} color={PALETTE.sidewalk} y={0.008} />
          <StreetSegments street={layout.street} width={STREET_WIDTH_M} color={PALETTE.street} y={0.012} />
        </>
      ) : null}
      {pack.doors.map((d) => {
        const box = houseBox(pack, d);
        return (
          <group
            key={d.listing_id}
            position={d.world.position}
            rotation-y={rotationY(d.world.heading_deg)}
          >
            {/* Lot */}
            <mesh rotation-x={-Math.PI / 2} position={[box.center[0], 0.005, box.center[2]]} receiveShadow>
              <planeGeometry args={[box.size[0] + 6, box.size[2] + 6]} />
              <meshStandardMaterial color={PALETTE.lot} />
            </mesh>
            {/* Walls */}
            <mesh position={box.center} castShadow receiveShadow>
              <boxGeometry args={box.size} />
              <meshStandardMaterial color={PALETTE.house} />
            </mesh>
            {/* Flat roof cap, so the box reads as a building rather than a crate */}
            <mesh position={[box.center[0], box.size[1] + 0.1, box.center[2]]} castShadow>
              <boxGeometry args={[box.size[0] + 0.4, 0.2, box.size[2] + 0.4]} />
              <meshStandardMaterial color={PALETTE.roof} />
            </mesh>
          </group>
        );
      })}
    </group>
  );
}

function PlaceholderBlockCollision({ pack, layout }: { pack: LoadedPack; layout: BlockLayout }) {
  const g = layout.ground;
  return (
    <>
      <RigidBody type="fixed" colliders={false}>
        <CuboidCollider collisionGroups={ZONE_COLLIDER} args={[g.w / 2, 0.05, g.d / 2]} position={[g.cx, -0.05, g.cz]} />
      </RigidBody>
      {pack.doors.map((d) => {
        const box = houseBox(pack, d);
        return (
          <RigidBody
            key={d.listing_id}
            type="fixed"
            colliders={false}
            position={d.world.position}
            rotation={[0, rotationY(d.world.heading_deg), 0]}
          >
            <CuboidCollider collisionGroups={ZONE_COLLIDER}
              args={[box.size[0] / 2, box.size[1] / 2, box.size[2] / 2]}
              position={box.center}
            />
          </RigidBody>
        );
      })}
    </>
  );
}

/** Lit door on the house face, a ring showing the trigger, and a label. Visual only. */
function DoorMarker({
  door,
  listing,
  outward,
}: {
  door: Door;
  listing: Listing | undefined;
  outward: [number, number];
}) {
  const [ox, oz] = outward;
  const facing = Math.atan2(ox, oz); // rotation-y that turns +Z onto the outward normal
  const local = door.local;
  return (
    <group position={door.world.position} rotation-y={rotationY(door.world.heading_deg)}>
      <mesh position={[local[0] + ox * 0.02, local[1] + 1.05, local[2] + oz * 0.02]} rotation-y={facing}>
        <planeGeometry args={[1.0, 2.1]} />
        <meshStandardMaterial color={PALETTE.door} emissive={PALETTE.door} emissiveIntensity={0.6} />
      </mesh>
      {/* Centred on the door point: this is exactly the radius stepZone tests. */}
      <mesh rotation-x={-Math.PI / 2} position={[local[0], 0.02, local[2]]}>
        <ringGeometry args={[DOOR_RADIUS_M - 0.08, DOOR_RADIUS_M, 40]} />
        <meshBasicMaterial color={PALETTE.door} transparent opacity={0.8} />
      </mesh>
      {/* Door labels are wayfinding on the block, so they stay up much further out. */}
      <WorldLabel
        position={[local[0] + ox * 0.6, 2.7, local[2] + oz * 0.6]}
        style={labelStyle}
        range={{ fadeFrom: 40, fadeTo: 50 }}
      >
        <div style={{ fontWeight: 600 }}>{listing?.address ?? door.listing_id}</div>
        <div style={{ opacity: 0.75 }}>Walk in</div>
      </WorldLabel>
    </group>
  );
}

const labelStyle: React.CSSProperties = {
  background: "rgba(16,20,26,0.85)",
  color: "#e8eaed",
  padding: "4px 8px",
  borderRadius: 6,
  fontSize: 12,
  whiteSpace: "nowrap",
  pointerEvents: "none",
  textAlign: "center",
};
