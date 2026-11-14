// Lane A pack QA viewer: NOT THE GAME. Testing aid only; never import, copy or port into apps/.
// The game's furniture renderer is Lane C's (apps/web/src/world/), and the real layout validator is Lane B's
// validate_layout (apps/agent/layout/). The checks here are rough eyeball aids for Lane A, not either of
// those. See worldgen/viewer/README.md.

import * as THREE from "three";
import {
  convexOverlap, doorZone, frontStrip, pointInPolygon, rectCorners, rectInsidePolygon,
} from "./geom2d.js";

const DOOR_REACH = 0.75; // m kept clear on both sides of a door (AGENTS.md walkway rule)
const FLAT_H = 0.05; // rugs, play mats: may overlap anything and never block

// Placeholder boxes with typical real-world sizes so placement can be tested before the furniture catalog
// exists. Ids start with qa_box_ so they can never be mistaken for catalog assets.
const box = (key, name, w, d, h, placement, front, cover) => ({
  id: `qa_box_${key}`, name: `${name} (QA box)`, category: key, tags: [], file: null,
  size_m: { w, d, h }, placement, clearance_m: front ? { front } : {}, cover, license: "n/a", source: "qa viewer placeholder",
  _placeholder: true,
});
export const PLACEHOLDERS = [
  box("couch", "Couch", 2.1, 0.9, 0.85, "wall", 0.75, "half"),
  box("sectional", "Sectional", 2.7, 1.7, 0.85, "corner", 0.75, "half"),
  box("armchair", "Armchair", 0.9, 0.9, 0.95, "free", 0.6, "half"),
  box("coffee_table", "Coffee table", 1.2, 0.6, 0.45, "free", 0, "none"),
  box("tv_stand", "TV + stand", 1.6, 0.45, 1.4, "wall", 1.5, "full"),
  box("rug", "Rug", 2.4, 1.7, 0.02, "free", 0, "none"),
  box("bookshelf", "Bookshelf", 0.9, 0.35, 1.9, "wall", 0.6, "full"),
  box("floor_lamp", "Floor lamp", 0.4, 0.4, 1.6, "corner", 0, "none"),
  box("double_bed", "Double bed", 1.6, 2.1, 1.0, "wall", 0.6, "half"),
  box("single_bed", "Single bed", 1.0, 2.0, 0.9, "wall", 0.6, "half"),
  box("nightstand", "Nightstand", 0.5, 0.4, 0.55, "wall", 0, "half"),
  box("dresser", "Dresser", 1.4, 0.5, 0.85, "wall", 0.75, "half"),
  box("desk", "Desk", 1.2, 0.6, 0.75, "wall", 0.75, "half"),
  box("chair", "Chair", 0.5, 0.55, 0.9, "free", 0, "half"),
  box("dining_table", "Dining table", 1.6, 0.9, 0.75, "free", 0.75, "half"),
  box("dining_chair", "Dining chair", 0.45, 0.5, 0.9, "free", 0, "half"),
  box("crib", "Crib", 0.75, 1.35, 0.95, "wall", 0.6, "half"),
  box("changing_table", "Changing table", 0.9, 0.55, 0.95, "wall", 0.6, "half"),
  box("rocking_chair", "Rocking chair", 0.7, 0.9, 1.0, "free", 0.5, "half"),
  box("toy_storage", "Toy storage", 0.9, 0.4, 0.8, "wall", 0.5, "half"),
  box("play_mat", "Play mat", 1.8, 1.8, 0.02, "free", 0, "none"),
  box("beanbag", "Beanbag", 0.9, 0.9, 0.7, "free", 0, "half"),
  box("plant", "Plant", 0.5, 0.5, 1.2, "corner", 0, "none"),
];

function colorFor(key) {
  let h = 0;
  for (const ch of key) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return new THREE.Color().setHSL((h % 360) / 360, 0.45, 0.55);
}

