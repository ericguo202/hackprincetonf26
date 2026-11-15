// Copies of the furniture library that the Matterport Showcase player can load.
//
// Our published furniture .glb files require EXT_meshopt_compression. The
// Showcase bundle's glTF loader knows the extension but has no meshopt decoder,
// so those files fail to load without an error. This writes each file again
// without meshopt (about 30% larger); every other extension stays.
//
// Usage: node worldgen/scripts/tour_furniture.mjs <in_dir> <out_dir>
// Example: node worldgen/scripts/tour_furniture.mjs \
//   apps/web/public/packs/furniture/v3 apps/web/public/matterport/furniture/v3
// Skips files already written and newer than their source.

import { mkdirSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

import { NodeIO } from "@gltf-transform/core";
import { ALL_EXTENSIONS } from "@gltf-transform/extensions";
import { MeshoptDecoder } from "meshoptimizer";

const [inDir, outDir] = process.argv.slice(2);
if (!inDir || !outDir) {
  console.error("usage: tour_furniture.mjs <in_dir> <out_dir>");
  process.exit(2);
}

await MeshoptDecoder.ready;
const io = new NodeIO().registerExtensions(ALL_EXTENSIONS).registerDependencies({ "meshopt.decoder": MeshoptDecoder });

mkdirSync(outDir, { recursive: true });
let written = 0;
let skipped = 0;
for (const name of readdirSync(inDir).sort()) {
  if (!name.endsWith(".glb")) continue;
  const src = join(inDir, name);
  const dst = join(outDir, name);
  try {
    if (statSync(dst).mtimeMs >= statSync(src).mtimeMs) {
      skipped++;
      continue;
    }
  } catch {
    // not written yet
  }
  const doc = await io.read(src);
  for (const ext of doc.getRoot().listExtensionsUsed()) {
    if (ext.extensionName === "EXT_meshopt_compression") ext.dispose();
  }
  await io.write(dst, doc);
  written++;
}
console.log(`tour furniture: ${written} written, ${skipped} up to date -> ${outDir}`);
