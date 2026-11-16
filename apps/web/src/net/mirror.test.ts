import { describe, expect, it } from "vitest";
import { type MirrorPrev, mirrorState, type PlayerJSON, type StateJSON } from "./mirror";

const pj = (id: string, x = 0): PlayerJSON => ({
  id, name: id, zone: "block", x, y: 0, z: 0, yaw: 0, anim: "idle", hp: 3, score: 0, alive: true,
});
const obj = (instance_id: string, x = 1) => ({
  instance_id, asset_id: "sofa_01", x, y: 0, z: 2, rot_deg: 90, reason: "Faces the TV", by_player: "a",
});
const house = (listing_id: string, plan_id: string, objects = [obj("o1"), obj("o2", 3)]) => ({
  listing_id, plan_id, objects,
});
const json = (over: Partial<StateJSON> = {}): StateJSON => ({
  block_id: "hou_navidad_01",
  mode: "explore",
  players: { a: pj("a"), b: pj("b") },
  houses: { h1: house("h1", "p1"), h2: house("h2", "p2") },
  focus: { a: "h1" },
  ...over,
});
const empty: MirrorPrev = { mode: "explore", players: {}, houses: {}, focus: {} };

/** Apply the first JSON onto an empty store, as the first patch would. */
function seed(j = json()): MirrorPrev {
  return { ...empty, ...mirrorState(empty, j) };
}

describe("mirrorState", () => {
  it("maps players and passes houses through on the first patch", () => {
    const s = seed();
    expect(s.players.a.pos).toEqual([0, 0, 0]);
    expect(s.players.a.anim).toBe("idle");
    expect(s.houses.h1).toMatchObject({ listing_id: "h1", plan_id: "p1" });
    expect(s.houses.h1.objects).toHaveLength(2);
    expect(s.focus).toEqual({ a: "h1" });
  });

  it("returns null for an unchanged state", () => {
    const s = seed();
    expect(mirrorState(s, json())).toBeNull();
  });

  it("a player move only replaces that player", () => {
    const s = seed();
    const patch = mirrorState(s, json({ players: { a: pj("a", 0.5), b: pj("b") } }));
    expect(patch).not.toBeNull();
    expect(Object.keys(patch!)).toEqual(["players"]);
    expect(patch!.players).not.toBe(s.players);
    expect(patch!.players!.a).not.toBe(s.players.a);
    expect(patch!.players!.a.pos).toEqual([0.5, 0, 0]);
    expect(patch!.players!.b).toBe(s.players.b);
  });

  it("carries a remote player's weapon, dropping unknown values", () => {
    const s = seed();
    const patch = mirrorState(s, json({ players: { a: { ...pj("a"), weapon: "noodle" }, b: pj("b") } }));
    expect(patch!.players!.a.weapon).toBe("noodle");
    expect(patch!.players!.b).toBe(s.players.b);
    // Unknown to this client: mirrored as absent (blaster), so nothing changed.
    expect(mirrorState(s, json({ players: { a: { ...pj("a"), weapon: "bazooka" as never }, b: pj("b") } }))).toBeNull();
  });

  it("detects a non-position player field change", () => {
    const s = seed();
    const patch = mirrorState(s, json({ players: { a: { ...pj("a"), hp: 2 }, b: pj("b") } }));
    expect(patch!.players!.a.hp).toBe(2);
    expect(patch!.players!.b).toBe(s.players.b);
  });

  it("a plan change only replaces that house", () => {
    const s = seed();
    const patch = mirrorState(s, json({ houses: { h1: house("h1", "p3"), h2: house("h2", "p2") } }));
    expect(Object.keys(patch!)).toEqual(["houses"]);
    expect(patch!.houses!.h1).not.toBe(s.houses.h1);
    expect(patch!.houses!.h1.plan_id).toBe("p3");
    expect(patch!.houses!.h2).toBe(s.houses.h2);
  });

  it("detects an object field change", () => {
    const s = seed();
    for (const changed of [
      { ...obj("o2", 3), rot_deg: 180 },
      { ...obj("o2", 3), x: 3.01 },
      { ...obj("o2", 3), reason: "Moved" },
      { ...obj("o2", 3), by_player: "b" },
    ]) {
      const patch = mirrorState(s, json({ houses: { h1: house("h1", "p1", [obj("o1"), changed]), h2: house("h2", "p2") } }));
      expect(patch!.houses!.h1).not.toBe(s.houses.h1);
      expect(patch!.houses!.h1.objects[1]).toMatchObject(changed);
      expect(patch!.houses!.h2).toBe(s.houses.h2);
    }
  });

  it("detects objects added or removed", () => {
    const s = seed();
    const fewer = mirrorState(s, json({ houses: { h1: house("h1", "p1", [obj("o1")]), h2: house("h2", "p2") } }));
    expect(fewer!.houses!.h1.objects).toHaveLength(1);
    const more = mirrorState(s, json({
      houses: { h1: house("h1", "p1", [obj("o1"), obj("o2", 3), obj("o3")]), h2: house("h2", "p2") },
    }));
    expect(more!.houses!.h1.objects).toHaveLength(3);
  });

  it("detects a house added and removed", () => {
    const s = seed();
    const added = mirrorState(s, json({ houses: { ...json().houses, h3: house("h3", "p9") } }));
    expect(Object.keys(added!.houses!)).toEqual(["h1", "h2", "h3"]);
    expect(added!.houses!.h1).toBe(s.houses.h1);
    const removed = mirrorState(s, json({ houses: { h1: house("h1", "p1") } }));
    expect(Object.keys(removed!.houses!)).toEqual(["h1"]);
    expect(removed!.houses!.h1).toBe(s.houses.h1);
  });

  it("detects focus changes", () => {
    const s = seed();
    expect(mirrorState(s, json({ focus: { a: "h2" } }))).toEqual({ focus: { a: "h2" } });
    expect(mirrorState(s, json({ focus: { a: "h1", b: "h1" } }))).toEqual({ focus: { a: "h1", b: "h1" } });
    expect(mirrorState(s, json({ focus: {} }))).toEqual({ focus: {} });
  });

  it("detects a mode change", () => {
    const s = seed();
    expect(mirrorState(s, json({ mode: "foam" }))).toEqual({ mode: "foam" });
  });

  it("treats missing collections as empty", () => {
    const s = seed();
    const patch = mirrorState(s, { block_id: "x", mode: "explore" } as StateJSON);
    expect(patch).toEqual({ players: {}, houses: {}, focus: {} });
    expect(mirrorState({ ...empty }, { block_id: "x", mode: "explore" } as StateJSON)).toBeNull();
  });
});
