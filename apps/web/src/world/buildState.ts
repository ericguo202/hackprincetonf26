// Lane C: manual furnishing ("build mode") UI state, shared by the 3D side
// (BuildController, FurnitureLayer, CameraArm, Blaster) and the DOM side
// (ui/FurnishPanel, ui/Hud). Local to this browser: what reaches other
// players is the furniture_place / furniture_move / furniture_remove message
// (contracts/messages.md v2.6.0), sent through net/game.ts.
//
// Build mode needs no pointer lock (browsers refuse a re-lock for a while
// after Esc, which used to leave the camera frozen). While it is on:
//   - the cursor is free; the ghost follows it (BuildController)
//   - dragging on the scene with either button turns the camera (CameraArm);
//     WASD still walks
//   - a click (pointer moved under DRAG_PX) places, picks up, or puts back
//   - the catalog drawer stays open beside the scene unless collapsed
// What is held is either a catalog asset (a new piece, kept after each click
// so a row of dining chairs is click, click, click) or an existing piece
// being moved (released after one click).

import { create } from "zustand";

export type Held = { kind: "new"; asset_id: string } | { kind: "move"; instance_id: string; asset_id: string };

/** What the ghost would do if clicked now; null while nothing is aimed at. */
export type GhostStatus = { valid: boolean; why: string | null; snapped: "wall" | "surface" | null };

/**
 * A piece hidden locally after this player moved it, until the game's state
 * patch brings it back at its new pose (key = its pose key before the move),
 * or PENDING_HIDE_MS passes (the move was rejected) and a timer clears it.
 */
export type PendingHide = { instance_id: string; key: string };

export const ROTATE_STEP_DEG = 45;
/** A press that moves the pointer less than this is a click; more is a camera drag. */
export const DRAG_PX = 5;
export const FINE_ROTATE_STEP_DEG = 15;
const PENDING_HIDE_MS = 2000;

type BuildState = {
  active: boolean;
  /** Catalog drawer shown (build mode only). */
  catalogOpen: boolean;
  held: Held | null;
  /** Rotation of the held piece, degrees, counterclockwise from above (contracts rot_deg). */
  rotDeg: number;
  /** instance_id of the piece under the crosshair when nothing is held. */
  hover: string | null;
  status: GhostStatus | null;
  pending: PendingHide | null;
  setActive: (on: boolean) => void;
  setCatalogOpen: (open: boolean) => void;
  toggle: () => void;
  /**
   * Hold a catalog asset as a new piece. Keeps the current rotation, so a row
   * comes out aligned. Folds the catalog away: in first person the floor near
   * you is exactly where the drawer sits.
   */
  pick: (assetId: string) => void;
  /** Pick up an existing piece to move it, at its current rotation. */
  pickUp: (piece: { instance_id: string; asset_id: string; rot_deg: number }) => void;
  /** Let go of what is held. putBack: the player put it back (Q, Esc, right click), so the catalog returns. */
  drop: (putBack?: boolean) => void;
  rotate: (deltaDeg: number) => void;
  setHover: (id: string | null) => void;
  setStatus: (s: GhostStatus | null) => void;
  hideUntilMoved: (instanceId: string, key: string) => void;
};

export function normDeg(d: number): number {
  const r = ((d % 360) + 360) % 360;
  return r >= 359.95 ? 0 : r;
}

function sameStatus(a: GhostStatus | null, b: GhostStatus | null): boolean {
  return a === b || (!!a && !!b && a.valid === b.valid && a.why === b.why && a.snapped === b.snapped);
}

let pendingTimer: ReturnType<typeof setTimeout> | undefined;

export const useBuild = create<BuildState>()((set, get) => ({
  active: false,
  catalogOpen: true,
  held: null,
  rotDeg: 0,
  hover: null,
  status: null,
  pending: null,
  setActive: (on) =>
    set(on ? { active: true, catalogOpen: true } : { active: false, held: null, hover: null, status: null }),
  setCatalogOpen: (catalogOpen) => set({ catalogOpen }),
  toggle: () => get().setActive(!get().active),
  pick: (assetId) =>
    set({ active: true, held: { kind: "new", asset_id: assetId }, hover: null, catalogOpen: false }),
  pickUp: (p) =>
    set({
      held: { kind: "move", instance_id: p.instance_id, asset_id: p.asset_id },
      rotDeg: normDeg(p.rot_deg),
      hover: null,
      catalogOpen: false,
    }),
  drop: (putBack = false) => set(putBack ? { held: null, status: null, catalogOpen: true } : { held: null, status: null }),
  rotate: (delta) => set((s) => ({ rotDeg: normDeg(s.rotDeg + delta) })),
  setHover: (id) => {
    if (get().hover !== id) set({ hover: id });
  },
  setStatus: (status) => {
    if (!sameStatus(get().status, status)) set({ status });
  },
  hideUntilMoved: (instanceId, key) => {
    clearTimeout(pendingTimer);
    set({ pending: { instance_id: instanceId, key } });
    pendingTimer = setTimeout(() => set({ pending: null }), PENDING_HIDE_MS);
  },
}));

/** Read live (per frame / per event) without subscribing. */
export function buildActiveNow(): boolean {
  return useBuild.getState().active;
}

export function buildHoldingNow(): boolean {
  const s = useBuild.getState();
  return s.active && s.held !== null;
}

/**
 * Should FurnitureLayer skip this piece locally? The one being carried, and
 * one just moved whose new pose has not arrived yet.
 */
export function hiddenLocally(
  s: Pick<BuildState, "active" | "held" | "pending">,
  instanceId: string,
  poseKey: string,
): boolean {
  if (s.active && s.held?.kind === "move" && s.held.instance_id === instanceId) return true;
  const p = s.pending;
  return !!p && p.instance_id === instanceId && p.key === poseKey;
}
