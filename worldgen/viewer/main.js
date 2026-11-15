// Lane A pack QA viewer: NOT THE GAME.
// A throwaway test page that lets Lane A eyeball house packs (interior, collision, rooms, doors), walk a
// capsule through them and try furniture placement. The real game client is apps/web (Lanes C and D).
// Agents building the game: do not import, copy, port, reference or deploy anything from worldgen/viewer/.
// See worldgen/viewer/README.md.

import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { MeshoptDecoder } from "three/addons/libs/meshopt_decoder.module.js";
import { CSS2DObject, CSS2DRenderer } from "three/addons/renderers/CSS2DRenderer.js";
import { buildCollider, Walker } from "./walker.js";
import { dedupeOpenings, Furniture, PLACEHOLDERS } from "./furniture.js";
import { labelPoint, pointInPolygon, polygonArea } from "./geom2d.js";

const $ = (id) => document.getElementById(id);
const LABEL_COLORS = {
  living_room: 0xf5a623, kitchen: 0x7ed321, dining_room: 0xbd10e0, bedroom: 0x4a90e2, primary_bedroom: 0x2f6fd6,
  bathroom: 0x50e3c2, hallway: 0x9b9b9b, staircase: 0xd0021b, office: 0xf8e71c, closet: 0x8b572a,
  garage: 0x777777, utility: 0x417505, other: 0xffffff,
};
const DOOR_H = 2.0;

// ------------------------------------------------------------------ scene

const viewport = $("viewport");
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
viewport.appendChild(renderer.domElement);
const labelRenderer = new CSS2DRenderer();
labelRenderer.domElement.className = "label-layer";
viewport.appendChild(labelRenderer.domElement);

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x1b1d22);
scene.add(new THREE.HemisphereLight(0xffffff, 0x404040, 1.8));
const sun = new THREE.DirectionalLight(0xffffff, 1.4);
sun.position.set(6, 12, 4);
scene.add(sun);
const grid = new THREE.GridHelper(60, 60, 0x3a3a3a, 0x262626);
grid.position.y = -0.01;
scene.add(grid, new THREE.AxesHelper(1));

const camera = new THREE.PerspectiveCamera(60, 1, 0.03, 500);
camera.position.set(8, 16, 14);
const orbit = new OrbitControls(camera, renderer.domElement);
orbit.enableDamping = true;

const loader = new GLTFLoader();
loader.setMeshoptDecoder(MeshoptDecoder);

const house = new THREE.Group();
house.name = "qa_house";
scene.add(house);
const layers = {};
for (const k of ["interior", "collision", "rooms", "doors", "labels"]) {
  layers[k] = new THREE.Group();
  layers[k].name = `qa_layer_${k}`;
  house.add(layers[k]);
}

const walker = new Walker(scene);
walker.mesh.visible = false;
walker.onFall = () => spawnAt($("spawn").value);

const state = {
  pack: null, listingId: null, manifest: null, rooms: [], openings: [], doorMeshes: [],
  interior: null, collision: null, ceilings: [], roofs: [], backdrop: [], collider: null,
  mode: "orbit", placing: null, drag: null, camDist: 2.5, pitch: -0.25, spawned: false,
};

const furniture = new Furniture(scene, loader, () => furnitureChanged());

function resize() {
  const w = viewport.clientWidth, h = viewport.clientHeight;
  renderer.setSize(w, h);
  labelRenderer.setSize(w, h);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
}
window.addEventListener("resize", resize);
resize();

function status(msg, isError = false) {
  const el = $("status");
  el.textContent = msg;
  el.className = isError ? "error" : "";
  if (isError) console.error(msg);
}

// ------------------------------------------------------------------ loading

