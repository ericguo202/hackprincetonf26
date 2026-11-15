import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useStore } from "../store";
import {
  _setAudioContextFactory,
  audioUnlocked,
  loadMuted,
  MUTE_KEY,
  play,
  RATE_LIMIT_S,
  RateLimiter,
  REMOTE_GAIN,
  REMOTE_MAX_M,
  remoteVolume,
  saveMuted,
  unlockAudio,
  useSfxMute,
} from "./sfx";

/** A minimal stand-in for AudioContext that counts the nodes a sound creates. */
class FakeAudioContext {
  state: AudioContextState = "suspended";
  currentTime = 0;
  sampleRate = 8000;
  destination = {};
  created = 0;
  resume = vi.fn(async () => {
    this.state = "running";
  });
  private param() {
    return { value: 0, setValueAtTime: vi.fn(), exponentialRampToValueAtTime: vi.fn() };
  }
  private node() {
    this.created++;
    const n = {
      connect: (to: unknown) => to,
      disconnect: vi.fn(),
      start: vi.fn(),
      stop: vi.fn(),
      gain: this.param(),
      frequency: this.param(),
      Q: this.param(),
      type: "",
      buffer: null,
    };
    return n;
  }
  createGain() {
    return this.node();
  }
  createBiquadFilter() {
    return this.node();
  }
  createOscillator() {
    return this.node();
  }
  createBufferSource() {
    return this.node();
  }
  createBuffer(_c: number, length: number) {
    const data = new Float32Array(length);
    return { getChannelData: () => data };
  }
}

describe("remoteVolume", () => {
  it("is REMOTE_GAIN up close, falls with distance, and is silent from 25 m", () => {
    expect(remoteVolume(0)).toBeCloseTo(REMOTE_GAIN, 9);
    expect(REMOTE_GAIN).toBeLessThan(1); // others are quieter than mine
    expect(remoteVolume(5)).toBeLessThan(remoteVolume(1));
    expect(remoteVolume(20)).toBeLessThan(remoteVolume(5));
    expect(remoteVolume(24.9)).toBeGreaterThan(0);
    expect(remoteVolume(REMOTE_MAX_M)).toBe(0);
    expect(REMOTE_MAX_M).toBe(25);
    expect(remoteVolume(100)).toBe(0);
    expect(remoteVolume(Number.NaN)).toBe(0);
  });
});

describe("RateLimiter", () => {
  it("allows one per RATE_LIMIT_S per key", () => {
    const r = new RateLimiter();
    expect(r.allow("fire", 1)).toBe(true);
    expect(r.allow("fire", 1 + RATE_LIMIT_S / 2)).toBe(false);
    expect(r.allow("fire:r", 1 + RATE_LIMIT_S / 2)).toBe(true); // separate key
    expect(r.allow("swing", 1.001)).toBe(true);
    expect(r.allow("fire", 1 + RATE_LIMIT_S)).toBe(true);
  });
  it("40 rapid calls in 0.2 s let through only about 5", () => {
    const r = new RateLimiter();
    let n = 0;
    for (let i = 0; i < 40; i++) if (r.allow("fire", i * 0.005)) n++;
    expect(n).toBe(5);
  });
});

describe("mute persistence", () => {
  const mockStorage = () => {
    const m = new Map<string, string>();
    return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v), m };
  };

  it("saves and loads through localStorage", () => {
    const s = mockStorage();
    expect(loadMuted(() => s)).toBe(false);
    saveMuted(true, () => s);
    expect(s.m.get(MUTE_KEY)).toBe("1");
    expect(loadMuted(() => s)).toBe(true);
    saveMuted(false, () => s);
    expect(loadMuted(() => s)).toBe(false);
  });

  it("never throws when storage is blocked", () => {
    const blocked = () => {
      throw new Error("SecurityError");
    };
    expect(loadMuted(blocked)).toBe(false);
    expect(() => saveMuted(true, blocked)).not.toThrow();
    const throwing = { getItem: () => { throw new Error("x"); }, setItem: () => { throw new Error("x"); } };
    expect(loadMuted(() => throwing)).toBe(false);
    expect(() => saveMuted(true, () => throwing)).not.toThrow();
  });

  it("the M toggle flips the state and persists it", () => {
    const set = vi.spyOn(Storage.prototype, "setItem");
    useSfxMute.getState().set(false);
    useSfxMute.getState().toggle();
    expect(useSfxMute.getState().muted).toBe(true);
    expect(set).toHaveBeenLastCalledWith(MUTE_KEY, "1");
    useSfxMute.getState().toggle();
    expect(useSfxMute.getState().muted).toBe(false);
    expect(set).toHaveBeenLastCalledWith(MUTE_KEY, "0");
    set.mockRestore();
  });
});

describe("play", () => {
  let made: FakeAudioContext[];
  beforeEach(() => {
    made = [];
    _setAudioContextFactory(() => {
      const c = new FakeAudioContext();
      made.push(c);
      return c as unknown as AudioContext;
    });
    useSfxMute.getState().set(false);
    useStore.setState({ mode: "foam" });
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
    useStore.setState({ mode: "explore" });
  });

  it("plays nothing and creates no context before the first gesture unlocks it", () => {
    expect(play("fire")).toBe(false);
    expect(play("doorOpen")).toBe(false);
    expect(made).toHaveLength(0);
    expect(audioUnlocked()).toBe(false);
  });

  it("after unlock: plays, with a few nodes per sound; rate-limited; muted is silent", async () => {
    unlockAudio();
    await Promise.resolve();
    expect(made).toHaveLength(1);
    expect(made[0].resume).toHaveBeenCalled();
    expect(audioUnlocked()).toBe(true);
    const before = made[0].created;
    expect(play("fire")).toBe(true);
    const perSound = made[0].created - before;
    expect(perSound).toBeGreaterThan(0);
    expect(perSound).toBeLessThanOrEqual(8);
    expect(play("fire")).toBe(false); // same instant: rate-limited
    made[0].currentTime += RATE_LIMIT_S;
    expect(play("fire")).toBe(true);
    useSfxMute.getState().set(true);
    made[0].currentTime += 1;
    expect(play("fire")).toBe(false);
    useSfxMute.getState().set(false);
    // A second unlock reuses the same context.
    unlockAudio();
    expect(made).toHaveLength(1);
  });

  it("combat sounds are silent in explore mode; door clicks still play", async () => {
    unlockAudio();
    await Promise.resolve();
    useStore.setState({ mode: "explore" });
    for (const k of ["fire", "hitPlayer", "hitSurface", "swing", "bonk", "pop", "hurt"] as const) expect(play(k)).toBe(false);
    expect(play("doorOpen")).toBe(true);
    expect(play("doorClose")).toBe(true);
  });

  it("a remote sound at 0 volume (beyond 25 m) is not played", async () => {
    unlockAudio();
    await Promise.resolve();
    expect(play("fire", { volume: remoteVolume(30), remote: true })).toBe(false);
    expect(play("fire", { volume: remoteVolume(5), remote: true })).toBe(true);
  });
});
