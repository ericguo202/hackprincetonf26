// Manual furnishing catalog: room tabs and search over the furniture catalog.
// Pure; tested in catalogGroups.test.ts.

import type { Asset } from "../contracts.gen";

/** Room tabs, in demo order (living room first); a category not listed goes under "Other". */
export const ROOM_GROUPS: { id: string; label: string; categories: string[] }[] = [
  {
    id: "living",
    label: "Living room",
    categories: [
      "sofa",
      "sectional",
      "loveseat",
      "armchair",
      "coffee_table",
      "side_table",
      "tv_stand",
      "tv",
      "rug",
      "bookshelf",
      "floor_lamp",
      "table_lamp",
      "ottoman",
      "beanbag",
      "plant",
    ],
  },
  { id: "bedroom", label: "Bedroom", categories: ["bed", "nightstand", "dresser", "wardrobe"] },
  {
    id: "kids",
    label: "Kids & nursery",
    categories: ["crib", "changing_table", "rocking_chair", "toy_storage", "play_mat", "kids_table", "kids_chair", "play_equipment", "canopy_tent"],
  },
  { id: "dining", label: "Dining & kitchen", categories: ["dining_table", "dining_chair", "bar_stool", "kitchen_island", "bench"] },
  { id: "office", label: "Office", categories: ["desk", "office_chair"] },
];
const KNOWN = new Map(ROOM_GROUPS.flatMap((g, gi) => g.categories.map((c, ci) => [c, gi * 100 + ci] as const)));

export function groupOf(category: string): string {
  return ROOM_GROUPS.find((g) => g.categories.includes(category))?.id ?? "other";
}

/** Catalog assets for a tab and a search, ordered by room, then category, then name. */
export function filterAssets(catalog: Record<string, Asset>, tab: string, query: string): Asset[] {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  return Object.values(catalog)
    .filter((a) => tab === "all" || groupOf(a.category) === tab)
    .filter((a) => {
      if (!words.length) return true;
      const hay = [a.name, a.category.replace(/_/g, " "), a.style, a.color, a.material, ...(a.tags ?? []), a.source.product_name]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      return words.every((w) => hay.includes(w));
    })
    .sort(
      (a, b) =>
        (KNOWN.get(a.category) ?? 9999) - (KNOWN.get(b.category) ?? 9999) ||
        a.category.localeCompare(b.category) ||
        a.name.localeCompare(b.name) ||
        a.id.localeCompare(b.id),
    );
}
