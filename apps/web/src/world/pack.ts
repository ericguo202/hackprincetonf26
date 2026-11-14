// Lane C: world pack loading. Pure: no React, no three.js, no Vite-only
// imports, so every rule here is unit-tested (pack.test.ts).
//
// What a pack is (AGENTS.md section 7): packs/<block_id>/v<N>/manifest.json
// plus the files it names. VITE_PACK_BASE_URL points at that v<N>/ folder.
// listings/index.json sits next to packs/ at the bucket root, so from the
// pack URL it is ../../../listings/index.json.
//
// Frames (AGENTS.md section 8, manifest.schema.json): the block frame has its
// origin at the block centre, +X east, +Y up, +Z south. Each house has its own
// house-local frame (rooms.json, placements). A listing's `world` places the
// house in the block: block = position + rotY(heading_deg) * local, with the
// same counterclockwise-from-above rotation as rot_deg (geometry.ts).
//
// Doors: the manifest has no door points. They come from Listing.world.door
// (house-local), so the listings index is part of loading a block.

import type { Asset, Listing, PackManifest, Point2, Room } from "../contracts.gen";
import { pointInPolygon, roomAt, rotationY, spawnPoints } from "./geometry.ts";

export type Vec3 = [number, number, number];
type ListingWorld = NonNullable<Listing["world"]>;

// ---------------------------------------------------------------------------
// Warnings: one line per problem, never one per frame or per retry
// ---------------------------------------------------------------------------

const warned = new Set<string>();

/** console.warn once per key. Returns true if it printed. */
export function warnOnce(key: string, message: string): boolean {
  if (warned.has(key)) return false;
  warned.add(key);
  console.warn(message);
  return true;
}

/** Tests only. */
export function resetWarnings(): void {
  warned.clear();
}

// ---------------------------------------------------------------------------
// Validation (hand-written mirror of contracts/*.schema.json; pack.test.ts
// checks the required-key lists against the schema files so they cannot drift)
// ---------------------------------------------------------------------------

export type Parsed<T> = { ok: true; value: T } | { ok: false; errors: string[] };

export const MANIFEST_KEYS = [
  "block_id",
  "version",
  "origin",
  "block",
  "houses",
  "spawns",
  "furniture_catalog",
] as const;
export const MANIFEST_HOUSE_KEYS = [
  "listing_id",
  "interior",
  "collision",
  "rooms",
  "graph",
  "audit",
] as const;

const isObj = (x: unknown): x is Record<string, unknown> =>
  typeof x === "object" && x !== null && !Array.isArray(x);
const isNum = (x: unknown): x is number => typeof x === "number" && Number.isFinite(x);
const isStr = (x: unknown): x is string => typeof x === "string";
const isVec3 = (x: unknown): x is Vec3 => Array.isArray(x) && x.length === 3 && x.every(isNum);
const isPoint2 = (x: unknown): x is Point2 =>
  Array.isArray(x) && x.length === 2 && x.every(isNum);

function exactKeys(
  o: Record<string, unknown>,
  required: readonly string[],
  where: string,
  errors: string[],
): void {
  for (const k of required) if (!(k in o)) errors.push(`${where}: missing "${k}"`);
  for (const k of Object.keys(o))
    if (!required.includes(k)) errors.push(`${where}: unexpected "${k}"`);
}

