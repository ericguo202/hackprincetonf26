import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import type { Asset } from "../contracts.gen";
import { filterAssets, groupOf } from "./catalogGroups";

// The published furniture library (pack v3 -> furniture/v3).
const PACKS = join(dirname(fileURLToPath(import.meta.url)), "../../public/packs");
const raw = JSON.parse(readFileSync(join(PACKS, "furniture/v3/catalog.json"), "utf8")) as Asset[];
const catalog = Object.fromEntries(raw.map((a) => [a.id, a]));

describe("catalog groups", () => {
  it("puts every published category under a room tab, none under Other", () => {
    const other = raw.filter((a) => groupOf(a.category) === "other").map((a) => a.category);
    expect(other).toEqual([]);
  });

  it("all = every asset once; living room starts with seating", () => {
    expect(filterAssets(catalog, "all", "")).toHaveLength(raw.length);
    const living = filterAssets(catalog, "living", "");
    expect(living[0].category).toBe("sofa");
    expect(living.every((a) => groupOf(a.category) === "living")).toBe(true);
  });

  it("search matches name, category words, colour and style, all words required", () => {
    expect(filterAssets(catalog, "all", "crib").every((a) => a.category === "crib")).toBe(true);
    expect(filterAssets(catalog, "all", "coffee table").every((a) => a.category === "coffee_table")).toBe(true);
    expect(filterAssets(catalog, "all", "charcoal sofa").map((a) => a.id)).toContain("sofa_abo_B072C8Q9RF");
    expect(filterAssets(catalog, "all", "zzz-nothing")).toEqual([]);
  });
});
