import { describe, expect, it } from "vitest";

import { FOCUS_WATCH_START, type FocusInput, type FocusWatchState, stepFocusWatch } from "./focusWatch";

const known = (id: string) => id === "hou_a" || id === "hou_b";

/** Feeds inputs in order; returns the goTo of each step. */
function run(inputs: FocusInput[], start: FocusWatchState = FOCUS_WATCH_START): (string | null)[] {
  let state = start;
  return inputs.map((input) => {
    const r = stepFocusWatch(state, input, known);
    state = r.state;
    return r.goTo;
  });
}

const on = (focus: string | null, session = "s1"): FocusInput => ({ session, focus });
const off: FocusInput = { session: null, focus: null };

describe("stepFocusWatch", () => {
  it("does not move on the first value seen", () => {
    expect(run([on("hou_a")])).toEqual([null]);
    expect(run([on(null)])).toEqual([null]);
  });

  it("does not move when the value repeats", () => {
    expect(run([on("hou_a"), on("hou_a"), on("hou_a")])).toEqual([null, null, null]);
  });

  it("moves on a change to a known house", () => {
    expect(run([on(null), on("hou_a")])).toEqual([null, "hou_a"]);
    expect(run([on("hou_a"), on("hou_b")])).toEqual([null, "hou_b"]);
  });

  it("does not move on a change to null or to a house not on this block", () => {
    expect(run([on("hou_a"), on(null)])).toEqual([null, null]);
    expect(run([on("hou_a"), on("hou_zzz")])).toEqual([null, null]);
  });

  it("moves when focus comes back after being cleared", () => {
    expect(run([on("hou_a"), on(null), on("hou_a")])).toEqual([null, null, "hou_a"]);
  });

  it("treats the first value after a reconnect as a baseline", () => {
    // Same session id after a drop (connected false, then true again).
    expect(run([on(null), on("hou_a"), off, on("hou_b")])).toEqual([null, "hou_a", null, null]);
    // New session id (Colyseus gives a new sessionId on rejoin), no disconnect seen.
    expect(run([on(null), on("hou_a"), on("hou_b", "s2")])).toEqual([null, "hou_a", null]);
    // And a change after that baseline moves again.
    expect(run([on("hou_a"), off, on("hou_b"), on("hou_a")])).toEqual([null, null, null, "hou_a"]);
  });

  it("ignores everything while disconnected", () => {
    expect(run([off, off])).toEqual([null, null]);
  });
});
