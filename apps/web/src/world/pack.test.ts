import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Listing, PackManifest, Room } from "../contracts.gen";
import {
  DOOR_RADIUS_M,
  DOOR_REARM_M,
  MANIFEST_HOUSE_KEYS,
  MANIFEST_KEYS,
  blockDoors,
  blockSpawn,
  blockToHouse,
  devStart,
  doorOutward,
  floorAt,
  houseToBlock,
  listingsIndexUrl,
  loadPack,
  makeDoor,
  normalizeBase,
  packUrl,
  parseCatalog,
  parseListings,
  parseManifest,
  parseRooms,
  polygonCentroid,
  resetWarnings,
  roomIdAt,
  stepZone,
  warnOnce,
  type FetchJson,
  type Vec3,
  type ZoneState,
} from "./pack";

// Not `new URL(..., import.meta.url)`: Vite rewrites that pattern into an asset import.
const CONTRACTS = join(dirname(fileURLToPath(import.meta.url)), "../../../../contracts");
const contracts = (p: string) => readFileSync(join(CONTRACTS, p), "utf8");

const FIXTURES = {
  manifest: contracts("fixtures/manifest.json"),
  listing: contracts("fixtures/listing.json"),
  room: contracts("fixtures/room.json"),
  asset: contracts("fixtures/asset.json"),
};
const manifest = JSON.parse(FIXTURES.manifest) as PackManifest;
const listing = JSON.parse(FIXTURES.listing) as Listing;
const room = JSON.parse(FIXTURES.room) as Room;
// Read from the fixture so a contract bump that renames the asset does not break these tests.
const asset = JSON.parse(FIXTURES.asset) as { id: string; size_m: { w: number } };
const ID = "hou_000123";

const close = (a: number[], b: number[]) =>
  a.forEach((v, i) => expect(v).toBeCloseTo(b[i], 6));

afterEach(() => {
  resetWarnings();
  vi.restoreAllMocks();
});

describe("parseManifest", () => {
  it("accepts the fixture, as text or as an object", () => {
    expect(parseManifest(FIXTURES.manifest).ok).toBe(true);
    expect(parseManifest(manifest).ok).toBe(true);
  });

  it("checks the same keys as manifest.schema.json", () => {
    const schema = JSON.parse(contracts("manifest.schema.json"));
    expect([...MANIFEST_KEYS].sort()).toEqual([...schema.required].sort());
    expect([...MANIFEST_KEYS].sort()).toEqual(Object.keys(schema.properties).sort());
    expect([...MANIFEST_HOUSE_KEYS].sort()).toEqual(
      [...schema.properties.houses.items.required].sort(),
    );
  });

  it("names a missing key", () => {
    const { block: _drop, ...rest } = manifest;
    void _drop;
    const r = parseManifest(rest);
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.errors).toContain('manifest: missing "block"');
  });

  it("rejects extra keys, as additionalProperties: false does", () => {
    const r = parseManifest({ ...manifest, extra: 1 });
    expect(r.ok).toBe(false);
    const h = parseManifest({ ...manifest, houses: [{ ...manifest.houses[0], door: [0, 0, 0] }] });
    expect(h.ok).toBe(false);
    if (!h.ok) expect(h.errors[0]).toMatch(/houses\[0\]: unexpected "door"/);
  });

  it("rejects bad values", () => {
    const bad = (patch: Record<string, unknown>) => parseManifest({ ...manifest, ...patch }).ok;
    expect(bad({ block_id: "Hou-Heights" })).toBe(false);
    expect(bad({ version: 0 })).toBe(false);
    expect(bad({ version: 1.5 })).toBe(false);
    expect(bad({ spawns: [[0, 0]] })).toBe(false);
    expect(bad({ origin: { lat: "29" , lon: 1 } })).toBe(false);
    expect(bad({ block: { render: "a.glb" } })).toBe(false);
  });

  it("reports text that is not JSON", () => {
    const r = parseManifest("<!doctype html>");
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.errors[0]).toMatch(/not JSON/);
  });
});

