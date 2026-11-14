import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { useStore } from "./store";
import { applyAgentEvent } from "./net/agent";

describe("store", () => {
  it("merges local player updates", () => {
    useStore.getState().setMe({ zone: "hou_000123", roomId: "hou_000123.r1" });
    const me = useStore.getState().me;
    expect(me.zone).toBe("hou_000123");
    expect(me.roomId).toBe("hou_000123.r1");
    expect(me.anim).toBe("idle");
  });

  it("applies agent events", () => {
    useStore.getState().set({ messages: [], todos: [], waiting: false });
    applyAgentEvent("agent.todos", { todos: [{ content: "Plan", status: "in_progress" }] });
    applyAgentEvent("agent.message", { text: "Hi ", final: false });
    applyAgentEvent("agent.message", { text: "there", final: true });
    const s = useStore.getState();
    expect(s.todos).toHaveLength(1);
    expect(s.waiting).toBe(false);
    expect(s.messages.at(-1)).toEqual({ role: "agent", text: "Hi there", final: true });
  });

  it("closes a streamed turn on an empty final delta", () => {
    useStore.getState().set({ messages: [], waiting: true });
    applyAgentEvent("agent.message", { text: "Done.", final: false });
    applyAgentEvent("agent.message", { text: "", final: true });
    const s = useStore.getState();
    expect(s.waiting).toBe(false);
    expect(s.messages).toEqual([{ role: "agent", text: "Done.", final: true }]);
  });

  it("replays the transcript fixture", () => {
    useStore.getState().set({ messages: [], todos: [], tools: [], subagents: [], listings: [], audits: {} });
    const path = resolve(__dirname, "../../../contracts/fixtures/agent.session.jsonl");
    for (const line of readFileSync(path, "utf8").split("\n")) {
      if (!line.trim()) continue;
      const event = JSON.parse(line) as { type: string; data: unknown };
      applyAgentEvent(event.type, event.data);
    }
    const s = useStore.getState();
    expect(s.todos.at(-1)?.status).toBe("pending");
    expect(s.listings[0]?.listing_id).toBe("hou_000123");
    expect(s.audits.hou_000123.findings[0].check).toBe("beds_vs_rooms");
    expect(s.messages.some((m) => m.text.includes("Listed as") || m.text.includes("fits"))).toBe(true);
  });
});