/** Validate against manifest.schema.json (which allows no extra keys anywhere). */
export function parseManifest(input: unknown): Parsed<PackManifest> {
  let data = input;
  if (isStr(input)) {
    try {
      data = JSON.parse(input);
    } catch (e) {
      return { ok: false, errors: [`manifest: not JSON (${(e as Error).message})`] };
    }
  }
  const errors: string[] = [];
  if (!isObj(data)) return { ok: false, errors: ["manifest: not an object"] };

  exactKeys(data, MANIFEST_KEYS, "manifest", errors);
  if ("block_id" in data && !(isStr(data.block_id) && /^[a-z0-9_]+$/.test(data.block_id)))
    errors.push("manifest.block_id: must match ^[a-z0-9_]+$");
  if ("version" in data && !(Number.isInteger(data.version) && (data.version as number) >= 1))
    errors.push("manifest.version: must be an integer >= 1");

  if ("origin" in data) {
    const o = data.origin;
    if (!isObj(o)) errors.push("manifest.origin: not an object");
    else {
      exactKeys(o, ["lat", "lon"], "manifest.origin", errors);
      if ("lat" in o && !isNum(o.lat)) errors.push("manifest.origin.lat: not a number");
      if ("lon" in o && !isNum(o.lon)) errors.push("manifest.origin.lon: not a number");
    }
  }
  if ("block" in data) {
    const b = data.block;
    if (!isObj(b)) errors.push("manifest.block: not an object");
    else {
      exactKeys(b, ["render", "collision"], "manifest.block", errors);
      for (const k of ["render", "collision"])
        if (k in b && !isStr(b[k])) errors.push(`manifest.block.${k}: not a string`);
    }
  }
  if ("houses" in data) {
    if (!Array.isArray(data.houses)) errors.push("manifest.houses: not an array");
    else
      data.houses.forEach((h, i) => {
        const where = `manifest.houses[${i}]`;
        if (!isObj(h)) return errors.push(`${where}: not an object`);
        exactKeys(h, MANIFEST_HOUSE_KEYS, where, errors);
        for (const k of MANIFEST_HOUSE_KEYS)
          if (k in h && !isStr(h[k])) errors.push(`${where}.${k}: not a string`);
      });
  }
  if ("spawns" in data) {
    if (!Array.isArray(data.spawns)) errors.push("manifest.spawns: not an array");
    else
      data.spawns.forEach((s, i) => {
        if (!isVec3(s)) errors.push(`manifest.spawns[${i}]: not [x, y, z]`);
      });
  }
  if ("furniture_catalog" in data && !isStr(data.furniture_catalog))
    errors.push("manifest.furniture_catalog: not a string");

  return errors.length ? { ok: false, errors } : { ok: true, value: data as unknown as PackManifest };
}

/** A file that may hold one item or an array of them (fixtures hold one). */
function asArray(data: unknown): unknown[] {
  if (Array.isArray(data)) return data;
  if (isObj(data) && Array.isArray(data.assets)) return data.assets; // {"assets": [...]}
  return data === undefined || data === null ? [] : [data];
}

function parseJson(input: unknown): unknown {
  return isStr(input) ? JSON.parse(input) : input;
}

/**
 * Listings index -> listings usable for doors. Structural checks only on the
 * fields the client reads; anything else is the agent's business.
 */
export function parseListings(input: unknown): Parsed<Listing[]> {
  const errors: string[] = [];
  const out: Listing[] = [];
  asArray(parseJson(input)).forEach((l, i) => {
    if (!isObj(l) || !isStr(l.id)) return errors.push(`listings[${i}]: no id`);
    const w = l.world;
    if (w !== null && w !== undefined) {
      if (!isObj(w) || !isVec3(w.position) || !isNum(w.heading_deg) || !isVec3(w.door))
        return errors.push(`listings[${i}] (${l.id}): bad world {position, heading_deg, door}`);
    }
    out.push(l as unknown as Listing);
  });
  return errors.length ? { ok: false, errors } : { ok: true, value: out };
}

export function parseRooms(input: unknown): Parsed<Room[]> {
  const errors: string[] = [];
  const out: Room[] = [];
  asArray(parseJson(input)).forEach((r, i) => {
    const where = `rooms[${i}]`;
    if (!isObj(r) || !isStr(r.id)) return errors.push(`${where}: no id`);
    if (!Array.isArray(r.polygon) || r.polygon.length < 3 || !r.polygon.every(isPoint2))
      return errors.push(`${where} (${r.id}): polygon needs 3+ [x, z] points`);
    if (!isNum(r.floor_y) || !isNum(r.height_m))
      return errors.push(`${where} (${r.id}): floor_y and height_m must be numbers`);
    if (!Array.isArray(r.openings)) return errors.push(`${where} (${r.id}): openings not an array`);
    out.push(r as unknown as Room);
  });
  return errors.length ? { ok: false, errors } : { ok: true, value: out };
}

/** catalog.json -> asset_id -> Asset, which is what FurnitureLayer takes. */
export function parseCatalog(input: unknown): Parsed<Record<string, Asset>> {
  const errors: string[] = [];
  const out: Record<string, Asset> = {};
  asArray(parseJson(input)).forEach((a, i) => {
    const where = `catalog[${i}]`;
    if (!isObj(a) || !isStr(a.id) || !isStr(a.file)) return errors.push(`${where}: needs id and file`);
    const s = a.size_m;
    if (!isObj(s) || !isNum(s.w) || !isNum(s.d) || !isNum(s.h))
      return errors.push(`${where} (${a.id}): size_m needs w, d, h`);
    out[a.id] = a as unknown as Asset;
  });
  return errors.length ? { ok: false, errors } : { ok: true, value: out };
}

