// Lane C: scan-position walls (docs/scan-position-walls.md, Lane A HANDOFF
// 06:55 / 06:58). interior.glb may carry node groups backdrop_s<kk>_<page>:
// one set of photo walls around the lot per Matterport scan position, each
// node with its scan position in extras -> userData.sweep (house-local, Y up).
// All sets overlap, so only the set nearest the local camera is drawn.
// backdrop_<n> nodes (ground strip, sky dome) are not touched. A house without
// backdrop_s nodes (6915, every pack before v7) has no sets: nothing changes.

import { Mesh, type Material, type Object3D } from "three";
import { create } from "zustand";

import type { Vec3 } from "./pack";

const SCAN_WALL = /^backdrop_s(\d+)_/;
/** Re-pick the nearest set after the camera has moved this far, metres. */
export const SCAN_WALL_RECHECK_M = 0.25;

export type ScanSet = { kk: number; sweep: Vec3; nodes: Object3D[] };

/** The scan position number of a backdrop_s<kk>_<page> node name, else null. */
export function scanSetOf(name: string): number | null {
  const m = SCAN_WALL.exec(name);
  return m ? Number(m[1]) : null;
}

function sweepOf(o: Object3D): Vec3 | null {
  const s = o.userData?.sweep;
  return Array.isArray(s) && s.length === 3 && s.every((v) => typeof v === "number" && Number.isFinite(v))
    ? [s[0], s[1], s[2]]
    : null;
}

/**
 * The scan-wall sets under `root`, grouped by <kk>, sorted by kk. A set's sweep
 * is the first one found on its nodes; a set with no valid sweep is dropped
 * (it would never be the nearest, so it stays hidden).
 */
export function collectScanSets(root: Object3D): ScanSet[] {
  const byKk = new Map<number, { sweep: Vec3 | null; nodes: Object3D[] }>();
  root.traverse((o) => {
    const kk = scanSetOf(o.name);
    if (kk === null) return;
    // GLTFLoader names the node and, for multi-primitive meshes, its children
    // the same: keep the outermost node of each set only.
    if (o.parent && scanSetOf(o.parent.name) === kk) return;
    const set = byKk.get(kk) ?? { sweep: null, nodes: [] };
    set.sweep ??= sweepOf(o);
    set.nodes.push(o);
    byKk.set(kk, set);
  });
  return [...byKk.entries()]
    .filter((e): e is [number, { sweep: Vec3; nodes: Object3D[] }] => e[1].sweep !== null)
    .sort((a, b) => a[0] - b[0])
    .map(([kk, s]) => ({ kk, sweep: s.sweep, nodes: s.nodes }));
}

/** The kk of the set whose sweep is nearest `eye` (plain 3D distance); null with no sets. */
export function nearestScanSet(sets: readonly Pick<ScanSet, "kk" | "sweep">[], eye: Vec3): number | null {
  let best: number | null = null;
  let bestD = Infinity;
  for (const s of sets) {
    const d = Math.hypot(s.sweep[0] - eye[0], s.sweep[1] - eye[1], s.sweep[2] - eye[2]);
    if (d < bestD) {
      bestD = d;
      best = s.kk;
    }
  }
  return best;
}

/** Show the nodes of set `kk`, hide every other set. */
export function showScanSet(sets: readonly ScanSet[], kk: number | null): void {
  for (const s of sets) for (const n of s.nodes) n.visible = s.kk === kk;
}

const noRaycast = () => {};

/**
 * Once per loaded interior: the walls are unlit photos with an alpha cut-out
 * (toneMapped off, alphaTest 0.5, never blended), cast and receive no shadow
 * and ignore three.js raycasts (they have no collider, so Rapier shots and the
 * camera already pass through). Every set starts hidden until the driver picks
 * one, so 15 overlapping sets are never drawn.
 */
export function prepareScanWalls(root: Object3D): ScanSet[] {
  const sets = collectScanSets(root);
  const fix = (m: Material) => {
    m.toneMapped = false;
    m.alphaTest = 0.5;
    m.transparent = false;
    m.needsUpdate = true;
  };
  for (const s of sets)
    for (const n of s.nodes)
      n.traverse((o) => {
        if (!(o instanceof Mesh)) return;
        o.castShadow = false;
        o.receiveShadow = false;
        o.raycast = noRaycast;
        if (Array.isArray(o.material)) o.material.forEach(fix);
        else fix(o.material);
      });
  showScanSet(sets, null);
  return sets;
}

/** The scan-wall sets of the interior on screen; set by HouseScene's interior prepare. */
export const useScanWalls = create<{ sets: ScanSet[]; set: (sets: ScanSet[]) => void }>()((set) => ({
  sets: [],
  set: (sets) => set({ sets }),
}));
