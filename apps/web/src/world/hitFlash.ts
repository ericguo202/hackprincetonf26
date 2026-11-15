// Lane C: my hits on remote players, by player id, for the hit flash
// (Character tints for HIT_FLASH_S) and for crediting an elimination to a hit
// of mine (a big foam burst). A small module store, not store.ts: it holds
// only local effects state. Times are seconds (performance.now based).

/** How long a hit player's model stays tinted. */
export const HIT_FLASH_S = 0.12;
/**
 * A remote going down within this long of my hit on them counts as my
 * elimination (covers the server round trip and the 100 ms interpolation).
 */
export const KILL_CREDIT_S = 1.0;

const lastHit = new Map<string, number>();

export const nowS = (): number => performance.now() / 1000;

/** I hit `id` (blaster dart or noodle swing with target_id). */
export function markHit(id: string, now = nowS()): void {
  lastHit.set(id, now);
}

/** Flash strength for `id`: 1 at the hit, falling to 0 at HIT_FLASH_S, 0 after (and with no hit). */
export function flashLevel(id: string, now = nowS()): number {
  const t = lastHit.get(id);
  if (t === undefined) return 0;
  const age = now - t;
  if (age < 0 || age >= HIT_FLASH_S) return 0;
  return 1 - age / HIT_FLASH_S;
}

/** I hit `id` within the last `within` seconds. */
export function hitRecently(id: string, now = nowS(), within = KILL_CREDIT_S): boolean {
  const t = lastHit.get(id);
  return t !== undefined && now - t >= 0 && now - t <= within;
}

/** Forget a credited hit (one elimination burst per hit). */
export function clearHit(id: string): void {
  lastHit.delete(id);
}

/** Tests only. */
export function resetHits(): void {
  lastHit.clear();
}