async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${r.status} ${url}`);
  return r.json();
}

function loadGLB(url) {
  return new Promise((resolve) => loader.load(url, (g) => resolve(g.scene), undefined, (e) => {
    console.warn("could not load", url, e);
    resolve(null);
  }));
}

// Contracts v2.3.0: furniture is one shared library at the published root, furniture/v<N>/, and both
// manifest.furniture_catalog and asset `file` are root-relative. serve.py maps /furniture/ to --furniture.
const ROOT_FURNITURE = /^furniture\/v\d+\//;

/** Catalog URL from the manifest; a pre-v2.3 manifest (furniture/catalog.json) gets the newest library. */
function furnitureCatalogUrl(rel) {
  if (ROOT_FURNITURE.test(rel ?? "")) return `/${rel}`;
  const v = state.furnitureVersions?.at(-1);
  if (v != null) {
    console.warn(`manifest furniture_catalog "${rel}" is pre-v2.3; using the shared library furniture/v${v}/`);
    return `/furniture/v${v}/catalog.json`;
  }
  return `/packs/${state.pack}/${rel}`;
}

/** Asset .glb URL: root-relative for v2.3 paths, pack-relative for older catalogs. */
function assetUrl(packBase, file) {
  return ROOT_FURNITURE.test(file) ? `/${file}` : `${packBase}/${file}`;
}

function normalizeCatalog(c) {
  if (Array.isArray(c)) return c;
  if (Array.isArray(c?.assets)) return c.assets;
  if (Array.isArray(c?.items)) return c.items;
  return c?.id ? [c] : [];
}

async function loadPackList() {
  const { packs, packs_root, furniture_root, furniture_versions } = await getJSON("/api/packs");
  state.furnitureVersions = furniture_versions ?? [];
  $("packs-root").textContent = packs_root + (furniture_root ? ` · furniture: ${furniture_root} (v${state.furnitureVersions.join(", v")})` : "");
  const sel = $("pack");
  sel.innerHTML = "";
  for (const p of packs) sel.add(new Option(`${p.pack} (${p.houses.length} houses)`, p.pack));
  if (!packs.length) return status(`no packs under ${packs_root}`, true);
  const want = new URLSearchParams(location.search).get("pack");
  sel.value = packs.some((p) => p.pack === want) ? want : packs[packs.length - 1].pack;
  await choosePack(sel.value);
}

async function choosePack(pack) {
  state.pack = pack;
  state.manifest = await getJSON(`/packs/${pack}/manifest.json`);
  const sel = $("house");
  sel.innerHTML = "";
  for (const h of state.manifest.houses) sel.add(new Option(h.listing_id, h.listing_id));
  const want = new URLSearchParams(location.search).get("house");
  if (state.manifest.houses.some((h) => h.listing_id === want)) sel.value = want;
  if (sel.value) await loadHouse(sel.value);
  else status("manifest lists no houses", true);
}

function clearHouse() {
  for (const g of Object.values(layers)) {
    g.traverse((o) => { if (o.isCSS2DObject) o.element.remove(); });
    g.clear();
  }
  furniture.clear();
  Object.assign(state, { rooms: [], openings: [], doorMeshes: [], interior: null, collision: null, ceilings: [], roofs: [], backdrop: [], collider: null, spawned: false });
  walker.collider = null;
}

async function loadHouse(listingId) {
  clearHouse();
  state.listingId = listingId;
  const url = new URL(location.href);
  url.searchParams.set("pack", state.pack);
  url.searchParams.set("house", listingId);
  history.replaceState(null, "", url);
  const base = `/packs/${state.pack}`;
  const entry = state.manifest.houses.find((h) => h.listing_id === listingId);
  status(`loading ${listingId}…`);
  const t0 = performance.now();
  const catalogUrl = furnitureCatalogUrl(state.manifest.furniture_catalog);
  const [rooms, catalog, interior, collision] = await Promise.all([
    getJSON(`${base}/${entry.rooms}`).catch((e) => (console.warn(e), [])),
    getJSON(catalogUrl).then(normalizeCatalog).catch(() => []),
    loadGLB(`${base}/${entry.interior}`),
    loadGLB(`${base}/${entry.collision}`),
  ]);
  state.rooms = Array.isArray(rooms) ? rooms : rooms.rooms ?? [];

  if (interior) {
    layers.interior.add(interior);
    state.interior = interior;
    interior.traverse((o) => {
      if (/^ceiling/i.test(o.name)) state.ceilings.push(o);
      // Outdoor nodes from worldgen/exterior.py (the scanned lot): the roof and the 45 m backdrop dome
      // would hide the house from an orbit camera, so they get their own toggles.
      if (/^roof/i.test(o.name)) state.roofs.push(o);
      if (/^backdrop/i.test(o.name)) state.backdrop.push(o);
    });
  }
  if (collision) {
    collision.traverse((o) => {
      if (!o.isMesh) return;
      const isFloor = /floor/i.test(o.name) || /floor/i.test(o.parent?.name ?? "");
      o.material = new THREE.MeshBasicMaterial({ color: isFloor ? 0x00d0ff : 0xff3cf0, wireframe: true, transparent: true, opacity: isFloor ? 0.35 : 0.8 });
    });
    layers.collision.add(collision);
    state.collision = collision;
  }
  buildRoomOverlay();
  buildDoorOverlay();
  fillSpawnList();
  furniture.setContext({ rooms: state.rooms, catalog, assetUrl: (file) => assetUrl(base, file) });
  fillPalette(catalog);
  restoreFurniture();
  rebuildCollider();
  frameHouse();
  refreshLayers();
  showStats(base, entry, performance.now() - t0, catalog.length);
  const missing = [!interior && "interior.glb", !collision && "collision.glb", !state.rooms.length && "rooms.json"].filter(Boolean);
  status(missing.length ? `loaded with missing files: ${missing.join(", ")}` : `loaded ${listingId}`, missing.length > 0);
}

// ------------------------------------------------------------------ overlays

function buildRoomOverlay() {
  for (const room of state.rooms) {
    const color = LABEL_COLORS[room.label] ?? 0xffffff;
    const y = room.floor_y ?? 0;
    const shape = new THREE.Shape(room.polygon.map(([x, z]) => new THREE.Vector2(x, z)));
    const geo = new THREE.ShapeGeometry(shape);
    geo.rotateX(Math.PI / 2); // (x, z, 0) -> (x, 0, z)
    const fill = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.18, side: THREE.DoubleSide, depthWrite: false }));
    fill.position.y = y + 0.015;
    fill.userData.room = room;
    fill.userData.floorPick = true;
    const line = new THREE.LineLoop(
      new THREE.BufferGeometry().setFromPoints(room.polygon.map(([x, z]) => new THREE.Vector3(x, y + 0.02, z))),
      new THREE.LineBasicMaterial({ color }),
    );
    layers.rooms.add(fill, line);

    const calc = polygonArea(room.polygon);
    const off = Math.abs(calc - room.area_m2) > Math.max(0.2, 0.03 * room.area_m2);
    const div = document.createElement("div");
    div.className = "room-label";
    div.style.borderColor = `#${color.toString(16).padStart(6, "0")}`;
    div.innerHTML = `<b>${room.label}</b> <span>${room.id.split(".").pop()}</span><br>${room.area_m2} m²`
      + `${off ? ` <em>≠ polygon ${calc.toFixed(1)}</em>` : ""} · conf ${room.label_confidence ?? "?"}`;
    const lbl = new CSS2DObject(div);
    const [lx, lz] = labelPoint(room.polygon);
    lbl.position.set(lx, y + 0.3, lz);
    layers.labels.add(lbl);
  }
}

