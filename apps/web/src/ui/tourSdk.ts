// The few Matterport Showcase SDK (Bundle SDK 26.10.4) calls TourView uses,
// typed by hand from the bundle's sdk.d.ts so the web app does not depend on it.
// Scene.* exists only in the self-hosted bundle, not in the hosted Embed SDK.

import { create } from "zustand";

export type Vec = { x: number; y: number; z: number };

export interface SceneNode {
  addComponent(name: string, inputs?: Record<string, unknown>): unknown;
  readonly position: { set(x: number, y: number, z: number): void };
  readonly quaternion: { setFromAxisAngle(axis: Vec, angle: number): void };
}

export interface SceneObject {
  addNode(): SceneNode;
  start(): void;
  stop(): void;
}

export interface Observable<T> {
  waitUntil(cond: (v: T) => boolean): Promise<T>;
}

export interface ShowcaseSdk {
  App: { state: Observable<{ phase: string }>; Phase: { PLAYING: string } };
  Scene: { createObjects(n: number): Promise<SceneObject[]>; Component: { GLTF_LOADER: string } };
  Sweep: {
    data: { subscribe(o: { onCollectionUpdated(c: Record<string, { position: Vec }>): void }): { cancel(): void } };
    moveTo(id: string, opts?: { transition?: string; transitionTime?: number }): Promise<string>;
    Transition: { FLY: string; INSTANT: string };
  };
  Camera: { lookAt(position: Vec, opts?: { moveToClosestSweep?: boolean; transitionTime?: number }): Promise<void> };
}

export type ShowcaseWindow = Window & { MP_SDK?: { connect(w: Window): Promise<ShowcaseSdk> } };

/** All sweeps of the loaded model (the collection arrives once, then updates). */
export function sweepsOf(sdk: ShowcaseSdk): Promise<{ id: string; position: Vec }[]> {
  return new Promise((resolve) => {
    const sub = sdk.Sweep.data.subscribe({
      onCollectionUpdated(c) {
        resolve(Object.entries(c).map(([id, s]) => ({ id, position: s.position })));
        queueMicrotask(() => sub.cancel());
      },
    });
  });
}

/**
 * Offered on localhost only: the SDK key's allow list has no deployed domain
 * (free keys are localhost-only), and the deployed build has no bundle.
 */
export const TOUR_AVAILABLE: boolean =
  typeof location !== "undefined" && ["localhost", "127.0.0.1"].includes(location.hostname);

/** "View in Matterport" open/closed. Local to this browser; nothing is sent. */
export const useTour = create<{ open: boolean; setOpen: (open: boolean) => void }>((set) => ({
  open: false,
  setOpen: (open) => set({ open }),
}));