// ---------------------------------------------------------------------------
// URLs
// ---------------------------------------------------------------------------

/** Trimmed base URL, or null when unset (fixture mode). */
export function normalizeBase(base: string | undefined | null): string | null {
  const b = (base ?? "").trim();
  return b ? b.replace(/\/+$/, "") : null;
}

/** A manifest-relative path resolved against the pack base. */
export function packUrl(base: string, rel: string): string {
  if (/^[a-z]+:\/\//i.test(rel)) return rel;
  return `${base}/${rel.replace(/^\/+/, "")}`;
}

/**
 * packs/<block>/v<N> -> the published root that mirrors worldgen/out (AGENTS.md section 7 layout).
 * Not the standard layout (a flat static folder): the pack folder itself is the root.
 */
export function publishedRoot(base: string): string {
  const parts = base.split("/");
  const tail = parts.slice(-3);
  if (tail.length === 3 && tail[0] === "packs" && /^v\d+$/.test(tail[2])) return parts.slice(0, -3).join("/");
  return base;
}

/** packs/<block>/v<N> -> <root>/listings/index.json. */
export function listingsIndexUrl(base: string): string {
  return `${publishedRoot(base)}/listings/index.json`;
}

/** Root-relative path (furniture/v<N>/..., contracts v2.3.0) resolved against the published root. */
export function rootUrl(base: string, rel: string): string {
  return packUrl(publishedRoot(base), rel);
}

// ---------------------------------------------------------------------------
// Frames
// ---------------------------------------------------------------------------

/** Rotate a house-local [x, z] by heading_deg, counterclockwise from above. */
function rotXZ(x: number, z: number, headingDeg: number): Point2 {
  const r = rotationY(headingDeg);
  const c = Math.cos(r);
  const s = Math.sin(r);
  return [x * c + z * s, -x * s + z * c];
}

export function houseToBlock(world: ListingWorld, p: Vec3): Vec3 {
  const [x, z] = rotXZ(p[0], p[2], world.heading_deg);
  return [world.position[0] + x, world.position[1] + p[1], world.position[2] + z];
}

export function blockToHouse(world: ListingWorld, p: Vec3): Vec3 {
  const dx = p[0] - world.position[0];
  const dz = p[2] - world.position[2];
  const [x, z] = rotXZ(dx, dz, -world.heading_deg);
  return [x, p[1] - world.position[1], z];
}

// ---------------------------------------------------------------------------
// Doors
// ---------------------------------------------------------------------------

/** How far to step through a door when changing zone, so you land clear of it. */
export const DOOR_STEP_M = 1.2;
/** Walk within this of a door point (XZ) to go through it. */
export const DOOR_RADIUS_M = 0.9;
/** After a zone change the door re-arms once you are this far from it. */
export const DOOR_REARM_M = 1.8;

export type Bounds = { minX: number; maxX: number; minZ: number; maxZ: number };

export function roomsBounds(rooms: Room[]): Bounds | null {
  if (!rooms.length) return null;
  const b: Bounds = { minX: Infinity, maxX: -Infinity, minZ: Infinity, maxZ: -Infinity };
  for (const r of rooms)
    for (const [x, z] of r.polygon) {
      b.minX = Math.min(b.minX, x);
      b.maxX = Math.max(b.maxX, x);
      b.minZ = Math.min(b.minZ, z);
      b.maxZ = Math.max(b.maxZ, z);
    }
  return b;
}

function centroid(poly: Point2[]): Point2 {
  let x = 0;
  let z = 0;
  for (const p of poly) {
    x += p[0];
    z += p[1];
  }
  return [x / poly.length, z / poly.length];
}

/**
 * House-local unit vector pointing out of the house at the door: the outward
 * normal of the footprint edge nearest the door. With no rooms, +Z (the
 * house's front at heading 0).
 */
export function doorOutward(doorLocal: Vec3, rooms: Room[]): Point2 {
  const b = roomsBounds(rooms);
  if (!b) return [0, 1];
  const [x, z] = [doorLocal[0], doorLocal[2]];
  const edges: [number, Point2][] = [
    [Math.abs(x - b.minX), [-1, 0]],
    [Math.abs(x - b.maxX), [1, 0]],
    [Math.abs(z - b.minZ), [0, -1]],
    [Math.abs(z - b.maxZ), [0, 1]],
  ];
  edges.sort((a, c) => a[0] - c[0]);
  return edges[0][1];
}

export type Door = {
  listing_id: string;
  world: ListingWorld;
  /** Door point in the block frame. */
  block: Vec3;
  /** Door point in the house-local frame. */
  local: Vec3;
  /** Where you land in the block when leaving (block frame, floor height). */
  exitTo: Vec3;
  /** Where you land in the house when entering (house-local, floor height). */
  enterTo: Vec3;
};

export function makeDoor(listing: Listing, rooms: Room[]): Door | null {
  const world = listing.world;
  if (!world) return null;
  const local = world.door as Vec3;
  const [ox, oz] = doorOutward(local, rooms);

  let inside: Point2 = [local[0] - ox * DOOR_STEP_M, local[2] - oz * DOOR_STEP_M];
  if (rooms.length && !rooms.some((r) => pointInPolygon(inside, r.polygon))) {
    // The step did not land in a room (odd footprint): use the nearest room's centre.
    const nearest = [...rooms].sort(
      (a, b) =>
        Math.hypot(...sub(centroid(a.polygon), [local[0], local[2]])) -
        Math.hypot(...sub(centroid(b.polygon), [local[0], local[2]])),
    )[0];
    inside = centroid(nearest.polygon);
  }
  const insideRoom = roomAt(inside, rooms);
  const floorY = insideRoom ? insideRoom.floor_y : local[1];

  const outsideLocal: Vec3 = [local[0] + ox * DOOR_STEP_M, local[1], local[2] + oz * DOOR_STEP_M];
  return {
    listing_id: listing.id,
    world,
    block: houseToBlock(world, local),
    local,
    exitTo: houseToBlock(world, outsideLocal),
    enterTo: [inside[0], floorY, inside[1]],
  };
}

function sub(a: Point2, b: Point2): [number, number] {
  return [a[0] - b[0], a[1] - b[1]];
}

/** Doors you can walk through on this block: listed in the manifest, placed in the world. */
export function blockDoors(
  manifest: PackManifest,
  listings: Listing[],
  roomsByHouse: Record<string, Room[]>,
): Door[] {
  const byId = new Map(listings.map((l) => [l.id, l]));
  const doors: Door[] = [];
  for (const h of manifest.houses) {
    const l = byId.get(h.listing_id);
    if (!l) {
      warnOnce(`nolisting:${h.listing_id}`, `[pack] house ${h.listing_id} is in the manifest but not in the listings index; it has no door`);
      continue;
    }
    if (l.block_id && l.block_id !== manifest.block_id) continue;
    const d = makeDoor(l, roomsByHouse[h.listing_id] ?? []);
    if (d) doors.push(d);
    else warnOnce(`noworld:${h.listing_id}`, `[pack] listing ${h.listing_id} has no world placement; it has no door`);
  }
  return doors;
}

// ---------------------------------------------------------------------------
// Zone selection
// ---------------------------------------------------------------------------

export type ZoneState = {
  /** "block" or a listing_id (the store's me.zone). */
  zone: string;
  /** False right after a zone change, until you walk clear of the door you came through. */
  armed: boolean;
};

export type ZoneChange = { zone: string; spawn: Vec3 };

function distXZ(a: Vec3, b: Vec3): number {
  return Math.hypot(a[0] - b[0], a[2] - b[2]);
}

/**
 * One step of the door trigger. `pos` is in the current zone's frame. In the
 * block, any door takes you into its house; in a house, only that house's own
 * door takes you back out. Returns the new state and, on a change, where to
 * put the player in the new zone's frame.
 */
export function stepZone(
  state: ZoneState,
  pos: Vec3,
  doors: Door[],
): { state: ZoneState; change: ZoneChange | null } {
  if (state.zone === "block") {
    const near = nearestDoor(pos, doors, (d) => d.block);
    if (!state.armed) {
      const armed = !near || near.dist > DOOR_REARM_M;
      return { state: { zone: "block", armed }, change: null };
    }
    if (near && near.dist <= DOOR_RADIUS_M) {
      return {
        state: { zone: near.door.listing_id, armed: false },
        change: { zone: near.door.listing_id, spawn: near.door.enterTo },
      };
    }
    return { state, change: null };
  }

  const door = doors.find((d) => d.listing_id === state.zone);
  if (!door) {
    // In a house the block no longer lists: let the caller send us outside.
    return { state, change: null };
  }
  const dist = distXZ(pos, door.local);
  if (!state.armed) {
    return { state: { zone: state.zone, armed: dist > DOOR_REARM_M }, change: null };
  }
  if (dist <= DOOR_RADIUS_M) {
    return {
      state: { zone: "block", armed: false },
      change: { zone: "block", spawn: door.exitTo },
    };
  }
  return { state, change: null };
}

function nearestDoor(
  pos: Vec3,
  doors: Door[],
  at: (d: Door) => Vec3,
): { door: Door; dist: number } | null {
  let best: { door: Door; dist: number } | null = null;
  for (const d of doors) {
    const dist = distXZ(pos, at(d));
    if (!best || dist < best.dist) best = { door: d, dist };
  }
  return best;
}

/**
 * Which floor's rooms to test for me.roomId: the highest floor whose floor_y
 * is at or below the player's feet (with a little slack for stairs).
 */
export function floorAt(y: number, rooms: Room[]): number | undefined {
  let best: Room | undefined;
  for (const r of rooms) {
    if (r.floor_y <= y + 0.3 && (!best || r.floor_y > best.floor_y)) best = r;
  }
  return best?.floor;
}

export function roomIdAt(pos: Vec3, rooms: Room[]): string | null {
  return roomAt([pos[0], pos[2]], rooms, floorAt(pos[1], rooms))?.id ?? null;
}

// ---------------------------------------------------------------------------
// Loading
// ---------------------------------------------------------------------------

export type LoadedPack = {
  /** "pack" when the manifest came from VITE_PACK_BASE_URL; "fixture" otherwise. */
  source: "pack" | "fixture";
  /** Base for manifest-relative files; null in fixture mode (no meshes exist). */
  base: string | null;
  manifest: PackManifest;
  listings: Listing[];
  roomsByHouse: Record<string, Room[]>;
  catalog: Record<string, Asset>;
  doors: Door[];
};

export type FixtureText = { manifest: string; listing: string; room: string; asset: string };

/** Resolves to parsed JSON, or throws on network error / non-2xx. */
export type FetchJson = (url: string) => Promise<unknown>;

export const fetchJson: FetchJson = async (url) => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const type = res.headers.get("content-type") ?? "";
  // Vite's dev server answers a missing file with index.html and a 200.
  if (type.includes("text/html")) throw new Error("got HTML, not JSON (file missing?)");
  return res.json();
};

