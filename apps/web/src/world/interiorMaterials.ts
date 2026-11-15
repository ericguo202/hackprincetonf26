// Lane C: interior.glb materials are KHR_materials_unlit (GLTFLoader makes
// them MeshBasicMaterial) and their textures are finished photos from the
// Matterport panoramas. The renderer's tone mapping would grade them a second
// time and wash them out (Lane A, HANDOFF 23:40), so interior meshes opt out.
//
// Applied once per loaded interior scene (GlbOrPlaceholder `prepare`), to
// interior.glb (every material) and, since pack v2, to block/render.glb
// (unlit materials only, disableUnlitToneMapping): never to furniture,
// characters or effects. Nothing
// else about the material changes; alphaTest / alphaMode MASK stays, since
// doorways are cut out of the wall textures.

import { Mesh, MeshBasicMaterial, type Material, type Object3D } from "three";

export type ToneMappingOptions = {
  /** Only materials that pass this are switched off; the rest keep tone mapping. Default: all. */
  only?: (m: Material) => boolean;
};

/** Turn tone mapping off on every mesh material under `root` (or those `only` accepts). Returns how many changed. */
export function disableToneMapping(root: Object3D, opts: ToneMappingOptions = {}): number {
  let changed = 0;
  const off = (m: Material) => {
    if (!m.toneMapped || (opts.only && !opts.only(m))) return;
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

const isUnlit = (m: Material) => m instanceof MeshBasicMaterial;

/**
 * block/render.glb (pack v2): the ground and far-zone meshes are
 * KHR_materials_unlit (MeshBasicMaterial) with photo-tinted textures, so they
 * opt out like the interiors. Lit block materials (roofs, walls, trees,
 * fences) keep tone mapping. Lane A, HANDOFF 22:20 note 2.
 */
export function disableUnlitToneMapping(root: Object3D): number {
  return disableToneMapping(root, { only: isUnlit });
}
