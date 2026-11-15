// FurnitureLayer's plan handling (useStableFurniture + useFurnitureLayout),
// rendered with react-dom in jsdom, fed the way net/game.ts feeds it: a new
// objects array on every state patch even when nothing changed.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { act, useMemo } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Asset, PlacementPlan, Room } from "../contracts.gen";
import { registerPlayer } from "../player/playerControl";
import { devCatalog } from "./devFurniture";
import { furnitureKey, type FurnitureLike } from "./furnitureKey";
import { footprints } from "./furniturePlan";
import { pointInRect } from "./geometry";
import { useFurnitureLayout, useStableFurniture } from "./useFurniturePlan";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const CONTRACTS = join(dirname(fileURLToPath(import.meta.url)), "../../../../contracts/fixtures");
const json = <T,>(f: string) => JSON.parse(readFileSync(join(CONTRACTS, f), "utf8")) as T;
const room = json<Room>("room.json");
const plan = json<PlacementPlan>("placement_plan.json");
const catalog: Record<string, Asset> = devCatalog(json<Asset>("asset.json"), plan.objects.map((o) => o.asset_id));
const ZONE = "hou_000123";

type House = { plan_id: string; objects: PlacementPlan["objects"] };
/** Stable, as HouseScene memoizes it per house. */
const ROOMS: Room[] = [room];
/** A brand-new house object and objects array with the same content, like every Colyseus patch. */
const patch = (h: House): House => JSON.parse(JSON.stringify(h));

let seenObjects: FurnitureLike[][] = [];
let seenKeys: string[] = [];

function Probe({
  house,
  setSpawns,
  assets = catalog,
}: {
  house: House | undefined;
  setSpawns: (id: string, s: unknown[]) => void;
  assets?: Record<string, Asset>;
}) {
  const { key, objects } = useStableFurniture(house);
  // As FurnitureLayer does it: footprints memoized on the stable objects and the catalog.
  const rects = useMemo(() => footprints(objects, assets), [objects, assets]);
  useFurnitureLayout({ zone: ZONE, rooms: ROOMS, rects, furnitureKey: key, setSpawns });
  seenObjects.push(objects);
  seenKeys.push(key);
  return null;
}

describe("FurnitureLayer plan handling under ~20 Hz identical patches", () => {
  let root: Root;
  let container: HTMLDivElement;
  let pos: [number, number, number];
  const getPos = vi.fn(() => pos);
  const teleport = vi.fn((p: [number, number, number]) => {
    pos = p;
  });
  let unregister: () => void;

  beforeEach(() => {
    seenObjects = [];
    seenKeys = [];
    getPos.mockClear();
    teleport.mockClear();
    // The local player stands inside the fixture plan's sofa (o1 at x 3.0, z 0.49).
    pos = [3.0, 0.95, 0.49];
    unregister = registerPlayer({ getPos, teleport });
    container = document.createElement("div");
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    unregister();
  });

  const render = (
    house: House | undefined,
    setSpawns: (id: string, s: unknown[]) => void,
    assets: Record<string, Asset> = catalog,
  ) => act(() => root.render(<Probe house={house} setSpawns={setSpawns} assets={assets} />));

  it("50 identical-content new arrays run push-out and setSpawns once", () => {
    const setSpawns = vi.fn();
    const first: House = { plan_id: plan.plan_id, objects: plan.objects };
    render(first, setSpawns);
    expect(setSpawns).toHaveBeenCalledTimes(1);
    expect(getPos).toHaveBeenCalledTimes(1); // one push-out evaluation...
    expect(teleport).toHaveBeenCalledTimes(1); // ...which moved the player out of the sofa
    const sofa = footprints(plan.objects.slice(0, 1), catalog)[0];
    expect(pointInRect([pos[0], pos[2]], sofa)).toBe(false);

    for (let i = 0; i < 50; i++) render(patch(first), setSpawns);

    expect(setSpawns).toHaveBeenCalledTimes(1);
    expect(getPos).toHaveBeenCalledTimes(1);
    expect(teleport).toHaveBeenCalledTimes(1);
    // Every render saw the same objects array, so footprints were not rebuilt either.
    expect(new Set(seenObjects).size).toBe(1);
    expect(new Set(seenKeys).size).toBe(1);
  });

  it("a real change runs them again, once", () => {
    const setSpawns = vi.fn();
    const first: House = { plan_id: plan.plan_id, objects: plan.objects };
    render(first, setSpawns);
    for (let i = 0; i < 10; i++) render(patch(first), setSpawns);

    // Move the sofa 1 m: the agent re-furnished (same plan_id).
    const moved = patch(first);
    moved.objects[0].x -= 1;
    render(moved, setSpawns);
    for (let i = 0; i < 10; i++) render(patch(moved), setSpawns);

    expect(setSpawns).toHaveBeenCalledTimes(2);
    expect(getPos).toHaveBeenCalledTimes(2);
    expect(new Set(seenObjects).size).toBe(2);
    // The remount key changed with the content even though plan_id did not.
    expect(new Set(seenKeys)).toEqual(new Set([furnitureKey(first), furnitureKey(moved)]));
  });

  it("same content key, but a catalog that adds a missing asset: footprints change, so it runs once more", () => {
    const setSpawns = vi.fn();
    const first: House = { plan_id: plan.plan_id, objects: plan.objects };
    // The catalog is missing the TV stand at first (o2 is skipped by footprints).
    const tvId = plan.objects[1].asset_id;
    const partial = Object.fromEntries(Object.entries(catalog).filter(([id]) => id !== tvId));
    render(first, setSpawns, partial);
    for (let i = 0; i < 10; i++) render(patch(first), setSpawns, partial);
    expect(setSpawns).toHaveBeenCalledTimes(1);
    expect(footprints(plan.objects, partial)).toHaveLength(1);

    // The real catalog arrives with the TV stand: same furniture content, new footprints.
    render(patch(first), setSpawns, catalog);
    for (let i = 0; i < 10; i++) render(patch(first), setSpawns, catalog);
    expect(footprints(plan.objects, catalog)).toHaveLength(2);
    expect(setSpawns).toHaveBeenCalledTimes(2);
    expect(new Set(seenKeys).size).toBe(1); // the content key never changed
  });

  it("a zone with no furniture still computes spawns once, and leaving furniture empty does not re-run", () => {
    const setSpawns = vi.fn();
    render(undefined, setSpawns);
    for (let i = 0; i < 20; i++) render(undefined, setSpawns);
    expect(setSpawns).toHaveBeenCalledTimes(1);
    expect(getPos).not.toHaveBeenCalled(); // no furniture, nothing to push out of
  });
});