export class Furniture {
  /**
   * @param scene three.js scene
   * @param loader GLTFLoader (with meshopt) for catalog .glb files
   * @param onChange called after any add/move/remove (rebuild collider, redraw panel)
   */
  constructor(scene, loader, onChange) {
    this.group = new THREE.Group();
    this.group.name = "qa_furniture";
    scene.add(this.group);
    this.loader = loader;
    this.onChange = onChange;
    this.assets = new Map(); // id -> asset
    this.objects = []; // {instance_id, asset_id, x, y, z, rot_deg, reason, node, outline, strip}
    this.conflicts = [];
    this.rooms = [];
    this.assetUrl = (file) => file;
    this.nextId = 1;
    this.selected = null;
  }

  /** assetUrl(file) -> fetchable URL for an asset's `file` (root-relative furniture/v<N>/... since v2.3). */
  setContext({ rooms, catalog, assetUrl }) {
    this.rooms = rooms;
    this.assetUrl = assetUrl;
    this.assets.clear();
    for (const a of catalog) this.assets.set(a.id, { ...a });
    for (const a of PLACEHOLDERS) this.assets.set(a.id, a);
  }

  clear() {
    this.group.clear(); // nodes, outlines and clearance strips
    this.objects = [];
    this.conflicts = [];
    this.selected = null;
    this.nextId = 1;
  }

  /** Meshes that should block the capsule (flat items excluded). */
  colliderRoot() {
    return this.group;
  }

  add({ asset_id, x, y, z, rot_deg = 0, reason = "QA test placement", instance_id }) {
    const asset = this.assets.get(asset_id) ?? {
      id: asset_id, name: `${asset_id} (unknown)`, category: "unknown", size_m: { w: 1, d: 1, h: 1 },
      clearance_m: {}, placement: "free", cover: "none", _missing: true,
    };
    const id = instance_id ?? `o${this.nextId}`;
    this.nextId = Math.max(this.nextId, Number(id.slice(1)) + 1 || this.nextId + 1);
    const node = new THREE.Group();
    node.name = `qa_obj_${id}`;
    node.userData.instanceId = id;
    const obj = { instance_id: id, asset_id, x, y, z, rot_deg, reason, node, asset };
    this._buildVisual(obj);
    this.group.add(node);
    this.objects.push(obj);
    this.apply(obj);
    return obj;
  }

  _buildVisual(obj) {
    const { asset, node } = obj;
    const { w, d, h } = asset.size_m;
    const flat = h < FLAT_H;
    const body = new THREE.Group();
    body.name = "body";
    node.add(body);
    const addBox = () => {
      const mesh = new THREE.Mesh(
        new THREE.BoxGeometry(w, Math.max(h, 0.01), d).translate(0, Math.max(h, 0.01) / 2, 0),
        new THREE.MeshStandardMaterial({ color: asset._missing ? 0xff3355 : colorFor(asset.category), roughness: 0.8 }),
      );
      mesh.userData.noCollide = flat;
      body.add(mesh);
      // Front marker on the +Z face
      const front = new THREE.Mesh(
        new THREE.BoxGeometry(w * 0.6, Math.min(0.08, Math.max(h, 0.01)), 0.04),
        new THREE.MeshBasicMaterial({ color: 0x111111 }),
      );
      front.position.set(0, Math.max(h, 0.01) * 0.6, d / 2 + 0.02);
      front.userData.noCollide = true;
      body.add(front);
    };
    if (asset.file && !asset._placeholder) {
      this.loader.load(
        this.assetUrl(asset.file),
        (gltf) => {
          const model = gltf.scene;
          model.traverse((m) => { if (m.isMesh) m.userData.noCollide = true; });
          obj.qa = checkNormalized(model, asset); // measure before parenting: the bbox must be glb-local
          body.add(model);
          // Invisible box keeps collision and picking consistent with size_m
          const hit = new THREE.Mesh(
            new THREE.BoxGeometry(w, h, d).translate(0, h / 2, 0),
            new THREE.MeshBasicMaterial({ visible: false }),
          );
          hit.userData.noCollide = flat;
          body.add(hit);
          this.onChange?.();
        },
        undefined,
        () => { obj.qa = `could not load ${asset.file}; showing a box`; addBox(); this.onChange?.(); },
      );
    } else {
      addBox();
    }
    // Footprint outline (green ok / red conflict) and front clearance strip
    obj.outline = new THREE.LineLoop(new THREE.BufferGeometry(), new THREE.LineBasicMaterial({ color: 0x33dd66 }));
    obj.outline.userData.noCollide = true;
    obj.strip = new THREE.Mesh(
      new THREE.BufferGeometry(),
      new THREE.MeshBasicMaterial({ color: 0x3399ff, transparent: true, opacity: 0.25, side: THREE.DoubleSide, depthWrite: false }),
    );
    obj.strip.userData.noCollide = true;
    obj.strip.visible = false;
    this.group.add(obj.outline, obj.strip);
  }

