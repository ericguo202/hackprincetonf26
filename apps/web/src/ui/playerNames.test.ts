import { describe, expect, it } from "vitest";

import { ADJECTIVES, ANIMALS, makePlayerName, PLAYER_NAME_MAX } from "./playerNames";

describe("makePlayerName", () => {
  it("is 'Adjective Animal' from the two lists", () => {
    for (let i = 0; i < 200; i++) {
      const n = makePlayerName();
      const [adj, animal, ...rest] = n.split(" ");
      expect(rest).toEqual([]);
      expect(ADJECTIVES as readonly string[]).toContain(adj);
      expect(ANIMALS as readonly string[]).toContain(animal);
    }
  });

  it("every possible name fits in 16 characters, without cutting", () => {
    expect(ADJECTIVES.length).toBe(30);
    expect(ANIMALS.length).toBe(30);
    for (const a of ADJECTIVES) for (const b of ANIMALS) expect(`${a} ${b}`.length).toBeLessThanOrEqual(PLAYER_NAME_MAX);
    expect(new Set(ADJECTIVES).size).toBe(ADJECTIVES.length);
    expect(new Set(ANIMALS).size).toBe(ANIMALS.length);
  });

  it("is deterministic for an injected random, including the edges 0 and 1", () => {
    const seq = (...xs: number[]) => {
      let i = 0;
      return () => xs[i++];
    };
    expect(makePlayerName(seq(0, 0))).toBe(`${ADJECTIVES[0]} ${ANIMALS[0]}`);
    expect(makePlayerName(seq(0.999, 0.5))).toBe(`${ADJECTIVES[29]} ${ANIMALS[15]}`);
    expect(makePlayerName(seq(1, 1))).toBe(`${ADJECTIVES[29]} ${ANIMALS[29]}`); // never out of range
    expect(makePlayerName(seq(0.1, 0.2))).toBe(makePlayerName(seq(0.1, 0.2)));
  });
});
