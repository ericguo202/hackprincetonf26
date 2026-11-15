// Lane C: a stable content key for a house's furniture. Pure; tested in
// furnitureKey.test.ts.
//
// Why: net/game.ts (Lane D) writes `houses` from Colyseus toJSON() on every
// state patch, so store.houses[zone].objects is a NEW array about 20 times a
// second per connected player even when nothing changed. Anything keyed on
// array identity (footprints, indoor spawns, push-out, the collider remount)
// then re-runs on every patch. FurnitureLayer keys on this instead, so it
// holds whatever the network layer does.
//
// Key: plan_id, then each object sorted by instance_id as
// instance_id|asset_id|x|y|z|rot_deg, positions rounded to 1 mm and rotation
// to 0.1 degree, so float noise from the wire never counts as a change.

export type FurnitureLike = {
  instance_id: string;
  asset_id: string;
  x: number;
  y: number;
  z: number;
  rot_deg: number;
};

export type HouseLike = { plan_id: string; objects: FurnitureLike[] };

/** Fixed-point with no "-0": -0.0004 and 0.0001 both read 0.000. */
function fixed(v: number, decimals: number): string {
  const f = 10 ** decimals;
  const r = Math.round(v * f) / f;
  return (r === 0 ? 0 : r).toFixed(decimals);
}

/** One piece at one pose: instance_id|asset_id|x|y|z|rot_deg. A moved or swapped piece gets a new key. */
export function pieceKey(o: FurnitureLike): string {
  return [o.instance_id, o.asset_id, fixed(o.x, 3), fixed(o.y, 3), fixed(o.z, 3), fixed(o.rot_deg, 1)].join("|");
}

/** Same furniture in any array, in any order, gives the same key. No house is "none". */
export function furnitureKey(house: HouseLike | undefined | null): string {
  if (!house) return "none";
  const parts = house.objects.map(pieceKey).sort();
  return `${house.plan_id}#${parts.join(";")}`;
}
