// Lane C: procedural sound effects, WebAudio only (no files, no dependencies).
//
// One shared AudioContext, created or resumed on the first user gesture
// (browsers block audio before one: unlockAudio, called by SfxHud.tsx), into
// a master gain of MASTER_GAIN. Each sound is a short recipe of a few
// short-lived nodes (filtered bursts from one shared noise buffer, or a
// pitch-swept oscillator), all under 0.4 s. Every kind is rate-limited
// (RATE_LIMIT_S) so rapid fire doesn't stack into noise. Combat sounds are
// silent in explore mode; door clicks are not. M mutes (SfxHud.tsx), and the
// setting is kept in localStorage.
//
// Pure parts (tested in sfx.test.ts): remoteVolume, RateLimiter, the mute
// persistence, and the unlock gate in play().

import { create } from "zustand";

import { foamModeNow } from "./foamMode";

export type SfxKind =
  | "fire" // blaster thwip
  | "hitPlayer" // my dart hits a player: wet splat
  | "hitSurface" // my dart hits a wall or furniture: tick
  | "swing" // noodle whoosh
  | "bonk" // noodle hits a player
  | "pop" // elimination foam pop + sparkle
  | "doorOpen" // click + creak-lite
  | "doorClose" // click
  | "hurt" // I got hit: muffled thud
  | "thunk"; // a furniture piece lands: low muffled thump (lower for bigger pieces)

/** Sounds that only play in foam mode. */
const COMBAT: ReadonlySet<SfxKind> = new Set(["fire", "hitPlayer", "hitSurface", "swing", "bonk", "pop", "hurt"]);

export const MASTER_GAIN = 0.3;
/** At most one sound of a kind (per source: mine, others') this often, seconds. */
export const RATE_LIMIT_S = 0.04;
/** Other players' sounds: this loud at 0 m, falling to silent at REMOTE_MAX_M. */
export const REMOTE_GAIN = 0.55;
export const REMOTE_MAX_M = 25;
export const MUTE_KEY = "lc.sfx.muted";
/**
 * Kinds with a longer gap than RATE_LIMIT_S. A furnished room drops its
 * pieces in 0.12 s apart; at 0.15 s about every other landing thunks, so a
 * whole room doesn't pile up.
 */
export const KIND_GAP_S: Partial<Record<SfxKind, number>> = { thunk: 0.15 };

/**
 * The thunk's pitch (Hz) for a piece whose largest footprint side is `sizeM`
 * metres: 130 Hz for small things, falling to 70 Hz for a 2.7 m sofa or bed.
 */
export function thunkPitch(sizeM: number): number {
  const s = Number.isFinite(sizeM) ? Math.max(0, sizeM) : 0;
  return Math.max(70, Math.min(130, 130 - 22 * s));
}

// ---------------------------------------------------------------------------
// Pure parts
// ---------------------------------------------------------------------------

/** Volume for another player's sound `dist` metres away: REMOTE_GAIN up close, 0 at and beyond REMOTE_MAX_M. */
export function remoteVolume(dist: number): number {
  if (!(dist >= 0) || dist >= REMOTE_MAX_M) return 0;
  const f = 1 - dist / REMOTE_MAX_M;
  return REMOTE_GAIN * f * f;
}

/** Per-key minimum gap. allow() is true at most once per `gap` seconds per key. */
export class RateLimiter {
  private last = new Map<string, number>();
  constructor(private gap = RATE_LIMIT_S) {}
  allow(key: string, now: number, gap = this.gap): boolean {
    const t = this.last.get(key);
    if (t !== undefined && now - t < gap && now >= t) return false;
    this.last.set(key, now);
    return true;
  }
}

type StorageLike = Pick<Storage, "getItem" | "setItem">;

/** The saved mute setting; false when there is none or storage is unavailable. */
export function loadMuted(storage: () => StorageLike | undefined = () => globalThis.localStorage): boolean {
  try {
    return storage()?.getItem(MUTE_KEY) === "1";
  } catch {
    return false;
  }
}

/** Save the mute setting; storage errors (private mode, blocked) are ignored. */
export function saveMuted(muted: boolean, storage: () => StorageLike | undefined = () => globalThis.localStorage): void {
  try {
    storage()?.setItem(MUTE_KEY, muted ? "1" : "0");
  } catch {
    /* not persisted */
  }
}

/** Mute state, for the indicator and play(). */
export const useSfxMute = create<{ muted: boolean; toggle: () => void; set: (m: boolean) => void }>()((set, get) => ({
  muted: loadMuted(),
  toggle: () => {
    const muted = !get().muted;
    saveMuted(muted);
    set({ muted });
  },
  set: (muted) => {
    saveMuted(muted);
    set({ muted });
  },
}));

