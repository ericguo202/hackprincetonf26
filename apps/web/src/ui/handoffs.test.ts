import { describe, expect, it } from "vitest";

import { agentLabel, busyLine, subagentRows } from "./handoffs";

describe("subagentRows", () => {
  it("pairs started and finished events into one row per task", () => {
    const rows = subagentRows(
      [
        { name: "room-scout", status: "started", summary: "Pick the living room" },
        { name: "room-scout", status: "finished", summary: "r2: biggest room with a window" },
        { name: "asset-curator", status: "started", summary: "Movie night picks" },
      ],
      [
        { agent: "room-scout", tool: "get_rooms", status: "finished", summary: "6 rooms" },
        { agent: "asset-curator", tool: "search_assets", status: "finished", summary: "8 matches" },
      ],
    );
    expect(rows.map((r) => [r.label, r.status])).toEqual([
      ["Room scout", "finished"],
      ["Asset curator", "running"],
    ]);
    expect(rows[0].task).toBe("Pick the living room");
    expect(rows[0].result).toBe("r2: biggest room with a window");
    expect(rows[0].lastTool).toBeNull();
    expect(rows[1].lastTool?.tool).toBe("search_assets");
  });

  it("closes parallel auditors in order and keeps errors", () => {
    const rows = subagentRows(
      [
        { name: "listing-auditor", status: "started", summary: "hou_1" },
        { name: "listing-auditor", status: "started", summary: "hou_2" },
        { name: "listing-auditor", status: "error", summary: "timeout" },
      ],
      [],
    );
    expect(rows.map((r) => r.status)).toEqual(["error", "running"]);
    expect(busyLine(rows, [])).toBe("Listing auditor working");
  });

  it("summarizes several running auditors for the dock", () => {
    const rows = subagentRows(
      [
        { name: "listing-auditor", status: "started", summary: "a" },
        { name: "listing-auditor", status: "started", summary: "b" },
      ],
      [],
    );
    expect(busyLine(rows, [])).toBe("2 listing auditors working");
    expect(busyLine([], [{ agent: "concierge", tool: "search_listings", status: "finished", summary: "" }])).toBe(
      "search listings",
    );
  });

  it("labels agent names", () => {
    expect(agentLabel("layout-planner")).toBe("Layout planner");
    expect(agentLabel("")).toBe("Subagent");
  });
});