function buildDoorOverlay() {
  state.openings = dedupeOpenings(state.rooms);
  for (const op of state.openings) {
    const [ax, az] = op.a, [bx, bz] = op.b;
    const len = Math.max(op.width, 0.02);
    const isDoor = op.type === "door";
    const y0 = op.floor_y + (isDoor ? 0 : 0.9);
    const h = isDoor ? DOOR_H : 1.1;
    const mesh = new THREE.Mesh(
      new THREE.BoxGeometry(len, h, 0.08),
      new THREE.MeshBasicMaterial({ transparent: true, opacity: 0.45, depthWrite: false }),
    );
    mesh.position.set((ax + bx) / 2, y0 + h / 2, (az + bz) / 2);
    mesh.rotation.y = Math.atan2(-(bz - az), bx - ax);
    mesh.userData.noCollide = true;
    const div = document.createElement("div");
    div.className = "door-label";
    const lbl = new CSS2DObject(div);
    lbl.position.set(mesh.position.x, y0 + h + 0.15, mesh.position.z);
    layers.doors.add(mesh);
    layers.labels.add(lbl);
    lbl.userData.doorLabel = true;
    state.doorMeshes.push({ op, mesh, div });
  }
  recolorDoors();
}

/** Door colours depend on the capsule size: red = narrower than the capsule plus 10 cm. */
function recolorDoors() {
  const need = walker.radius * 2 + 0.1;
  for (const { op, mesh, div } of state.doorMeshes) {
    const exterior = op.type === "door" && op.to_room == null && op.rooms.length === 1;
    const tight = op.type === "door" && op.width < need;
    const color = op.type === "window" ? 0x3d8bff : tight ? 0xff3355 : exterior ? 0xff9a1f : 0x2ee66b;
    mesh.material.color.setHex(color);
    div.textContent = `${op.type === "window" ? "window" : exterior ? "ext door" : "door"} ${op.width.toFixed(2)} m${tight ? " TIGHT" : ""}`;
    div.style.color = `#${color.toString(16).padStart(6, "0")}`;
  }
}

function refreshLayers() {
  const on = (id) => $(id).checked;
  layers.interior.visible = on("l-interior");
  layers.collision.visible = on("l-collision");
  layers.rooms.visible = on("l-rooms");
  layers.doors.visible = on("l-doors");
  for (const c of state.ceilings) c.visible = on("l-ceilings");
  for (const c of state.roofs) c.visible = on("l-roof");
  for (const c of state.backdrop) c.visible = on("l-backdrop");
  layers.labels.traverse((o) => {
    if (o.isCSS2DObject) o.visible = on("l-labels") && (o.userData.doorLabel ? on("l-doors") : on("l-rooms"));
  });
}
for (const id of ["l-interior", "l-collision", "l-rooms", "l-doors", "l-ceilings", "l-roof", "l-backdrop", "l-labels"]) $(id).addEventListener("change", refreshLayers);