// ---------------------------------------------------------------------------
// Engine
// ---------------------------------------------------------------------------

type Engine = { ctx: AudioContext; master: GainNode; noise: AudioBuffer };

let engine: Engine | null = null;
let makeContext: () => AudioContext = () => new AudioContext();
const limiter = new RateLimiter();

/** Tests only: swap the AudioContext constructor and forget the engine. */
export function _setAudioContextFactory(f: () => AudioContext): void {
  makeContext = f;
  engine = null;
}

/**
 * Create (first time) or resume the shared context. Call from a user gesture
 * handler: before one, browsers keep the context suspended.
 */
export function unlockAudio(): void {
  try {
    if (!engine) {
      const ctx = makeContext();
      const master = ctx.createGain();
      master.gain.value = MASTER_GAIN;
      master.connect(ctx.destination);
      // 0.5 s of white noise, reused by every noise recipe.
      const noise = ctx.createBuffer(1, Math.ceil(ctx.sampleRate * 0.5), ctx.sampleRate);
      const d = noise.getChannelData(0);
      for (let i = 0; i < d.length; i++) d[i] = Math.random() * 2 - 1;
      engine = { ctx, master, noise };
    }
    if (engine.ctx.state === "suspended") void engine.ctx.resume();
  } catch {
    engine = null; // no WebAudio: stay silent
  }
}

/** The context exists and runs (a gesture has unlocked it). */
export function audioUnlocked(): boolean {
  return engine !== null && engine.ctx.state === "running";
}

export type PlayOptions = {
  /** 0..1 on top of the master gain (remoteVolume for other players' sounds). */
  volume?: number;
  /** thunk: the piece's largest footprint side, metres (thunkPitch). */
  size?: number;
  /** Rate-limit separately from my own sounds of the same kind. */
  remote?: boolean;
};

/**
 * Play a sound. False (and nothing created) when audio isn't unlocked yet,
 * muted, a combat sound in explore mode, silent (volume 0), or rate-limited.
 */
export function play(kind: SfxKind, opts: PlayOptions = {}): boolean {
  const vol = opts.volume ?? 1;
  if (!engine || engine.ctx.state !== "running") return false;
  if (useSfxMute.getState().muted || !(vol > 0)) return false;
  if (COMBAT.has(kind) && !foamModeNow()) return false;
  const { ctx } = engine;
  if (!limiter.allow(opts.remote ? `${kind}:r` : kind, ctx.currentTime, KIND_GAP_S[kind] ?? RATE_LIMIT_S)) return false;
  const out = ctx.createGain();
  out.gain.value = Math.min(1, vol);
  out.connect(engine.master);
  RECIPES[kind](engine, out, ctx.currentTime, opts);
  // Let the per-sound gain go once its recipe is over (all under 0.4 s).
  setTimeout(() => out.disconnect(), 600);
  return true;
}

// ---------------------------------------------------------------------------
// Recipes: a few short-lived nodes each.
// ---------------------------------------------------------------------------

type Burst = {
  dur: number;
  type: BiquadFilterType;
  f0: number;
  f1?: number;
  q?: number;
  gain: number;
  attack?: number;
  delay?: number;
};

/** A filtered slice of the shared noise, with a quick envelope and an optional filter sweep. */
function noise(e: Engine, out: AudioNode, t0: number, b: Burst): void {
  const t = t0 + (b.delay ?? 0);
  const src = e.ctx.createBufferSource();
  src.buffer = e.noise;
  const f = e.ctx.createBiquadFilter();
  f.type = b.type;
  f.Q.value = b.q ?? 0.8;
  f.frequency.setValueAtTime(b.f0, t);
  if (b.f1 !== undefined) f.frequency.exponentialRampToValueAtTime(b.f1, t + b.dur);
  const g = e.ctx.createGain();
  const a = b.attack ?? 0.004;
  g.gain.setValueAtTime(0.0001, t);
  g.gain.exponentialRampToValueAtTime(b.gain, t + a);
  g.gain.exponentialRampToValueAtTime(0.0001, t + b.dur);
  src.connect(f).connect(g).connect(out);
  // A random offset into the buffer so repeats don't sound identical.
  src.start(t, Math.random() * 0.4, b.dur + 0.02);
}

type Tone = { dur: number; type: OscillatorType; f0: number; f1?: number; gain: number; delay?: number; attack?: number };

