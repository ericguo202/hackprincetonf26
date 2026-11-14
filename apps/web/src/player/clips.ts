// Lane C: map the six animation states to the character pack's exact clip names.
// Fill this in from the chosen Quaternius pack BEFORE writing animation code (AGENTS.md section 11).
import type { AnimState } from "../../../../contracts/game-state";

export const CLIPS: Record<AnimState, string> = {
  idle: "TODO",
  run: "TODO",
  jump: "TODO",
  shoot: "TODO",
  hit: "TODO",
  eliminated: "TODO",
};

export const CROSSFADE_S = 0.2;
