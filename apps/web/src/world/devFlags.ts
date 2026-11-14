// Lane C: dev switches, dev builds only. A production build (`vite build`)
// replaces import.meta.env.DEV with false, so everything behind these is dead
// code there and dropped from the bundle, even if the variable is set on the
// host. Checked by building with them set (docs/lane-c-status.md section 14).
// Written without `?.` so Vite can replace them statically.
//
// VITE_DEV_REMOTES has the same gate, in fakeRemotes.ts.

/** VITE_DEV_PLAN=1: furnish the current house from the fixture plan (DevPlan.tsx). */
export const DEV_PLAN_ON: boolean = import.meta.env.DEV && Boolean(import.meta.env.VITE_DEV_PLAN);

/** VITE_START_ROOM=<room_id>: start inside that room instead of on the block (pack.devStart). */
export const START_ROOM: string | undefined = import.meta.env.DEV
  ? (import.meta.env.VITE_START_ROOM as string | undefined)
  : undefined;
