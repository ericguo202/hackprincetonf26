import { beforeEach, describe, expect, it } from "vitest";
import { useStore } from "../store";
import { applyBroadcast, FEED_MAX, FEED_MS, FX_MAX, pruneBroadcasts, TOAST_MAX, TOAST_MS } from "./broadcasts";

const player = (id: string, name: string) => ({
  id, name, zone: "block", pos: [0, 0, 0] as [number, number, number], yaw: 0, anim: "idle" as const,
  hp: 3, score: 0, alive: true,
});

describe("game broadcasts", () => {
  beforeEach(() => {
    useStore.getState().set({
      toasts: [], killFeed: [], fx: [], lastHitOnMe: null, hitsOnMe: 0,
      players: { me: player("me", "Ana"), b: player("b", "Ben") },
    });
    useStore.getState().setMe({ id: "me" });
  });

  it("caps toasts and expires them after TOAST_MS", () => {
    for (let i = 0; i < TOAST_MAX + 2; i++) applyBroadcast("toast", { text: `t${i}` }, 1000 + i);
    const toasts = useStore.getState().toasts;
    expect(toasts).toHaveLength(TOAST_MAX);
    expect(toasts.at(-1)?.text).toBe(`t${TOAST_MAX + 1}`);
    pruneBroadcasts(1000 + TOAST_MS + 2);
    expect(useStore.getState().toasts.map((t) => t.text)).toEqual([`t${TOAST_MAX - 1}`, `t${TOAST_MAX}`, `t${TOAST_MAX + 1}`]);
    pruneBroadcasts(1000 + TOAST_MS + 100);
    expect(useStore.getState().toasts).toEqual([]);
  });

  it("ignores empty toasts", () => {
    applyBroadcast("toast", { text: "" });
    applyBroadcast("toast", {});
    expect(useStore.getState().toasts).toEqual([]);
  });

  it("counts only hits on me and resolves the shooter name", () => {
    applyBroadcast("hit", { shooter: "me", target: "b", hp: 2 }, 5);
    expect(useStore.getState().hitsOnMe).toBe(0);
    applyBroadcast("hit", { shooter: "b", target: "me", hp: 2 }, 6);
    applyBroadcast("hit", { shooter: "b", target: "me", hp: 1 }, 7);
    const s = useStore.getState();
    expect(s.hitsOnMe).toBe(2);
    expect(s.lastHitOnMe).toEqual({ shooter: "b", shooterName: "Ben", hp: 1, at: 7 });
  });

  it("builds a capped kill feed with names, and expires it", () => {
    for (let i = 0; i < FEED_MAX + 1; i++) applyBroadcast("eliminated", { target: "b", by: "me" }, 100);
    applyBroadcast("eliminated", { target: "me", by: "ghost" }, 100);
    const feed = useStore.getState().killFeed;
    expect(feed).toHaveLength(FEED_MAX);
    expect(feed.at(-1)).toMatchObject({ target: "me", targetName: "Ana", by: "ghost", byName: "Someone" });
    pruneBroadcasts(100 + FEED_MS);
    expect(useStore.getState().killFeed).toEqual([]);
  });

  it("keeps the latest fx and drops malformed ones", () => {
    for (let i = 0; i < FX_MAX + 3; i++) applyBroadcast("fx", { kind: "dart", origin: [i, 1, 0], dir: [0, 0, 1] });
    applyBroadcast("fx", { kind: "dart", origin: [0, 1], dir: [0, 0, 1] });
    const fx = useStore.getState().fx;
    expect(fx).toHaveLength(FX_MAX);
    expect(fx.at(-1)?.origin).toEqual([FX_MAX + 2, 1, 0]);
  });
});