describe("listings, rooms and catalog files", () => {
  it("accept a single fixture object or an array", () => {
    expect(parseListings(FIXTURES.listing)).toMatchObject({ ok: true, value: [{ id: ID }] });
    expect(parseListings([listing, listing])).toMatchObject({ ok: true });
    expect(parseRooms(FIXTURES.room)).toMatchObject({ ok: true, value: [{ id: `${ID}.r1` }] });
  });

  it("indexes the catalog by asset id, from an array or {assets: [...]}", () => {
    const one = parseCatalog(FIXTURES.asset);
    expect(one.ok && Object.keys(one.value)).toEqual([asset.id]);
    const wrapped = parseCatalog({ assets: [JSON.parse(FIXTURES.asset)] });
    expect(wrapped.ok && wrapped.value[asset.id].size_m.w).toBe(asset.size_m.w);
  });

  it("reject what the client cannot use", () => {
    expect(parseListings([{ ...listing, world: { position: [0, 0], heading_deg: 0, door: [0, 0, 0] } }]).ok).toBe(false);
    expect(parseListings([{ ...listing, world: null }]).ok).toBe(true); // not on a built block: fine
    expect(parseRooms([{ ...room, polygon: [[0, 0], [1, 0]] }]).ok).toBe(false);
    expect(parseCatalog([{ id: "x", file: "x.glb", size_m: { w: 1, d: 1 } }]).ok).toBe(false);
  });
});

describe("urls", () => {
  it("normalizes the base", () => {
    expect(normalizeBase(undefined)).toBeNull();
    expect(normalizeBase("  ")).toBeNull();
    expect(normalizeBase("https://b.s3.aws/packs/hou_heights_01/v1/")).toBe(
      "https://b.s3.aws/packs/hou_heights_01/v1",
    );
  });

  it("resolves manifest-relative paths, and leaves absolute ones alone", () => {
    expect(packUrl("https://x/p/v1", "block/render.glb")).toBe("https://x/p/v1/block/render.glb");
    expect(packUrl("https://x/p/v1", "/block/render.glb")).toBe("https://x/p/v1/block/render.glb");
    expect(packUrl("https://x/p/v1", "https://cdn/r.glb")).toBe("https://cdn/r.glb");
  });

  it("finds listings/index.json next to packs/, per the AGENTS.md layout", () => {
    expect(listingsIndexUrl("https://b.s3.aws/packs/hou_heights_01/v2")).toBe(
      "https://b.s3.aws/listings/index.json",
    );
    expect(listingsIndexUrl("/static/world")).toBe("/static/world/listings/index.json");
  });
});

describe("frames", () => {
  const world = listing.world!;

  it("places the fixture's house-local door in the block frame", () => {
    // heading 90: house +X turns to block -Z.
    close(houseToBlock(world, [1.2, 0, 0]), [12, 0, -31.7]);
  });

  it("block -> house inverts house -> block", () => {
    const p: Vec3 = [3.3, 0.4, -2.1];
    for (const heading_deg of [0, 37, 90, 180, -135]) {
      const w = { ...world, heading_deg };
      close(blockToHouse(w, houseToBlock(w, p)), p);
    }
  });
});

