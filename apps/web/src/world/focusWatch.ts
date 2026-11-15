// Lane C: when to act on the agent's focus_listing (HANDOFF lane-b 16:45,
// lane-d 21:58 item 2). Pure; tested in focusWatch.test.ts. FocusWatcher.tsx
// does the move.
//
// Only a CHANGE of my focus entry moves the player. The first value seen in a
// session is a baseline, never a jump: that covers the initial sync after
// mount and the full state a reconnect brings back, so rejoining does not
// yank anyone to a house. A "session" is store.connected plus me.id (net/game.ts
// sets me.id = room.sessionId and connected = true on join, connected = false
// on leave; a reconnect gets a new sessionId). The dev solo toggle uses a fixed
// session key, so its baseline is the first value after mount.

/** Session key while connected (or the dev solo key), else null. */
export type FocusInput = { session: string | null; focus: string | null };

export type FocusWatchState = { session: string | null; listing: string | null };

export const FOCUS_WATCH_START: FocusWatchState = { session: null, listing: null };

/** The fixed session key the VITE_SOLO dev toggle runs under. */
export const DEV_SOLO_SESSION = "dev-solo";

/**
 * Next watcher state, and the listing to go to if this input is a change to a
 * house on this block (`known`). Null, unknown and repeated values never move.
 */
export function stepFocusWatch(
  state: FocusWatchState,
  input: FocusInput,
  known: (listingId: string) => boolean,
): { state: FocusWatchState; goTo: string | null } {
  // Disconnected: forget everything, so the next session starts a new baseline.
  if (input.session === null) return { state: FOCUS_WATCH_START, goTo: null };
  const next: FocusWatchState = { session: input.session, listing: input.focus };
  // First value of this session: baseline only.
  if (input.session !== state.session) return { state: next, goTo: null };
  if (input.focus === state.listing || input.focus === null || !known(input.focus)) {
    return { state: next, goTo: null };
  }
  return { state: next, goTo: input.focus };
}
