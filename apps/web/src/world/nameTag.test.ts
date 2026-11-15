import { describe, expect, it } from "vitest";

import { labelOpacity } from "./labelFade";
import { NAME_TAG_MAX_CHARS, NAME_TAG_RANGE, nameTagText, showNameTag } from "./nameTag";

describe("nameTagText", () => {
  it("keeps names up to 16 characters, trimmed", () => {
    expect(nameTagText("Alice")).toBe("Alice");
    expect(nameTagText("  Bob  ")).toBe("Bob");
    expect(nameTagText("x".repeat(16))).toBe("x".repeat(16));
  });
  it("cuts longer names to 15 characters plus an ellipsis", () => {
    const t = nameTagText("Bartholomew the Third");
    expect(t).toBe("Bartholomew the…");
    expect([...t]).toHaveLength(NAME_TAG_MAX_CHARS);
  });
  it("counts emoji as one character and never splits them", () => {
    const t = nameTagText("🎯".repeat(20));
    expect([...t]).toHaveLength(16);
    expect(t.startsWith("🎯".repeat(15))).toBe(true);
  });
  it("empty or missing names give no text", () => {
    expect(nameTagText("")).toBe("");
    expect(nameTagText("   ")).toBe("");
    expect(nameTagText(undefined)).toBe("");
    expect(nameTagText(null)).toBe("");
  });
});

describe("showNameTag and its range", () => {
  it("never for myself, never while eliminated, never without a name", () => {
    expect(showNameTag("p2", "p1", true, "Ana")).toBe(true);
    expect(showNameTag("p1", "p1", true, "Me")).toBe(false);
    expect(showNameTag("p2", "p1", false, "Ana")).toBe(false);
    expect(showNameTag("p2", "p1", true, "  ")).toBe(false);
    expect(showNameTag("p2", null, true, "Ana")).toBe(true); // before I have an id
  });
  it("is visible at play distances and gone from 25 m", () => {
    expect(labelOpacity(5, NAME_TAG_RANGE)).toBeCloseTo(1, 6);
    expect(labelOpacity(18, NAME_TAG_RANGE)).toBeCloseTo(1, 6);
    expect(labelOpacity(23, NAME_TAG_RANGE)).toBeGreaterThan(0);
    expect(labelOpacity(25, NAME_TAG_RANGE)).toBe(0);
    expect(labelOpacity(40, NAME_TAG_RANGE)).toBe(0);
  });
});