function loadFixtures(fx: FixtureText): LoadedPack {
  const m = parseManifest(fx.manifest);
  if (!m.ok) throw new Error(`contracts/fixtures/manifest.json is invalid: ${m.errors.join("; ")}`);
  const manifest = m.value;
  const listings = unwrap(parseListings(fx.listing), "fixture listing");
  const rooms = unwrap(parseRooms(fx.room), "fixture room");
  const catalog = unwrap(parseCatalog(fx.asset), "fixture asset");
  const roomsByHouse: Record<string, Room[]> = {};
  for (const h of manifest.houses) {
    roomsByHouse[h.listing_id] = rooms.filter((r) => r.id.startsWith(`${h.listing_id}.`));
  }
  return {
    source: "fixture",
    base: null,
    manifest,
    listings,
    roomsByHouse,
    catalog,
    doors: blockDoors(manifest, listings, roomsByHouse),
  };
}

function unwrap<T>(p: Parsed<T>, what: string): T {
  if (!p.ok) throw new Error(`${what} is invalid: ${p.errors.join("; ")}`);
  return p.value;
}

/**
 * Load the pack at `base`, or the contract fixtures when `base` is unset or the
 * manifest cannot be fetched or validated. Past the manifest, a missing or bad
 * file degrades to empty (one warning each) rather than dropping the pack.
 */
