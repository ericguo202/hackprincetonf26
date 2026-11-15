import { useEffect, useState } from "react";

/** True while the game canvas owns mouse look (clicks do not hit the overlay). */
export function looking(): boolean {
  return document.pointerLockElement instanceof HTMLCanvasElement;
}

export function useLooking(): boolean {
  const [on, setOn] = useState(looking);
  useEffect(() => {
    const sync = () => setOn(looking());
    document.addEventListener("pointerlockchange", sync);
    return () => document.removeEventListener("pointerlockchange", sync);
  }, []);
  return on;
}

export function stopLooking(): void {
  if (looking()) document.exitPointerLock();
}

/** Resume mouse look. Call from a user gesture (click, key) so the browser allows it. */
export function startLooking(): void {
  const canvas = document.querySelector("canvas");
  if (!canvas || looking()) return;
  const p = canvas.requestPointerLock() as Promise<void> | undefined;
  p?.catch(() => {});
}