function frameHouse() {
  const box = new THREE.Box3().setFromObject(state.collision ?? state.interior ?? layers.rooms);
  if (box.isEmpty()) return;
  const c = box.getCenter(new THREE.Vector3());
  const r = box.getSize(new THREE.Vector3()).length();
  orbit.target.copy(c);
  camera.position.set(c.x + r * 0.35, c.y + r * 0.9, c.z + r * 0.55);
  orbit.update();
}

// ------------------------------------------------------------------ collider + walking

let colliderTimer = 0;
function rebuildCollider(delay = 0) {
  clearTimeout(colliderTimer);
  colliderTimer = setTimeout(() => {
    const base = state.collision ?? state.interior;
    if (!state.collision && state.interior) status("no collision.glb: walking against the interior mesh", true);
    state.collider = buildCollider([base, furniture.group]);
    walker.collider = state.collider;
  }, delay);
}

function fillSpawnList() {
  const sel = $("spawn");
  sel.innerHTML = "";
  const order = (r) => (r.label === "living_room" ? 0 : r.label === "hallway" ? 1 : 2);
  for (const r of [...state.rooms].sort((a, b) => order(a) - order(b) || b.area_m2 - a.area_m2)) {
    sel.add(new Option(`${r.label} ${r.id.split(".").pop()} (${r.area_m2} m²)`, r.id));
  }
  state.yardSpawns = yardSpawns();
  state.yardSpawns.forEach((p, i) => sel.add(new Option(`yard: outside ${p.label} door (${p.x.toFixed(1)}, ${p.z.toFixed(1)})`, `yard:${i}`)));
}

/** 1.5 m outside each outside door (opening with no room behind), kept only where the collision mesh has
 * scanned yard ground (a `ground` node, from worldgen/exterior.py) within a metre of the floor. */
function yardSpawns() {
  const grounds = [];
  state.collision?.traverse((o) => { if (o.isMesh && /ground/i.test(o.name)) grounds.push(o); });
  if (!grounds.length) return [];
  const out = [];
  const down = new THREE.Raycaster();
  for (const room of state.rooms) {
    const [cx, cz] = labelPoint(room.polygon);
    for (const o of room.openings ?? []) {
      if (o.type !== "door" || o.to_room) continue;
      const mx = (o.a[0] + o.b[0]) / 2, mz = (o.a[1] + o.b[1]) / 2;
      let nx = -(o.b[1] - o.a[1]), nz = o.b[0] - o.a[0];
      const len = Math.hypot(nx, nz) || 1;
      nx /= len; nz /= len;
      if (nx * (mx - cx) + nz * (mz - cz) < 0) { nx = -nx; nz = -nz; } // away from the room
      const x = mx + nx * 1.5, z = mz + nz * 1.5;
      down.set(new THREE.Vector3(x, (room.floor_y ?? 0) + 1.5, z), new THREE.Vector3(0, -1, 0));
      const hit = down.intersectObjects(grounds, false)[0];
      const dup = out.some((q) => Math.hypot(q.x - x, q.z - z) < 0.5); // a door shared by split rooms
      if (hit && !dup && hit.point.y > (room.floor_y ?? 0) - 1.0) out.push({ x, y: hit.point.y, z, label: room.label.replace(/_/g, " ") });
    }
  }
  return out;
}

function spawnAt(roomId) {
  if (String(roomId).startsWith("yard:")) {
    const p = state.yardSpawns?.[Number(roomId.slice(5))];
    if (p) { walker.spawn(p.x, p.y, p.z); state.spawned = true; return; }
  }
  const room = state.rooms.find((r) => r.id === roomId) ?? state.rooms[0];
  if (room) {
    const [x, z] = labelPoint(room.polygon);
    walker.spawn(x, room.floor_y ?? 0, z);
  } else {
    walker.spawn(0, 0, 0);
  }
  state.spawned = true;
}

