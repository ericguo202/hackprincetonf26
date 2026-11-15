// Lane C: helpers for TV screens (furniture v3 team TVs, tv_team_stand and
// tv_team_wall). The screen is the mesh whose material is named "tv_screen";
// with no video it keeps the file's glossy black glass (off). The component
// is ScreenVideo in TvScreen.tsx.
import * as THREE from "three";

export const SCREEN_MATERIAL = "tv_screen";

/** VITE_TV_VIDEO_URL: muted looping clip, same-origin or CORS-enabled. Unset: TVs stay off. */
export const TV_VIDEO_URL: string | undefined = import.meta.env.VITE_TV_VIDEO_URL || undefined;

/** The first mesh in `root` whose material is named "tv_screen", if any. */
export function findScreen(root: THREE.Object3D): THREE.Mesh | undefined {
  let found: THREE.Mesh | undefined;
  root.traverse((o) => {
    const m = o as THREE.Mesh;
    if (found || !m.isMesh) return;
    const mats = Array.isArray(m.material) ? m.material : [m.material];
    if (mats.some((mat) => mat?.name === SCREEN_MATERIAL)) found = m;
  });
  return found;
}

/**
 * Put `tex` on the screen as an unlit picture (reads as a lit screen in any
 * room lighting). Returns the undo: the file's own "off" material comes back.
 */
export function showVideo(mesh: THREE.Mesh, tex: THREE.VideoTexture): () => void {
  // glTF UVs have a top-left origin; without this the video plays upside down.
  tex.flipY = false;
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.needsUpdate = true;
  const off = mesh.material;
  const on = new THREE.MeshBasicMaterial({ map: tex, toneMapped: false });
  mesh.material = on;
  return () => {
    on.dispose();
    mesh.material = off;
  };
}
