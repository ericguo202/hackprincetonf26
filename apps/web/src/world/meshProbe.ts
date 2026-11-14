// Lane C: find out whether a pack mesh exists BEFORE giving it to the GLTF
// loader. A missing file must never reach the loader: R3F's useLoader throws
// the parse error ("Unexpected token '<'" when Vite answers a missing file
// with index.html), and an error boundary catching it still leaves an
// uncaught error in the console. So: one HEAD request per URL (cached), one
// warning per missing URL, and the loader only ever sees files that exist.
// LoadBoundary stays as the last resort for a file that exists but is corrupt.

import { useEffect, useState } from "react";

import { warnOnce } from "./pack.ts";

/**
 * What a HEAD answer says about a mesh URL. Static hosts answer a missing
 * file in one of two ways, and both mean missing:
 *   - an SPA rewrite (Vite's dev server, and Render's web service, whose
 *     render.yaml rewrites /* to /index.html): 200 with text/html
 *   - no rewrite: 404 (or 403 from some buckets), often with an HTML body
 * A host that does not allow HEAD (405, 501) says nothing about the file:
 * "unknown", and the loader gets to try.
 */
export type MeshAnswer = "mesh" | "missing" | "unknown";

export function classifyHead(status: number, contentType: string | null): MeshAnswer {
  if (status === 405 || status === 501) return "unknown";
  if (status < 200 || status >= 300) return "missing";
  return (contentType ?? "").toLowerCase().includes("text/html") ? "missing" : "mesh";
}

/** A HEAD response means "this is a mesh" when it is 2xx and not an HTML page. */
export function isMeshResponse(status: number, contentType: string | null): boolean {
  return classifyHead(status, contentType) === "mesh";
}

const probes = new Map<string, Promise<boolean>>();

/**
 * Resolves true when the URL serves a mesh. A network error (for example a
 * bucket whose CORS rules do not allow HEAD) resolves true: we cannot tell,
 * so the loader gets to try and LoadBoundary catches a failure.
 */
export function probeMesh(url: string, label: string): Promise<boolean> {
  let p = probes.get(url);
  if (!p) {
    p = fetch(url, { method: "HEAD" })
      .then((r) => {
        const answer = classifyHead(r.status, r.headers.get("content-type"));
        if (answer === "unknown") return true; // HEAD not allowed: let the loader try
        const ok = answer === "mesh";
        if (!ok) {
          warnOnce(
            `missing:${url}`,
            `[pack] ${label} is missing (${r.status}${r.ok ? ", got an HTML page" : ""} at ${url}); rendering placeholder geometry`,
          );
        }
        return ok;
      })
      .catch(() => true);
    probes.set(url, p);
  }
  return p;
}

export type MeshState = "none" | "checking" | "ok" | "missing";

/** "none" for a null URL (no pack base), else the probe's answer. Render the placeholder for anything but "ok". */
export function useMeshState(url: string | null, label: string): MeshState {
  const [result, setResult] = useState<{ url: string; ok: boolean } | null>(null);
  useEffect(() => {
    if (!url) return;
    let alive = true;
    void probeMesh(url, label).then((ok) => {
      if (alive) setResult({ url, ok });
    });
    return () => {
      alive = false;
    };
  }, [url, label]);
  if (!url) return "none";
  if (!result || result.url !== url) return "checking";
  return result.ok ? "ok" : "missing";
}
