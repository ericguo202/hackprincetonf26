import { afterEach, describe, expect, it, vi } from "vitest";

import { DEFAULT_RANGE, labelOpacity } from "./labelFade";
import { classifyHead, isMeshResponse, probeMesh } from "./meshProbe";
import { resetWarnings } from "./pack";

afterEach(() => {
  resetWarnings();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("isMeshResponse", () => {
  it("accepts a served mesh", () => {
    expect(isMeshResponse(200, "model/gltf-binary")).toBe(true);
    expect(isMeshResponse(200, "application/octet-stream")).toBe(true);
    expect(isMeshResponse(200, null)).toBe(true);
  });

  it("rejects 404s and Vite's index.html answer for a missing file", () => {
    expect(isMeshResponse(404, "text/plain")).toBe(false);
    expect(isMeshResponse(200, "text/html")).toBe(false);
    expect(isMeshResponse(200, "text/html; charset=utf-8")).toBe(false);
  });
});

describe("classifyHead: every way a static host answers", () => {
  it("Render's web service (SPA rewrite /* -> /index.html) and Vite: missing = 200 HTML", () => {
    expect(classifyHead(200, "text/html; charset=utf-8")).toBe("missing");
  });
  it("a host without a rewrite: 404, with or without an HTML body; 403 from a bucket", () => {
    expect(classifyHead(404, "text/html")).toBe("missing");
    expect(classifyHead(404, null)).toBe("missing");
    expect(classifyHead(403, "application/xml")).toBe("missing");
  });
  it("a real mesh, whatever binary type the host labels it", () => {
    for (const t of ["model/gltf-binary", "model/gltf+json", "application/octet-stream", null]) {
      expect(classifyHead(200, t)).toBe("mesh");
    }
  });
  it("a host that rejects HEAD says nothing about the file", () => {
    expect(classifyHead(405, "text/html")).toBe("unknown");
    expect(classifyHead(501, null)).toBe("unknown");
  });
});

describe("probeMesh", () => {
  const head = (status: number, type: string) =>
    vi.fn(async () => new Response(null, { status, headers: { "content-type": type } }));

  it("finds a missing furniture mesh without loading it, and warns once per URL", async () => {
    const fetch = head(200, "text/html");
    vi.stubGlobal("fetch", fetch);
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const url = "/packs/x/v1/furniture/couch_01.glb?a";
    expect(await probeMesh(url, "furniture/couch_01.glb")).toBe(false);
    expect(await probeMesh(url, "furniture/couch_01.glb")).toBe(false);
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(fetch.mock.calls[0]).toEqual([url, { method: "HEAD" }]);
    expect(warn).toHaveBeenCalledTimes(1);
    expect(String(warn.mock.calls[0][0])).toMatch(/couch_01\.glb is missing/);
  });

  it("lets a real mesh through, silently", async () => {
    vi.stubGlobal("fetch", head(200, "model/gltf-binary"));
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    expect(await probeMesh("/packs/x/v1/houses/h/interior.glb?b", "interior.glb")).toBe(true);
    expect(warn).not.toHaveBeenCalled();
  });

  it("lets the loader try when the host rejects HEAD (405), silently", async () => {
    vi.stubGlobal("fetch", head(405, "text/html"));
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    expect(await probeMesh("/packs/x/v1/houses/h/interior.glb?d", "interior.glb")).toBe(true);
    expect(warn).not.toHaveBeenCalled();
  });

  it("treats a 404 as missing, like the HTML fallback", async () => {
    vi.stubGlobal("fetch", head(404, "text/html"));
    vi.spyOn(console, "warn").mockImplementation(() => {});
    expect(await probeMesh("/packs/x/v1/block/render.glb?e", "block/render.glb")).toBe(false);
  });

  it("lets the loader try when HEAD itself fails (CORS), rather than hide a real mesh", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("Failed to fetch"))));
    expect(await probeMesh("https://bucket/x.glb?c", "x.glb")).toBe(true);
  });
});

describe("labelOpacity", () => {
  it("is hidden up close, so labels never fill the screen", () => {
    expect(labelOpacity(0)).toBe(0);
    expect(labelOpacity(1.0)).toBe(0);
    expect(labelOpacity(DEFAULT_RANGE.hideBelow)).toBe(0);
  });

  it("is fully visible across a room", () => {
    for (const d of [2, 3, 5, 8]) expect(labelOpacity(d)).toBe(1);
  });

  it("fades out beyond 8 m and is gone by 10 m", () => {
    expect(labelOpacity(9)).toBeGreaterThan(0);
    expect(labelOpacity(9)).toBeLessThan(1);
    expect(labelOpacity(10)).toBe(0);
    expect(labelOpacity(30)).toBe(0);
  });

  it("never increases as you walk away past the fade start", () => {
    let prev = 1;
    for (let d = 8; d <= 10.5; d += 0.1) {
      const a = labelOpacity(d);
      expect(a).toBeLessThanOrEqual(prev + 1e-12);
      prev = a;
    }
  });

  it("takes a longer range for block door labels", () => {
    expect(labelOpacity(35, { fadeFrom: 40, fadeTo: 50 })).toBe(1);
  });
});