  /** Push obj.x/z/rot_deg into three.js and redraw its footprint. */
  apply(obj) {
    obj.node.position.set(obj.x, obj.y, obj.z);
    obj.node.rotation.y = (obj.rot_deg * Math.PI) / 180;
    const { w, d } = obj.asset.size_m;
    const y = obj.y + 0.02;
    const corners = rectCorners(obj.x, obj.z, w, d, obj.rot_deg);
    obj.outline.geometry.setFromPoints(corners.map(([x, z]) => new THREE.Vector3(x, y, z)));
    const front = obj.asset.clearance_m?.front ?? 0;
    if (front > 0) {
      const s = frontStrip(obj.x, obj.z, w, d, obj.rot_deg, front);
      const v = s.map(([x, z]) => new THREE.Vector3(x, y, z));
      obj.strip.geometry.setFromPoints([v[0], v[1], v[2], v[0], v[2], v[3]]);
      obj.strip.geometry.computeBoundingSphere();
    }
    obj.strip.visible = front > 0 && obj === this.selected;
  }

  remove(obj) {
    this.group.remove(obj.node, obj.outline, obj.strip);
    this.objects = this.objects.filter((o) => o !== obj);
    if (this.selected === obj) this.selected = null;
  }

  select(obj) {
    const prev = this.selected;
    this.selected = obj;
    if (prev) this.apply(prev);
    if (obj) this.apply(obj);
  }

  findByNode(node) {
    while (node && !node.userData.instanceId) node = node.parent;
    return node ? this.objects.find((o) => o.instance_id === node.userData.instanceId) : null;
  }

  roomOf(obj) {
    return this.rooms.find((r) => Math.abs(r.floor_y - obj.y) < 0.5 && pointInPolygon([obj.x, obj.z], r.polygon)) ?? null;
  }

  /** Rough viewer checks in the contract's conflict shape. Not Lane B's validator. */
  check() {
    const out = [];
    const flat = (o) => o.asset.size_m.h < FLAT_H || o.asset.placement === "on_surface";
    const rects = new Map(this.objects.map((o) => [o, rectCorners(o.x, o.z, o.asset.size_m.w, o.asset.size_m.d, o.rot_deg)]));
    const doors = dedupeOpenings(this.rooms).filter((op) => op.type === "door");
    for (const o of this.objects) {
      const room = this.roomOf(o);
      const r = rects.get(o);
      if (!room) out.push({ instance_id: o.instance_id, type: "outside_room", detail: "centre is not inside any room" });
      else if (!rectInsidePolygon(r, room.polygon))
        out.push({ instance_id: o.instance_id, type: "outside_room", detail: `footprint leaves ${room.label} (${room.id})` });
      if (flat(o)) continue;
      for (const p of this.objects) {
        if (p === o || flat(p)) continue;
        if (convexOverlap(r, rects.get(p)))
          out.push({ instance_id: o.instance_id, type: "overlap", detail: `overlaps ${p.instance_id} (${p.asset.name})` });
      }
      for (const dr of doors) {
        if (Math.abs(dr.floor_y - o.y) > 0.5) continue;
        if (convexOverlap(r, doorZone(dr.a, dr.b, DOOR_REACH))) {
          out.push({ instance_id: o.instance_id, type: "blocks_opening", detail: `within ${DOOR_REACH} m of door ${dr.key}` });
          break;
        }
      }
      const front = o.asset.clearance_m?.front ?? 0;
      if (front > 0) {
        const s = frontStrip(o.x, o.z, o.asset.size_m.w, o.asset.size_m.d, o.rot_deg, front);
        const blocker = this.objects.find((p) => p !== o && !flat(p) && convexOverlap(s, rects.get(p)));
        if (blocker) out.push({ instance_id: o.instance_id, type: "clearance", detail: `${front} m front clearance hits ${blocker.instance_id}` });
        else if (room && !rectInsidePolygon(s, room.polygon))
          out.push({ instance_id: o.instance_id, type: "clearance", detail: `${front} m front clearance runs into a wall` });
      }
    }
    this.conflicts = out;
    const bad = new Set(out.map((c) => c.instance_id));
    for (const o of this.objects) o.outline.material.color.set(bad.has(o.instance_id) ? 0xff3355 : o === this.selected ? 0xffffff : 0x33dd66);
    return out;
  }