/** One oscillator with a pitch sweep and a quick envelope. */
function tone(e: Engine, out: AudioNode, t0: number, s: Tone): void {
  const t = t0 + (s.delay ?? 0);
  const o = e.ctx.createOscillator();
  o.type = s.type;
  o.frequency.setValueAtTime(s.f0, t);
  if (s.f1 !== undefined) o.frequency.exponentialRampToValueAtTime(s.f1, t + s.dur);
  const g = e.ctx.createGain();
  g.gain.setValueAtTime(0.0001, t);
  g.gain.exponentialRampToValueAtTime(s.gain, t + (s.attack ?? 0.005));
  g.gain.exponentialRampToValueAtTime(0.0001, t + s.dur);
  o.connect(g).connect(out);
  o.start(t);
  o.stop(t + s.dur + 0.02);
}

const RECIPES: Record<SfxKind, (e: Engine, out: AudioNode, t: number, opts: PlayOptions) => void> = {
  thunk: (e, out, t, opts) => {
    const f = thunkPitch(opts.size ?? 0.8);
    tone(e, out, t, { dur: 0.22, type: "sine", f0: f, f1: f * 0.55, gain: 0.7, attack: 0.004 });
    noise(e, out, t, { dur: 0.12, type: "lowpass", f0: 380, f1: 160, gain: 0.35 });
  },
  fire: (e, out, t) => {
    noise(e, out, t, { dur: 0.09, type: "bandpass", f0: 3200, f1: 700, q: 1.2, gain: 0.6 });
    tone(e, out, t, { dur: 0.06, type: "sine", f0: 900, f1: 280, gain: 0.25 });
  },
  hitPlayer: (e, out, t) => {
    noise(e, out, t, { dur: 0.16, type: "lowpass", f0: 1100, f1: 260, q: 2, gain: 0.8, attack: 0.006 });
    tone(e, out, t, { dur: 0.1, type: "sine", f0: 220, f1: 120, gain: 0.25 });
  },
  hitSurface: (e, out, t) => {
    noise(e, out, t, { dur: 0.03, type: "highpass", f0: 3800, gain: 0.35, attack: 0.002 });
  },
  swing: (e, out, t) => {
    noise(e, out, t, { dur: 0.24, type: "bandpass", f0: 350, f1: 2600, q: 1.6, gain: 0.55, attack: 0.08 });
  },
  bonk: (e, out, t) => {
    tone(e, out, t, { dur: 0.2, type: "sine", f0: 340, f1: 110, gain: 0.75 });
    tone(e, out, t, { dur: 0.08, type: "triangle", f0: 680, f1: 300, gain: 0.2 });
  },
  pop: (e, out, t) => {
    tone(e, out, t, { dur: 0.07, type: "sine", f0: 520, f1: 1500, gain: 0.6 });
    tone(e, out, t, { dur: 0.07, type: "sine", f0: 760, f1: 1900, gain: 0.4, delay: 0.05 });
    noise(e, out, t, { dur: 0.08, type: "bandpass", f0: 1800, q: 1, gain: 0.3 });
    // Sparkle: three quick high blips.
    tone(e, out, t, { dur: 0.06, type: "sine", f0: 2600, gain: 0.12, delay: 0.12 });
    tone(e, out, t, { dur: 0.06, type: "sine", f0: 3300, gain: 0.1, delay: 0.17 });
    tone(e, out, t, { dur: 0.08, type: "sine", f0: 4100, gain: 0.08, delay: 0.22 });
  },
  doorOpen: (e, out, t) => {
    noise(e, out, t, { dur: 0.025, type: "highpass", f0: 2400, gain: 0.35, attack: 0.002 });
    tone(e, out, t, { dur: 0.025, type: "square", f0: 1700, gain: 0.06 });
    // Creak-lite: a soft, slow rising sawtooth.
    tone(e, out, t, { dur: 0.32, type: "sawtooth", f0: 170, f1: 260, gain: 0.05, delay: 0.03, attack: 0.06 });
  },
  doorClose: (e, out, t) => {
    noise(e, out, t, { dur: 0.03, type: "highpass", f0: 1800, gain: 0.4, attack: 0.002 });
    tone(e, out, t, { dur: 0.05, type: "sine", f0: 220, f1: 140, gain: 0.25 });
  },
  hurt: (e, out, t) => {
    tone(e, out, t, { dur: 0.18, type: "sine", f0: 130, f1: 55, gain: 0.9 });
    noise(e, out, t, { dur: 0.1, type: "lowpass", f0: 320, gain: 0.5 });
  },
};

/** Distance from `a` to `b` (block or house frame, metres). */
export function distance(a: readonly number[], b: readonly number[]): number {
  return Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
}
