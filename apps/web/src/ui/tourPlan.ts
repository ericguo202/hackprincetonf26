// "View in Matterport" (TourView.tsx): pure helpers. Tested in tourPlan.test.ts.
//
// Frames: the house-local frame (Y up, X = Matterport x, Z = -Matterport y,
// origin = the Matterport model origin; worldgen/build_house.py) is the same
// frame the Showcase SDK uses for sweeps and scene nodes. Checked on
// hou_236595179: every SDK sweep position equals the Model API pano position
// with that axis swap, and a couch placed at a room centroid lands there.
// So placed furniture goes into the tour with its x, y, z and rot_deg as is.

import type { Room } from "../contracts.gen";
import { roomIdAt } from "../world/pack";

export type XYZ = { x: number; y: number; z: number };
type Placed = { asset_id: string; x: number; y: number; z: number };

/** Where the setup script puts the bundle and the tour furniture (public/matterport/). */
export function tourRoot(baseUrl: string): string {
  return `${baseUrl.endsWith("/") ? baseUrl : `${baseUrl}/`}matterport/`;
}

/** The meshopt-free copy of an asset's catalog `file` (furniture/v<N>/<id>.glb). */
export function tourFurnitureUrl(root: string, file: string): string {
  return `${root}${file}`;
}

/** Showcase page for a model, with the SDK key; UI trimmed for a kiosk-like view. */
export function showcaseUrl(root: string, modelId: string, sdkKey: string): string {
  const q = new URLSearchParams({ m: modelId, applicationKey: sdkKey, play: "1", qs: "1", title: "0", brand: "0" });
  return `${root}bundle/showcase.html?${q.toString()}`;
}

/** Height of a sweep's camera over its floor, about 1.5 m; used to find the sweep's floor. */
const CAMERA_HEIGHT_M = 1.4;

/** The room holding most of the furniture (ties: first seen), or null if none is in a room. */
export function furnishedRoom(objects: Placed[], rooms: Room[]): string | null {
  const counts = new Map<string, number>();
  let best: string | null = null;
  for (const o of objects) {
    const id = roomIdAt([o.x, o.y, o.z], rooms);
    if (!id) continue;
    const n = (counts.get(id) ?? 0) + 1;
    counts.set(id, n);
    if (best === null || n > (counts.get(best) ?? 0)) best = id;
  }
  return best;
}

/** Floor-level centre of the furniture, raised to `lookHeight` (what the camera looks at). */
export function furnitureCentre(objects: Placed[], lookHeight = 0.5): XYZ | null {
  if (objects.length === 0) return null;
  let x = 0;
  let y = 0;
  let z = 0;
  for (const o of objects) {
    x += o.x;
    y += o.y;
    z += o.z;
  }
  const n = objects.length;
  return { x: x / n, y: y / n + lookHeight, z: z / n };
}

/**
 * The sweep to view the furniture from: among sweeps inside `roomId`, the one
 * farthest from the furniture's centre, so the whole layout is in view rather
 * than one piece at the camera's feet. Null when no sweep is in that room.
 */
export function viewpointSweep(
  sweeps: { id: string; position: XYZ }[],
  rooms: Room[],
  roomId: string,
  centre: XYZ,
): string | null {
  let best: string | null = null;
  let bestD = -1;
  for (const s of sweeps) {
    const p = s.position;
    if (roomIdAt([p.x, p.y - CAMERA_HEIGHT_M, p.z], rooms) !== roomId) continue;
    const d = (p.x - centre.x) ** 2 + (p.z - centre.z) ** 2;
    if (d > bestD) {
      bestD = d;
      best = s.id;
    }
  }
  return best;
}
