// Lane C: the block's clouds (drei <Clouds>/<Cloud>, BlockSky.tsx). Pure data
// and the puff texture, so the layout is testable without a canvas.
//
// Six soft clouds high up (70 to 110 m) on a ring around the horizon, 200 to
// 250 m from the block centre: past every house (block collision is about
// +-163 m), inside the 600 m sky box and the 1000 m camera far plane.

import type { Vec3 } from "./pack";

export type CloudSpec = {
  seed: number;
  position: Vec3;
  bounds: Vec3;
  volume: number;
  segments: number;
};

export const CLOUD_SEGMENTS = 20;
export const CLOUD_OPACITY = 0.8;
/** drei Cloud speed: a slow drift of the puffs, no travel. */
export const CLOUD_SPEED = 0.05;

/** [azimuth deg (0 = +Z, counterclockwise from above), ring radius m, height m]. */
const RING: Array<[number, number, number]> = [
  [10, 230, 90],
  [70, 210, 75],
  [135, 245, 105],
  [190, 220, 85],
  [250, 240, 110],
  [310, 205, 80],
];

export const BLOCK_CLOUDS: CloudSpec[] = RING.map(([az, r, h], i) => {
  const a = (az * Math.PI) / 180;
  return {
    seed: i + 1,
    position: [Math.sin(a) * r, h, Math.cos(a) * r],
    // Wide and flat: cumulus seen from below.
    bounds: [42, 8, 18],
    volume: 22,
    segments: CLOUD_SEGMENTS,
  };
});

/**
 * A soft white puff (radial gradient) as a data: URL, drawn once on a small
 * canvas. Stands in for drei Cloud's default texture, which it would fetch
 * from a CDN. Null where there is no 2D canvas (tests).
 */
let puff: string | null | undefined;
export function cloudPuffTexture(): string | null {
  if (puff !== undefined) return puff;
  const c = typeof document !== "undefined" ? document.createElement("canvas") : null;
  const g = c?.getContext("2d");
  if (!c || !g) return (puff = null);
  c.width = c.height = 128;
  const grad = g.createRadialGradient(64, 64, 0, 64, 64, 64);
  grad.addColorStop(0, "rgba(255,255,255,1)");
  grad.addColorStop(0.45, "rgba(255,255,255,0.75)");
  grad.addColorStop(1, "rgba(255,255,255,0)");
  g.fillStyle = grad;
  g.fillRect(0, 0, 128, 128);
  return (puff = c.toDataURL("image/png"));
}