function setMode(mode) {
  state.mode = mode;
  $("mode").textContent = mode === "walk" ? "Back to orbit (Tab)" : "Walk with capsule (Tab)";
  document.body.classList.toggle("walking", mode === "walk");
  if (mode === "walk") {
    if (!state.spawned) spawnAt($("spawn").value);
    orbit.enabled = false;
    for (const id of ["l-ceilings", "l-roof", "l-backdrop"]) $(id).checked = true;
    cancelPlacing();
  } else {
    if (document.pointerLockElement) document.exitPointerLock();
    orbit.enabled = true;
    orbit.target.copy(walker.position);
    for (const id of ["l-ceilings", "l-roof", "l-backdrop"]) $(id).checked = false;
  }
  refreshLayers();
}
$("mode").addEventListener("click", () => setMode(state.mode === "walk" ? "orbit" : "walk"));
$("spawn-btn").addEventListener("click", () => { spawnAt($("spawn").value); setMode("walk"); });
for (const id of ["cap-r", "cap-h"]) {
  $(id).addEventListener("change", () => {
    walker.setSize(Number($("cap-r").value), Number($("cap-h").value));
    recolorDoors();
  });
}

renderer.domElement.addEventListener("click", () => {
  if (state.mode === "walk" && !document.pointerLockElement) renderer.domElement.requestPointerLock();
});
document.addEventListener("mousemove", (e) => {
  if (state.mode !== "walk" || document.pointerLockElement !== renderer.domElement) return;
  walker.yaw -= e.movementX * 0.0025;
  state.pitch = Math.max(-1.35, Math.min(1.2, state.pitch - e.movementY * 0.0025));
});
const MIN_THIRD = 0.6; // closest third-person distance; scrolling in past it switches to first person
renderer.domElement.addEventListener("wheel", (e) => {
  if (state.mode !== "walk") return;
  if ($("fp").checked) {
    if (e.deltaY > 0) { $("fp").checked = false; state.camDist = MIN_THIRD; }
    return;
  }
  const next = state.camDist + e.deltaY * 0.003;
  if (next < MIN_THIRD) $("fp").checked = true;
  else state.camDist = Math.min(8, next);
}, { passive: true });

/** First person on/off. From orbit mode, turning it on also starts walking. */
function toggleFirstPerson(on = !$("fp").checked) {
  $("fp").checked = on;
  if (on && state.mode !== "walk") setMode("walk");
}
$("fp").addEventListener("change", (e) => toggleFirstPerson(e.target.checked));

const _dir = new THREE.Vector3(), _ray = new THREE.Ray(), _eye = new THREE.Vector3();
function updateWalkCamera() {
  _eye.copy(walker.eye);
  const cp = Math.cos(state.pitch);
  _dir.set(-Math.sin(walker.yaw) * cp, Math.sin(state.pitch), -Math.cos(walker.yaw) * cp);
  const firstPerson = $("fp").checked;
  walker.mesh.visible = !firstPerson;
  if (firstPerson) {
    camera.position.copy(_eye);
  } else {
    let dist = state.camDist;
    _ray.set(_eye, _dir.clone().negate());
    const hit = state.collider?.boundsTree.raycastFirst(_ray, THREE.DoubleSide);
    if (hit) dist = Math.max(0.2, Math.min(dist, hit.distance - 0.15));
    camera.position.copy(_eye).addScaledVector(_dir, -dist);
  }
  camera.lookAt(_eye.clone().add(_dir));
}

// ------------------------------------------------------------------ furniture placement

function fillPalette(catalog) {
  const sel = $("asset");
  sel.innerHTML = "";
  if (catalog.length) {
    const g = document.createElement("optgroup");
    g.label = "Pack catalog (furniture/catalog.json)";
    for (const a of catalog) g.appendChild(new Option(`${a.name} · ${a.size_m.w}×${a.size_m.d}×${a.size_m.h}`, a.id));
    sel.appendChild(g);
  }
  const g = document.createElement("optgroup");
  g.label = "QA placeholder boxes (not catalog assets)";
  for (const a of PLACEHOLDERS) g.appendChild(new Option(`${a.name} · ${a.size_m.w}×${a.size_m.d}×${a.size_m.h}`, a.id));
  sel.appendChild(g);
  $("catalog-note").textContent = catalog.length ? `${catalog.length} catalog assets` : "no furniture library found: placeholder boxes only";
}

function startPlacing() {
  state.placing = $("asset").value;
  document.body.classList.add("placing");
  $("place").textContent = "Click the floor… (Esc to cancel)";
}
function cancelPlacing() {
  state.placing = null;
  document.body.classList.remove("placing");
  $("place").textContent = "Place on floor";
}
$("place").addEventListener("click", () => (state.placing ? cancelPlacing() : startPlacing()));

const raycaster = new THREE.Raycaster();
const _ndc = new THREE.Vector2();
function pick(e, objects) {
  const r = renderer.domElement.getBoundingClientRect();
  _ndc.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
  raycaster.setFromCamera(_ndc, camera);
  return raycaster.intersectObjects(objects, true);
}

