// Lane C: cut opened interior doorways out of interior.glb's wall textures.
//
// The doors that were closed in the scan are openable (doorSplit.ts ->
// HouseDoors.tsx), but the photo texture still shows the closed door. For each
// opened door, the doorway is the vertical rectangle a -> b, floorY to
// floorY + DOORWAY_H_M, in the house-local frame. Every wall triangle lying in
// that wall plane (both faces of the wall: both rooms) is clipped against the
// rectangle, and the clipped polygon is mapped into the triangle's UV with
// barycentric coordinates (the wall quads are planar and affinely mapped). The
// UV polygons are then cleared to alpha 0 in a copy of each texture
// (applyDoorCutouts), and alphaTest turns that into a real hole.
//
// The math here is pure (doorCutout.test.ts). applyDoorCutouts does the
// one-time canvas pass; HouseScene calls it once per interior scene.

import { CanvasTexture, Matrix4, Mesh, Vector3, type Material, type Object3D, type Texture } from "three";

import type { Point2 } from "../contracts.gen";
import type { OpenedDoor } from "./doorSplit";
import { warnOnce } from "./pack";

export type V2 = [number, number];
export type V3 = [number, number, number];

/** Height of the doorway cut, metres above the door's floor. */
export const DOORWAY_H_M = 2.03;
/** A triangle counts as part of the door's wall within this distance of the a -> b line (both faces). */
export const WALL_PLANE_TOL_M = 0.15;
/** Most a wall triangle's normal may lean along the door line, or up/down (sin of the angle). */
const NORMAL_TOL = 0.2;
/** The cut stops this many texture pixels inside the doorway, so the frame stays. */
export const INSET_PX = 2;

/** One textured triangle in the house-local frame. `tex` identifies its texture (shared ones share a key). */
export type Tri<K = unknown> = { p: [V3, V3, V3]; uv: [V2, V2, V2]; tex: K };

/** The doorway as a 2D rectangle in the door's wall frame: s along a -> b (0..width), y up. */
type Frame = { a: Point2; dir: V2; nrm: V2; width: number; floorY: number; height: number };

function frameOf(door: Pick<OpenedDoor, "a" | "b" | "floorY">, height = DOORWAY_H_M): Frame | null {
  const dx = door.b[0] - door.a[0];
  const dz = door.b[1] - door.a[1];
  const width = Math.hypot(dx, dz);
  if (!(width > 1e-6)) return null;
  const dir: V2 = [dx / width, dz / width];
  return { a: door.a, dir, nrm: [-dir[1], dir[0]], width, floorY: door.floorY, height };
}

/** (s, y) of a house-local point in the door's wall frame, and its distance off the wall line. */
function toWall(f: Frame, p: V3): { s: number; y: number; off: number } {
  const rx = p[0] - f.a[0];
  const rz = p[2] - f.a[1];
  return { s: rx * f.dir[0] + rz * f.dir[1], y: p[1], off: rx * f.nrm[0] + rz * f.nrm[1] };
}

/** True when the triangle lies in the door's wall plane: vertical, facing across the door line, near it. */
function inWallPlane(f: Frame, t: Tri): boolean {
  const [p0, p1, p2] = t.p;
  const e1 = [p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2]];
  const e2 = [p2[0] - p0[0], p2[1] - p0[1], p2[2] - p0[2]];
  const n = [e1[1] * e2[2] - e1[2] * e2[1], e1[2] * e2[0] - e1[0] * e2[2], e1[0] * e2[1] - e1[1] * e2[0]];
  const l = Math.hypot(n[0], n[1], n[2]);
  if (!(l > 1e-12)) return false;
  // Not a floor or ceiling, and not a wall running across the door line.
  if (Math.abs(n[1] / l) > NORMAL_TOL) return false;
  if (Math.abs((n[0] * f.dir[0] + n[2] * f.dir[1]) / l) > NORMAL_TOL) return false;
  return t.p.every((p) => Math.abs(toWall(f, p).off) <= WALL_PLANE_TOL_M);
}

/** Signed area of a 2D polygon (positive counterclockwise). */
export function area2(poly: V2[]): number {
  let s = 0;
  for (let i = 0; i < poly.length; i++) {
    const [x0, y0] = poly[i];
    const [x1, y1] = poly[(i + 1) % poly.length];
    s += x0 * y1 - x1 * y0;
  }
  return s / 2;
}

