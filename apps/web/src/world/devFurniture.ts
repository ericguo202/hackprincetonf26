// Lane C: dev-only furniture data for VITE_DEV_PLAN. Not contract data, not
// shipped content: it exists so the furniture path can be exercised before
// Lane A's catalog and Lane B's planner land.
//
// devCatalog: contracts/fixtures/asset.json (a real ABO sofa) plus stand-ins
// for the MVP living-room pieces in AGENTS.md section 9, with ids in the
// contract's <category>_<source>_<id> form (source "dev"). Any plan asset the
// catalog lacks (the fixture plan's ABO TV stand) gets a stand-in of the same
// category under its own id, so plans written against the real library still
// render. Sizes are typical real-world dimensions; there are no meshes, so
// every stand-in renders as the labelled-box fallback. cover follows the
// asset schema's height rule.
//
// DEV_PLAN_B: a second layout, authored like the fixture plan for the fixture
// room (6 x 4.2 m, centre 3, 2.1), so both can be retargeted the same way. It
// deliberately puts a sectional on the room's centre, which is where
// VITE_START_ROOM drops the player, to exercise push-out on re-furnish.

import type { Asset, PlacementPlan } from "../contracts.gen";

// The switch itself (DEV_PLAN_ON) lives in devFlags.ts, gated to dev builds.

type Box = { w: number; d: number; h: number };

function stub(
  id: string,
  name: string,
  category: string,
  size: Box,
  placement: Asset["placement"],
  tags: string[],
  clearance: Asset["clearance_m"] = {},
): Asset {
  return {
    id,
    name,
    category,
    tags,
    file: `furniture/v1/${id}.glb`,
    size_m: size,
    placement,
    clearance_m: clearance,
    cover: size.h > 1.2 ? "full" : size.h >= 0.5 ? "half" : "none",
    source: {
      // Not a contract dataset: stand-ins exist only in the dev client, are
      // never published and never validated against asset.schema.json.
      dataset: "dev" as Asset["source"]["dataset"],
      item_id: null,
      product_name: null,
      brand: null,
      license: "n/a (no mesh)",
      credit: "Lane C dev stand-in",
    },
  };
}

/** <category> from a <category>_<source>_<id> asset id. */
export function assetCategory(id: string): string {
  const m = /^(.+?)_(abo|polyhaven|kaykit|kenney|dev)_/i.exec(id);
  return m ? m[1] : id.replace(/_\d+$/, "");
}

/**
 * The dev catalog, plus a same-category stand-in under each of `neededIds`
 * the catalog lacks. An id with no matching category is left out (the
 * furniture layer warns and skips it).
 */
export function devCatalog(fixtureAsset: Asset, neededIds: string[] = []): Record<string, Asset> {
  const list: Asset[] = [
    fixtureAsset,
    stub("tv_stand_dev_01", "TV and stand", "tv_stand", { w: 1.6, d: 0.4, h: 1.25 }, "wall", ["living_room", "movie_night"], { front: 2.0 }),
    stub("sectional_dev_01", "Sectional", "sectional", { w: 2.8, d: 1.9, h: 0.85 }, "free", ["living_room", "seating", "cover"], { front: 0.75 }),
    stub("armchair_dev_01", "Armchair", "armchair", { w: 0.9, d: 0.9, h: 0.9 }, "free", ["living_room", "seating", "cover"], { front: 0.6 }),
    stub("coffee_table_dev_01", "Coffee table", "coffee_table", { w: 1.1, d: 0.6, h: 0.45 }, "free", ["living_room"]),
    stub("rug_dev_01", "Rug", "rug", { w: 2.4, d: 1.7, h: 0.02 }, "free", ["living_room"]),
    stub("bookshelf_dev_01", "Bookshelf", "bookshelf", { w: 0.9, d: 0.35, h: 1.9 }, "wall", ["living_room", "storage", "cover"], { front: 0.6 }),
    stub("floor_lamp_dev_01", "Floor lamp", "floor_lamp", { w: 0.4, d: 0.4, h: 1.6 }, "corner", ["living_room", "lighting"]),
    stub("plant_dev_01", "Plant", "plant", { w: 0.5, d: 0.5, h: 1.1 }, "corner", ["living_room", "decor"]),
    stub("beanbag_dev_01", "Beanbag", "beanbag", { w: 0.9, d: 0.9, h: 0.6 }, "free", ["living_room", "playroom", "seating", "cover"]),
  ];
  const byId: Record<string, Asset> = Object.fromEntries(list.map((a) => [a.id, a]));
  const byCategory = new Map(list.map((a) => [a.category, a]));
  for (const id of neededIds) {
    if (byId[id]) continue;
    const like = byCategory.get(assetCategory(id));
    if (like) byId[id] = { ...like, id, file: `furniture/v1/${id}.glb` };
  }
  return byId;
}

export const DEV_PLAN_B: PlacementPlan = {
  plan_id: "p_dev_b",
  listing_id: "hou_000123",
  room_id: "hou_000123.r1",
  status: "validated",
  objects: [
    { instance_id: "b1", asset_id: "sectional_dev_01", x: 3.0, y: 0, z: 2.1, rot_deg: 0, reason: "Centre of the room, cover for both sides" },
    { instance_id: "b2", asset_id: "bookshelf_dev_01", x: 0.25, y: 0, z: 2.1, rot_deg: 90, reason: "Full cover against the west wall" },
    { instance_id: "b3", asset_id: "beanbag_dev_01", x: 5.0, y: 0, z: 0.9, rot_deg: 0, reason: "Soft seat in the open corner" },
    { instance_id: "b4", asset_id: "floor_lamp_dev_01", x: 5.7, y: 0, z: 0.3, rot_deg: 0, reason: "Lights the corner by the beanbag" },
  ],
  conflicts: [],
};

/**
 * The pack with the dev catalog underneath its own: real catalog entries win,
 * dev stand-ins fill the gaps (all of them, today: the hero pack has none).
 */
export function withDevCatalog<T extends { catalog: Record<string, Asset> }>(
  pack: T,
  fixtureAsset: Asset,
  neededIds: string[] = [],
): T {
  return { ...pack, catalog: { ...devCatalog(fixtureAsset, neededIds), ...pack.catalog } };
}
