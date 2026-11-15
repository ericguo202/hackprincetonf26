// Lane C: turn a loaded collision.glb into one Rapier trimesh. The scene is
// never added to the render tree, so the collision mesh is invisible by
// construction and the render mesh never gets a collider.

import { Matrix4, Mesh, type Object3D } from "three";

export type Trimesh = { vertices: Float32Array; indices: Uint32Array };

export type ExtractTrimeshOptions = {
  skipNodes?: readonly string[];
};

/**
 * Every mesh under `root`, with its transform relative to `root` baked in,
 * merged into one vertex/index buffer pair.
 * Optionally skips nodes whose names match skipNodes.
 */
export function extractTrimesh(root: Object3D, options?: ExtractTrimeshOptions): Trimesh {
  root.updateMatrixWorld(true);
  const toRoot = new Matrix4().copy(root.matrixWorld).invert();

  const verts: number[] = [];
  const idx: number[] = [];
  const m = new Matrix4();

  const skipSet = options?.skipNodes ? new Set(options.skipNodes) : null;

  root.traverse((o) => {
    if (skipSet?.has(o.name)) return;
    if (!(o instanceof Mesh)) return;
    const pos = o.geometry?.attributes?.position;
    if (!pos) return;
    m.multiplyMatrices(toRoot, o.matrixWorld);
    const e = m.elements;
    const base = verts.length / 3;
    for (let i = 0; i < pos.count; i++) {
      const x = pos.getX(i);
      const y = pos.getY(i);
      const z = pos.getZ(i);
      verts.push(
        e[0] * x + e[4] * y + e[8] * z + e[12],
        e[1] * x + e[5] * y + e[9] * z + e[13],
        e[2] * x + e[6] * y + e[10] * z + e[14],
      );
    }
    const index = o.geometry.index;
    if (index) for (let i = 0; i < index.count; i++) idx.push(base + index.getX(i));
    else for (let i = 0; i < pos.count; i++) idx.push(base + i);
  });

  return { vertices: new Float32Array(verts), indices: new Uint32Array(idx) };
}
