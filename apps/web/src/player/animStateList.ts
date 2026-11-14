// Lane C: the six wire states as a value, for tests that must cover all of
// them. contracts/game-state.ts exports ANIM_STATES, but importing it as a
// value pulls in @colyseus/schema at runtime; the AnimState[] annotation keeps
// this list honest at typecheck time instead.
import type { AnimState } from "../../../../contracts/game-state";

export const ANIM_STATES_FOR_TEST: AnimState[] = ["idle", "run", "jump", "shoot", "hit", "eliminated"];
