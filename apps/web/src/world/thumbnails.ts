// Lane C: catalog thumbnails for the manual furnishing panel. The furniture
// catalog has an optional `thumb` image (contracts asset.schema.json), but no
// published furniture version ships one yet, so when it is missing we render
// the asset's own .glb once, in the browser:
//
//   - one shared offscreen WebGLRenderer (a second GL context, created on the
//     first request, never torn down), not one canvas per tile
//   - at most CONCURRENCY loads at a time; tiles ask only when scrolled into
//     view (ui/FurnishPanel), so opening the panel does not fetch 30 MB
//   - a 3/4 view from the front-right (assets face +Z), framed on the bounds
//   - cached per URL for the session as a PNG data URL; the meshes are freed
//     right after (this loader is separate from drei's useGLTF cache)
//
// A load or render failure resolves to null and the tile keeps its plain
// placeholder.

import {
  Box3,
  DirectionalLight,
  HemisphereLight,
  type Material,
  type Mesh,
  type Object3D,
  PerspectiveCamera,
  Scene,
  type Texture,
  Vector3,
  WebGLRenderer,
} from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { MeshoptDecoder } from "three/examples/jsm/libs/meshopt_decoder.module.js";

export const THUMB_PX = 192;
const CONCURRENCY = 2;
const FOV_DEG = 28;
/** From the front-right and a little above: front is +Z, right is +X. */
const VIEW_DIR = new Vector3(0.75, 0.55, 1).normalize();

let renderer: WebGLRenderer | null = null;
let scene: Scene | null = null;
let camera: PerspectiveCamera | null = null;
let loader: GLTFLoader | null = null;

const cache = new Map<string, Promise<string | null>>();
const queue: (() => void)[] = [];
let running = 0;

function setup() {
  if (renderer) return;
  renderer = new WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true });
  renderer.setPixelRatio(1);
  renderer.setSize(THUMB_PX, THUMB_PX, false);
  renderer.setClearColor(0x000000, 0);
  scene = new Scene();
  scene.add(new HemisphereLight(0xffffff, 0x8a7f72, 2.2));
  const key = new DirectionalLight(0xffffff, 2.4);
  key.position.set(2, 4, 3);
  scene.add(key);
  const rim = new DirectionalLight(0xdfe8ff, 0.8);
  rim.position.set(-3, 2, -2);
  scene.add(rim);
  camera = new PerspectiveCamera(FOV_DEG, 1, 0.01, 100);
  loader = new GLTFLoader();
  loader.setMeshoptDecoder(MeshoptDecoder);
}

function dispose(root: Object3D) {
  root.traverse((o) => {
    const mesh = o as Mesh;
    if (!mesh.isMesh) return;
    mesh.geometry?.dispose();
    const mats = (Array.isArray(mesh.material) ? mesh.material : [mesh.material]) as Material[];
    for (const m of mats) {
      for (const v of Object.values(m)) if ((v as Texture | null)?.isTexture) (v as Texture).dispose();
      m.dispose();
    }
  });
}

async function render(url: string): Promise<string | null> {
  setup();
  const gltf = await loader!.loadAsync(url);
  const model = gltf.scene;
  try {
    const box = new Box3().setFromObject(model);
    if (box.isEmpty()) return null;
    const centre = box.getCenter(new Vector3());
    const radius = box.getSize(new Vector3()).length() / 2;
    const dist = radius / Math.sin(((FOV_DEG / 2) * Math.PI) / 180);
    camera!.position.copy(centre).addScaledVector(VIEW_DIR, dist * 1.02);
    camera!.near = dist / 50;
    camera!.far = dist * 4;
    camera!.updateProjectionMatrix();
    camera!.lookAt(centre);
    scene!.add(model);
    renderer!.render(scene!, camera!);
    scene!.remove(model);
    return renderer!.domElement.toDataURL("image/png");
  } finally {
    dispose(model);
  }
}

function pump() {
  while (running < CONCURRENCY && queue.length) {
    running++;
    queue.shift()!();
  }
}

/** A PNG data URL of the model at `url`, or null if it cannot be rendered. Cached per URL. */
export function thumbnailFor(url: string): Promise<string | null> {
  let p = cache.get(url);
  if (!p) {
    p = new Promise<string | null>((resolve) => {
      queue.push(() => {
        render(url)
          .catch((e: unknown) => {
            console.warn(`[thumb] ${url}: ${(e as Error)?.message ?? e}`);
            return null;
          })
          .then((r) => {
            running--;
            resolve(r);
            pump();
          });
      });
      pump();
    });
    cache.set(url, p);
  }
  return p;
}

/** Already rendered (or failed) for this URL? Lets a remounted tile skip its placeholder. */
export function cachedThumbnail(url: string): Promise<string | null> | undefined {
  return cache.get(url);
}
