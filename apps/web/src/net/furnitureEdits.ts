// Lane D: manual furnishing messages (contracts/messages.md v2.6.0), and the
// same edits applied locally when there is no game server (SOLO=1, or the
// server is down in dev). Pure; tested in furnitureEdits.test.ts.

import type { HouseFurniture, PlacedObject } from "../store";

export type FurniturePlace = {
  listing_id: string;
  room_id: string;
  asset_id: string;
  x: number;
  y: number;
  z: number;
  rot_deg: number;
};
export type FurnitureMove = Omit<FurniturePlace, "asset_id"> & { instance_id: string };
export type FurnitureRemove = { listing_id: string; instance_id: string };

export type FurnitureEdit =
  | { type: "furniture_place"; payload: FurniturePlace }
  | { type: "furniture_move"; payload: FurnitureMove }
  | { type: "furniture_remove"; payload: FurnitureRemove };

let localSeq = 0;

/**
 * The game's rules for one edit, on the store's houses: new pieces get
 * "<room_id>/m<n>", a move keeps the id unless it changes room, a move clears
 * the reason. Returns the new houses record, or null when the edit does nothing.
 */
export function applyLocalEdit(
  houses: Record<string, HouseFurniture>,
  edit: FurnitureEdit,
  byPlayer: string,
): Record<string, HouseFurniture> | null {
  const listing = edit.payload.listing_id;
  const house = houses[listing] ?? { plan_id: "manual", objects: [] };
  let objects: PlacedObject[];
  if (edit.type === "furniture_place") {
    const p = edit.payload;
    objects = [
      ...house.objects,
      {
        instance_id: `${p.room_id}/m${++localSeq}`,
        asset_id: p.asset_id,
        x: p.x,
        y: p.y,
        z: p.z,
        rot_deg: p.rot_deg,
        reason: "",
        by_player: byPlayer,
      },
    ];
  } else {
    const id = edit.payload.instance_id;
    const at = house.objects.findIndex((o) => o.instance_id === id);
    if (at < 0) return null;
    if (edit.type === "furniture_remove") {
      objects = house.objects.filter((_, i) => i !== at);
    } else {
      const p = edit.payload;
      const old = house.objects[at];
      const sameRoom = old.instance_id.startsWith(`${p.room_id}/`);
      const moved: PlacedObject = {
        ...old,
        instance_id: sameRoom ? old.instance_id : `${p.room_id}/m${++localSeq}`,
        x: p.x,
        y: p.y,
        z: p.z,
        rot_deg: p.rot_deg,
        reason: "",
        by_player: byPlayer,
      };
      objects = sameRoom
        ? house.objects.map((o, i) => (i === at ? moved : o))
        : [...house.objects.filter((_, i) => i !== at), moved];
    }
  }
  return { ...houses, [listing]: { plan_id: house.plan_id, objects } };
}
