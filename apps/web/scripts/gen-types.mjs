// Generate TypeScript types from contracts/*.schema.json into src/contracts.gen.ts.
// pnpm gen:types
import { readdir, readFile, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { compile } from "json-schema-to-typescript";

const here = dirname(fileURLToPath(import.meta.url));
const contracts = join(here, "..", "..", "..", "contracts");
const out = join(here, "..", "src", "contracts.gen.ts");

const files = (await readdir(contracts)).filter((f) => f.endsWith(".schema.json")).sort();
const parts = ["/* Generated from contracts/. Do not edit; run `pnpm gen:types`. */\n"];
for (const file of files) {
  const schema = JSON.parse(await readFile(join(contracts, file), "utf8"));
  parts.push(await compile(schema, schema.title, { bannerComment: "", additionalProperties: false }));
}
await writeFile(out, parts.join("\n"));
console.log(`wrote ${out} from ${files.length} schemas`);