/** Floor point under the cursor: room polygons first, then the collision floor, then y = 0. */
function floorHit(e) {
  const fills = layers.rooms.children.filter((o) => o.userData.floorPick);
  const hit = pick(e, fills)[0];
  if (hit) return { point: hit.point, y: hit.object.userData.room.floor_y ?? 0 };
  const floors = [];
  state.collision?.traverse((o) => { if (o.isMesh && /floor|ground/i.test(o.name)) floors.push(o); });
  const h2 = pick(e, floors)[0];
  if (h2) return { point: h2.point, y: h2.point.y };
  const p = new THREE.Vector3();
  return raycaster.ray.intersectPlane(new THREE.Plane(new THREE.Vector3(0, 1, 0), 0), p) ? { point: p, y: 0 } : null;
}

let downAt = null;
renderer.domElement.addEventListener("pointerdown", (e) => {
  if (state.mode !== "orbit" || e.button !== 0) return;
  downAt = [e.clientX, e.clientY];
  if (state.placing) {
    const f = floorHit(e);
    if (!f) return;
    const obj = furniture.add({ asset_id: state.placing, x: f.point.x, y: f.y, z: f.point.z, rot_deg: Number($("rot-default").value) || 0 });
    furniture.select(obj);
    if (!e.shiftKey) cancelPlacing();
    furnitureChanged();
    return;
  }
  const hit = pick(e, [furniture.group])[0];
  const obj = hit && furniture.findByNode(hit.object);
  if (obj) {
    furniture.select(obj);
    const plane = new THREE.Plane(new THREE.Vector3(0, 1, 0), -obj.y);
    const p = new THREE.Vector3();
    raycaster.ray.intersectPlane(plane, p);
    state.drag = { obj, plane, dx: obj.x - p.x, dz: obj.z - p.z };
    orbit.enabled = false;
    furnitureChanged(false);
  }
});
renderer.domElement.addEventListener("pointermove", (e) => {
  if (!state.drag) return;
  pick(e, []);
  const p = new THREE.Vector3();
  if (!raycaster.ray.intersectPlane(state.drag.plane, p)) return;
  const snap = $("snap").checked ? 0.05 : 0;
  const s = (v) => (snap ? Math.round(v / snap) * snap : v);
  state.drag.obj.x = s(p.x + state.drag.dx);
  state.drag.obj.z = s(p.z + state.drag.dz);
  furniture.apply(state.drag.obj);
  furniture.check();
  renderSelected();
});
renderer.domElement.addEventListener("pointerup", (e) => {
  if (state.drag) {
    state.drag = null;
    orbit.enabled = state.mode === "orbit";
    furnitureChanged();
  } else if (downAt && Math.hypot(e.clientX - downAt[0], e.clientY - downAt[1]) < 4 && state.mode === "orbit" && !state.placing) {
    if (!pick(e, [furniture.group])[0]) { furniture.select(null); furnitureChanged(false); }
  }
  downAt = null;
});
renderer.domElement.addEventListener("dblclick", (e) => {
  if (state.mode !== "orbit") return;
  const f = floorHit(e);
  if (!f) return;
  walker.spawn(f.point.x, f.y, f.point.z);
  state.spawned = true;
  setMode("walk");
});

function rotateSelected(deg) {
  const o = furniture.selected;
  if (!o) return;
  o.rot_deg = (((o.rot_deg + deg) % 360) + 360) % 360;
  furniture.apply(o);
  furnitureChanged();
}
function nudgeSelected(dx, dz) {
  const o = furniture.selected;
  if (!o) return;
  o.x += dx;
  o.z += dz;
  furniture.apply(o);
  furnitureChanged();
}

let saveTimer = 0;
/** After any furniture edit: re-check, redraw the panel, rebuild the walk collider, remember the layout. */
function furnitureChanged(rebuild = true) {
  furniture.check();
  renderSelected();
  renderConflicts();
  fillPlanRooms();
  if (rebuild) rebuildCollider(150);
  clearTimeout(saveTimer);
  saveTimer = setTimeout(saveFurniture, 300);
}

