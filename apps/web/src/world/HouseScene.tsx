// Lane C: one house zone, in its house-local frame (AGENTS.md section 8).
// interior.glb is drawn, the house's collision.glb is collided with; when
// either is missing, placeholders built from rooms.json stand in: a floor per
// room, walls along each polygon edge with gaps for doors, and the front door
// lit so you can find the way out.

import { Suspense, useMemo, useState } from "react";
import { CuboidCollider, RigidBody, TrimeshCollider } from "@react-three/rapier";
import { Shape, type Object3D } from "three";

import type { Point2, Room } from "../contracts.gen";
import { FurnitureLayer } from "./FurnitureLayer";
import { disableToneMapping } from "./interiorMaterials";
import { ZONE_COLLIDER } from "./collisionGroups";
import { extractWallBoxes, splitClosedDoors } from "./doorSplit";
import { GlbOrPlaceholder, LoadBoundary } from "./Glb";
import { HouseDoors } from "./HouseDoors";
import { DOOR_RADIUS_M, doorOutward, packUrl, publishedRoot, roomsBounds, type Door, type LoadedPack } from "./pack";
import { ceilingTrimesh, segmentBox, wallSegments } from "./placeholder";
import { WorldLabel } from "./WorldLabel";

const WALL_T = 0.12;
const SLAB_MARGIN_M = 8;
const SKIP_WALLS = ["walls"] as const;
/** Invisible fence around the yard edge, so nobody walks off the world. */
const FENCE_H = 2;
const FENCE_T = 0.2;
/** Yard surface height: just under the scanned floor (floor_y is about 0.005). */
const GROUND_TOP_Y = -0.02;
const FLOOR_COLORS: Record<string, string> = {
  living_room: "#c9b79c",
  kitchen: "#d8d4cc",
  dining_room: "#c4ae8f",
  bedroom: "#b9a58c",
  primary_bedroom: "#b39e84",
  bathroom: "#cfd6d9",
  hallway: "#c2b8a8",
};

type Props = { pack: LoadedPack; listingId: string };

export function HouseScene({ pack, listingId }: Props) {
  const house = pack.manifest.houses.find((h) => h.listing_id === listingId);
  const rooms = useMemo(() => pack.roomsByHouse[listingId] ?? [], [pack, listingId]);
  const door = pack.doors.find((d) => d.listing_id === listingId) ?? null;
  const base = pack.base;

  const doorLocal = door?.local;
  const [loadedCollision, setLoadedCollision] = useState<{ id: string; scene: Object3D } | null>(null);
  const collisionScene = loadedCollision?.id === listingId ? loadedCollision.scene : null;

  const split = useMemo(() => {
    if (!collisionScene || !listingId) return null;
    const boxes = extractWallBoxes(collisionScene);
    return splitClosedDoors(boxes, rooms, doorLocal);
  }, [collisionScene, listingId, rooms, doorLocal]);

  if (!house) return null;

  return (
    <>
      <GlbOrPlaceholder
        kind="render"
        url={base && packUrl(base, house.interior)}
        label={house.interior}
        // Finished photo textures: no second tone-mapping pass (interiorMaterials.ts).
        prepare={disableToneMapping}
        placeholder={<PlaceholderHouseRender rooms={rooms} door={door} />}
      />
      <GlbOrPlaceholder
        kind="collision"
        url={base && packUrl(base, house.collision)}
        label={house.collision}
        skipNodes={SKIP_WALLS}
        onScene={(scene) => setLoadedCollision({ id: listingId, scene })}
        placeholder={<PlaceholderHouseCollision rooms={rooms} door={door} />}
      />
      {split ? (
        <RigidBody type="fixed" colliders={false}>
          {split.boxes.map((b, i) => {
            const hx = (b.maxX - b.minX) / 2;
            const hy = (b.maxY - b.minY) / 2;
            const hz = (b.maxZ - b.minZ) / 2;
            const cx = (b.minX + b.maxX) / 2;
            const cy = (b.minY + b.maxY) / 2;
            const cz = (b.minZ + b.maxZ) / 2;
            return (
              <CuboidCollider
                key={`wb-${listingId}-${i}`}
                collisionGroups={ZONE_COLLIDER}
                args={[hx, hy, hz]}
                position={[cx, cy, cz]}
              />
            );
          })}
        </RigidBody>
      ) : null}
      {split ? <HouseDoors doors={split.doors} /> : null}
      <HouseGround rooms={rooms} door={door} />
      <HouseCeilings rooms={rooms} />
      {door ? <ExitMarker door={door} rooms={rooms} /> : null}
      <LoadBoundary url={`furniture:${listingId}`} label="furniture" fallback={null}>
        <Suspense fallback={null}>
          <FurnitureLayer catalog={pack.catalog} rooms={rooms} packBaseUrl={base ? publishedRoot(base) : null} />
        </Suspense>
      </LoadBoundary>
    </>
  );
}