describe("doors", () => {
  it("points out of the house through the footprint edge nearest the door", () => {
    expect(doorOutward([1.2, 0, 0], [room])).toEqual([0, -1]);
    expect(doorOutward([6, 0, 3], [room])).toEqual([1, 0]);
    expect(doorOutward([0, 0, 0], [])).toEqual([0, 1]);
  });

  it("enters inside a room and exits outside the house", () => {
    const d = makeDoor(listing, [room])!;
    close(d.block, [12, 0, -31.7]);
    close(d.enterTo, [1.2, 0, 1.2]);
    expect(roomIdAt(d.enterTo, [room])).toBe(`${ID}.r1`);
    close(d.exitTo, [10.8, 0, -31.7]);
  });

  it("falls back to the nearest room's centre when one step lands outside every room", () => {
    const corner = { ...listing, world: { ...listing.world!, door: [6, 0, 4.2] as Vec3 } };
    const thin: Room = { ...room, polygon: [[0, 0], [6, 0], [6, 0.5], [0, 0.5]] };
    const d = makeDoor(corner, [thin])!;
    expect(roomIdAt(d.enterTo, [thin])).toBe(thin.id);
  });

  it("only gives doors to manifest houses on this block, with a placement", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    expect(blockDoors(manifest, [listing], { [ID]: [room] })).toHaveLength(1);
    expect(blockDoors(manifest, [], {})).toHaveLength(0);
    expect(blockDoors(manifest, [{ ...listing, block_id: "lax_other" }], {})).toHaveLength(0);
    expect(blockDoors(manifest, [{ ...listing, world: null }], {})).toHaveLength(0);
    expect(warn).toHaveBeenCalledTimes(2);
  });
});

describe("stepZone", () => {
  const door = makeDoor(listing, [room])!;
  const doors = [door];
  const block = (armed = true): ZoneState => ({ zone: "block", armed });
  const away = (p: Vec3, by: number, dx = 1, dz = 0): Vec3 => [p[0] + dx * by, p[1], p[2] + dz * by];

  it("does nothing far from any door", () => {
    expect(stepZone(block(), [0, 0, 4], doors)).toEqual({ state: block(), change: null });
  });

  it("enters the house at its door, landing inside in the house frame", () => {
    const at = away(door.block, DOOR_RADIUS_M - 0.05, 0, 1);
    const r = stepZone(block(), at, doors);
    expect(r.change).toEqual({ zone: ID, spawn: door.enterTo });
    expect(r.state).toEqual({ zone: ID, armed: false });
  });

  it("does not bounce straight back out: the door re-arms only once you walk clear", () => {
    let s: ZoneState = { zone: ID, armed: false };
    // Standing on the arrival point, and even right on the door, does nothing yet.
    for (const p of [door.enterTo, door.local]) {
      const r = stepZone(s, p, doors);
      expect(r.change).toBeNull();
      s = r.state;
    }
    expect(s.armed).toBe(false);
    s = stepZone(s, [door.local[0], 0, door.local[2] + DOOR_REARM_M + 0.1], doors).state;
    expect(s.armed).toBe(true);
    const out = stepZone(s, door.local, doors);
    expect(out.change).toEqual({ zone: "block", spawn: door.exitTo });
  });

  it("in a house, only that house's door leads out", () => {
    const other = { ...door, listing_id: "hou_999", local: [50, 0, 50] as Vec3 };
    const r = stepZone({ zone: ID, armed: true }, [50, 0, 50], [door, other]);
    expect(r.change).toBeNull();
  });

  it("on the block, a disarmed door re-arms past the rearm distance", () => {
    expect(stepZone(block(false), door.exitTo, doors).state.armed).toBe(false);
    expect(stepZone(block(false), away(door.block, DOOR_REARM_M + 0.1, -1), doors).state.armed).toBe(true);
  });
});

describe("rooms under the player", () => {
  const upstairs: Room = { ...room, id: `${ID}.r9`, floor: 1, floor_y: 3, label: "bedroom" };

  it("picks the floor by height, then the room by position", () => {
    expect(floorAt(0.1, [room, upstairs])).toBe(0);
    expect(floorAt(3.05, [room, upstairs])).toBe(1);
    expect(roomIdAt([3, 0.1, 2], [room, upstairs])).toBe(`${ID}.r1`);
    expect(roomIdAt([3, 3.1, 2], [room, upstairs])).toBe(`${ID}.r9`);
    expect(roomIdAt([30, 0, 2], [room])).toBeNull();
  });
});

