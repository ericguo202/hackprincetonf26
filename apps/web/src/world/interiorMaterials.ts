// Lane C: interior.glb materials are KHR_materials_unlit (GLTFLoader makes
// them MeshBasicMaterial) and their textures are finished photos from the
// Matterport panoramas. The renderer's tone mapping would grade them a second
// time and wash them out (Lane A, HANDOFF 23:40), so interior meshes opt out.
//
// Applied once per loaded interior scene (GlbOrPlaceholder `prepare`), only to
// interior.glb: never to furniture, characters, effects or the block. Nothing
// else about the material changes; alphaTest / alphaMode MASK stays, since
// doorways are cut out of the wall textures.

import { Mesh, type Material, type Object3D } from "three";

/** Turn tone mapping off on every mesh material under `root`. Returns how many materials changed. */
export function disableToneMapping(root: Object3D): number {
  let changed = 0;
  const off = (m: Material) => {
    if (!m.toneMapped) return;
    m.toneMapped = false;
    m.needsUpdate = true; // tone mapping is compiled into the shader
    changed++;
  };
  root.traverse((o) => {
    if (!(o instanceof Mesh)) return;
    if (Array.isArray(o.material)) o.material.forEach(off);
    else off(o.material);
  });
  return changed;
}