function slab(rooms: Room[], door: Door | null) {
  const b = roomsBounds(rooms) ?? {
    minX: (door?.local[0] ?? 0) - 4,
    maxX: (door?.local[0] ?? 0) + 4,
    minZ: (door?.local[2] ?? 0) - 6,
    maxZ: (door?.local[2] ?? 0) + 2,
  };
  return {
    cx: (b.minX + b.maxX) / 2,
    cz: (b.minZ + b.maxZ) / 2,
    w: b.maxX - b.minX + SLAB_MARGIN_M * 2,
    d: b.maxZ - b.minZ + SLAB_MARGIN_M * 2,
  };
}

function frontDoorGap(door: Door | null): Point2[] {
  return door ? [[door.local[0], door.local[2]]] : [];
}

function floorShape(poly: Point2[]): Shape {
  // Shape lives in XY; rotating -90 deg about X sends (x, y) to (x, 0, -y),
  // so feed it -z to land at (x, 0, z).
  const s = new Shape();
  poly.forEach(([x, z], i) => (i === 0 ? s.moveTo(x, -z) : s.lineTo(x, -z)));
  s.closePath();
  return s;
}

function PlaceholderHouseRender({ rooms, door }: { rooms: Room[]; door: Door | null }) {
  const shapes = useMemo(() => rooms.map((r) => floorShape(r.polygon)), [rooms]);
  const gaps = frontDoorGap(door);
  return (
    <group>
      {rooms.map((r, i) => (
        <group key={r.id}>
          <mesh rotation-x={-Math.PI / 2} position={[0, r.floor_y + 0.005, 0]} receiveShadow>
            <shapeGeometry args={[shapes[i]]} />
            <meshStandardMaterial color={FLOOR_COLORS[r.label] ?? "#bdb3a3"} />
          </mesh>
          {wallSegments(r, gaps).map((s, j) => {
            const w = segmentBox(s);
            return (
              <mesh
                key={j}
                position={[w.cx, r.floor_y + r.height_m / 2, w.cz]}
                rotation-y={w.yaw}
                castShadow
                receiveShadow
              >
                <boxGeometry args={[w.len, r.height_m, WALL_T]} />
                <meshStandardMaterial color="#efeae2" transparent opacity={0.92} />
              </mesh>
            );
          })}
          <WorldLabel position={roomLabelPos(r)} style={roomLabelStyle}>
            {r.label.replace(/_/g, " ")}
          </WorldLabel>
        </group>
      ))}
    </group>
  );
}

/**
 * Yard under and around the house, always present, render and collider.
 * A scanned collision.glb only has floor inside the walls, so without this
 * an exterior door gap (to_room: null) drops the player into the void.
 * Top sits just under floor level so it never fights the scanned floor.
 */
/**
 * One flat ceiling collider per room at floor_y + height_m, always present:
 * collision.glb has no ceiling. Zone group, so the body and the camera arm's
 * ray both stop at it. Collider only; the interior mesh draws the ceiling.
 */
function HouseCeilings({ rooms }: { rooms: Room[] }) {
  const meshes = useMemo(() => rooms.map((r) => ({ id: r.id, ...ceilingTrimesh(r) })), [rooms]);
  return (
    <RigidBody type="fixed" colliders={false}>
      {meshes.map((m) => (
        <TrimeshCollider key={m.id} args={[m.vertices, m.indices]} collisionGroups={ZONE_COLLIDER} />
      ))}
    </RigidBody>
  );
}

