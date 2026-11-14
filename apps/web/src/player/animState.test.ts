/// <reference types="node" />
// node --experimental-strip-types apps/web/src/player/animState.test.ts
// or: pnpm vitest run src/player/animState.test.ts

import assert from "node:assert/strict";
import type { AnimState } from "../../../../contracts/game-state";
import {
  ACTION_HOLD_S,
  animChanged,
  toAnimState,
  type LocomotionState,
} from "./animState.ts";

// Runs two ways: plain node prints "N passed"; under vitest (VITEST is set)
// each case registers as a vitest test. vitest is only imported in the latter.
const vitest = process.env.VITEST ? await import("vitest") : null;
let passed = 0;
function test(name: string, fn: () => void) {
  if (vitest) return vitest.test(name, fn);
  fn();
  passed++;
  console.log("  ok  " + name);
}

// Mirrors ANIM_STATES. Imported as a type rather than a value on purpose:
// contracts/game-state.ts pulls in @colyseus/schema at runtime, so a value
// import would make this test need node_modules. The AnimState[] annotation
// still fails typecheck if the contract's states change.
const ANIM_STATES: AnimState[] = [
  "idle",
  "run",
  "jump",
  "shoot",
  "hit",
  "eliminated",
];

const ALL_LOCOMOTION: LocomotionState[] = [
  "IDLE",
  "WALK",
  "RUN",
  "JUMP_START",
  "JUMP_IDLE",
  "JUMP_FALL",
  "JUMP_LAND",
];

test("every ecctrl state maps to a state the contract carries", () => {
  for (const l of ALL_LOCOMOTION) {
    assert.ok(
      ANIM_STATES.includes(toAnimState(l)),
      `${l} produced a state outside ANIM_STATES`,
    );
  }
});

test("standing still is idle", () => {
  assert.equal(toAnimState("IDLE"), "idle");
});

test("walking and running both read as run on the wire", () => {
  assert.equal(toAnimState("WALK"), "run");
  assert.equal(toAnimState("RUN"), "run");
});

test("all four jump phases collapse to jump", () => {
  for (const l of ["JUMP_START", "JUMP_IDLE", "JUMP_FALL", "JUMP_LAND"] as const) {
    assert.equal(toAnimState(l), "jump");
  }
});

test("an action beats locomotion", () => {
  assert.equal(toAnimState("RUN", "shoot"), "shoot");
  assert.equal(toAnimState("JUMP_FALL", "hit"), "hit");
  assert.equal(toAnimState("IDLE", "eliminated"), "eliminated");
});

test("eliminated holds until respawn, one-shots expire", () => {
  assert.equal(ACTION_HOLD_S.eliminated, Infinity);
  assert.ok(ACTION_HOLD_S.shoot > 0 && ACTION_HOLD_S.shoot < 1);
  assert.ok(ACTION_HOLD_S.hit > 0 && ACTION_HOLD_S.hit < 1);
});

test("animChanged only fires on a real change", () => {
  assert.ok(!animChanged("run", "run"));
  assert.ok(animChanged("run", "idle"));
});

if (!vitest) console.log(`\n${passed} passed`);