function renderSelected() {
  const o = furniture.selected;
  $("sel").hidden = !o;
  if (!o) return;
  const room = furniture.roomOf(o);
  $("sel-name").textContent = `${o.instance_id} · ${o.asset.name}`;
  $("sel-room").textContent = room ? `${room.label} (${room.id})` : "not in a room";
  for (const k of ["x", "z", "rot_deg"]) if (document.activeElement !== $(`sel-${k}`)) $(`sel-${k}`).value = Number(o[k]).toFixed(k === "rot_deg" ? 0 : 2);
  if (document.activeElement !== $("sel-reason")) $("sel-reason").value = o.reason;
  $("sel-qa").textContent = o.asset._placeholder ? "placeholder box" : o.qa ?? "";
  const mine = furniture.conflicts.filter((c) => c.instance_id === o.instance_id);
  $("sel-conflicts").textContent = mine.map((c) => `${c.type}: ${c.detail}`).join("\n");
}
for (const k of ["x", "z", "rot_deg"]) {
  $(`sel-${k}`).addEventListener("change", (e) => {
    const o = furniture.selected;
    if (!o) return;
    o[k] = Number(e.target.value);
    furniture.apply(o);
    furnitureChanged();
  });
}
$("sel-reason").addEventListener("change", (e) => {
  if (furniture.selected) furniture.selected.reason = e.target.value.split(/\s+/).filter(Boolean).slice(0, 12).join(" ") || "QA test placement";
  furnitureChanged(false);
});
$("sel-del").addEventListener("click", () => {
  if (furniture.selected) furniture.remove(furniture.selected);
  furnitureChanged();
});

function renderConflicts() {
  const list = $("conflicts");
  list.innerHTML = "";
  $("conflict-count").textContent = `${furniture.objects.length} objects · ${furniture.conflicts.length} conflicts`;
  for (const c of furniture.conflicts) {
    const li = document.createElement("li");
    li.textContent = `${c.instance_id} ${c.type}: ${c.detail}`;
    li.onclick = () => { furniture.select(furniture.objects.find((o) => o.instance_id === c.instance_id)); furnitureChanged(false); };
    list.appendChild(li);
  }
}

function fillPlanRooms() {
  const sel = $("plan-room");
  const keep = sel.value;
  const counts = new Map();
  for (const o of furniture.objects) {
    const r = furniture.roomOf(o);
    if (r) counts.set(r.id, (counts.get(r.id) ?? 0) + 1);
  }
  sel.innerHTML = "";
  for (const r of state.rooms) sel.add(new Option(`${r.label} ${r.id.split(".").pop()} (${counts.get(r.id) ?? 0} objects)`, r.id));
  // Keep the user's choice while it has objects; otherwise jump to the room with the most objects.
  if (counts.get(keep)) sel.value = keep;
  else if (counts.size) sel.value = [...counts.entries()].sort((a, b) => b[1] - a[1])[0][0];
  else if ([...sel.options].some((o) => o.value === keep)) sel.value = keep;
}

$("export").addEventListener("click", async () => {
  const roomId = $("plan-room").value;
  const plan = furniture.exportPlan(state.listingId, roomId);
  const text = JSON.stringify(plan, null, 2);
  $("plan-text").value = text;
  const label = state.rooms.find((r) => r.id === roomId)?.label ?? "room";
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text + "\n"], { type: "application/json" }));
  a.download = `qa_plan_${state.listingId}_${label}.json`;
  a.click();
  try { await navigator.clipboard.writeText(text); status("plan exported (downloaded and copied)"); } catch { status("plan exported (downloaded)"); }
});
function importText(text) {
  try {
    const plan = JSON.parse(text);
    if (plan.listing_id && plan.listing_id !== state.listingId) status(`plan is for ${plan.listing_id}, loading anyway`, true);
    furniture.importPlan(plan);
    furnitureChanged();
  } catch (err) {
    status(`could not import plan: ${err.message}`, true);
  }
}
$("import-text").addEventListener("click", () => importText($("plan-text").value));
$("import-file").addEventListener("change", async (e) => {
  const f = e.target.files?.[0];
  if (f) importText(await f.text());
  e.target.value = "";
});
$("clear").addEventListener("click", () => {
  furniture.clear();
  furnitureChanged();
});

// Per-browser memory of test layouts (QA convenience only; may be empty in private windows).
const storeKey = () => `qa-viewer:${state.pack}:${state.listingId}`;
function saveFurniture() {
  try { localStorage.setItem(storeKey(), JSON.stringify(furniture.serialize())); } catch { /* ignore */ }
}
function restoreFurniture() {
  let saved = [];
  try { saved = JSON.parse(localStorage.getItem(storeKey()) || "[]"); } catch { /* ignore */ }
  for (const o of saved) furniture.add(o);
  furnitureChanged(false);
}

// ------------------------------------------------------------------ keys

