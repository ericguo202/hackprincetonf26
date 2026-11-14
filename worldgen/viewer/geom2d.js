// Lane A pack QA viewer: NOT THE GAME. Testing aid only; never import, copy or port into apps/.
// See worldgen/viewer/README.md.
//
// 2D helpers in the house-local XZ plane. Points are [x, z] (same as rooms.json polygons).
// rot_deg is counterclockwise seen from above = three.js rotation.y in radians, no negation.

export function polygonArea(poly) {
  let a = 0;
  for (let i = 0; i < poly.length; i++) {
    const [px, pz] = poly[i];
    const [qx, qz] = poly[(i + 1) % poly.length];
    a += px * qz - qx * pz;
  }
  return Math.abs(a / 2);
}

/** Area-weighted centroid; falls back to the vertex mean for degenerate input. */
export function polygonCentroid(poly) {
  let a = 0, cx = 0, cz = 0;
  for (let i = 0; i < poly.length; i++) {
    const [px, pz] = poly[i];
    const [qx, qz] = poly[(i + 1) % poly.length];
    const c = px * qz - qx * pz;
    a += c; cx += (px + qx) * c; cz += (pz + qz) * c;
  }
  if (Math.abs(a) < 1e-9) {
    const n = poly.length;
    return [poly.reduce((s, p) => s + p[0], 0) / n, poly.reduce((s, p) => s + p[1], 0) / n];
  }
  return [cx / (3 * a), cz / (3 * a)];
}

export function pointInPolygon(p, poly) {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const a = poly[i], b = poly[j];
    if (a[1] > p[1] !== b[1] > p[1]) {
      const t = (p[1] - a[1]) / (b[1] - a[1]);
      if (p[0] < a[0] + t * (b[0] - a[0])) inside = !inside;
    }
  }
  return inside;
}

/** A label point that is inside the polygon (centroid if inside, else the inside grid point nearest it). */
export function labelPoint(poly) {
  const c = polygonCentroid(poly);
  if (pointInPolygon(c, poly)) return c;
  const xs = poly.map((p) => p[0]), zs = poly.map((p) => p[1]);
  const [x0, x1, z0, z1] = [Math.min(...xs), Math.max(...xs), Math.min(...zs), Math.max(...zs)];
  let best = poly[0], bestD = Infinity;
  for (let i = 1; i < 20; i++) for (let j = 1; j < 20; j++) {
    const p = [x0 + ((x1 - x0) * i) / 20, z0 + ((z1 - z0) * j) / 20];
    const d = Math.hypot(p[0] - c[0], p[1] - c[1]);
    if (d < bestD && pointInPolygon(p, poly)) { best = p; bestD = d; }
  }
  return best;
}

/** Corners of a w x d rectangle centred at (x, z), rotated rotDeg CCW from above. Local +Z is the front. */
export function rectCorners(x, z, w, d, rotDeg) {
  const t = (rotDeg * Math.PI) / 180;
  const c = Math.cos(t), s = Math.sin(t);
  // three.js rotation.y: local (lx, lz) -> world (lx*c + lz*s, -lx*s + lz*c)
  return [[-w / 2, -d / 2], [w / 2, -d / 2], [w / 2, d / 2], [-w / 2, d / 2]].map(([lx, lz]) => [
    x + lx * c + lz * s,
    z - lx * s + lz * c,
  ]);
}

/** Unit vector of local +Z (the asset's front) in world XZ. */
export function frontDir(rotDeg) {
  const t = (rotDeg * Math.PI) / 180;
  return [Math.sin(t), Math.cos(t)];
}

/** Separating-axis test for two convex polygons. Touching edges do not count as overlap. */
export function convexOverlap(a, b, eps = 1e-3) {
  for (const poly of [a, b]) {
    for (let i = 0; i < poly.length; i++) {
      const p = poly[i], q = poly[(i + 1) % poly.length];
      const ax = -(q[1] - p[1]), az = q[0] - p[0];
      const proj = (P) => P.map((v) => v[0] * ax + v[1] * az);
      const pa = proj(a), pb = proj(b);
      const len = Math.hypot(ax, az) || 1;
      if (Math.max(...pa) - Math.min(...pb) <= eps * len || Math.max(...pb) - Math.min(...pa) <= eps * len) return false;
    }
  }
  return true;
}

function segmentsCross(p1, p2, p3, p4) {
  const d = (a, b, c) => (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]);
  const d1 = d(p3, p4, p1), d2 = d(p3, p4, p2), d3 = d(p1, p2, p3), d4 = d(p1, p2, p4);
  return d1 * d2 < 0 && d3 * d4 < 0;
}

/** True if every corner is inside the (possibly concave) room polygon and no edges cross. */
export function rectInsidePolygon(corners, poly) {
  if (!corners.every((c) => pointInPolygon(c, poly))) return false;
  for (let i = 0; i < corners.length; i++) {
    const a = corners[i], b = corners[(i + 1) % corners.length];
    for (let j = 0; j < poly.length; j++) {
      if (segmentsCross(a, b, poly[j], poly[(j + 1) % poly.length])) return false;
    }
  }
  return true;
}

/** Rectangle around a door segment a-b, reaching `reach` metres to both sides (swing / walk zone). */
export function doorZone(a, b, reach) {
  const dx = b[0] - a[0], dz = b[1] - a[1];
  const len = Math.hypot(dx, dz) || 1;
  const nx = (-dz / len) * reach, nz = (dx / len) * reach;
  return [[a[0] + nx, a[1] + nz], [b[0] + nx, b[1] + nz], [b[0] - nx, b[1] - nz], [a[0] - nx, a[1] - nz]];
}

/** Strip of depth `depth` in front of a rectangle (local +Z side). */
export function frontStrip(x, z, w, d, rotDeg, depth) {
  const [fx, fz] = frontDir(rotDeg);
  const off = d / 2 + depth / 2;
  return rectCorners(x + fx * off, z + fz * off, w, depth, rotDeg);
}
