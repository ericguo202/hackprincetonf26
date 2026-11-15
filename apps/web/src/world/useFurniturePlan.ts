// Lane C: FurnitureLayer's plan handling as hooks, keyed on the furniture's
// CONTENT (furnitureKey), not on array identity. Tested in
// useFurniturePlan.test.tsx by rendering them with react-dom.

import { useLayoutEffect, useMemo } from "react";

import type { Room } from "../contracts.gen";
import { localPlayer } from "../player/playerControl";
import { furnitureKey, type FurnitureLike, type HouseLike } from "./furnitureKey";
import type { Spawn } from "./furniturePlan";
import { houseSpawns } from "./furniturePlan";
import { pushOut, type Rect } from "./geometry";
import { roomIdAt } from "./pack";

const NONE: never[] = [];

/**
 * The house's furniture with a stable identity: the objects array is the one
 * from the first render with this content key, and only changes when the key
 * does. A new-but-identical array from a network patch is ignored.
 */
export function useStableFurniture<T extends FurnitureLike>(
  house: { plan_id: string; objects: T[] } | undefined,
): { key: string; objects: T[] } {
  const key = furnitureKey(house as HouseLike | undefined);
  // Keyed on content on purpose: `house` is a new object on every patch.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const objects = useMemo(() => house?.objects ?? (NONE as T[]), [key]);
  return { key, objects };
}

type LayoutArgs = {
  zone: string;
  rooms: Room[];
  /**
   * Footprints of the current objects. Must be stable: memoize it on the
   * stable objects (useStableFurniture) and the catalog, so it changes only
   * when the furniture's content or the catalog does.
   */
  rects: Rect[];
  /** furnitureKey of the current objects. */
  furnitureKey: string;
  setSpawns: (listingId: string, spawns: Spawn[]) => void;
  /** Called with the room the player was pushed into, if push-out moved them. */
  onPushedInto?: (roomId: string) => void;
};

/**
 * On every furniture change, before the next physics step (layout effect, so
 * new colliders never get a step to shove the player around):
 *   1. recompute indoor spawns from the new free floor
 *   2. push the local player out of anything that landed on them, moving the
 *      physics body itself (playerControl), not just the store
 * Runs when the content key, zone, rooms or footprints change (footprints
 * also change when the catalog does, e.g. a missing asset arrives), never on
 * an identical patch.
 * Deterministic, so every client agrees without the server deciding.
 */
export function useFurnitureLayout({ zone, rooms, rects, furnitureKey: key, setSpawns, onPushedInto }: LayoutArgs): void {
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
      onPushedInto?.(room.id);
    }
    // The callbacks (setSpawns, onPushedInto) are left out on purpose: they
    // may be new functions on every render. rects is stable (see LayoutArgs).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, zone, rooms, rects]);
}
