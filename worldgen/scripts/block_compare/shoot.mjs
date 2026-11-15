// Block comparison tooling (Lane A QA only; not the game, never deployed). See docs/block-pipeline-handoff.md.
//
// The page, cameras and this script live in git (worldgen/scripts/block_compare/); the models and shots they
// show live in worldgen/out/block_compare/<approach>/{render.glb, shots/} (gitignored, per machine).
//
//   cd worldgen && npm i                                   # once: playwright + gltf-transform + three
//   node scripts/block_compare/shoot.mjs --serve           # http://localhost:8765/compare.html?a=procedural,combined
//   node scripts/block_compare/shoot.mjs combined          # every camera in cameras.json -> combined/shots/<id>.png
//   node scripts/block_compare/shoot.mjs combined 03_street_long_north 13_drone_shared_walk
//
// Shots render <approach>/render.glb with compare.html?shoot=<approach>&cam=<id> in headless Chrome (three.js
// r169 from a CDN, so it needs network; same sky, street-level-only fog, hemisphere + sun light, ACES tone
// mapping for lit materials, tone mapping off for unlit photo materials) at cameras.json's size. Every
// approach gets identical lighting, so differences come from the models only.

import { createServer } from 'node:http';
import { readFile, mkdir } from 'node:fs/promises';
import { extname, join, dirname, normalize, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const DATA = process.env.BLOCK_COMPARE_DIR || resolve(HERE, '../../out/block_compare');
const FROM_GIT = new Set(['compare.html', 'cameras.json']); // served from HERE; everything else from DATA

const args = process.argv.slice(2);
const serve = args[0] === '--serve';
const [approach, ...only] = serve ? [] : args;
if (!serve && !approach) {
  console.error('usage: node shoot.mjs --serve [port] | node shoot.mjs <approach> [camera_id ...]');
  process.exit(2);
}

const TYPES = {
  '.html': 'text/html', '.json': 'application/json', '.glb': 'model/gltf-binary', '.png': 'image/png',
  '.js': 'text/javascript', '.mjs': 'text/javascript', '.webp': 'image/webp', '.jpg': 'image/jpeg', '.md': 'text/plain',
};
const server = createServer(async (req, res) => {
  try {
    const p = normalize(decodeURIComponent(new URL(req.url, 'http://x').pathname)).replace(/^([/\\])+/, '');
    if (p.startsWith('..')) throw new Error('bad path');
    const body = await readFile(join(FROM_GIT.has(p) ? HERE : DATA, p));
    res.writeHead(200, { 'content-type': TYPES[extname(p)] || 'application/octet-stream', 'cache-control': 'no-store' }).end(body);
  } catch { res.writeHead(404).end(); }
});

if (serve) {
  const port = Number(args[1] || 8765);
  server.listen(port, '127.0.0.1', () => {
    console.log(`serving ${HERE} (page, cameras) + ${DATA} (models, shots)`);
    console.log(`open http://localhost:${port}/compare.html?a=procedural,combined   (Ctrl+C to stop)`);
  });
} else {
  const { chromium } = await import('playwright');
  const cams = JSON.parse(await readFile(join(HERE, 'cameras.json'), 'utf8'));
  const ids = only.length ? only : cams.cameras.map(c => c.id);
  const [W, H] = cams.size_px;
  await new Promise(r => server.listen(0, '127.0.0.1', r));
  const port = server.address().port;
  const browser = await chromium.launch({ channel: 'chrome', headless: true, args: ['--use-angle=metal', '--enable-gpu', '--ignore-gpu-blocklist'] });
  const page = await browser.newPage({ viewport: { width: W, height: H } });
  page.on('console', m => { if (m.type() === 'error') console.log(`[page error] ${m.text()}`); });
  await mkdir(join(DATA, approach, 'shots'), { recursive: true });
  for (const id of ids) {
    const t0 = Date.now();
    await page.goto(`http://127.0.0.1:${port}/compare.html?shoot=${encodeURIComponent(approach)}&cam=${encodeURIComponent(id)}`);
    await page.waitForFunction(() => window.__ready === true, null, { timeout: 180_000 });
    const out = join(DATA, approach, 'shots', `${id}.png`);
    await page.screenshot({ path: out, clip: { x: 0, y: 0, width: W, height: H } });
    console.log(`${out}  (${((Date.now() - t0) / 1000).toFixed(1)} s)`);
  }
  await browser.close();
  server.close();
}
