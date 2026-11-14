// Lane A pack QA viewer: NOT THE GAME. Testing aid only; never import, copy or port into apps/.
// The game's character controller is Lane C's (ecctrl + Rapier, AGENTS.md section 5). This file is a crude
// stand-in so Lane A can check door widths and room sizes against a capsule. See worldgen/viewer/README.md.
//
// Capsule vs triangle-BVH collision (the three-mesh-bvh "character movement" approach).

import * as THREE from "three";
import { MeshBVH } from "three-mesh-bvh";
import { mergeGeometries } from "three/addons/utils/BufferGeometryUtils.js";

const GRAVITY = -20;
const SPEED = 3.0; // m/s walk
const RUN = 5.5; // m/s with Shift
const JUMP = 6.0;
const SUBSTEPS = 5;

/** Merge every mesh under `roots` (world transforms applied) into one position-only geometry with a BVH. */
export function buildCollider(roots) {
  const geoms = [];
  for (const root of roots) {
    if (!root) continue;
    root.updateMatrixWorld(true);
    root.traverse((o) => {
      if (!o.isMesh || o.userData.noCollide) return;
      let g = o.geometry.clone();
      g.applyMatrix4(o.matrixWorld);
      g = g.index ? g.toNonIndexed() : g;
      const pos = g.getAttribute("position");
      const plain = new THREE.BufferGeometry();
      // Copy into a plain float array (meshopt/quantized attributes can be normalized ints).
      const arr = new Float32Array(pos.count * 3);
      for (let i = 0; i < pos.count; i++) {
        arr[i * 3] = pos.getX(i); arr[i * 3 + 1] = pos.getY(i); arr[i * 3 + 2] = pos.getZ(i);
      }
      plain.setAttribute("position", new THREE.BufferAttribute(arr, 3));
      geoms.push(plain);
    });
  }
  if (!geoms.length) return null;
  const merged = mergeGeometries(geoms, false);
  merged.boundsTree = new MeshBVH(merged);
  return merged;
}

export class Walker {
  constructor(scene) {
    this.radius = 0.3;
    this.height = 1.7;
    this.position = new THREE.Vector3(); // centre of the bottom sphere
    this.velocity = new THREE.Vector3();
    this.onGround = false;
    this.yaw = 0; // camera yaw (radians); WASD is relative to it
    this.keys = new Set();
    this.collider = null;
    this.mesh = new THREE.Group();
    this.mesh.name = "qa_capsule";
    scene.add(this.mesh);
    this._rebuildMesh();
    this._tmpBox = new THREE.Box3();
    this._tmpSeg = new THREE.Line3();
    this._v1 = new THREE.Vector3();
    this._v2 = new THREE.Vector3();
  }

  setSize(radius, height) {
    this.radius = Math.max(0.1, radius);
    this.height = Math.max(this.radius * 2 + 0.05, height);
    this._rebuildMesh();
  }

  _rebuildMesh() {
    this.mesh.clear();
    const r = this.radius, mid = this.height - 2 * r;
    const body = new THREE.Mesh(
      new THREE.CapsuleGeometry(r, mid, 6, 16),
      new THREE.MeshStandardMaterial({ color: 0xffb020, transparent: true, opacity: 0.85 }),
    );
    body.position.y = mid / 2; // capsule centre sits mid/2 above the bottom sphere centre
    const nose = new THREE.Mesh(new THREE.ConeGeometry(r * 0.35, r * 0.8, 12), new THREE.MeshBasicMaterial({ color: 0x202020 }));
    nose.rotation.x = Math.PI / 2;
    nose.position.set(0, mid * 0.8, -r * 1.1); // faces -Z (forward when yaw = 0)
    this.mesh.add(body, nose);
  }

  /** Feet on the floor at (x, floorY, z). */
  spawn(x, floorY, z) {
    this.position.set(x, floorY + this.radius + 0.05, z);
    this.velocity.set(0, 0, 0);
    this._syncMesh();
  }

  get eye() {
    return this._v2.set(this.position.x, this.position.y - this.radius + this.height * 0.92, this.position.z);
  }

  update(dt) {
    if (!this.collider) return;
    dt = Math.min(dt, 0.05);
    for (let i = 0; i < SUBSTEPS; i++) this._step(dt / SUBSTEPS);
    this._syncMesh();
  }

  _step(dt) {
    const k = this.keys;
    if (this.onGround) this.velocity.y = GRAVITY * dt;
    else this.velocity.y += GRAVITY * dt;
    if (this.onGround && k.has("Space")) { this.velocity.y = JUMP; this.onGround = false; }
    this.position.addScaledVector(this.velocity, dt);

    const fwd = (k.has("KeyW") || k.has("ArrowUp") ? 1 : 0) - (k.has("KeyS") || k.has("ArrowDown") ? 1 : 0);
    const side = (k.has("KeyD") || k.has("ArrowRight") ? 1 : 0) - (k.has("KeyA") || k.has("ArrowLeft") ? 1 : 0);
    if (fwd || side) {
      const speed = k.has("ShiftLeft") || k.has("ShiftRight") ? RUN : SPEED;
      const s = Math.sin(this.yaw), c = Math.cos(this.yaw);
      // yaw 0 looks down -Z; right is +X
      const mx = -s * fwd + c * side, mz = -c * fwd - s * side;
      const len = Math.hypot(mx, mz);
      this.position.x += (mx / len) * speed * dt;
      this.position.z += (mz / len) * speed * dt;
    }

    const r = this.radius;
    const seg = this._tmpSeg;
    seg.start.copy(this.position);
    seg.end.copy(this.position).add(this._v1.set(0, this.height - 2 * r, 0));
    const box = this._tmpBox.makeEmpty().expandByPoint(seg.start).expandByPoint(seg.end);
    box.min.addScalar(-r);
    box.max.addScalar(r);

    const triPoint = new THREE.Vector3(), capPoint = new THREE.Vector3();
    this.collider.boundsTree.shapecast({
      intersectsBounds: (b) => b.intersectsBox(box),
      intersectsTriangle: (tri) => {
        const dist = tri.closestPointToSegment(seg, triPoint, capPoint);
        if (dist < r) {
          const depth = r - dist;
          const dir = capPoint.sub(triPoint).normalize();
          seg.start.addScaledVector(dir, depth);
          seg.end.addScaledVector(dir, depth);
        }
      },
    });

    const delta = this._v1.subVectors(seg.start, this.position);
    this.onGround = delta.y > Math.abs(dt * this.velocity.y * 0.25);
    const offset = Math.max(0, delta.length() - 1e-5);
    delta.normalize().multiplyScalar(offset);
    this.position.add(delta);
    if (!this.onGround) {
      delta.normalize();
      this.velocity.addScaledVector(delta, -delta.dot(this.velocity));
    } else {
      this.velocity.set(0, 0, 0);
    }
    if (this.position.y < -20) this.onFall?.();
  }

  _syncMesh() {
    this.mesh.position.copy(this.position);
    this.mesh.rotation.y = this.yaw;
  }
}
