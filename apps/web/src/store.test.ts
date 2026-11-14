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
    applyAgentEvent("agent.todos", { todos: [{ content: "Plan", status: "in_progress" }] });
    applyAgentEvent("agent.message", { text: "Hi ", final: false });
    applyAgentEvent("agent.message", { text: "there", final: true });
    const s = useStore.getState();
    expect(s.todos).toHaveLength(1);
    expect(s.messages.at(-1)).toEqual({ role: "agent", text: "Hi there", final: true });
  });
});
