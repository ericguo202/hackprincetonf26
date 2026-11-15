// Lane C: Render house interior doors with animated door leaves and collision.
// While closed: fixed CuboidCollider (ZONE_COLLIDER) fills the doorway.
// Door leaf: off-white mesh hinged at point a, rotating 90° over 0.3 s when opened.
// Doors to the outside swing outward, away from the house (doorInteract.doorSwingSign);
// room-to-room doors keep the +90° swing.

import { useEffect, useMemo, useRef, useState } from "react";
import { useFrame } from "@react-three/fiber";
import { CuboidCollider, RigidBody } from "@react-three/rapier";
import type { Group } from "three";

import { ZONE_COLLIDER } from "./collisionGroups";
import type { Room } from "../contracts.gen";
import { doorSwingSign } from "./doorInteract";
import type { OpenedDoor } from "./doorSplit";
import { useDoorState } from "./doorState";

type Props = {
  listingId: string;
  doors: readonly OpenedDoor[];
  /** Boxes found in collision.glb's walls node, for the dev probe. */
  wallBoxes: number;
  /** This house's rooms.json rooms: an outside door's swing direction comes from its room. */
  rooms: readonly Room[];
};

export function HouseDoors({ listingId, doors, wallBoxes, rooms }: Props) {
  // Register this house's doors. A new split (a reloaded collision scene)
  // replaces them in place and keeps the open flags.
  useEffect(() => {
    useDoorState.getState().setDoors(listingId, doors, { collision: "glb", wallBoxes });
  }, [listingId, doors, wallBoxes]);
  // Only unmounting (leaving the house) forgets them, and only for this listing.
  useEffect(() => () => useDoorState.getState().clearDoors(listingId), [listingId]);

  return (
    <group>
      {doors.map((d) => (
        <SingleDoor key={d.id} door={d} rooms={rooms} />
      ))}
    </group>
  );
}

function SingleDoor({ door, rooms }: { door: OpenedDoor; rooms: readonly Room[] }) {
  const isOpen = useDoorState((s) => s.isOpen(door.id));
  // +1 or -1: outside doors open outward, the rest as before (+90 deg).
  const swing = useMemo(() => doorSwingSign(door, rooms) * (Math.PI / 2), [door, rooms]);
  const [initialProgress] = useState(() => (isOpen ? 1 : 0));
  const hingeRef = useRef<Group>(null);
  const progress = useRef(initialProgress);

  const dx = door.b[0] - door.a[0];
  const dz = door.b[1] - door.a[1];
  const yaw = Math.atan2(-dz, dx);
  const cx = (door.a[0] + door.b[0]) / 2;
  const cz = (door.a[1] + door.b[1]) / 2;

  useFrame((_, delta) => {
    const target = isOpen ? 1 : 0;
    if (progress.current !== target) {
      const step = delta / 0.3;
      if (target > progress.current) {
        progress.current = Math.min(target, progress.current + step);
      } else {
        progress.current = Math.max(target, progress.current - step);
      }
      const t = progress.current;
      // Smoothstep easing over 0.3 s
      const eased = t * t * (3 - 2 * t);
      if (hingeRef.current) {
        hingeRef.current.rotation.y = eased * swing;
      }
    }
  }, 0);

  return (
    <group>
      {/* While closed: a CuboidCollider (ZONE_COLLIDER) filling the gap */}
      {!isOpen ? (
        <RigidBody type="fixed" colliders={false}>
          <CuboidCollider
            collisionGroups={ZONE_COLLIDER}
            args={[door.width / 2, 1.0, 0.05]}
            position={[cx, door.floorY + 1.0, cz]}
            rotation={[0, yaw, 0]}
          />
        </RigidBody>
      ) : null}

      {/* Door leaf mesh: hinged at point a, rotates 90 deg when open */}
      <group position={[door.a[0], door.floorY, door.a[1]]} rotation-y={yaw}>
        <group ref={hingeRef} rotation-y={initialProgress * swing}>
          <mesh position={[door.width / 2, 1.0, 0]} castShadow receiveShadow>
            <boxGeometry args={[door.width, 2.0, 0.04]} />
            <meshStandardMaterial color="#f0ede6" roughness={0.7} />
          </mesh>
        </group>
      </group>
    </group>
  );
}
