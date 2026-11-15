// Headless walk test of the Lane A QA viewer (worldgen/viewer): does a pack load, do the yard spawns
// land on the ground, and can the capsule walk? Lane A testing only, like the viewer itself.
//
// Setup once (playwright-core is not a repo dependency; any scratch folder works):
//   npm i --prefix <scratch> playwright-core@1.52.0
// Run (the viewer must be serving, e.g. `python3 worldgen/viewer/serve.py --port 8767` from your worktree):
//   PW_CORE=<scratch>/node_modules/playwright-core \
//     node worldgen/scripts/preview/viewer_walk.mjs "http://127.0.0.1:8767/?pack=hou_navidad_01/v41&house=hou_236595179" [out_prefix]
//
// For every "yard: outside ... door" spawn and for the first room in the spawn list: spawn, let the capsule
// settle 2.5 s, read its position from the viewer's status line, hold W for 1.5 s, read it again, and (with
// out_prefix) save a screenshot <out_prefix>_<n>.jpg. W moves along the camera's view, so "did not move" can
// be a wall straight ahead (6915's front porch faces the brick end wall): look at the screenshot.
// Fails (exit 1) when the pack does not load, the page throws, or a capsule ends more than 3 m below where
// it spawned (it fell through the ground).
//
// Use a private browser like this one, not a shared one: the shared Playwright MCP browser once received
// another session's key presses and switched the viewer into walk mode on its own.
// CHROMIUM overrides the browser binary; the default is Playwright 1.52's cached Chromium (build 1169).
import { pathToFileURL } from "node:url";
import { existsSync } from "node:fs";
import { join } from "node:path";
import { homedir } from "node:os";

const [url, outPrefix] = process.argv.slice(2);
if (!url) {
  console.error("usage: PW_CORE=<path to playwright-core> node viewer_walk.mjs <viewer url> [out_prefix]");
  process.exit(2);
}
const core = process.env.PW_CORE ? pathToFileURL(join(process.env.PW_CORE, "index.mjs")).href : "playwright-core";
const { chromium } = await import(core);
const cached = join(homedir(), "Library/Caches/ms-playwright/chromium-1169/chrome-mac/Chromium.app/Contents/MacOS/Chromium");
const executablePath = process.env.CHROMIUM ?? (existsSync(cached) ? cached : undefined);
const browser = await chromium.launch(executablePath ? { executablePath } : { channel: "chrome" });
const page = await browser.newPage({ viewport: { width: 1400, height: 800 } });
const problems = [];
page.on("pageerror", (e) => problems.push(`page error: ${e.message}`));
page.on("console", (m) => {
  if (m.type() === "error" && !/favicon/.test(m.location()?.url ?? "")) problems.push(`console: ${m.text()}`);
});

let failed = false;
try {
  await page.goto(url);
  await page.waitForFunction(() => /loaded/.test(document.getElementById("status").textContent), null, { timeout: 180000 });
} catch (e) {
  console.error(`viewer did not load the pack: ${e.message}`);
  await browser.close();
  process.exit(1);
}
const status = await page.evaluate(() => document.getElementById("status").textContent);
console.log(`status: ${status}`);
const capsule = async () => {
  const text = await page.evaluate(() =>
    ([...document.querySelectorAll("div,span")].find((e) => /capsule \(/.test(e.textContent) && e.children.length === 0) ?? {}).textContent ?? "");
  const m = text.match(/capsule \(([-\d.]+), ([-\d.]+), ([-\d.]+)\)(.*)$/);
  return m ? { x: +m[1], y: +m[2], z: +m[3], where: m[4].trim() } : null;
};
const options = await page.evaluate(() => [...document.getElementById("spawn").options].map((o) => [o.value, o.text]));
const yard = options.filter(([v]) => v.startsWith("yard:"));
console.log(`yard spawns: ${yard.length}${yard.length ? "" : " (pack has no scanned lot, or the viewer predates lane-a/outdoor)"}`);
const fmt = (p) => (p ? `(${p.x.toFixed(2)}, ${p.y.toFixed(2)}, ${p.z.toFixed(2)}) ${p.where}` : "no capsule readout");
let n = 0;
for (const [value, text] of [...yard, options[0]].filter(Boolean)) {
  await page.selectOption("#spawn", value);
  await page.click("#spawn-btn");
  await page.waitForTimeout(2500);
  const a = await capsule();
  await page.keyboard.down("w");
  await page.waitForTimeout(1500);
  await page.keyboard.up("w");
  await page.waitForTimeout(800);
  const b = await capsule();
  const moved = a && b ? Math.hypot(b.x - a.x, b.z - a.z) : NaN;
  const fell = a && b && b.y < a.y - 3;
  failed ||= Boolean(fell);
  console.log(`${text}\n  spawn ${fmt(a)}\n  after W ${fmt(b)}  moved ${moved.toFixed(2)} m${fell ? "  FELL" : ""}`);
  if (outPrefix) await page.screenshot({ path: `${outPrefix}_${n}.jpg`, type: "jpeg", quality: 85 });
  n += 1;
  await page.click("#mode"); // back to orbit for the next spawn
  await page.waitForTimeout(300);
}
for (const p of problems) console.log(p);
failed ||= problems.some((p) => p.startsWith("page error"));
await browser.close();
process.exit(failed ? 1 : 0);