  /** PlacementPlan (contracts/placement_plan.schema.json) for one room, status "draft". */
  exportPlan(listingId, roomId) {
    const objs = this.objects.filter((o) => this.roomOf(o)?.id === roomId);
    const ids = new Set(objs.map((o) => o.instance_id));
    const r3 = (v) => Math.round(v * 1000) / 1000;
    return {
      plan_id: `qa_${Date.now().toString(36)}`,
      listing_id: listingId,
      room_id: roomId,
      status: "draft",
      objects: objs.map((o) => ({
        instance_id: o.instance_id, asset_id: o.asset_id, x: r3(o.x), y: r3(o.y), z: r3(o.z),
        rot_deg: Math.round(o.rot_deg * 10) / 10, reason: o.reason,
      })),
      conflicts: this.conflicts.filter((c) => ids.has(c.instance_id)),
    };
  }

  importPlan(plan) {
    for (const o of [...this.objects]) if (this.roomOf(o)?.id === plan.room_id) this.remove(o);
    for (const o of plan.objects ?? []) {
      const taken = this.objects.some((p) => p.instance_id === o.instance_id);
      this.add({ ...o, instance_id: taken ? undefined : o.instance_id });
    }
  }

  serialize() {
    return this.objects.map(({ instance_id, asset_id, x, y, z, rot_deg, reason }) => ({ instance_id, asset_id, x, y, z, rot_deg, reason }));
  }
}

/** Doors and windows once each (rooms.json lists a shared door in both rooms). */
export function dedupeOpenings(rooms) {
  const seen = new Map();
  for (const room of rooms) {
    for (const op of room.openings ?? []) {
      const k = [op.a, op.b].map((p) => p.map((v) => v.toFixed(1)).join(",")).sort().join("|");
      const key = `${op.type}:${k}`;
      if (seen.has(key)) { seen.get(key).rooms.push(room.id); continue; }
      seen.set(key, {
        ...op, key: `${room.id.split(".").pop()}-${seen.size + 1}`, floor_y: room.floor_y, rooms: [room.id],
        width: Math.hypot(op.b[0] - op.a[0], op.b[1] - op.a[1]),
      });
    }
  }
  return [...seen.values()];
}

/** Catalog QA: is the glb Y-up, metres, origin at bottom centre, and close to size_m? */
function checkNormalized(model, asset) {
  const b = new THREE.Box3().setFromObject(model);
  const s = b.getSize(new THREE.Vector3());
  const c = b.getCenter(new THREE.Vector3());
  const issues = [];
  const off = (a, e) => Math.abs(a - e) / e > 0.15;
  if (off(s.x, asset.size_m.w) || off(s.z, asset.size_m.d) || off(s.y, asset.size_m.h))
    issues.push(`mesh ${s.x.toFixed(2)}x${s.z.toFixed(2)}x${s.y.toFixed(2)} m vs size_m ${asset.size_m.w}x${asset.size_m.d}x${asset.size_m.h}`);
  if (Math.abs(b.min.y) > 0.02) issues.push(`bottom at y=${b.min.y.toFixed(3)} (want 0)`);
  if (Math.hypot(c.x, c.z) > 0.05) issues.push(`centre off origin by ${Math.hypot(c.x, c.z).toFixed(2)} m`);
  return issues.length ? issues.join("; ") : "normalized ok";
}