describe("loadPack", () => {
  const quiet = () => vi.spyOn(console, "warn").mockImplementation(() => {});

  it("uses the fixtures when no base URL is set, with one warning", async () => {
    const warn = quiet();
    const p = await loadPack({ base: undefined, fixtures: FIXTURES });
    expect(p.source).toBe("fixture");
    expect(p.base).toBeNull();
    expect(p.doors.map((d) => d.listing_id)).toEqual([ID]);
    expect(p.roomsByHouse[ID].map((r) => r.id)).toEqual([`${ID}.r1`]);
    expect(Object.keys(p.catalog)).toEqual([asset.id]);
    expect(blockSpawn(p.manifest)).toEqual([0, 0, 4]);
    await loadPack({ base: "", fixtures: FIXTURES });
    expect(warn).toHaveBeenCalledTimes(1);
  });

  it("falls back to the fixtures when the manifest fetch fails", async () => {
    const warn = quiet();
    const fetch: FetchJson = async () => {
      throw new Error("HTTP 404");
    };
    const p = await loadPack({ base: "https://x/packs/b/v1", fixtures: FIXTURES, fetch });
    expect(p.source).toBe("fixture");
    expect(warn.mock.calls[0][0]).toMatch(/manifest\.json \(HTTP 404\)/);
  });

  it("falls back to the fixtures when the manifest is invalid", async () => {
    quiet();
    const fetch: FetchJson = async () => ({ ...manifest, version: "one" });
    const p = await loadPack({ base: "https://x/packs/b/v1", fixtures: FIXTURES, fetch });
    expect(p.source).toBe("fixture");
  });

  it("loads a real pack, fetching the listings index from the bucket root", async () => {
    const files: Record<string, unknown> = {
      "https://x/packs/hou_heights_01/v1/manifest.json": manifest,
      "https://x/listings/index.json": [listing],
      "https://x/furniture/v1/catalog.json": [JSON.parse(FIXTURES.asset)], // shared library at the root (v2.3.0)
      "https://x/packs/hou_heights_01/v1/houses/hou_000123/rooms.json": [room],
    };
    const fetch: FetchJson = async (u) => {
      if (!(u in files)) throw new Error(`HTTP 404 ${u}`);
      return files[u];
    };
    const p = await loadPack({ base: "https://x/packs/hou_heights_01/v1/", fixtures: FIXTURES, fetch });
    expect(p.source).toBe("pack");
    expect(p.base).toBe("https://x/packs/hou_heights_01/v1");
    expect(p.doors).toHaveLength(1);
    expect(p.roomsByHouse[ID]).toHaveLength(1);
    expect(p.catalog[asset.id]).toBeDefined();
  });

  it("keeps a real pack when only a side file is missing", async () => {
    const warn = quiet();
    const fetch: FetchJson = async (u) => {
      if (u.endsWith("manifest.json")) return manifest;
      if (u.endsWith("listings/index.json")) return [listing];
      throw new Error("HTTP 404");
    };
    const p = await loadPack({ base: "https://x/packs/hou_heights_01/v1", fixtures: FIXTURES, fetch });
    expect(p.source).toBe("pack");
    expect(p.catalog).toEqual({});
    expect(p.roomsByHouse[ID]).toEqual([]);
    expect(p.doors).toHaveLength(1); // a door with no rooms still works
    expect(warn).toHaveBeenCalledTimes(2); // catalog + rooms, once each
  });
});

describe("warnOnce", () => {
  it("prints each key once", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    expect(warnOnce("a", "first")).toBe(true);
    expect(warnOnce("a", "again")).toBe(false);
    expect(warnOnce("b", "other")).toBe(true);
    expect(warn).toHaveBeenCalledTimes(2);
  });
});