function typing(e) {
  const t = e.target;
  const textInput = t instanceof HTMLInputElement && t.type !== "checkbox";
  return textInput || t instanceof HTMLTextAreaElement || t instanceof HTMLSelectElement;
}
document.addEventListener("keydown", (e) => {
  if (typing(e)) return;
  if (e.code === "Tab") {
    e.preventDefault();
    setMode(state.mode === "walk" ? "orbit" : "walk");
    return;
  }
  if (e.code === "KeyV") {
    toggleFirstPerson(state.mode === "walk" ? undefined : true);
    return;
  }
  if (state.mode === "walk") {
    walker.keys.add(e.code);
    if (e.code === "Space") e.preventDefault();
    return;
  }
  const big = e.shiftKey ? 90 : 15;
  switch (e.code) {
    case "KeyQ": rotateSelected(big); break;
    case "KeyE": rotateSelected(-big); break;
    case "ArrowLeft": nudgeSelected(-0.05, 0); e.preventDefault(); break;
    case "ArrowRight": nudgeSelected(0.05, 0); e.preventDefault(); break;
    case "ArrowUp": nudgeSelected(0, -0.05); e.preventDefault(); break;
    case "ArrowDown": nudgeSelected(0, 0.05); e.preventDefault(); break;
    case "Delete":
    case "Backspace":
      if (furniture.selected) { furniture.remove(furniture.selected); furnitureChanged(); }
      break;
    case "Escape":
      cancelPlacing();
      furniture.select(null);
      furnitureChanged(false);
      break;
    case "KeyP": startPlacing(); break;
  }
});
document.addEventListener("keyup", (e) => walker.keys.delete(e.code));
window.addEventListener("blur", () => walker.keys.clear());

// ------------------------------------------------------------------ stats + loop

function showStats(base, entry, ms, catalogCount) {
  let tris = 0;
  const textures = new Set();
  state.interior?.traverse((o) => {
    if (!o.isMesh) return;
    const g = o.geometry;
    tris += (g.index ? g.index.count : g.getAttribute("position").count) / 3;
    for (const m of [o.material].flat()) for (const k of ["map", "emissiveMap", "normalMap"]) if (m[k]) textures.add(m[k]);
  });
  let gpu = 0;
  for (const t of textures) {
    const img = t.image;
    if (img?.width) gpu += img.width * img.height * 4 * (4 / 3);
  }
  const size = (p) => {
    const e = performance.getEntriesByName(new URL(`${base}/${p}`, location.href).href)[0];
    return e?.encodedBodySize ? `${(e.encodedBodySize / 1e6).toFixed(1)} MB` : "?";
  };
  $("stats").textContent = [
    `interior.glb ${size(entry.interior)} · ${Math.round(tris).toLocaleString()} tris`,
    `${textures.size} textures · ~${Math.round(gpu / 1e6)} MB GPU (uncompressed RGBA + mips)`,
    `collision.glb ${size(entry.collision)}`,
    `${state.rooms.length} rooms · ${state.openings.filter((o) => o.type === "door").length} doors · ${state.openings.filter((o) => o.type === "window").length} windows`,
    `${catalogCount} catalog assets · loaded in ${(ms / 1000).toFixed(1)} s`,
  ].join("\n");
}

let last = performance.now();
let fpsT = 0, fpsN = 0, fps = 0;
renderer.setAnimationLoop((now) => {
  const dt = Math.min(0.1, (now - last) / 1000);
  last = now;
  if (state.mode === "walk") {
    walker.update(dt);
    updateWalkCamera();
  } else {
    orbit.update();
  }
  renderer.render(scene, camera);
  labelRenderer.render(scene, camera);
  fpsT += dt; fpsN++;
  if (fpsT > 0.5) { fps = fpsN / fpsT; fpsT = 0; fpsN = 0; updateHud(); }
});

function updateHud() {
  const info = renderer.info.render;
  let where = "";
  if (state.spawned) {
    const p = walker.position;
    const feet = p.y - walker.radius;
    const room = state.rooms.find((r) => Math.abs((r.floor_y ?? 0) - feet) < 0.6 && pointInPolygon([p.x, p.z], r.polygon));
    where = ` · capsule (${p.x.toFixed(2)}, ${feet.toFixed(2)}, ${p.z.toFixed(2)}) in ${room ? room.label : "no room"}`;
  }
  $("hud").textContent = `${fps.toFixed(0)} fps · ${info.calls} draws · ${Math.round(info.triangles / 1000)}k tris${where}`;
}

$("pack").addEventListener("change", (e) => choosePack(e.target.value).catch((err) => status(err.message, true)));
$("house").addEventListener("change", (e) => loadHouse(e.target.value).catch((err) => status(err.message, true)));
$("reload").addEventListener("click", () => choosePack(state.pack).catch((err) => status(err.message, true)));

loadPackList().catch((err) => status(`${err.message} (is serve.py running with --packs?)`, true));
