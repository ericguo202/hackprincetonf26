// Lane C: the block zone. block/render.glb is drawn, block/collision.glb is
// collided with; when either is missing, a placeholder stands in for it.
//
// The placeholder is meant to read as "the block, before the scan lands":
// lawn, a street and sidewalk past the spawns, a massing box per listed house
// with a lit front door. Render and collision placeholders are separate
// components built from the same numbers, so the render path never collides.

import { Html } from "@react-three/drei";
import { CuboidCollider, RigidBody } from "@react-three/rapier";

import type { Listing } from "../contracts.gen";
import { GlbOrPlaceholder } from "./Glb";
import { ZONE_COLLIDER } from "./collisionGroups";
import { rotationY } from "./geometry";
import { DOOR_RADIUS_M, doorOutward, packUrl, type Door, type LoadedPack } from "./pack";
import { houseBoxLocal } from "./placeholder";

const GROUND_M = 160;
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
  return (
    <>
      <GlbOrPlaceholder
        kind="render"
        url={base && packUrl(base, manifest.block.render)}
        label={manifest.block.render}
        placeholder={<PlaceholderBlockRender pack={pack} />}
      />
      <GlbOrPlaceholder
        kind="collision"
        url={base && packUrl(base, manifest.block.collision)}
        label={manifest.block.collision}
        placeholder={<PlaceholderBlockCollision pack={pack} />}
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

/** Street along X through the spawns' far side, sidewalk on the spawn line. */
function streetLayout(pack: LoadedPack) {
  const spawns = pack.manifest.spawns;
  const z = spawns.length ? spawns.reduce((s, p) => s + p[2], 0) / spawns.length : 0;
  return { sidewalkZ: z, streetZ: z + 5 };
}

function houseBox(pack: LoadedPack, d: Door) {
  return houseBoxLocal(d.local, pack.roomsByHouse[d.listing_id] ?? []);
}

function PlaceholderBlockRender({ pack }: { pack: LoadedPack }) {
  const { sidewalkZ, streetZ } = streetLayout(pack);
  return (
    <group>
      <mesh rotation-x={-Math.PI / 2} receiveShadow>
        <planeGeometry args={[GROUND_M, GROUND_M]} />
        <meshStandardMaterial color={PALETTE.lawn} />
      </mesh>
      <mesh rotation-x={-Math.PI / 2} position={[0, 0.01, sidewalkZ]} receiveShadow>
        <planeGeometry args={[GROUND_M, 3]} />
        <meshStandardMaterial color={PALETTE.sidewalk} />
      </mesh>
      <mesh rotation-x={-Math.PI / 2} position={[0, 0.01, streetZ]} receiveShadow>
        <planeGeometry args={[GROUND_M, 7]} />
        <meshStandardMaterial color={PALETTE.street} />
      </mesh>
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

function PlaceholderBlockCollision({ pack }: { pack: LoadedPack }) {
  return (
    <>
      <RigidBody type="fixed" colliders={false}>
        <CuboidCollider collisionGroups={ZONE_COLLIDER} args={[GROUND_M / 2, 0.05, GROUND_M / 2]} position={[0, -0.05, 0]} />
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
      <Html position={[local[0] + ox * 0.6, 2.7, local[2] + oz * 0.6]} center distanceFactor={10}>
        <div style={labelStyle}>
          <div style={{ fontWeight: 600 }}>{listing?.address ?? door.listing_id}</div>
          <div style={{ opacity: 0.75 }}>Walk in</div>
        </div>
      </Html>
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