export async function loadPack(opts: {
  base: string | null | undefined;
  fixtures: FixtureText;
  fetch?: FetchJson;
}): Promise<LoadedPack> {
  const get = opts.fetch ?? fetchJson;
  const base = normalizeBase(opts.base);

  if (!base) {
    warnOnce(
      "fixture-mode",
      "[pack] VITE_PACK_BASE_URL is not set: using contracts/fixtures and placeholder geometry",
    );
    return loadFixtures(opts.fixtures);
  }

  let manifest: PackManifest;
  try {
    const parsed = parseManifest(await get(packUrl(base, "manifest.json")));
    if (!parsed.ok) throw new Error(`invalid manifest: ${parsed.errors.join("; ")}`);
    manifest = parsed.value;
  } catch (e) {
    warnOnce(
      "fixture-fallback",
      `[pack] could not load ${packUrl(base, "manifest.json")} (${(e as Error).message}): using contracts/fixtures and placeholder geometry`,
    );
    return loadFixtures(opts.fixtures);
  }

  const soft = async <T>(url: string, parse: (x: unknown) => Parsed<T>, empty: T): Promise<T> => {
    try {
      const p = parse(await get(url));
      if (!p.ok) throw new Error(p.errors.join("; "));
      return p.value;
    } catch (e) {
      warnOnce(`soft:${url}`, `[pack] ${url}: ${(e as Error).message}; continuing without it`);
      return empty;
    }
  };

  const [listings, catalog, roomLists] = await Promise.all([
    soft(listingsIndexUrl(base), parseListings, [] as Listing[]),
    soft(rootUrl(base, manifest.furniture_catalog), parseCatalog, {} as Record<string, Asset>),
    Promise.all(manifest.houses.map((h) => soft(packUrl(base, h.rooms), parseRooms, [] as Room[]))),
  ]);
  const roomsByHouse: Record<string, Room[]> = {};
  manifest.houses.forEach((h, i) => (roomsByHouse[h.listing_id] = roomLists[i]));

  return {
    source: "pack",
    base,
    manifest,
    listings,
    roomsByHouse,
    catalog,
    doors: blockDoors(manifest, listings, roomsByHouse),
  };
}

