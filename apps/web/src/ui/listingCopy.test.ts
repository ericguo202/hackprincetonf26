import { describe, expect, it } from "vitest";
import { findingLine, matchLabel, needLine, priceText } from "./listingCopy";

const ev = (criterion: string, passed: boolean | null, value: unknown = null) =>
  ({ criterion, passed, value, source: "x" }) as { criterion: string; passed: boolean; value: unknown; source: string };

const finding = (check: string, listed: unknown, observed: unknown, severity: "info" | "warn" | "conflict" = "warn") =>
  ({ check, severity, listed, observed, evidence: "x" }) as never;

describe("listing card wording", () => {
  it("hides hard criteria the buyer already set, but flags unknown ones", () => {
    expect(needLine(ev("max_price_usd", true, 230000))).toBeNull();
    expect(needLine(ev("max_price_usd", null))).toEqual({ criterion: "max_price_usd", mark: "unknown", text: "Price: not listed" });
  });

  it("writes needs as plain phrases; unknown is never 'no'", () => {
    expect(needLine(ev("single_story", true))?.text).toBe("One story, no stairs");
    expect(needLine(ev("fenced_yard", null))).toMatchObject({ mark: "unknown", text: "Fenced yard: not listed" });
    expect(needLine(ev("no_pool", false))).toMatchObject({ mark: "no", text: "Has a pool" });
  });

  it("marks a nursery across a hallway as partial", () => {
    const near = needLine(ev("small_bedroom_near_primary", true, { graph_distance: 1, credit: 1 }));
    const far = needLine(ev("small_bedroom_near_primary", true, { graph_distance: 2, credit: 0.5 }));
    expect(near).toMatchObject({ mark: "yes", text: "Small bedroom right off the main bedroom" });
    expect(far?.mark).toBe("partial");
  });

  it("turns 6915's area warning into square feet", () => {
    expect(findingLine(finding("area_vs_scan", 122.4, 103.9))).toBe(
      "The listing says 1,318 sq ft, but our scan measures about 1,118 sq ft (15% smaller).",
    );
    expect(findingLine(finding("beds_vs_rooms", 3, 3, "info"))).toBeNull();
    expect(findingLine(finding("stories_vs_scan", 1, 2, "conflict"))).toBe("Listed as 1 story, but our scan shows 2 floors.");
  });

  it("labels and prices", () => {
    expect(matchLabel(0.75)).toBe("Strong match");
    expect(matchLabel(0.562)).toBe("Good match");
    expect(priceText({ listing_id: "a", score: 1, evidence: [ev("max_price_usd", true, 220000)] })).toBe("$220,000");
  });
});
