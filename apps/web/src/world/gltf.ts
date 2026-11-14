// Lane C: the one way pack .glb files are loaded. Worldgen compresses meshes
// with gltf-transform (EXT_meshopt_compression + KHR_mesh_quantization,
// AGENTS.md section 7), so every loader needs a meshopt decoder.
//
// We set three's own decoder (examples/jsm/libs/meshopt_decoder.module.js,
// matched to the installed three version) rather than drei's default, which
// is the copy bundled in three-stdlib. drei runs the extendLoader callback
// first and then installs its own decoder whenever useMeshopt is true, so
// useMeshopt must be false here or ours is overwritten.
//
// KHR_mesh_quantization needs no decoder: the loader reads quantized
// attributes directly. Draco stays on (drei default); its decoder is only
// fetched if a file actually uses KHR_draco_mesh_compression.

import { useGLTF } from "@react-three/drei";
import { MeshoptDecoder } from "three/examples/jsm/libs/meshopt_decoder.module.js";

type Loader = Parameters<NonNullable<Parameters<typeof useGLTF>[3]>>[0];

function withMeshopt(loader: Loader): void {
  // three-stdlib's GLTFLoader types the decoder as its own copy; same interface.
  loader.setMeshoptDecoder(MeshoptDecoder as unknown as Parameters<Loader["setMeshoptDecoder"]>[0]);
}

export function usePackGLTF(url: string) {
  return useGLTF(url, true, false, withMeshopt);
}

/** Drop a file from the loader cache so its meshes and textures can be freed. */
export function clearPackGLTF(url: string): void {
  useGLTF.clear(url);
}