/** Block-frame spawn: the manifest's first sidewalk spawn, else the origin. */
export function blockSpawn(manifest: PackManifest): Vec3 {
  return (manifest.spawns[0] as Vec3 | undefined) ?? [0, 0, 0];
}

// ---------------------------------------------------------------------------
// Dev start: begin inside a house, skipping the block
// ---------------------------------------------------------------------------

/** Area-weighted centroid; the vertex average for a degenerate polygon. */
export function polygonCentroid(poly: Point2[]): Point2 {
  let a = 0;
  let cx = 0;
  let cz = 0;
  for (let i = 0; i < poly.length; i++) {
    const [x0, z0] = poly[i];
    const [x1, z1] = poly[(i + 1) % poly.length];
    const c = x0 * z1 - x1 * z0;
    a += c;
    cx += (x0 + x1) * c;
    cz += (z0 + z1) * c;
  }
  if (Math.abs(a) < 1e-9) return centroid(poly);
  return [cx / (3 * a), cz / (3 * a)];
}

export type DevStart = { zone: string; pos: Vec3; roomId: string };

/**
 * VITE_START_ROOM=<room_id> (e.g. hou_237901269.r4): start inside that room's
 * house, at the room's centroid, in the house-local frame. For walking a
 * house before its block exists. If the centroid falls outside the room
 * (an L or U shape), the room's most open floor point is used instead.
 * Returns null, with one warning, when the room is not in this pack.
 */
export function devStart(pack: LoadedPack, roomId: string | undefined | null): DevStart | null {
  const id = (roomId ?? "").trim();
  if (!id) return null;
  const dot = id.lastIndexOf(".");
  const zone = dot > 0 ? id.slice(0, dot) : id;
  if (!pack.manifest.houses.some((h) => h.listing_id === zone)) {
    warnOnce(`devstart:${id}`, `[pack] VITE_START_ROOM=${id}: house ${zone} is not in this pack; starting on the block`);
    return null;
  }
  const room = (pack.roomsByHouse[zone] ?? []).find((r) => r.id === id);
  if (!room) {
    warnOnce(`devstart:${id}`, `[pack] VITE_START_ROOM=${id}: no such room in ${zone}/rooms.json; starting on the block`);
    return null;
  }
  let p = polygonCentroid(room.polygon);
  if (!pointInPolygon(p, room.polygon)) p = spawnPoints(room, [], 1)[0] ?? room.polygon[0];
  return { zone, pos: [p[0], room.floor_y, p[1]], roomId: room.id };
}
