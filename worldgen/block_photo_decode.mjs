// Decode Vexcel 3D Cities tiles (glTF with KHR_draco_mesh_compression + KHR_texture_basisu) into plain glb
// (raw geometry, JPEG textures) that Python/trimesh can read. Part of worldgen/block_photo.py.
//
//   node worldgen/block_photo_decode.mjs <out_dir> <tile.glb> [<tile.glb> ...]
//
// Inputs are downloaded tiles: they are parsed as data only (glTF JSON + binary buffers), nothing in them runs.
// Needs `npm i` in worldgen/ (gltf-transform, draco3dgltf, sharp, three for the Basis transcoder).

import { createRequire } from 'node:module';
import { readFile, mkdir } from 'node:fs/promises';
import { basename, join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { NodeIO } from '@gltf-transform/core';
import { KHRDracoMeshCompression, KHRTextureBasisu } from '@gltf-transform/extensions';
import draco3d from 'draco3dgltf';
import sharp from 'sharp';

const HERE = dirname(fileURLToPath(import.meta.url));
const basisDir = join(HERE, 'node_modules', 'three', 'examples', 'jsm', 'libs', 'basis');
// three ships the Emscripten transcoder as a classic script inside a "type": "module" package, so evaluate it
// as a function body instead of require()-ing it.
const src = await readFile(join(basisDir, 'basis_transcoder.js'), 'utf8');
const BASIS = new Function('require', '__dirname', '__filename', `${src}; return BASIS;`)(
  createRequire(import.meta.url), basisDir, join(basisDir, 'basis_transcoder.js'));
const basis = await BASIS({ wasmBinary: await readFile(join(basisDir, 'basis_transcoder.wasm')) });
basis.initializeBasis();
const RGBA32 = 13; // transcoder_texture_format.cTFRGBA32

function ktx2ToRgba(bytes) {
  const f = new basis.KTX2File(new Uint8Array(bytes));
  try {
    if (!f.isValid()) throw new Error('invalid KTX2');
    const w = f.getWidth(), h = f.getHeight();
    if (!f.startTranscoding()) throw new Error('startTranscoding failed');
    const size = f.getImageTranscodedSizeInBytes(0, 0, 0, RGBA32);
    const dst = new Uint8Array(size);
    if (!f.transcodeImage(dst, 0, 0, 0, RGBA32, 0, -1, -1)) throw new Error('transcodeImage failed');
    return { w, h, data: dst };
  } finally {
    f.close();
    f.delete();
  }
}

const [outDir, ...inputs] = process.argv.slice(2);
if (!outDir || !inputs.length) {
  console.error('usage: node block_photo_decode.mjs <out_dir> <tile.glb> ...');
  process.exit(2);
}
await mkdir(outDir, { recursive: true });
const io = new NodeIO()
  .registerExtensions([KHRDracoMeshCompression, KHRTextureBasisu])
  .registerDependencies({ 'draco3d.decoder': await draco3d.createDecoderModule() });

for (const path of inputs) {
  const doc = await io.read(path);
  for (const tex of doc.getRoot().listTextures()) {
    if (tex.getMimeType() !== 'image/ktx2') continue;
    const { w, h, data } = ktx2ToRgba(tex.getImage());
    const jpg = await sharp(Buffer.from(data), { raw: { width: w, height: h, channels: 4 } })
      .removeAlpha()
      .jpeg({ quality: 92 })
      .toBuffer();
    tex.setImage(new Uint8Array(jpg)).setMimeType('image/jpeg');
  }
  for (const ext of doc.getRoot().listExtensionsUsed()) ext.dispose();
  const out = join(outDir, basename(path).replace(/\.(bin|glb)$/, '') + '.glb');
  await io.write(out, doc);
  console.log(out);
}
