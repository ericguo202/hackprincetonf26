// Lane D: buyer-facing wording for listing cards. Pure functions, no model involved:
// the search evidence and the audit are turned into short plain-English lines.
// Areas are shown in square feet (AGENTS.md section 5). Raw values stay behind "Show the data".
import type { Audit } from "../contracts.gen";
import type { ListingMatch } from "../store";

export type Mark = "yes" | "partial" | "no" | "unknown";
export type NeedLine = { criterion: string; mark: Mark; text: string };
type Evidence = ListingMatch["evidence"][number];
type Finding = Audit["findings"][number];

const SQFT_PER_M2 = 10.7639;
export const sqft = (m2: number) => Math.round(m2 * SQFT_PER_M2);
const n = (x: number) => x.toLocaleString("en-US");

/** Hard criteria the buyer set; a passing one says nothing new, so it is hidden. */
const HARD = new Set(["status", "property_type", "max_price_usd", "max_rent_usd_month", "min_beds", "min_baths"]);
const HARD_UNKNOWN: Record<string, string> = {
  max_price_usd: "Price: not listed",
  max_rent_usd_month: "Rent: not listed",
  min_beds: "Bedrooms: not listed",
  min_baths: "Bathrooms: not listed",
};

/** [met, not met, unknown] per soft need. */
const NEEDS: Record<string, [string, string, string]> = {
  single_story: ["One story, no stairs", "More than one floor", "Number of floors: not listed"],
  no_pool: ["No pool", "Has a pool", "Pool: not listed"],
  fenced_yard: ["Fenced yard", "No fenced yard", "Fenced yard: not listed"],
  garage: ["Garage", "No garage", "Garage: not listed"],
  small_bedroom_near_primary: [
    "Small bedroom right off the main bedroom",
    "No small bedroom near the main bedroom",
    "Bedroom layout: not scanned",
  ],
  elementary_school: ["Elementary school listed", "No elementary school listed", "Schools: not listed"],
};

const words = (key: string) => {
  const s = key.replace(/[_-]+/g, " ").trim();
  return s.charAt(0).toUpperCase() + s.slice(1);
};

function graphDistance(value: unknown): number | null {
  if (value && typeof value === "object" && "graph_distance" in value) {
    const d = (value as { graph_distance: unknown }).graph_distance;
    return typeof d === "number" ? d : null;
  }
  return null;
}

/** One line per evidence item the buyer should see, or null to hide it. */
export function needLine(e: Evidence): NeedLine | null {
  const passed = e.passed as boolean | null;
  if (HARD.has(e.criterion)) {
    if (passed === true) return null;
    const text = passed === null ? HARD_UNKNOWN[e.criterion] ?? `${words(e.criterion)}: not listed` : `${words(e.criterion)}: no`;
    return { criterion: e.criterion, mark: passed === null ? "unknown" : "no", text };
  }
  const copy = NEEDS[e.criterion];
  if (e.criterion === "small_bedroom_near_primary" && passed === true) {
    const d = graphDistance(e.value);
    if (d !== null && d > 1) {
      return { criterion: e.criterion, mark: "partial", text: "Small bedroom one room away from the main bedroom" };
    }
  }
  if (!copy) {
    const mark: Mark = passed === null ? "unknown" : passed ? "yes" : "no";
    return { criterion: e.criterion, mark, text: passed === null ? `${words(e.criterion)}: not listed` : words(e.criterion) };
  }
  if (passed === null) return { criterion: e.criterion, mark: "unknown", text: copy[2] };
  return { criterion: e.criterion, mark: passed ? "yes" : "no", text: passed ? copy[0] : copy[1] };
}

const count = (x: number, one: string, many: string) => `${x} ${x === 1 ? one : many}`;

/** A plain sentence for a warn or conflict finding; null for info (checked and consistent). */
export function findingLine(f: Finding): string | null {
  if (f.severity === "info") return null;
  const listed = f.listed as unknown;
  const seen = f.observed as unknown;
  const nums = typeof listed === "number" && typeof seen === "number";
  if (f.check === "area_vs_scan" && nums && listed) {
    const pct = Math.round(100 * Math.abs(seen / listed - 1));
    return `The listing says ${n(sqft(listed))} sq ft, but our scan measures about ${n(sqft(seen))} sq ft (${pct}% ${seen < listed ? "smaller" : "larger"}).`;
  }
  if (f.check === "stories_vs_scan" && nums) {
    return `Listed as ${count(listed, "story", "stories")}, but our scan shows ${count(seen, "floor", "floors")}.`;
  }
  if (f.check === "beds_vs_rooms" && nums) return `Listed with ${count(listed, "bedroom", "bedrooms")}, but our scan shows ${seen}.`;
  if (f.check === "baths_vs_rooms" && nums) return `Listed with ${count(listed, "bathroom", "bathrooms")}, but our scan shows ${seen}.`;
  if (f.check === "pool_vs_vexcel" && typeof listed === "boolean" && typeof seen === "boolean") {
    return listed ? "The listing says it has a pool, but aerial imagery shows none." : "The listing says no pool, but aerial imagery shows one.";
  }
  return `The listing and our scan disagree on ${words(f.check).toLowerCase()}.`;
}

export function matchLabel(score: number): string {
  if (score >= 0.75) return "Strong match";
  if (score >= 0.5) return "Good match";
  return "Partial match";
}

/** Listed price from the search evidence (present when the buyer gave a budget). */
export function priceText(m: ListingMatch): string | null {
  const e = m.evidence.find((x) => x.criterion === "max_price_usd" || x.criterion === "max_rent_usd_month");
  if (typeof e?.value !== "number") return null;
  return e.criterion === "max_rent_usd_month" ? `$${n(e.value)}/month` : `$${n(e.value)}`;
}
