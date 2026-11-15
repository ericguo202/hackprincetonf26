// Lane C: plays a video on a catalog TV's "tv_screen" mesh (helpers in
// tvVideo.ts). Adapted from the team's Tv.tsx so it works on the instance
// FurnitureLayer already loaded and cloned (no second load of the same file).
import { useEffect } from "react";
import { useVideoTexture } from "@react-three/drei";
import type * as THREE from "three";

import { showVideo } from "./tvVideo";

/** Swaps the screen's material for an unlit video while mounted; restores it after. */
export function ScreenVideo({ mesh, src }: { mesh: THREE.Mesh; src: string }) {
  const tex = useVideoTexture(src, { muted: true, loop: true, start: true, crossOrigin: "anonymous" });
  useEffect(() => showVideo(mesh, tex), [mesh, tex]);
  return null;
}
