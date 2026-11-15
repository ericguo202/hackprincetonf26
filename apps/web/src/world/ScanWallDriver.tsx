// Lane C: drives scan-position walls (scanWalls.ts) for the local camera.
// Inside a house zone the scene is in the house-local frame, so the camera's
// world position is the eye in house-local coordinates. Hard switch, no fade.

import { useEffect, useRef } from "react";
import { useFrame } from "@react-three/fiber";
import { Vector3 } from "three";

import { SCAN_WALL_RECHECK_M, nearestScanSet, showScanSet, useScanWalls } from "./scanWalls";

export function ScanWallDriver() {
  const sets = useScanWalls((s) => s.sets);
  const last = useRef<{ x: number; y: number; z: number } | null>(null);
  const active = useRef<number | null>(null);
  const eye = useRef(new Vector3());

  // New interior: pick again on the next frame.
  useEffect(() => {
    last.current = null;
    active.current = null;
  }, [sets]);
  // No clear on unmount: every interior's prepare overwrites the sets (a house
  // without scan walls sets []), and a StrictMode double effect could wipe sets
  // a cached interior has just prepared. Stale sets belong to a detached scene.

  useFrame(({ camera }) => {
    if (sets.length === 0) return;
    const p = camera.getWorldPosition(eye.current);
    const l = last.current;
    if (l && Math.hypot(p.x - l.x, p.y - l.y, p.z - l.z) < SCAN_WALL_RECHECK_M) return;
    last.current = { x: p.x, y: p.y, z: p.z };
    const kk = nearestScanSet(sets, [p.x, p.y, p.z]);
    if (kk === active.current) return;
    active.current = kk;
    showScanSet(sets, kk);
    if (import.meta.env.DEV) console.debug(`[scan-walls] set s${String(kk).padStart(2, "0")}`);
  });
  return null;
}