/**
 * Clip a polygon to a triangle (Sutherland-Hodgman, any winding). Returns the
 * part of `poly` inside `tri`; empty when they don't overlap.
 */
export function clipToTriangle(poly: V2[], tri: [V2, V2, V2]): V2[] {
  const ccw = area2(tri) >= 0;
  let out = poly;
  for (let i = 0; i < 3 && out.length; i++) {
    const a = tri[i];
    const b = tri[(i + 1) % 3];
    // Inside = left of a->b for a CCW triangle, right for CW.
    const side = (p: V2) => {
      const c = (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0]);
      return ccw ? c : -c;
    };
    const input = out;
    out = [];
    for (let k = 0; k < input.length; k++) {
      const cur = input[k];
      const prev = input[(k + input.length - 1) % input.length];
      const sc = side(cur);
      const sp = side(prev);
      if (sc >= 0) {
        if (sp < 0) out.push(cross(prev, cur, sp, sc));
        out.push(cur);
      } else if (sp >= 0) out.push(cross(prev, cur, sp, sc));
    }
  }
  return out;
}

function cross(p: V2, q: V2, sp: number, sq: number): V2 {
  const t = sp / (sp - sq);
  return [p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t];
}

/** Barycentric coordinates of `p` in 2D triangle `t` (null if degenerate). */
export function barycentric(p: V2, t: [V2, V2, V2]): [number, number, number] | null {
  const [a, b, c] = t;
  const d = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1]);
  if (Math.abs(d) < 1e-14) return null;
  const l0 = ((b[1] - c[1]) * (p[0] - c[0]) + (c[0] - b[0]) * (p[1] - c[1])) / d;
  const l1 = ((c[1] - a[1]) * (p[0] - c[0]) + (a[0] - c[0]) * (p[1] - c[1])) / d;
  return [l0, l1, 1 - l0 - l1];
}

/** Texture pixels per metre of this triangle along the wall (the smaller of the s and y directions). */
function pxPerMetre(w: [V2, V2, V2], uv: [V2, V2, V2], texSize: V2): number {
  const at = (p: V2): V2 | null => {
    const l = barycentric(p, w);
    return l ? [l[0] * uv[0][0] + l[1] * uv[1][0] + l[2] * uv[2][0], l[0] * uv[0][1] + l[1] * uv[1][1] + l[2] * uv[2][1]] : null;
  };
  const o = at(w[0]);
  const s = at([w[0][0] + 1, w[0][1]]);
  const y = at([w[0][0], w[0][1] + 1]);
  if (!o || !s || !y) return Infinity;
  const ds = Math.hypot((s[0] - o[0]) * texSize[0], (s[1] - o[1]) * texSize[1]);
  const dy = Math.hypot((y[0] - o[0]) * texSize[0], (y[1] - o[1]) * texSize[1]);
  return Math.min(ds, dy);
}

/** side: which face of the wall (sign of the offset from the door line, -1 or 1). */
export type DoorCut<K> = { doorId: string; tex: K; uv: V2[]; side: -1 | 1 };

/**
 * The UV polygons to clear for one door: one per wall triangle the doorway
 * rectangle overlaps, on both faces of the wall. `texSize` gives each texture's
 * pixel size, for the INSET_PX inset. Null (skip the door, with a reason) when
 * no triangle matches or a polygon falls outside [0, 1] UV.
 */
