import { describe, expect, it, vi } from "vitest";
import { useStore } from "./store";
import { applyAgentEvent, fetchTrace } from "./net/agent";

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

  it("attaches the turn's LangSmith link to the latest agent reply", async () => {
    const url = "https://smith.langchain.com/o/t/projects/p/p/r/run?poll=true";
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ url }))));
    useStore.getState().set({
      messages: [
        { role: "agent", text: "Earlier", final: true },
        { role: "agent", text: "Placed a sofa.", final: true },
      ],
    });
    await fetchTrace("s1");
    const [earlier, latest] = useStore.getState().messages;
    expect(earlier.trace).toBeUndefined();
    expect(latest.trace).toBe(url);
    vi.unstubAllGlobals();
  });
});
