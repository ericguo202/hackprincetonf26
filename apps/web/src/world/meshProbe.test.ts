import { afterEach, describe, expect, it, vi } from "vitest";

import { DEFAULT_RANGE, labelOpacity } from "./labelFade";
import { isMeshResponse, probeMesh } from "./meshProbe";
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