export function doorUvPolygons<K>(
  door: Pick<OpenedDoor, "id" | "a" | "b" | "floorY">,
  tris: readonly Tri<K>[],
  texSize: (tex: K) => V2,
  height = DOORWAY_H_M,
): { cuts: DoorCut<K>[] } | { skip: string } {
  const f = frameOf(door, height);
  if (!f) return { skip: "zero-width door" };
  const hits: { w: [V2, V2, V2]; t: Tri<K> }[] = [];
  for (const t of tris) {
    if (!inWallPlane(f, t)) continue;
    const w = t.p.map((p) => {
      const q = toWall(f, p);
      return [q.s, q.y] as V2;
    }) as [V2, V2, V2];
    // Quick reject: the triangle's (s, y) box must overlap the doorway.
    const minS = Math.min(w[0][0], w[1][0], w[2][0]);
    const maxS = Math.max(w[0][0], w[1][0], w[2][0]);
    const minY = Math.min(w[0][1], w[1][1], w[2][1]);
    const maxY = Math.max(w[0][1], w[1][1], w[2][1]);
    if (maxS <= 0 || minS >= f.width || maxY <= f.floorY || minY >= f.floorY + f.height) continue;
    hits.push({ w, t });
  }
  if (!hits.length) return { skip: "no wall triangle in the doorway plane" };

  // One inset for the whole door: INSET_PX at the densest texture it touches.
  let density = 0;
  for (const h of hits) {
    const d = pxPerMetre(h.w, h.t.uv, texSize(h.t.tex));
    if (Number.isFinite(d)) density = Math.max(density, d);
  }
  const inset = density > 0 ? INSET_PX / density : 0;
  const s0 = inset;
  const s1 = f.width - inset;
  const y0 = f.floorY; // the floor edge needs no frame
  const y1 = f.floorY + f.height - inset;
  if (!(s1 > s0)) return { skip: "doorway narrower than its inset" };
  const rect: V2[] = [
    [s0, y0],
    [s1, y0],
    [s1, y1],
    [s0, y1],
  ];

  const cuts: DoorCut<K>[] = [];
  for (const { w, t } of hits) {
    const clipped = clipToTriangle(rect, w);
    if (clipped.length < 3 || Math.abs(area2(clipped)) < 1e-8) continue;
    const uv: V2[] = [];
    for (const p of clipped) {
      const l = barycentric(p, w);
      if (!l) return { skip: "degenerate wall triangle" };
      const u = l[0] * t.uv[0][0] + l[1] * t.uv[1][0] + l[2] * t.uv[2][0];
      const v = l[0] * t.uv[0][1] + l[1] * t.uv[1][1] + l[2] * t.uv[2][1];
      if (u < -1e-6 || u > 1 + 1e-6 || v < -1e-6 || v > 1 + 1e-6) return { skip: "UV outside [0, 1]" };
      uv.push([u, v]);
    }
    const off = t.p.reduce((sum, p) => sum + toWall(f, p).off, 0);
    cuts.push({ doorId: door.id, tex: t.tex, uv, side: off < 0 ? -1 : 1 });
  }
  if (!cuts.length) return { skip: "no wall triangle overlaps the doorway" };
  return { cuts };
}

/** Group cuts by texture, so each shared texture is copied and redrawn once. */
export function groupByTexture<K>(cuts: readonly DoorCut<K>[]): Map<K, V2[][]> {
  const out = new Map<K, V2[][]>();
  for (const c of cuts) {
    const list = out.get(c.tex);
    if (list) list.push(c.uv);
    else out.set(c.tex, [c.uv]);
  }
  return out;
}

/**
 * All the cuts for `doors` (the split's opened doors only: never the front
 * door). A door that can't be cut is skipped with one warnOnce.
 */
export function planCutouts<K>(
  listingId: string,
  doors: readonly OpenedDoor[],
  tris: readonly Tri<K>[],
  texSize: (tex: K) => V2,
): { byTexture: Map<K, V2[][]>; cut: string[]; skipped: { id: string; why: string }[] } {
  const all: DoorCut<K>[] = [];
  const cut: string[] = [];
  const skipped: { id: string; why: string }[] = [];
  for (const d of doors) {
    const r = doorUvPolygons(d, tris, texSize);
    if ("skip" in r) {
      skipped.push({ id: d.id, why: r.skip });
      warnOnce(`cutout:${listingId}:${d.id}`, `[door cutout] ${listingId} ${d.id}: skipped (${r.skip})`);
      continue;
    }
    all.push(...r.cuts);
    cut.push(d.id);
  }
  return { byTexture: groupByTexture(all), cut, skipped };
}

// ---------------------------------------------------------------------------
// three.js side: gather the wall triangles, redraw the textures once.
// ---------------------------------------------------------------------------

const WALL_NODE = /^walls_/;

