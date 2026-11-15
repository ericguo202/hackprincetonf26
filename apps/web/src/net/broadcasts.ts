// Lane D: game -> client broadcasts (contracts/messages.md) into the store.
// hit {shooter, target, hp}, eliminated {target, by}, fx {kind, origin, dir}, toast {text}.
// Toasts and kill-feed lines expire by time; every list is capped so a busy match stays small.
import { type FxEvent, type KillFeedEntry, type Toast, type Vec3, useStore } from "../store";

export const TOAST_MS = 4000;
export const TOAST_MAX = 4;
export const FEED_MS = 8000;
export const FEED_MAX = 5;
export const FX_MAX = 8;

let nextId = 1;

function vec3(v: unknown): Vec3 | null {
  if (!Array.isArray(v) || v.length !== 3) return null;
  const n = v.map(Number);
  return n.every(Number.isFinite) ? (n as Vec3) : null;
}

function nameOf(id: string): string {
  return useStore.getState().players[id]?.name ?? "Someone";
}

/** Drop toasts and kill-feed lines older than their lifetime. Safe to call any time. */
export function pruneBroadcasts(now = Date.now()): void {
  const s = useStore.getState();
  const toasts = s.toasts.filter((t) => now - t.at < TOAST_MS);
  const killFeed = s.killFeed.filter((k) => now - k.at < FEED_MS);
  if (toasts.length !== s.toasts.length || killFeed.length !== s.killFeed.length) s.set({ toasts, killFeed });
}

function schedulePrune(ms: number) {
  if (typeof setTimeout === "function") setTimeout(() => pruneBroadcasts(), ms + 20);
}

export function applyBroadcast(type: string, data: unknown, now = Date.now()): void {
  const s = useStore.getState();
  const d = (data ?? {}) as Record<string, unknown>;
  switch (type) {
    case "toast": {
      if (typeof d.text !== "string" || !d.text) return;
      const t: Toast = { id: nextId++, text: d.text, at: now };
      s.set({ toasts: [...s.toasts, t].slice(-TOAST_MAX) });
      schedulePrune(TOAST_MS);
      return;
    }
    case "hit": {
      const target = String(d.target ?? "");
      if (!target || target !== s.me.id) return;
      const shooter = String(d.shooter ?? "");
      s.set({
        lastHitOnMe: { shooter, shooterName: nameOf(shooter), hp: Number(d.hp) || 0, at: now },
        hitsOnMe: s.hitsOnMe + 1,
      });
      return;
    }
    case "eliminated": {
      const target = String(d.target ?? "");
      const by = String(d.by ?? "");
      if (!target) return;
      const k: KillFeedEntry = { id: nextId++, target, by, targetName: nameOf(target), byName: nameOf(by), at: now };
      s.set({ killFeed: [...s.killFeed, k].slice(-FEED_MAX) });
      schedulePrune(FEED_MS);
      return;
    }
    case "fx": {
      const origin = vec3(d.origin);
      const dir = vec3(d.dir);
      if (!origin || !dir) return;
      const f: FxEvent = { id: nextId++, kind: String(d.kind ?? ""), origin, dir, at: now };
      s.set({ fx: [...s.fx, f].slice(-FX_MAX) });
      return;
    }
  }
}

export const BROADCAST_TYPES = ["toast", "hit", "eliminated", "fx"] as const;