function HouseGround({ rooms, door }: { rooms: Room[]; door: Door | null }) {
  const g = slab(rooms, door);
  return (
    <>
      <mesh rotation-x={-Math.PI / 2} position={[g.cx, GROUND_TOP_Y - 0.01, g.cz]} receiveShadow>
        <planeGeometry args={[g.w, g.d]} />
        <meshStandardMaterial color="#7d9168" />
      </mesh>
      <RigidBody type="fixed" colliders={false}>
        <CuboidCollider collisionGroups={ZONE_COLLIDER} args={[g.w / 2, 0.05, g.d / 2]} position={[g.cx, GROUND_TOP_Y - 0.05, g.cz]} />
        {[-1, 1].map((side) => (
          <CuboidCollider collisionGroups={ZONE_COLLIDER}
            key={`x${side}`}
            args={[FENCE_T / 2, FENCE_H / 2, g.d / 2]}
            position={[g.cx + (side * g.w) / 2, FENCE_H / 2, g.cz]}
          />
        ))}
        {[-1, 1].map((side) => (
          <CuboidCollider collisionGroups={ZONE_COLLIDER}
            key={`z${side}`}
            args={[g.w / 2, FENCE_H / 2, FENCE_T / 2]}
            position={[g.cx, FENCE_H / 2, g.cz + (side * g.d) / 2]}
          />
        ))}
      </RigidBody>
    </>
  );
}

function roomLabelPos(r: Room): [number, number, number] {
  const n = r.polygon.length;
  const x = r.polygon.reduce((s, p) => s + p[0], 0) / n;
  const z = r.polygon.reduce((s, p) => s + p[1], 0) / n;
  return [x, r.floor_y + 0.05, z];
}

function PlaceholderHouseCollision({ rooms, door }: { rooms: Room[]; door: Door | null }) {
  const gaps = frontDoorGap(door);
  return (
    <RigidBody type="fixed" colliders={false}>
      {rooms.flatMap((r) => {
        const parts = wallSegments(r, gaps).map((s, j) => {
          const w = segmentBox(s);
          return (
            <CuboidCollider collisionGroups={ZONE_COLLIDER}
              key={`${r.id}-w${j}`}
              args={[w.len / 2, r.height_m / 2, WALL_T / 2]}
              position={[w.cx, r.floor_y + r.height_m / 2, w.cz]}
              rotation={[0, w.yaw, 0]}
            />
          );
        });
        // Upper floors need their own floor; the slab only covers ground level.
        if (r.floor_y > 0.05) {
          const b = roomsBounds([r])!;
          parts.push(
            <CuboidCollider collisionGroups={ZONE_COLLIDER}
              key={`${r.id}-floor`}
              args={[(b.maxX - b.minX) / 2, 0.05, (b.maxZ - b.minZ) / 2]}
              position={[(b.minX + b.maxX) / 2, r.floor_y - 0.05, (b.minZ + b.maxZ) / 2]}
            />,
          );
        }
        return parts;
      })}
    </RigidBody>
  );
}

/** The way back out: lit doorway and the trigger ring, house-local. Visual only. */
function ExitMarker({ door, rooms }: { door: Door; rooms: Room[] }) {
  const [ox, oz] = doorOutward(door.local, rooms);
  const [x, y, z] = door.local;
  return (
    <group>
      <mesh position={[x + ox * 0.08, y + 1.05, z + oz * 0.08]} rotation-y={Math.atan2(ox, oz) + Math.PI}>
        <planeGeometry args={[1.0, 2.1]} />
        <meshStandardMaterial color="#ffb347" emissive="#ffb347" emissiveIntensity={0.5} transparent opacity={0.55} />
      </mesh>
      <mesh rotation-x={-Math.PI / 2} position={[x, y + 0.02, z]}>
        <ringGeometry args={[DOOR_RADIUS_M - 0.08, DOOR_RADIUS_M, 40]} />
        <meshBasicMaterial color="#ffb347" transparent opacity={0.8} />
      </mesh>
      <WorldLabel position={[x - ox * 0.6, y + 2.3, z - oz * 0.6]} style={roomLabelStyle}>
        Exit to street
      </WorldLabel>
    </group>
  );
}

const roomLabelStyle: React.CSSProperties = {
  background: "rgba(16,20,26,0.7)",
  color: "#e8eaed",
  padding: "2px 6px",
  borderRadius: 4,
  fontSize: 11,
  whiteSpace: "nowrap",
  pointerEvents: "none",
  textTransform: "capitalize",
};