/** Every textured triangle of the walls_N meshes under `root`, in root's frame, keyed by its map. */
export function wallTriangles(root: Object3D): Tri<Texture>[] {
  const out: Tri<Texture>[] = [];
  root.updateMatrixWorld(true);
  const toRoot = new Matrix4().copy(root.matrixWorld).invert();
  const m = new Matrix4();
  const v = new Vector3();
  root.traverse((o) => {
    if (!(o instanceof Mesh) || !isWallMesh(o)) return;
    const mat = o.material as Material & { map?: Texture | null };
    const tex = !Array.isArray(mat) ? mat.map : null;
    const pos = o.geometry.getAttribute("position");
    const uv = o.geometry.getAttribute("uv");
    if (!tex || !pos || !uv) return;
    m.multiplyMatrices(toRoot, o.matrixWorld);
    const idx = o.geometry.getIndex();
    const n = idx ? idx.count : pos.count;
    const P = (k: number): V3 => {
      const i = idx ? idx.getX(k) : k;
      v.fromBufferAttribute(pos, i).applyMatrix4(m);
      return [v.x, v.y, v.z];
    };
    const U = (k: number): V2 => {
      const i = idx ? idx.getX(k) : k;
      return [uv.getX(i), uv.getY(i)];
    };
    for (let k = 0; k + 2 < n; k += 3) out.push({ p: [P(k), P(k + 1), P(k + 2)], uv: [U(k), U(k + 1), U(k + 2)], tex });
  });
  return out;
}

/** A walls_N mesh: the node itself or (glTF primitives) its parent is named walls_N. */
function isWallMesh(o: Object3D): boolean {
  return WALL_NODE.test(o.name) || (!!o.parent && WALL_NODE.test(o.parent.name));
}

type Img = CanvasImageSource & { width: number; height: number };

const done = new WeakMap<Object3D, Set<string>>();

/**
 * One-time pass on a loaded interior scene: clear each opened doorway from
 * the wall textures. Idempotent per scene and door (the scene is cached by the
 * loader across remounts). Never throws: a failure is one warnOnce.
 */
export function applyDoorCutouts(listingId: string, root: Object3D, doors: readonly OpenedDoor[]): string[] {
  try {
    const seen = done.get(root) ?? new Set<string>();
    done.set(root, seen);
    const todo = doors.filter((d) => !seen.has(d.id));
    if (!todo.length) return [];
    const tris = wallTriangles(root);
    const size = (t: Texture): V2 => {
      const img = t.image as Img | null;
      return img && img.width && img.height ? [img.width, img.height] : [1024, 1024];
    };
    const plan = planCutouts(listingId, todo, tris, size);
    for (const d of todo) seen.add(d.id); // cut or skipped: never retried

    for (const [tex, polys] of plan.byTexture) {
      const img = tex.image as Img | null;
      if (!img || !img.width || !img.height || typeof document === "undefined") continue;
      const canvas = document.createElement("canvas");
      canvas.width = img.width;
      canvas.height = img.height;
      const ctx = canvas.getContext("2d");
      if (!ctx) continue;
      ctx.drawImage(img, 0, 0);
      ctx.globalCompositeOperation = "destination-out";
      ctx.fillStyle = "#000";
      for (const poly of polys) {
        ctx.beginPath();
        poly.forEach(([u, v], i) => {
          // glTF UV: v down from the top of the image (flipY false), so v maps straight to canvas y.
          const x = u * img.width;
          const y = (tex.flipY ? 1 - v : v) * img.height;
          if (i === 0) ctx.moveTo(x, y);
          else ctx.lineTo(x, y);
        });
        ctx.closePath();
        ctx.fill();
      }
      const next = new CanvasTexture(canvas);
      next.flipY = tex.flipY;
      next.colorSpace = tex.colorSpace;
      next.wrapS = tex.wrapS;
      next.wrapT = tex.wrapT;
      next.magFilter = tex.magFilter;
      next.minFilter = tex.minFilter;
      next.anisotropy = tex.anisotropy;
      next.generateMipmaps = tex.generateMipmaps;
      next.channel = tex.channel;
      next.needsUpdate = true;
      root.traverse((o) => {
        if (!(o instanceof Mesh)) return;
        for (const m of Array.isArray(o.material) ? o.material : [o.material]) {
          const mm = m as Material & { map?: Texture | null };
          if (mm.map !== tex) continue;
          mm.map = next;
          // Opaque (OPAQUE alphaMode) walls need the alpha test too, or the cut shows black.
          if (!(mm.alphaTest > 0)) mm.alphaTest = 0.5;
          mm.needsUpdate = true; // keeps toneMapped as interiorMaterials set it
        }
      });
      tex.dispose();
    }
    return plan.cut;
  } catch (e) {
    warnOnce(`cutout:${listingId}:error`, `[door cutout] ${listingId}: skipped (${(e as Error).message})`);
    return [];
  }
}