describe("devStart (VITE_START_ROOM)", () => {
  // The hero living room's real outline (hou_237901269.r4, an 11-point L-ish shape).
  const r4: Room = {
    ...room,
    id: "hou_237901269.r4",
    floor_y: 0.005,
    polygon: [
      [-0.497, 0.512], [-0.496, 0.826], [0.56, 0.823], [1.278, 0.821], [3.701, 0.814],
      [5.2, -0.693], [5.191, -3.85], [-0.809, -3.85], [-0.806, -2.975], [-3.192, -0.575], [-3.189, 0.519],
    ],
    openings: [],
  };
  const hero = {
    ...manifest,
    houses: [{ ...manifest.houses[0], listing_id: "hou_237901269" }],
    spawns: [],
  } as PackManifest;
  const pack = {
    source: "pack" as const,
    base: "/packs/hou_navidad_01/v1",
    manifest: hero,
    listings: [],
    roomsByHouse: { hou_237901269: [r4] },
    catalog: {},
    doors: [],
  };

  it("takes the area centroid, not the vertex average", () => {
    const [x, z] = polygonCentroid(r4.polygon);
    expect(x).toBeCloseTo(1.44, 2);
    expect(z).toBeCloseTo(-1.453, 2);
    expect(polygonCentroid([[0, 0], [4, 0], [4, 2], [0, 2]])).toEqual([2, 1]);
  });

  it("starts inside the room's house, at its centroid, on its floor", () => {
    const s = devStart(pack, "hou_237901269.r4")!;
    expect(s.zone).toBe("hou_237901269");
    expect(s.roomId).toBe("hou_237901269.r4");
    expect(s.pos[0]).toBeCloseTo(1.44, 2);
    expect(s.pos[1]).toBe(0.005);
    expect(s.pos[2]).toBeCloseTo(-1.453, 2);
    expect(roomIdAt(s.pos, [r4])).toBe(r4.id);
  });

  it("uses an open floor point when the centroid falls outside (U shape)", () => {
    const u: Room = { ...r4, id: "hou_237901269.r9", polygon: [[0, 0], [6, 0], [6, 4], [4, 4], [4, 1], [2, 1], [2, 4], [0, 4]] };
    const s = devStart({ ...pack, roomsByHouse: { hou_237901269: [u] } }, u.id)!;
    expect(roomIdAt(s.pos, [u])).toBe(u.id);
  });

  it("is off when unset, and falls back to the block with one warning when wrong", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    expect(devStart(pack, undefined)).toBeNull();
    expect(devStart(pack, "  ")).toBeNull();
    expect(devStart(pack, "hou_999.r1")).toBeNull();
    expect(devStart(pack, "hou_237901269.r42")).toBeNull();
    expect(devStart(pack, "hou_237901269.r42")).toBeNull();
    expect(warn).toHaveBeenCalledTimes(2);
  });
});

describe("loadPack with the hero pack's real file set", () => {
  it("loads with no catalog, no listings, no block meshes and no spawns", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const base = "/packs/hou_navidad_01/v1";
    const hero = {
      ...manifest,
      block_id: "hou_navidad_01",
      houses: [{ ...manifest.houses[0], listing_id: "hou_237901269",
        rooms: "houses/hou_237901269/rooms.json", audit: "houses/hou_237901269/audit.json" }],
      spawns: [],
    };
    const asked: string[] = [];
    const fetch: FetchJson = async (u) => {
      asked.push(u);
      if (u === `${base}/manifest.json`) return hero;
      if (u === `${base}/houses/hou_237901269/rooms.json`) return [room];
      // furniture/catalog.json and listings/index.json are not in the zip.
      throw new Error("got HTML, not JSON (file missing?)");
    };
    const p = await loadPack({ base, fixtures: FIXTURES, fetch });
    expect(p.source).toBe("pack");
    expect(p.catalog).toEqual({});
    expect(p.doors).toEqual([]);
    expect(p.roomsByHouse.hou_237901269).toHaveLength(1);
    expect(blockSpawn(p.manifest)).toEqual([0, 0, 0]);
    // audit.json is the agent's file; the client never asks for it.
    expect(asked.some((u) => u.includes("audit"))).toBe(false);
    expect(warn.mock.calls.map((c) => String(c[0]))).toEqual([
      expect.stringMatching(/listings\/index\.json/),
      expect.stringMatching(/^\[pack\] \/furniture\/v\d+\/catalog\.json/), // shared library at the published root (contracts v2.3.0)
      expect.stringMatching(/hou_237901269 is in the manifest but not in the listings index/),
    ]);
  });
});
